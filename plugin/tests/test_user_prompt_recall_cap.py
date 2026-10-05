# ABOUTME: Regression tests for the UserPromptSubmit per-prompt learning cap.
# ABOUTME: The header line must not count against USER_PROMPT_LIMIT.
"""filter_to_new used to keep the first ``USER_PROMPT_LIMIT`` markdown *blocks*,
but recall's markdown opens with a ``## Prior learnings ...`` header block, so
only ``LIMIT - 1`` learnings reached the model per prompt. These tests feed the
REAL ``render_markdown`` output into the REAL ``filter_to_new``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "recall" / "scripts"))
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "recall" / "hooks"))

from recall import Learning, render_markdown  # noqa: E402
from user_prompt_submit_recall import USER_PROMPT_LIMIT, filter_to_new  # noqa: E402


def _markdown(n: int) -> str:
    learnings = [
        Learning(
            chunk_text=f"**How to apply:** apply {i}",
            frontmatter={
                "id": f"lrn-cap-{i}",
                "key_insight": f"insight {i}",
            },
        )
        for i in range(1, n + 1)
    ]
    return render_markdown(learnings, "how do I cap recall", max_chars=100_000)


def _entry_ids(text: str) -> list[str]:
    return re.findall(r"^- \*\*\[(lrn-[a-z0-9\-]+)\]\*\*", text, re.M)


def test_header_plus_four_entries_keeps_exactly_the_limit_of_learnings():
    out, ids = filter_to_new(_markdown(4), set())
    assert USER_PROMPT_LIMIT == 3
    assert out.startswith("## Prior learnings relevant to")  # header preserved
    assert _entry_ids(out) == ["lrn-cap-1", "lrn-cap-2", "lrn-cap-3"]
    assert ids == ["lrn-cap-1", "lrn-cap-2", "lrn-cap-3"]
    assert "lrn-cap-4" not in out


def test_already_injected_ids_are_skipped_without_eating_the_cap():
    out, ids = filter_to_new(_markdown(5), {"lrn-cap-1", "lrn-cap-3"})
    assert _entry_ids(out) == ["lrn-cap-2", "lrn-cap-4", "lrn-cap-5"]
    assert ids == ["lrn-cap-2", "lrn-cap-4", "lrn-cap-5"]


def test_fewer_entries_than_cap_keeps_all_of_them():
    out, ids = filter_to_new(_markdown(2), set())
    assert _entry_ids(out) == ["lrn-cap-1", "lrn-cap-2"]
    assert ids == ["lrn-cap-1", "lrn-cap-2"]
    assert out.startswith("## Prior learnings relevant to")


def test_multiline_entry_stays_whole_and_header_is_not_a_learning():
    out, _ = filter_to_new(_markdown(3), set())
    assert out.count("How to apply:") == 3


def test_everything_already_injected_returns_nothing_not_a_lone_header():
    out, ids = filter_to_new(_markdown(3), {"lrn-cap-1", "lrn-cap-2", "lrn-cap-3"})
    assert (out, ids) == ("", [])


def test_empty_markdown_returns_nothing():
    assert filter_to_new("", set()) == ("", [])
