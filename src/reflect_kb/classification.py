"""Data classification vocabulary and the export floor.

One place for the four labels and the rule every egress path enforces:
``restricted`` and ``pii`` never leave the local store. The frontmatter schema
carries the same enum; the Postgres migration 0003 carries the same floor as a
check constraint; the Context Broker refuses to return anything above the floor.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

__all__ = ["CLASSIFICATIONS", "DEFAULT_CLASSIFICATION", "LOCAL_ONLY", "classification_of", "may_leave_machine"]

CLASSIFICATIONS = frozenset({"public", "internal", "restricted", "pii"})
DEFAULT_CLASSIFICATION = "internal"
LOCAL_ONLY = frozenset({"restricted", "pii"})


def classification_of(metadata: Optional[Mapping[str, Any]]) -> str:
    """Read the label from a frontmatter or metadata mapping; missing means internal."""
    value = (metadata or {}).get("classification")
    return str(value) if value else DEFAULT_CLASSIFICATION


def may_leave_machine(metadata: Optional[Mapping[str, Any]]) -> bool:
    """True when the item is below the floor and may be shared or served."""
    return classification_of(metadata) not in LOCAL_ONLY
