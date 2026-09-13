"""The one frontmatter parser: delimiters are whole lines, never a ``---``
inside a value or the body, and every reader agrees with it."""

from __future__ import annotations

from reflect_kb.frontmatter import split_frontmatter, split_frontmatter_text

NOTE = (
    "---\n"
    "title: cost --- benefit\n"
    "classification: restricted\n"
    "note: \"a---b\"\n"
    "---\n"
    "\n"
    "body line\n"
    "---\n"
    "a rule inside the body, not a delimiter\n"
)


def test_a_dash_run_inside_a_value_does_not_close_the_block() -> None:
    fm = split_frontmatter(NOTE)
    assert fm.present and not fm.malformed
    assert fm.mapping["title"] == "cost --- benefit"
    assert fm.mapping["classification"] == "restricted"
    assert fm.body == "\nbody line\n---\na rule inside the body, not a delimiter\n"


def test_every_reader_keeps_the_label_behind_a_dash_run() -> None:
    from reflect_kb.classification import classification_of_note
    from reflect_kb.cli.learnings_cli import parse_frontmatter
    from reflect_kb.write_flow import parse_frontmatter as write_flow_parse

    assert classification_of_note(NOTE) == "restricted"
    assert parse_frontmatter(NOTE)[0]["classification"] == "restricted"
    assert write_flow_parse(NOTE)[0]["classification"] == "restricted"
    raw, body = split_frontmatter_text(NOTE)
    assert raw.startswith("title: cost --- benefit") and body.lstrip().startswith("body line")


def test_no_block_empty_block_and_malformed_block() -> None:
    assert split_frontmatter("plain text") == split_frontmatter("plain text")
    assert not split_frontmatter("plain text").present
    assert not split_frontmatter("--- not a delimiter line\nx\n---\n").present
    assert not split_frontmatter("---\ntitle: open\n").present  # never closed
    empty = split_frontmatter("---\n---\nbody")
    assert empty.mapping == {} and empty.body == "body"
    bad = split_frontmatter("---\ntitle: [unclosed\n---\nbody")
    assert bad.malformed and not bad.present and bad.body.startswith("---")
    listy = split_frontmatter("---\n- a\n- b\n---\nbody")
    assert listy.malformed


def test_crlf_and_trailing_spaces_on_the_delimiter() -> None:
    fm = split_frontmatter("--- \r\ntitle: t\r\nclassification: pii\r\n---\t\r\nbody\r\n")
    assert fm.mapping == {"title": "t", "classification": "pii"} and fm.body == "body\r\n"
