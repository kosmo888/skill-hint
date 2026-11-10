#!/usr/bin/env python3
"""Render the 13-second demo clip shown in the README (1280x720, 30 fps, H.264).

Frames are piped straight into ffmpeg; nothing is written to disk but the mp4.
Text is rendered with PIL so the terminal mock is pixel-exact, not a screen
recording of a real session that might leak a prompt.

Every line of skill-hint output below is copied verbatim from a real run:

    python -c "import sys;sys.path.insert(0,'.');\\
    from skill_matcher import SkillIndex;\\
    print(SkillIndex(['~/.hermes/skills']).build().match('merge these two pdf files'))"

The header strings are the module constants _HEADER_EN / _HEADER_ZH in
__init__.py, hard-coded here so the render is reproducible on any machine.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 30
DURATION = 13.0
BG = (13, 16, 21)
CARD = (23, 27, 34)
FG = (235, 239, 245)
DIM = (128, 138, 152)
ACCENT = (94, 200, 255)
GREEN = (122, 222, 152)
YELLOW = (240, 200, 110)

SANS_B = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
SANS = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"
MONO_B = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"
CJK = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"

f_title = ImageFont.truetype(SANS_B, 72)
f_tag = ImageFont.truetype(SANS, 28)
f_mono = ImageFont.truetype(MONO, 22)
f_mono_b = ImageFont.truetype(MONO_B, 22)
f_cjk = ImageFont.truetype(CJK, 23)
f_foot = ImageFont.truetype(MONO, 20)

# ── verbatim output of the plugin (see module docstring) ─────────────────────
HEADER_EN = (
    "[skill-hint] The message may match these skills \u2014 load one with skill_view "
    "before acting (auto-matched, may be wrong):"
)
HEADER_ZH = "[skill-hint] \u8fd9\u53e5\u8bdd\u53ef\u80fd\u5bf9\u5e94\u4e0b\u9762\u6280\u80fd\uff0c\u52a8\u624b\u524d\u5148 skill_view \u52a0\u8f7d\u786e\u8ba4\uff08\u81ea\u52a8\u5339\u914d\uff0c\u672a\u5fc5\u51c6\uff09\uff1a"

Q1 = "merge these two pdf files"
HITS1 = [
    "- pdf: PDF files: create, read, merge, fill, OCR, edit text.",
    "- hermes-agent-skill-authoring: Author in-repo SKILL.md files: frontmatter and structure.",
]
Q2 = "\u722c\u53d6\u8fd9\u4e2a\u516c\u4f17\u53f7\u6587\u7ae0"
HITS2 = [
    "- wechat-article-spider: \u5fae\u4fe1\u516c\u4f17\u53f7\u6587\u7ae0\u722c\u866b - "
    "\u5c06\u5fae\u4fe1\u516c\u53f7\u6587\u7ae0\u8f6c\u6362\u4e3a Markdown + \u672c\u5730\u56fe\u7247",
]


def base() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 5], fill=ACCENT)
    return img, d


def terminal_card(d: ImageDraw.ImageDraw, y: int = 196) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = 78, y, W - 78, H - 66
    d.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=CARD)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([x0 + 24 + i * 28, y0 + 20, x0 + 40 + i * 28, y0 + 36], fill=c)
    return x0, y0, x1, y1


def glyph_font(ch: str):
    return f_cjk if ord(ch) > 0x2E80 else f_mono


def mixed_width(text: str) -> float:
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    return sum(d.textlength(ch, font=glyph_font(ch)) for ch in text)


def wrap_mixed(text: str, max_px: float) -> list[str]:
    """Word-wrap like a terminal: ASCII words stay whole, CJK breaks per glyph."""
    tokens: list[str] = []
    buf = ""
    for ch in text:
        if ord(ch) > 0x2E80:
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(ch)
        elif ch == " ":
            if buf:
                tokens.append(buf)
                buf = ""
            tokens.append(" ")
        else:
            buf += ch
    if buf:
        tokens.append(buf)

    lines: list[str] = []
    cur = ""
    cur_w = 0.0
    for tok in tokens:
        w = mixed_width(tok)
        if cur_w + w > max_px and cur.strip():
            lines.append(cur.rstrip())
            cur, cur_w = "", 0.0
            if tok == " ":
                continue
        if not cur and tok == " ":
            continue
        cur += tok
        cur_w += w
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def draw_mixed(d, x, y, text, fill, cursor=False):
    """Draw a string that may contain CJK, advancing x by each glyph's own width."""
    for ch in text:
        font = glyph_font(ch)
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font)
    if cursor:
        d.rectangle([x + 3, y + 4, x + 14, y + 25], fill=ACCENT)
    return x


def frame(n: int) -> Image.Image:
    t = n / FPS
    img, d = base()

    slide = max(0, 24 - int(t * 60))
    d.text((80, 52 + slide), "skill-hint", font=f_title, fill=FG)
    d.text((82, 138 + slide), "local-first skill discovery for Hermes Agent", font=f_tag, fill=ACCENT)

    x0, y0, x1, _y1 = terminal_card(d)
    lh = 29
    inner = x1 - x0 - 56
    ty = y0 + 62

    def emit(text: str, fill=FG, indent: int = 0) -> None:
        nonlocal ty
        for i, line in enumerate(wrap_mixed(text, inner - indent)):
            draw_mixed(d, x0 + 28 + (indent if i else 0), ty, line, fill)
            ty += lh

    # ── scene 0: prompt ──────────────────────────────────────────────────
    if t >= 1.0:
        d.text((x0 + 28, ty), "$ hermes chat", font=f_mono, fill=DIM)
        ty += lh + 6

    # ── scene 1: english query ───────────────────────────────────────────
    if t >= 1.4:
        shown1 = Q1[: max(0, int((t - 1.5) * 26))]
        d.text((x0 + 28, ty), "you >", font=f_mono_b, fill=YELLOW)
        draw_mixed(d, x0 + 112, ty, shown1, FG,
                   cursor=(t < 3.6 and len(shown1) == len(Q1)))
        ty += lh + 12

    if t >= 3.5:
        emit(HEADER_EN, FG)
        seen = 0
        for hit in HITS1:
            if t < 3.9 + seen * 0.55:
                break
            emit(hit, GREEN)
            seen += 1
        ty += 8

    # ── scene 2: chinese query, same index, no extra config ──────────────
    if t >= 6.4:
        shown2 = Q2[: max(0, int((t - 6.5) * 16))]
        d.text((x0 + 28, ty), "you >", font=f_mono_b, fill=YELLOW)
        draw_mixed(d, x0 + 112, ty, shown2, FG,
                   cursor=(t < 8.4 and len(shown2) == len(Q2)))
        ty += lh + 12

    if t >= 8.3:
        emit(HEADER_ZH, FG)
        seen = 0
        for hit in HITS2:
            if t < 8.7 + seen * 0.55:
                break
            emit(hit, GREEN)
            seen += 1

    d.text((80, H - 34), "BM25 \u00b7 stdlib only \u00b7 p50 0.031 ms per query \u00b7 no network \u00b7 no API key",
           font=f_foot, fill=DIM)
    return img


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "skill-hint-demo.mp4")
    total = int(DURATION * FPS)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20",
        "-movflags", "+faststart", str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    assert proc.stdin
    for n in range(total):
        proc.stdin.write(frame(n).tobytes())
    proc.stdin.close()
    code = proc.wait()
    if code == 0:
        print(f"wrote {out} ({out.stat().st_size / 1024:.0f} KB, {total / FPS:.1f}s)")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
