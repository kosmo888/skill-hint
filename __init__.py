"""skill-hint — surface the skills that match what the user just asked.

Why
---
Hermes lists every installed skill (name + description) in the system prompt
and leaves discovery to the model. Descriptions are written for humans, not
for the way people actually type: colloquial phrasing, shorthand, another
language. The skill exists, the model never looks at it, and the work gets
redone by hand.

What it does
------------
On every ``pre_llm_call`` (once per user turn, not inside tool loops) it runs
a local BM25 query over the skill library and, only when something clearly
matches, appends a few lines of context naming the candidate skills:

    [skill-hint] The message may match these skills — load with skill_view
    before acting (auto-matched, may be wrong):
    - pdf: PDF files: create, read, merge, fill, OCR, edit text.

Guarantees
----------
* No network, no model call, no API key, no third-party dependency.
* Silent by default: no hit -> nothing is injected.
* Never raises into the conversation; any failure degrades to "no hint".
* Bounded: at most ``max_hits`` entries, ``max_context_chars`` characters.
* Skill library is re-indexed on a TTL, so newly installed skills show up
  without restarting the gateway.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_PLUGIN_DIR = Path(__file__).resolve().parent

# Defaults; every one of these is overridable from plugin settings.
_DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "max_hits": 3,
    "max_context_chars": 600,
    "min_score": 3.0,
    "relative_floor": 0.35,
    "index_ttl_seconds": 300,
    "aliases_file": "",
    "extra_skill_roots": "",
    "language": "auto",
}

# Inputs that never need a skill hint.
_TRIVIAL = {
    "好", "好的", "可以", "行", "嗯", "对", "是的", "继续", "不用", "算了", "停",
    "谢谢", "ok", "okay", "yes", "no", "go", "continue", "thanks", "thank you",
    "收到", "继续吧", "往下",
}

_HEADER_EN = (
    "[skill-hint] The message may match these skills — load one with skill_view "
    "before acting (auto-matched, may be wrong):"
)
_HEADER_ZH = "[skill-hint] 这句话可能对应下面技能，动手前先 skill_view 加载确认（自动匹配，未必准）："

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


# ── runtime configuration ────────────────────────────────────────────────
def _hermes_home() -> Path:
    env = os.environ.get("HERMES_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / ".hermes"


class _Config:
    """Values resolved once at register(): plugin settings, then env override."""

    def __init__(self, raw: dict[str, Any] | None = None) -> None:
        cfg = dict(_DEFAULTS)
        for key, value in (raw or {}).items():
            if key in cfg and value is not None:
                cfg[key] = value
        for key in cfg:
            env_key = "SKILL_HINT_" + key.upper()
            if env_key in os.environ:
                cfg[key] = os.environ[env_key]
        self.enabled = _as_bool(cfg["enabled"])
        self.max_hits = max(1, _as_int(cfg["max_hits"], 3))
        self.max_context_chars = max(120, _as_int(cfg["max_context_chars"], 600))
        self.min_score = _as_float(cfg["min_score"], 3.0)
        self.relative_floor = min(1.0, max(0.0, _as_float(cfg["relative_floor"], 0.35)))
        self.index_ttl = max(0, _as_int(cfg["index_ttl_seconds"], 300))
        self.aliases_file = str(cfg["aliases_file"] or "").strip()
        self.extra_skill_roots = [
            part.strip() for part in str(cfg["extra_skill_roots"] or "").split(",") if part.strip()
        ]
        self.language = str(cfg["language"] or "auto").strip().lower()

    def header(self, text: str) -> str:
        if self.language == "zh":
            return _HEADER_ZH
        if self.language == "en":
            return _HEADER_EN
        cjk = len(_CJK_RE.findall(text))
        return _HEADER_ZH if cjk and cjk / max(1, len(text)) > 0.10 else _HEADER_EN


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("0", "false", "no", "off", "")


def _as_int(value: Any, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _collect_config(ctx: Any) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    getter = getattr(ctx, "get_config", None)
    if callable(getter):
        for key in _DEFAULTS:
            try:
                value = getter(key)
            except Exception:
                continue
            if value is not None:
                raw[key] = value
    return raw


# ── skill library ────────────────────────────────────────────────────────
def _skill_roots(cfg: _Config) -> list[Path]:
    home = _hermes_home()
    roots = [home / "skills", home / "hermes-agent" / "skills"]
    plugins = home / "plugins"
    try:
        if plugins.is_dir():
            roots += [p for p in sorted(plugins.glob("*/skills")) if p.is_dir()]
    except OSError:
        pass
    roots += [Path(p).expanduser() for p in cfg.extra_skill_roots]
    out: list[Path] = []
    for root in roots:  # dedupe, keep order
        if root not in out:
            out.append(root)
    return out


def _aliases_path(cfg: _Config) -> Path | None:
    if cfg.aliases_file:
        return Path(cfg.aliases_file).expanduser()
    # standard location first, then the plugin-directory file used by 0.x installs,
    # so upgrading never silently drops a user's alias map
    for candidate in (_hermes_home() / "skill-hint-aliases.yaml", _PLUGIN_DIR / "aliases.yaml"):
        if candidate.exists():
            return candidate
    return None


class _IndexCache:
    def __init__(self, cfg: _Config) -> None:
        self.cfg = cfg
        self._index = None
        self._built_at = 0.0
        self._lock = threading.Lock()

    def get(self):
        ttl = self.cfg.index_ttl
        fresh = self._index is not None and (ttl <= 0 or (time.time() - self._built_at) < ttl)
        if fresh:
            return self._index
        with self._lock:
            if self._index is not None and (ttl <= 0 or (time.time() - self._built_at) < ttl):
                return self._index
            try:
                from .skill_matcher import SkillIndex
            except Exception:  # loaded outside the package (dev harness)
                import sys

                sys.path.insert(0, str(_PLUGIN_DIR))
                from skill_matcher import SkillIndex  # type: ignore[no-redef]

            aliases = _aliases_path(self.cfg)
            index = SkillIndex(_skill_roots(self.cfg), aliases).build()
            self._index = index
            self._built_at = time.time()
            logger.info(
                "skill-hint: indexed %d skills from %d roots (%d aliases)",
                len(index.docs), len(index.roots), len(index.aliases),
            )
        return self._index


_CACHE: _IndexCache | None = None
_CONFIG: _Config | None = None


# ── hook ──────────────────────────────────────────────────────────────────
def _extract_text(message: Any) -> str:
    """The hook may hand us a plain string or multimodal content parts."""
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts: list[str] = []
        for part in message:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") in (None, "text") and isinstance(part.get("text"), str):
                    parts.append(part["text"])
        return "\n".join(parts)
    if isinstance(message, dict):
        content = message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return _extract_text(content)
    return ""


def _on_pre_llm_call(**kwargs: Any):
    cfg = _CONFIG
    cache = _CACHE
    if cfg is None or cache is None or not cfg.enabled:
        return None
    try:
        if kwargs.get("parent_session_id"):
            return None  # subagent: the parent turn already carries the hint
        text = _extract_text(kwargs.get("user_message")).strip()
        if len(text) < 4 or text.lower().strip("。.!?！？ ") in _TRIVIAL:
            return None
        if text.startswith("/"):  # slash commands go to the command handler
            return None

        index = cache.get()
        hits = index.match(
            text,
            top_n=cfg.max_hits,
            min_score=cfg.min_score,
            relative_floor=cfg.relative_floor,
        )
        logger.debug("skill-hint: %d hit(s) for %r", len(hits), text[:60])
        if not hits:
            return None

        lines = [cfg.header(text)]
        budget = cfg.max_context_chars - len(lines[0]) - 1
        for _score, name, description in hits:
            desc = description if len(description) <= 110 else description[:107].rstrip() + "…"
            line = f"- {name}: {desc}"
            if len(line) + 1 > budget:
                break
            lines.append(line)
            budget -= len(line) + 1
        if len(lines) == 1:
            return None
        return {"context": "\n".join(lines)}
    except Exception:
        logger.warning("skill-hint: pre_llm_call failed", exc_info=True)
        return None


def register(ctx: Any) -> None:
    global _CACHE, _CONFIG
    _CONFIG = _Config(_collect_config(ctx))
    _CACHE = _IndexCache(_CONFIG)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    logger.debug("skill-hint registered (enabled=%s, ttl=%ss)", _CONFIG.enabled, _CONFIG.index_ttl)
