#!/usr/bin/env python3
"""Render the 11-second demo clip shown in the README (1280x720, 30 fps, H.264).

Frames are piped straight into ffmpeg; nothing is written to disk but the mp4.
Text is rendered with PIL so the terminal mock is pixel-exact, not a screen
recording of a real session that might leak a prompt.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1280, 720, 30
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
CJK_B = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

f_title = ImageFont.truetype(SANS_B, 76)
f_tag = ImageFont.truetype(SANS, 30)
f_mono = ImageFont.truetype(MONO, 25)
f_mono_b = ImageFont.truetype(MONO_B, 25)
f_cjk = ImageFont.truetype(CJK, 26)
f_foot = ImageFont.truetype(MONO, 21)
f_label = ImageFont.truetype(MONO, 20)


def base() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 5], fill=ACCENT)
    return img, d


def terminal_card(d: ImageDraw.ImageDraw, y: int = 250) -> tuple[int, int, int, int]:
    x0, y0, x1, y1 = 78, y, W - 78, H - 70
    d.rounded_rectangle([x0, y0, x1, y1], radius=16, fill=CARD)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([x0 + 24 + i * 28, y0 + 20, x0 + 40 + i * 28, y0 + 36], fill=c)
    return x0, y0, x1, y1


def draw_mixed(d, x, y, text, font_ascii, font_cjk, fill, cursor=False):
    """Draw a string that may contain CJK, advancing x by each glyph's own width."""
    for ch in text:
        font = font_cjk if ord(ch) > 0x2E80 else font_ascii
        d.text((x, y), ch, font=font, fill=fill)
        x += d.textlength(ch, font=font)
    if cursor:
        d.rectangle([x + 3, y + 4, x + 15, y + 28], fill=ACCENT)
    return x


def frame(n: int) -> Image.Image:
    t = n / FPS
    img, d = base()

    # ── header: always present, slides up slightly during the intro ──────
    slide = max(0, 26 - int(t * 60))
    d.text((80, 58 + slide), "skill-hint", font=f_title, fill=FG)
    d.text((82, 150 + slide), "local-first skill discovery for Hermes Agent", font=f_tag, fill=ACCENT)


    x0, y0, _x1, _y1 = terminal_card(d, y=200)
    lh = 32
    ty = y0 + 64

    if t >= 1.2:
        d.text((x0 + 28, ty), "$ hermes chat", font=f_mono, fill=DIM)
        ty += lh + 8

        # scene 1: english query, typed
        q1 = "merge these two pdf files"
        shown1 = q1[: max(0, int((t - 1.4) * 26))]
        d.text((x0 + 28, ty), "you >", font=f_mono_b, fill=YELLOW)
        end = draw_mixed(d, x0 + 118, ty, shown1, f_mono, f_cjk, FG,
                         cursor=(t < 4.0 and len(shown1) == len(q1)))
        ty += lh + 18

        if t >= 3.0:
            hint1 = [
                ("[skill-hint] The message may match these skills", FG),
                ("- load one with skill_view before acting:", FG),
                ("- pdf: PDF files: create, read, merge, fill, OCR, edit text.", GREEN),
            ]
            reveal = min(len(hint1), 1 + int((t - 3.0) * 9))
            for i, (line, color) in enumerate(hint1[:reveal]):
                d.text((x0 + 28, ty), line, font=f_mono, fill=color)
                ty += lh
            ty += 10

    # scene 2: chinese query, no aliases, still matched
    if t >= 5.4:
        q2 = "把这份周报做成ppt"
        shown2 = q2[: max(0, int((t - 5.5) * 18))]
        d.text((x0 + 28, ty), "you >", font=f_mono_b, fill=YELLOW)
        draw_mixed(d, x0 + 118, ty, shown2, f_mono, f_cjk, FG,
                   cursor=(t < 8.2 and len(shown2) == len(q2)))
        ty += lh + 18

    if t >= 7.4:
        hint2 = [
            ("[skill-hint] 这句话可能对应下面技能：", FG),
            ("- pptx-template-modification: 修改现有 PPTX 模板", GREEN),
            ("- weekly-report-xlsx-fill: 填充周总结与活动汇总表", GREEN),
        ]
        reveal = min(len(hint2), 1 + int((t - 7.4) * 9))
        for i, (line, color) in enumerate(hint2[:reveal]):
            draw_mixed(d, x0 + 28, ty, line, f_mono, f_cjk, color)
            ty += lh

    # ── footer ───────────────────────────────────────────────────────────
    d.text((80, H - 36), "BM25 · stdlib only · 0.03 ms per query · no network · no API key",
           font=f_foot, fill=DIM)
    return img


def main() -> int:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "skill-hint-demo.mp4")
    total = int(11.0 * FPS)
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
