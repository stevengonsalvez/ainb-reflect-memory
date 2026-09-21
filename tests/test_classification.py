"""Classification vocabulary and the export floor."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("label", ["public", "internal", "restricted", "pii"])
def test_classification_vocabulary(label: str) -> None:
    from reflect_kb.classification import CLASSIFICATIONS, may_leave_machine

    assert label in CLASSIFICATIONS
    assert may_leave_machine({"classification": label}) == (label in {"public", "internal"})


@pytest.mark.parametrize("label", ["secret", "Public", "", "  ", None, 7, ["public"]])
def test_unknown_empty_or_malformed_labels_fail_closed(label) -> None:
    from reflect_kb.classification import (
        INVALID_CLASSIFICATION,
        classification_of,
        may_leave_machine,
    )

    # An explicit null reads as missing (internal); an empty or non-string
    # value is malformed, never shareable.
    assert may_leave_machine({"classification": label}) == (label is None)
    if label not in (None, "secret", "Public"):
        assert classification_of({"classification": label}) == INVALID_CLASSIFICATION
    assert may_leave_machine({}) and may_leave_machine(None)


def test_note_classification_from_frontmatter() -> None:
    from reflect_kb.classification import classification_of_note

    assert classification_of_note("---\ntitle: t\nclassification: pii\n---\nbody") == "pii"
    assert classification_of_note("---\ntitle: t\n---\nbody") == "internal"
    assert classification_of_note("no frontmatter") == "internal"
    assert classification_of_note("---\ntitle: t\nclassification: ''\n---\n") == "<invalid>"
    # A dash run inside a value used to truncate the block and drop the label.
    assert classification_of_note("---\ntitle: a --- b\nclassification: restricted\n---\nbody") == "restricted"
    assert classification_of_note("---\ntitle: t\nclassification: pii\n---\nbody\n---\nrule") == "pii"
    assert classification_of_note("---\n- not\n- a mapping\n---\n") == "<invalid>"
