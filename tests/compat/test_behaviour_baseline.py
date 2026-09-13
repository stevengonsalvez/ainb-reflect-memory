"""Gate 3: user-visible behaviour, this checkout versus the merge-base baseline.

capture.py runs the same pipeline on both trees: reflect add on a legacy note
(no classification) and on a note carrying a fixture secret, reindex and
search in Mode 1, the SessionStart recall hook, the cascade slice and bounded
input on a recorded transcript, and the extract writer with a canned model
reply. The only whitelisted differences are (a) a text that equals the
baseline text after redact_secrets, and (b) a text that equals the baseline
after removing exactly one ``classification: internal`` line. Anything else
is a diff.
"""

from __future__ import annotations

import pytest

from .conftest import assert_same_as_baseline, refused_diffs

try:  # the capture-side redactor lands in #38; main has no such function
    from reflect_kb.issues.sanitize import redact_secrets
except ImportError:  # pragma: no cover - main baseline
    redact_secrets = None

FAKE_TOKEN = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"
_CLASSIFICATION_LINE = "classification: internal\n"


def _strip_one_classification(text: str) -> str:
    """Remove exactly one classification: internal line (frontmatter default)."""
    idx = text.find(_CLASSIFICATION_LINE)
    return text if idx < 0 else text[:idx] + text[idx + len(_CLASSIFICATION_LINE):]


def redaction_or_classification(key: str, old, new) -> bool:
    if not isinstance(old, str) or not isinstance(new, str):
        return False
    candidate = _strip_one_classification(new) if new.count(_CLASSIFICATION_LINE) == old.count(_CLASSIFICATION_LINE) + 1 else new
    if candidate == old:
        return True
    return redact_secrets is not None and candidate == redact_secrets(old).text


ALLOWED_BEHAVIOUR_DIFF = redaction_or_classification


@pytest.fixture(scope="module")
def behaviour(captures):
    return captures("behaviour")


def test_reflect_add_legacy_and_secret_notes(behaviour) -> None:
    baseline, branch = behaviour
    for run in branch["add"]["runs"].values():
        assert run["exit"] == 0, run
    assert branch["add"]["added"], "reflect add wrote nothing"
    assert_same_as_baseline("add", baseline["add"], branch["add"], allowed=ALLOWED_BEHAVIOUR_DIFF)


def test_reindex_and_search_mode1(behaviour) -> None:
    baseline, branch = behaviour
    if "skipped" in branch["reindex"]:
        pytest.skip(branch["reindex"]["skipped"])
    assert branch["reindex"]["exit"] == 0 and branch["reindex"]["indexed"], branch["reindex"]
    assert branch["search"]["ranked"], "search returned no ranked notes for the fixture query"
    assert_same_as_baseline("reindex", baseline["reindex"], branch["reindex"])
    assert_same_as_baseline("search", baseline["search"], branch["search"])


def test_session_start_recall_hook(behaviour) -> None:
    baseline, branch = behaviour
    assert branch["recall"]["exit"] == 0, branch["recall"]
    assert branch["recall"]["context_nonempty"], (
        "SessionStart recall injected nothing for the seeded KB and the jwt commit: "
        f"{branch['recall']['stderr_tail']}"
    )
    assert_same_as_baseline("recall", baseline["recall"], branch["recall"], allowed=ALLOWED_BEHAVIOUR_DIFF)


def test_cascade_slice_and_bounded_input(behaviour) -> None:
    baseline, branch = behaviour
    assert branch["cascade"]["exit"] == 0, branch["cascade"]
    assert branch["cascade"]["slice"], "the cascade produced no slice for the recorded transcript"
    assert_same_as_baseline("cascade", baseline["cascade"], branch["cascade"], allowed=ALLOWED_BEHAVIOUR_DIFF)


def test_extract_writer_with_canned_model_reply(behaviour) -> None:
    baseline, branch = behaviour
    assert branch["extract"]["exit"] == 0, branch["extract"]
    assert branch["extract"]["summary"]["created"] == 1, branch["extract"]["summary"]
    assert_same_as_baseline("extract", baseline["extract"], branch["extract"], allowed=ALLOWED_BEHAVIOUR_DIFF)


# --------------------------------------------------------------------------- #
# The whitelist itself is under test: it must not accept arbitrary edits.
# --------------------------------------------------------------------------- #


def test_whitelist_accepts_redaction_and_one_classification_line() -> None:
    if redact_secrets is None:
        pytest.skip("redact_secrets lands in #38")
    old = f"---\ntitle: t\nkey_insight: k\n---\nexport TOKEN={FAKE_TOKEN}\n"
    redacted = redact_secrets(old).text
    assert redaction_or_classification("x", old, redacted)
    assert redaction_or_classification("x", old, old.replace("---\ntitle", "---\nclassification: internal\ntitle", 1))
    assert redaction_or_classification("x", old, redacted.replace("---\ntitle", "---\nclassification: internal\ntitle", 1))


def test_whitelist_refuses_any_other_edit(behaviour) -> None:
    """Negative: drop key_insight from a captured note; the gate must fail."""
    baseline, branch = behaviour
    name, text = next((k, v) for k, v in branch["add"]["added"].items() if k.endswith(".md"))
    assert "key_insight:" in text
    mutated = {**branch["add"], "added": {**branch["add"]["added"], name: text.replace("key_insight:", "insight:", 1)}}
    refused = refused_diffs(baseline["add"], mutated, ALLOWED_BEHAVIOUR_DIFF)
    assert any(k == f"added.{name}" for k, _, _ in refused), refused
    assert not redaction_or_classification("x", "a: 1\nb: 2\n", "a: 1\n")
    assert not redaction_or_classification("x", "a\n", "a\nclassification: internal\nclassification: internal\n")
