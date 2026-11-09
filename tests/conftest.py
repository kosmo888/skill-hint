"""Test fixtures: load the plugin the way Hermes does and build a throwaway skill library."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

PLUGIN_DIR = Path(__file__).resolve().parent.parent


def _load_plugin_module():
    """Import the plugin package from its directory, as the loader would."""
    name = "skill_hint_under_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, PLUGIN_DIR / "__init__.py", submodule_search_locations=[str(PLUGIN_DIR)]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def plugin():
    return _load_plugin_module()


@pytest.fixture()
def matcher(plugin):
    return sys.modules[f"{plugin.__name__}.skill_matcher"]


def write_skill(root: Path, dirname: str, front: str, body: str = "") -> Path:
    folder = root / dirname
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\n{front}\n---\n\n{body}\n", encoding="utf-8")
    return folder


@pytest.fixture()
def skill_lib(tmp_path):
    """A small library covering the three things that used to break: CJK, block scalars, symlinks."""
    root = tmp_path / "skills"
    root.mkdir()
    write_skill(root, "pdf", "name: pdf\ndescription: PDF files: create, read, merge, fill, OCR, edit text.", "## Body\nUse pypdf.")
    write_skill(
        root,
        "diagram",
        "name: diagram-maker\ndescription: >-\n  Draw flowcharts, swimlanes and architecture diagrams\n  from a structured description.",
        "## Body\nExport to PNG.",
    )
    write_skill(root, "ehs", "name: ehs-violation\ndescription: |\n  登记违章记录。适用于违章登记、考核通报录入、台账追加。", "## 正文\n先查台账。")
    write_skill(root, "notes", "name: apple-notes\ndescription: Manage Apple Notes via memo CLI.", "## Body\ncreate, search, edit notes on macOS only.")
    # a skill reachable only through a symlink (the layout dotfile users actually run)
    external = tmp_path / "external" / "telegram-bridge"
    external.mkdir(parents=True)
    (external / "SKILL.md").write_text("---\nname: telegram-bridge\ndescription: Push files and messages to Telegram.\n---\n", encoding="utf-8")
    (root / "telegram-bridge").symlink_to(external, target_is_directory=True)
    return root
