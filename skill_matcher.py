"""Local BM25 index over a Hermes skill library. Standard library only.

No network, no LLM call, no API key, no third-party dependency: the whole
matcher is ~350 lines of pure Python and runs in single-digit milliseconds
once the index is warm.

Design notes
------------
* Two fields per document: ``core`` (name + front-matter description, the
  part a human actually curates) and ``body`` (first N chars of the SKILL.md
  body with fenced code stripped). ``core`` terms are weighted
  ``core_weight`` x so a description match beats a mention buried in prose.
* A document is only a candidate when at least ``min_core`` query terms hit
  its ``core`` field, or when a user alias fires. This is the "rather miss
  than spam" gate: injecting a wrong skill every turn is worse than
  injecting none.
* Aliases are a plain ``alias: skill-name`` mapping — the escape hatch for
  shorthand ("screenshot" -> a long skill name) and for non-English vocab.
* Symlinked skill directories are followed (``os.walk(followlinks=True)``):
  many setups manage skills as symlinks into a dotfiles repo, and a naive
  ``rglob`` silently hides them.
"""
from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Iterable, NamedTuple

# ── tuning ────────────────────────────────────────────────────────────────
K1 = 1.2
B = 0.75
CORE_WEIGHT = 2.0
NAME_WEIGHT = 3.0   # the name is the strongest human-curated signal there is
BODY_WEIGHT = 1.0
BODY_CHARS = 1000
DEFAULT_MIN_SCORE = 3.0
DEFAULT_RELATIVE_FLOOR = 0.35
DEFAULT_MIN_CORE_HITS = 1
MAX_NAME_CHARS = 64
ALIAS_SCORE = 1e6  # an alias is a deliberate user instruction: it outranks any BM25 hit

_SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", "node_modules", "site-packages",
    ".venv", "venv", ".archive", ".trash", ".inbox", "backups", ".mypy_cache",
    ".pytest_cache", "dist", "build",
}

_ASCII_STOP = frozenset("""
a an the and or but if then than that this these those of in on at to for from by
with without into onto over under about as is are was were be been being am do does
did done have has had having i me my mine you your yours it its we our us they them
their theirs he she his her him not no yes ok okay can could would should will shall
may might must just very more most some any all each every few many much there here
what which who whom whose when where why how please help need wants want make makes
making get gets got let lets use uses using via per also too only even still yet
again already then once so such own same other another both either neither
create creates creating add adds adding check checks generate generates make
makes making show shows list lists find finds open opens run runs put takes take
give gives tell tells think look looks try tries work works know call calls
read reads write writes get gets got set sets update updates install installs
""".split())

# CJK fragments that carry no routing signal on their own.
_CJK_STOP = frozenset("""
帮我 我帮 我想 我要 我要 我要 一下 一个 这个 那个 这些 那些 什么 怎么 怎样 如何 可以
是不是 有没有 现在 然后 还有 就是 但是 因为 所以 已经 之前 之后 时候 请问 麻烦 谢谢
我们 你们 他们 她们 它们 自己的 东西 事情 问题 需要 应该 帮我查 我查 帮忙
""".split())

_CJK_CHAR = "\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af"
_CJK_RE = re.compile(f"[{_CJK_CHAR}]")
_CJK_RUN = re.compile(f"[{_CJK_CHAR}]+")
_ASCII_RE = re.compile(r"[a-z0-9][a-z0-9_.+#-]*")
_FRONT_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*(?:\n|\Z)", re.S)
_FM_LINE = re.compile(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$")
_FM_KEYS = ("name", "description", "summary", "title")
_FM_BLOCK = ("|", "|-", "|+", ">", ">-", ">+", "||", ">-")
_FENCE_RE = re.compile(r"```.*?```", re.S)
_ALIAS_RE = re.compile(r"^\s*([^\s:#][^:]*?)\s*:\s*([A-Za-z0-9_.-]+)\s*$")
_ALIAS_KEY_RE = re.compile(r"^([^\s:#][^:]{0,63}):\s*(.*)$")
_ALIAS_INLINE_RE = re.compile(r"^\[(.*)\]$")


class SkillDoc(NamedTuple):
    name: str
    path: str
    description: str
    core: frozenset[str]
    body: frozenset[str]
    name_terms: frozenset[str] = frozenset()


def has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))


def _stem(word: str) -> str:
    """Crude English plural strip: "flowcharts" -> "flowchart", "files" -> "file".

    Deliberately not a real stemmer — it only has to make the query and the
    description agree on number, and it runs on both sides so it can never
    create a mismatch.
    """
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("sses", "shes", "ches", "xes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is", "os")):
        return word[:-1]
    return word


def _cjk_fragments(chunk: str) -> list[str]:
    """2- and 3-gram slices of a CJK run — the standard no-tokenizer trick."""
    out: list[str] = []
    n = len(chunk)
    for size in (2, 3):
        for i in range(n - size + 1):
            gram = chunk[i:i + size]
            if gram not in _CJK_STOP:
                out.append(gram)
    return out


def tokens(text: str) -> list[str]:
    """Lowercase the text and split it into ASCII words + CJK n-grams."""
    low = (text or "").lower()
    out: list[str] = []
    seen: set[str] = set()
    for raw in _ASCII_RE.findall(low):
        if len(raw) < 2 or raw in _ASCII_STOP:
            continue
        w = _stem(raw)
        if w not in _ASCII_STOP and w not in seen:
            seen.add(w)
            out.append(w)
    for run in _CJK_RUN.findall(low):
        for gram in _cjk_fragments(run):
            if gram not in seen:
                seen.add(gram)
                out.append(gram)
    return out


def _name_tokens(name: str) -> list[str]:
    parts = [p for p in re.split(r"[-_./\s]+", (name or "").lower()) if len(p) >= 2]
    joined = (name or "").lower().replace("-", "").replace("_", "")
    return parts + ([joined] if len(joined) >= 3 else [])


def _front_matter(text: str) -> tuple[dict[str, str], str]:
    """Small YAML front-matter reader: plain scalars and ``|`` / ``>`` blocks.

    Skill descriptions are routinely written as multi-line block scalars; a
    naive ``split(":")`` turns those into the literal string "|" and throws
    the whole description away.
    """
    m = _FRONT_RE.match(text)
    if not m:
        return {}, text
    meta: dict[str, str] = {}
    block_key: str | None = None
    block_buf: list[str] = []

    def flush() -> None:
        nonlocal block_key, block_buf
        if block_key and block_buf:
            meta[block_key] = " ".join(block_buf).strip()
        block_key, block_buf = None, []

    for raw in m.group(1).splitlines():
        if raw[:1] in (" ", "\t"):
            if block_key:
                block_buf.append(raw.strip())
            continue
        flush()
        if raw.lstrip().startswith("#"):
            continue
        mm = _FM_LINE.match(raw)
        if not mm:
            continue
        key = mm.group(1).strip().lower()
        value = mm.group(2).strip()
        if key not in _FM_KEYS:
            continue
        if value in _FM_BLOCK:
            block_key = key
            block_buf = []
        else:
            meta[key] = value.strip('"').strip("'")
    flush()
    return meta, text[m.end():]


def _clean_body(body: str, limit: int) -> str:
    body = _FENCE_RE.sub(" ", body)
    body = re.sub(r"^#+.*$", " ", body, flags=re.M)          # headings duplicate the description
    body = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", body)      # keep link text, drop URLs
    body = re.sub(r"\s+", " ", body)
    return body[:limit]


def _iter_skill_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root, followlinks=True):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        if "SKILL.md" in filenames:
            yield Path(dirpath) / "SKILL.md"


def load_aliases(path: str | Path | None) -> dict[str, str]:
    """Parse an alias file. Malformed lines are ignored, never fatal.

    Two shapes are accepted, because both are in the wild::

        alias: skill-name          # one alias per line
        skill-name: [a, b, c]      # inline list
        skill-name:                # block list
          - a
          - b

    Returns ``{alias_lowercased: skill_name}``.
    """
    aliases: dict[str, str] = {}
    if not path:
        return aliases
    p = Path(path).expanduser()
    try:
        raw = p.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return aliases

    skill_header: str | None = None
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped == "-" or stripped.startswith("- "):
            if skill_header:  # block-list item belonging to the preceding skill key
                alias = stripped[1:].strip().strip("\"'").lower()
                if alias:
                    aliases[alias] = skill_header
            continue
        m = _ALIAS_KEY_RE.match(stripped)
        if not m:
            skill_header = None
            continue
        key = m.group(1).strip()
        value = m.group(2).strip()
        skill_header = None
        if not value:
            skill_header = key  # a block list of aliases follows
            continue
        inline = _ALIAS_INLINE_RE.match(value)
        if inline:
            for item in inline.group(1).split(","):
                alias = item.strip().strip("\"'").lower()
                if alias:
                    aliases[alias] = key
            continue
        if _ALIAS_RE.match(stripped):  # alias: skill-name
            aliases[key.strip("\"'").lower()] = value.strip("\"'")
    return aliases


class SkillIndex:
    """A built index. ``build()`` returns self so it can be used inline."""

    def __init__(
        self,
        roots: Iterable[str | Path],
        aliases_path: str | Path | None = None,
        *,
        body_chars: int = BODY_CHARS,
        extra_aliases: dict[str, str] | None = None,
    ) -> None:
        self.roots = [Path(r).expanduser() for r in roots]
        self.body_chars = body_chars
        self.docs: list[SkillDoc] = []
        self.by_name: dict[str, SkillDoc] = {}
        self.aliases: dict[str, str] = dict(extra_aliases or {})
        self.aliases.update(load_aliases(aliases_path))
        self._postings: dict[str, dict[int, float]] = {}
        self._idf: dict[str, float] = {}
        self._avg_len = 0.0

    # ── build ─────────────────────────────────────────────────────────────
    def build(self) -> "SkillIndex":
        seen: set[str] = set()
        for root in self.roots:
            for skill_md in _iter_skill_files(root):
                doc = self._load(skill_md, seen)
                if doc:
                    self.docs.append(doc)
                    self.by_name[doc.name] = doc
        self._index()
        self.aliases = {a: s for a, s in self.aliases.items() if s in self.by_name}
        return self

    def _load(self, skill_md: Path, seen: set[str]) -> SkillDoc | None:
        try:
            text = skill_md.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return None
        meta, rest = _front_matter(text)
        name = (meta.get("name") or skill_md.parent.name).strip()
        if not name or name in seen or len(name) > MAX_NAME_CHARS:
            return None
        seen.add(name)
        desc = meta.get("description") or meta.get("summary") or meta.get("title") or ""
        if not desc:  # fall back to the first non-heading line of the body
            for line in rest.splitlines():
                if line.strip() and not line.startswith("#"):
                    desc = line.strip()
                    break
        name_terms = frozenset(_name_tokens(name)) | frozenset(tokens(name.replace("-", " ").replace("_", " ")))
        core = frozenset(tokens(f"{name.replace('-', ' ').replace('_', ' ')} {desc}")) | name_terms
        body = frozenset(tokens(_clean_body(rest, self.body_chars)))
        return SkillDoc(name=name, path=str(skill_md), description=desc.strip(),
                        core=core, body=body, name_terms=name_terms)

    def _index(self) -> None:
        n = len(self.docs)
        df: dict[str, int] = {}
        postings: dict[str, dict[int, float]] = {}
        lengths: list[float] = []
        for i, doc in enumerate(self.docs):
            weights: dict[str, float] = {}
            for term in doc.core:
                weights[term] = weights.get(term, 0.0) + CORE_WEIGHT
            for term in doc.name_terms:
                weights[term] = weights.get(term, 0.0) + NAME_WEIGHT
            for term in doc.body:
                if term not in doc.core:
                    weights[term] = weights.get(term, 0.0) + BODY_WEIGHT
            for term, w in weights.items():
                postings.setdefault(term, {})[i] = w
                df[term] = df.get(term, 0) + 1
            lengths.append(sum(weights.values()) or 1.0)
        self._postings = postings
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}
        self._lengths = lengths
        self._avg_len = (sum(lengths) / len(lengths)) if lengths else 1.0

    # ── query ─────────────────────────────────────────────────────────────
    def match(
        self,
        query: str,
        top_n: int = 3,
        *,
        min_score: float = DEFAULT_MIN_SCORE,
        relative_floor: float = DEFAULT_RELATIVE_FLOOR,
        min_core_hits: int = DEFAULT_MIN_CORE_HITS,
    ) -> list[tuple[float, str, str]]:
        """Return ``[(score, name, description)]`` best-first; empty means "say nothing"."""
        if not self.docs or not (query or "").strip():
            return []
        qterms = list(dict.fromkeys(tokens(query)))
        if not qterms:
            return []
        low = query.lower()
        alias_hits = [(0.0, skill, self.by_name[skill].description)
                      for alias, skill in self.aliases.items() if len(alias) >= 2 and alias in low]

        wanted = set(qterms)
        idf = {t: self._idf.get(t, 0.0) for t in wanted}
        scores: dict[int, float] = {}
        core_hits: dict[int, int] = {}
        for term in wanted:
            if term not in self._postings:
                continue
            for doc_i, weight in self._postings[term].items():
                norm = 1 - B + B * (self._lengths[doc_i] / self._avg_len)
                scores[doc_i] = scores.get(doc_i, 0.0) + idf[term] * weight * (K1 + 1) / (weight + K1 * norm)
                if term in self.docs[doc_i].core:
                    core_hits[doc_i] = core_hits.get(doc_i, 0) + 1

        candidates = [
            (score, self.docs[i].name, self.docs[i].description)
            for i, score in scores.items()
            if core_hits.get(i, 0) >= min_core_hits
        ]
        floor = min_score * min(1.0, max(0.35, math.sqrt(len(self.docs) / 150.0)))
        candidates = [c for c in candidates if c[0] >= floor]
        if candidates:
            top = max(c[0] for c in candidates)
            candidates = [c for c in candidates if c[0] >= top * relative_floor]
        out = {name: (ALIAS_SCORE, name, desc) for _s, name, desc in alias_hits}
        for entry in sorted(candidates, reverse=True)[:top_n]:
            out.setdefault(entry[1], entry)
        return sorted(out.values(), key=lambda e: (-e[0], e[1]))[:top_n]

    def explain(self, query: str, **kwargs) -> str:
        """Human-readable debug dump — used by the CLI/test helper, not the hook."""
        hits = self.match(query, **kwargs)
        if not hits:
            return f"{query!r}: no hit"
        return f"{query!r}: " + ", ".join(f"{n}({s:.1f})" for s, n, _ in hits)
