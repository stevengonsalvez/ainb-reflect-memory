"""Classification vocabulary and the export floor."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("label", ["public", "internal", "restricted", "pii"])
def test_classification_vocabulary(label: str) -> None:
    from reflect_kb.classification import CLASSIFICATIONS, may_leave_machine

    assert label in CLASSIFICATIONS
    assert may_leave_machine({"classification": label}) == (label in {"public", "internal"})


@pytest.mark.parametrize("label", ["secret", "Public", "", None, 7])
def test_unknown_labels_fail_closed(label) -> None:
    from reflect_kb.classification import may_leave_machine

    # Missing means internal (shareable); anything else unknown is not.
    assert may_leave_machine({"classification": label}) == (label in ("", None))
