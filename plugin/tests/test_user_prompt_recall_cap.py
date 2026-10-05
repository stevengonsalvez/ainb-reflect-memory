# ABOUTME: Regression tests for the UserPromptSubmit per-prompt learning cap.
# ABOUTME: The header line must not count against USER_PROMPT_LIMIT; the char budget cuts at block boundaries.
"""filter_to_new used to keep the first ``USER_PROMPT_LIMIT`` markdown *blocks*,
but recall's markdown opens with a ``## Prior learnings ...`` header block, so
only ``LIMIT - 1`` learnings reached the model per prompt. These tests feed the
REAL ``render_markdown`` output into the REAL ``filter_to_new``.
"""

from __future__ import annotations

import io
import json
import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "recall" / "scripts"))
sys.path.insert(0, str(PLUGIN_ROOT / "skills" / "recall" / "hooks"))

from recall import Learning, render_markdown  # noqa: E402
import user_prompt_submit_recall as hook  # noqa: E402
from user_prompt_submit_recall import (  # noqa: E402
    USER_PROMPT_LIMIT,
    USER_PROMPT_MAX_CHARS,
    filter_to_new,
)


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


# --- char budget: enforced at block boundaries, ids recorded only if emitted --


def _long_markdown(n: int, insight_len: int = 600) -> str:
    # Same shape render_markdown emits (header, then "- **[id]** ..." bullets
    # with an indented continuation line), but with entries long enough that
    # three of them cannot fit the cap (render_markdown caps field lengths).
    lines = ["## Prior learnings relevant to `how do I cap recall`\n"]
    for i in range(1, n + 1):
        lines.append(f"- **[lrn-long-{i}]** {chr(96 + i) * insight_len}\n  How to apply: apply {i}\n")
    return "".join(lines).rstrip() + "\n"


def test_three_long_entries_over_the_cap_drop_the_trailing_block_whole():
    md = _long_markdown(3)
    assert len(md) > USER_PROMPT_MAX_CHARS  # premise: all three cannot fit
    out, ids = filter_to_new(md, set())
    assert len(out) <= USER_PROMPT_MAX_CHARS
    assert _entry_ids(out) == ["lrn-long-1", "lrn-long-2"]
    assert ids == ["lrn-long-1", "lrn-long-2"]
    assert "lrn-long-3" not in out
    assert "…" not in out  # nothing cut mid-text
    assert out.count("a" * 600) == 1 and out.count("b" * 600) == 1


def test_single_block_alone_over_budget_is_hard_cut_and_still_recorded():
    out, ids = filter_to_new(_long_markdown(1, insight_len=3000), set())
    assert len(out) <= USER_PROMPT_MAX_CHARS
    assert out.endswith(" …")
    assert ids == ["lrn-long-1"]  # visible, so recorded (else re-cut forever)


def test_idless_bullet_does_not_count_toward_the_limit():
    md = _markdown(4) + "- _(…2 more truncated)_\n"
    md = md.replace("- **[lrn-cap-1]**", "- _(no id here)_\n- **[lrn-cap-1]**", 1)
    out, ids = filter_to_new(md, set())
    assert ids == ["lrn-cap-1", "lrn-cap-2", "lrn-cap-3"]
    assert "no id here" not in out and "more truncated" not in out


def _run_hook(monkeypatch, state: Path, markdown: str, session: str = "sess-cap"):
    """Drive the real hook main body with recall stubbed to ``markdown``."""
    monkeypatch.setenv("REFLECT_STATE_DIR", str(state))
    monkeypatch.setattr(hook, "query_recall", lambda q, sid="": (markdown, []))
    monkeypatch.setattr(
        sys, "stdin",
        io.StringIO(json.dumps({"session_id": session, "prompt": "how do I cap recall output"})),
    )
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    with pytest.raises(SystemExit):
        hook._main_body()
    return json.loads(out.getvalue())["hookSpecificOutput"]["additionalContext"]


def test_dropped_learning_is_not_marked_injected_and_shows_next_prompt(monkeypatch, tmp_path):
    md = _long_markdown(3)
    first = _run_hook(monkeypatch, tmp_path, md)
    emitted = _entry_ids(first)
    assert emitted == ["lrn-long-1", "lrn-long-2"]

    saved = json.loads((tmp_path / "session-injected" / "sess-cap.json").read_text())
    assert saved["injected"] == sorted(emitted)  # saved ids == emitted ids

    # Next prompt: the same recall result; the dropped learning is now first in line, whole.
    second = _run_hook(monkeypatch, tmp_path, md)
    assert _entry_ids(second) == ["lrn-long-3"]
    assert second.count("c" * 600) == 1
    assert "…" not in second
