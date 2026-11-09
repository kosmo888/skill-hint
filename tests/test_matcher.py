"""Unit tests for the matcher: tokenisation, front-matter parsing, ranking, gates."""
from __future__ import annotations

from conftest import write_skill


def test_cjk_runs_become_bigrams_and_trigrams(matcher):
    toks = matcher.tokens("画一张流程图")
    assert "流程" in toks and "流程图" in toks
    # single CJK characters carry no routing signal on their own
    assert "画" not in toks


def test_ascii_stopwords_and_case(matcher):
    toks = matcher.tokens("How do I take a screenshot of the desktop")
    assert "screenshot" in toks and "desktop" in toks
    assert "do" not in toks and "the" not in toks and "take" not in toks


def test_front_matter_plain_and_block_scalars(matcher):
    meta, body = matcher._front_matter("---\nname: x\ndescription: >-\n  one\n  two\n---\nbody")
    assert meta["name"] == "x" and meta["description"] == "one two" and "body" in body

    meta, _ = matcher._front_matter("---\nname: y\ndescription: |\n  登记违章记录。\n  适用于台账。\n---\n")
    assert "登记违章记录" in meta["description"]


def test_index_reads_descriptions_and_symlinks(matcher, skill_lib):
    index = matcher.SkillIndex([skill_lib]).build()
    assert set(index.by_name) == {"pdf", "diagram-maker", "ehs-violation", "apple-notes", "telegram-bridge"}
    assert index.by_name["ehs-violation"].description.startswith("登记违章记录")
    # the symlinked skill must be indexed, not silently skipped
    assert index.by_name["telegram-bridge"].path.endswith("SKILL.md")


def test_english_query_hits_the_right_skill(matcher, skill_lib):
    index = matcher.SkillIndex([skill_lib]).build()
    assert index.match("merge two pdf files", top_n=1)[0][1] == "pdf"
    assert index.match("draw a flowchart for the build pipeline", top_n=1)[0][1] == "diagram-maker"
    assert index.match("push this file to telegram", top_n=1)[0][1] == "telegram-bridge"


def test_chinese_query_hits_the_right_skill(matcher, skill_lib):
    index = matcher.SkillIndex([skill_lib]).build()
    hits = index.match("登记一条违章记录", top_n=1)
    assert hits and hits[0][1] == "ehs-violation"


def test_body_only_mentions_do_not_trip_the_core_gate(matcher, tmp_path):
    """apple-notes mentions "create ... notes" only in its body: that must not outrank a real hit."""
    root = tmp_path / "s"
    write_skill(root, "notes", "name: apple-notes\ndescription: Manage Apple Notes via memo CLI.", "create search edit notes")
    write_skill(root, "pdf", "name: pdf\ndescription: PDF files: create, read, merge.", "")
    index = matcher.SkillIndex([root]).build()
    hits = index.match("create a pdf", top_n=2)
    assert hits[0][1] == "pdf"


def test_alias_outranks_bm25(matcher, tmp_path):
    root = tmp_path / "s"
    write_skill(root, "pdf", "name: pdf\ndescription: PDF files.", "")
    write_skill(root, "slides", "name: deck-builder\ndescription: Presentation workflow.", "")
    aliases = tmp_path / "aliases.yaml"
    aliases.write_text("slides: deck-builder\n", encoding="utf-8")
    index = matcher.SkillIndex([root], aliases).build()
    hits = index.match("turn these slides into a deck", top_n=2)
    assert hits[0][1] == "deck-builder"
    assert hits[0][0] >= matcher.ALIAS_SCORE


def test_alias_for_missing_skill_is_ignored(matcher, tmp_path):
    root = tmp_path / "s"
    write_skill(root, "pdf", "name: pdf\ndescription: PDF files.", "")
    aliases = tmp_path / "aliases.yaml"
    aliases.write_text("slides: no-such-skill\n", encoding="utf-8")
    index = matcher.SkillIndex([root], aliases).build()
    assert index.aliases == {}


def test_unrelated_chatter_returns_nothing(matcher, skill_lib):
    index = matcher.SkillIndex([skill_lib]).build()
    assert index.match("good morning, how are you") == []


def test_empty_index_and_empty_query_are_safe(matcher, tmp_path):
    index = matcher.SkillIndex([tmp_path / "nope"]).build()
    assert index.match("anything") == []
    index = matcher.SkillIndex([tmp_path]).build()
    assert index.match("") == []
    assert index.match("   ") == []


def test_top_n_and_relative_floor_are_respected(matcher, tmp_path):
    root = tmp_path / "s"
    for i in range(6):
        write_skill(root, f"pdf-{i}", f"name: pdf-{i}\ndescription: PDF files, merge and split.", "")
    index = matcher.SkillIndex([root]).build()
    assert len(index.match("merge pdf files", top_n=3)) <= 3


def test_alias_file_accepts_all_three_shapes(matcher):
    """The alias file format that shipped in 0.x must keep parsing: skill key + block list."""
    import tempfile, pathlib

    d = pathlib.Path(tempfile.mkdtemp())
    f = d / "aliases.yaml"
    f.write_text(
        "# comment\n"
        "deck: deck-builder\n"                    # alias: skill
        "xlsx: [表格, spreadsheet]\n"              # inline list
        "google-workspace:\n  - 日历\n  - 日程\n",  # block list
        encoding="utf-8",
    )
    aliases = matcher.load_aliases(f)
    assert aliases["deck"] == "deck-builder"
    assert aliases["表格"] == "xlsx" and aliases["spreadsheet"] == "xlsx"
    assert aliases["日历"] == "google-workspace" and aliases["日程"] == "google-workspace"


def test_alias_file_is_missing_or_malformed_safe(matcher, tmp_path):
    assert matcher.load_aliases(None) == {}
    assert matcher.load_aliases(tmp_path / "nope.yaml") == {}
    bad = tmp_path / "bad.yaml"
    bad.write_text("this is not a map at all\n:::\n", encoding="utf-8")
    assert matcher.load_aliases(bad) == {}
