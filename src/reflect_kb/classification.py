"""Data classification vocabulary and the export floor.

One place for the four labels and the rule every egress path enforces:
``restricted`` and ``pii`` never leave the local store. The frontmatter schema
carries the same enum; the Postgres migration 0003 carries the same floor as a
check constraint; the Context Broker refuses to return anything above the floor.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from reflect_kb.frontmatter import split_frontmatter

__all__ = [
    "CLASSIFICATIONS",
    "DEFAULT_CLASSIFICATION",
    "INVALID_CLASSIFICATION",
    "LOCAL_ONLY",
    "SHAREABLE",
    "classification_of",
    "classification_of_note",
    "may_leave_machine",
]

CLASSIFICATIONS = frozenset({"public", "internal", "restricted", "pii"})
DEFAULT_CLASSIFICATION = "internal"
LOCAL_ONLY = frozenset({"restricted", "pii"})
SHAREABLE = CLASSIFICATIONS - LOCAL_ONLY
# What an empty or non-string label reads as: never a real label, so it can
# never be shareable and an insert boundary can name the problem.
INVALID_CLASSIFICATION = "<invalid>"


def classification_of(metadata: Mapping[str, Any] | None) -> str:
    """Read the label from a frontmatter or metadata mapping.

    An absent key (or an explicit null) means ``internal``. An empty string or
    a non-string value is not a missing label, it is a malformed one, and reads
    as ``INVALID_CLASSIFICATION`` so it fails every floor check.
    """
    if not metadata or "classification" not in metadata or metadata["classification"] is None:
        return DEFAULT_CLASSIFICATION
    value = metadata["classification"]
    if not isinstance(value, str) or not value.strip():
        return INVALID_CLASSIFICATION
    return value


def classification_of_note(text: str) -> str:
    """The label of a markdown learning note, read from its YAML frontmatter.

    The block is split on delimiter lines (``reflect_kb.frontmatter``), so a
    ``---`` inside a value cannot truncate it and drop the label. A block
    that is not valid YAML, or not a mapping, reads as malformed.
    """
    fm = split_frontmatter(text)
    if fm.malformed:
        return INVALID_CLASSIFICATION
    return classification_of(fm.mapping)


def may_leave_machine(metadata: Mapping[str, Any] | None) -> bool:
    """True only for a known label below the floor (public, internal).

    Unknown, empty or malformed labels fail closed: an egress path must never
    treat a typo as permission to share.
    """
    return classification_of(metadata) in SHAREABLE
