"""Regression tests for recency_norm tz handling.

Aware ``archived_at`` values (explicit ``+00:00`` / ``+05:30`` offsets) used to
raise TypeError inside the aware-vs-naive subtraction and fall back to the
neutral 0.5, silently killing recency ranking for those docs. The fix strips
tzinfo (converting to UTC first) so offset-aware timestamps produce real
recency, while genuinely malformed strings still degrade to 0.5.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "plugin" / "skills" / "recall" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from recall import recency_norm  # noqa: E402

NOW = datetime(2026, 7, 14, 12, 0, 0)


def _recent(offset: str) -> str:
    return (NOW - timedelta(days=1)).isoformat() + offset


def _old(offset: str) -> str:
    return (NOW - timedelta(days=300)).isoformat() + offset


@pytest.mark.parametrize("offset", ["", "Z", "+00:00", "+05:30"])
def test_recent_scores_near_one(offset):
    assert recency_norm(_recent(offset), NOW) > 0.9


@pytest.mark.parametrize("offset", ["", "Z", "+00:00", "+05:30"])
def test_old_scores_below_half(offset):
    assert recency_norm(_old(offset), NOW) < 0.5


def test_malformed_falls_back_to_neutral():
    assert recency_norm("not-a-timestamp", NOW) == 0.5


def test_missing_falls_back_to_neutral():
    assert recency_norm(None, NOW) == 0.5
