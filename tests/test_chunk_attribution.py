"""Regression tests for orphan-chunk attribution in naive-mode parsing.

nano-graphrag's naive arm splits a long document into similarity-ranked chunks
and emits only each chunk's raw body; the ``---`` frontmatter block rides the
head chunk alone. Continuation chunks parse to empty frontmatter and used to
surface as orphan results with id ``?`` and title ``(no title)`` — a mangled
duplicate of a real document. parse_learnings_output now drops those headerless
continuation chunks so recall never emits an unattributed entry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugin" / "skills" / "recall" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from recall import CHUNK_SEPARATOR, parse_learnings_output  # noqa: E402

DOC_NAME = "lrn-long-body-splits-into-two-chunks"

HEAD_CHUNK = (
    "---\n"
    f"name: {DOC_NAME}\n"
    "title: Long body splits into two chunks\n"
    "confidence: high\n"
    "---\n\n"
    "**How to apply:** first slice of the body that carries the header.\n"
)

# What nano-graphrag emits for chunk 2+: a raw body slice with no `---` header.
TAIL_CHUNK = (
    "continuation of the same document, far enough into the body that the\n"
    "frontmatter block was left behind on the head chunk.\n"
)


def _context(*chunks: str) -> str:
    return json.dumps({"context": CHUNK_SEPARATOR.join(chunks)})


def test_orphan_continuation_chunk_is_not_surfaced():
    learnings = parse_learnings_output(_context(HEAD_CHUNK, TAIL_CHUNK))
    ids = [lrn.id for lrn in learnings]
    titles = [lrn.title for lrn in learnings]
    assert "?" not in ids, f"orphan id surfaced: {ids}"
    assert "(no title)" not in titles, f"orphan title surfaced: {titles}"
    assert all(lrn.id == DOC_NAME for lrn in learnings), ids


def test_head_chunk_is_still_returned_with_real_identity():
    learnings = parse_learnings_output(_context(HEAD_CHUNK, TAIL_CHUNK))
    assert len(learnings) == 1
    assert learnings[0].id == DOC_NAME
    assert learnings[0].title == "Long body splits into two chunks"


def test_normal_single_chunk_docs_are_untouched():
    # Every chunk carries frontmatter (the normal claude/codex recall shape),
    # so none are dropped and output is unchanged.
    other = (
        "---\n"
        "name: lrn-other\n"
        "title: Another learning\n"
        "confidence: high\n"
        "---\n\n"
        "body\n"
    )
    learnings = parse_learnings_output(_context(HEAD_CHUNK, other))
    assert [lrn.id for lrn in learnings] == [DOC_NAME, "lrn-other"]
