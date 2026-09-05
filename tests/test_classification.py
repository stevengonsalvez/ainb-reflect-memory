"""Classification vocabulary and the export floor."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize("label", ["public", "internal", "restricted", "pii"])
def test_classification_vocabulary(label: str) -> None:
    from reflect_kb.classification import CLASSIFICATIONS, may_leave_machine

    assert label in CLASSIFICATIONS
    assert may_leave_machine({"classification": label}) == (label in {"public", "internal"})
