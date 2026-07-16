"""F3: unit tests for the fleet-context injection renderer.

BANK-parity budget from fleet-lambda bank_lookup.py: at most 5 items and
<=2000 estimated tokens for the whole block, with the fleet-context/v1 contract
marker and authority-labelled subsections rendered high-to-low.
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "plugin" / "skills" / "recall" / "scripts"
sys.path.insert(0, str(SCRIPTS))
from recall import (  # noqa: E402
    FLEET_CONTEXT_MARKER,
    FLEET_CONTEXT_MAX_ITEMS,
    FLEET_CONTEXT_MAX_TOKENS,
    Learning,
    _est_tokens,
    render_fleet_context,
)


def _learning(title: str, authority: str = "advisory", insight: str = "") -> Learning:
    return Learning(
        chunk_text=title,
        frontmatter={
            "title": title,
            "authority": authority,
            "key_insight": insight or f"insight for {title}",
            "source_path": f"/fleet/{title}.md",
        },
    )


def test_empty_result_is_empty_string():
    assert render_fleet_context([], "q") == ""


def test_marker_present():
    out = render_fleet_context([_learning("a")], "query")
    assert FLEET_CONTEXT_MARKER in out
    assert out.startswith("## Reflect Recall (fleet memory, advisory)")


def test_caps_to_five_items():
    learnings = [_learning(f"note{i}") for i in range(10)]
    out = render_fleet_context(learnings, "q")
    # Each entry's title line starts with "- **"; the source line does not.
    item_lines = [l for l in out.splitlines() if l.startswith("- **")]
    assert len(item_lines) <= FLEET_CONTEXT_MAX_ITEMS


def test_token_budget_never_overflows():
    huge = "x " * 1500  # ~3000 chars -> ~750 est tokens each
    learnings = [_learning(f"note{i}", insight=huge) for i in range(5)]
    out = render_fleet_context(learnings, "q")
    assert _est_tokens(out) <= FLEET_CONTEXT_MAX_TOKENS


def test_authority_sections_ordered_high_to_low():
    learnings = [
        _learning("adv", authority="advisory"),
        _learning("arch", authority="archived"),
        _learning("law", authority="law"),
        _learning("promo", authority="promoted"),
    ]
    out = render_fleet_context(learnings, "q")
    i_law = out.index("### Law (binding)")
    i_promo = out.index("### Promoted memory")
    i_adv = out.index("### Advisory memory")
    i_arch = out.index("### Archived (historical)")
    assert i_law < i_promo < i_adv < i_arch


def test_unknown_authority_falls_into_advisory():
    out = render_fleet_context([_learning("x", authority="mystery")], "q")
    assert "### Advisory memory" in out
    assert "### Law (binding)" not in out


def test_source_path_and_score_rendered():
    from recall import _learning_key

    lrn = _learning("a")
    scores = {_learning_key(lrn): 0.875}
    out = render_fleet_context([lrn], "q", scores=scores)
    assert "/fleet/a.md" in out
    assert "0.875" in out
