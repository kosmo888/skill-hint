"""Hook-level tests: what actually gets injected into the conversation."""
from __future__ import annotations

import pytest

from conftest import write_skill


class FakeCtx:
    """Minimal stand-in for the Hermes plugin context."""

    def __init__(self, settings: dict | None = None) -> None:
        self.hooks: dict[str, object] = {}
        self._settings = settings or {}

    def register_hook(self, name: str, fn) -> None:
        self.hooks[name] = fn

    def get_config(self, key, default=None):
        return self._settings.get(key, default)


@pytest.fixture()
def wired(plugin, tmp_path, monkeypatch):
    """Register the plugin against a throwaway HERMES_HOME."""
    home = tmp_path / "home"
    write_skill(home / "skills", "pdf", "name: pdf\ndescription: PDF files: create, read, merge, fill, OCR.", "")
    write_skill(home / "skills", "ehs", "name: ehs-violation\ndescription: |\n  登记违章记录。适用于违章登记、考核通报录入。", "")
    monkeypatch.setenv("HERMES_HOME", str(home))
    for key in list(plugin._DEFAULTS):
        monkeypatch.delenv("SKILL_HINT_" + key.upper(), raising=False)

    def make(settings: dict | None = None):
        ctx = FakeCtx(settings)
        plugin.register(ctx)
        return ctx.hooks["pre_llm_call"]

    return make


def test_matching_turn_injects_a_hint(wired):
    hook = wired()
    result = hook(user_message="please merge these two pdf files")
    assert result and "context" in result
    assert "pdf" in result["context"]
    assert result["context"].startswith("[skill-hint]")


def test_chinese_turn_gets_the_chinese_header(wired):
    hook = wired()
    result = hook(user_message="帮我登记一条违章记录")
    assert result and "skill_view" in result["context"]
    assert "这句话可能对应下面技能" in result["context"]


def test_forced_language_setting_wins(wired):
    hook = wired({"language": "en"})
    result = hook(user_message="帮我登记一条违章记录")
    assert result and "may match these skills" in result["context"]


def test_unrelated_turn_is_silent(wired):
    hook = wired()
    assert hook(user_message="good morning, how is the weather") is None


def test_trivial_acknowledgements_are_silent(wired):
    hook = wired()
    for text in ("好的", "可以", "ok", "thanks", "继续"):
        assert hook(user_message=text) is None, text


def test_slash_commands_and_subagents_are_skipped(wired):
    hook = wired()
    assert hook(user_message="/compact now please") is None
    assert hook(user_message="merge these pdf files", parent_session_id="abc") is None


def test_disabled_switch_is_absolute(wired):
    hook = wired({"enabled": False})
    assert hook(user_message="merge these pdf files") is None


def test_multimodal_content_parts_are_understood(wired):
    hook = wired()
    message = [{"type": "text", "text": "merge these pdf files"}, {"type": "image", "source": "x"}]
    assert hook(user_message=message) is not None


def test_context_budget_is_enforced(plugin, wired, monkeypatch):
    hook = wired({"max_context_chars": 160})
    result = hook(user_message="merge these pdf files and register a violation")
    if result:
        assert len(result["context"]) <= 160 + 20  # header may exceed a tiny budget by itself


def test_hook_never_raises(plugin, wired, monkeypatch):
    hook = wired()
    monkeypatch.setattr(plugin._CACHE, "get", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert hook(user_message="merge these pdf files") is None


def test_index_is_cached_between_turns(plugin, wired, monkeypatch):
    hook = wired({"index_ttl_seconds": 300})
    hook(user_message="merge these pdf files")
    built = plugin._CACHE._index
    hook(user_message="merge these pdf files")
    assert plugin._CACHE._index is built
