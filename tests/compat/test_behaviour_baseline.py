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

import os
from pathlib import Path

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


def _baseline_extractor_sidecar(baseline_tree: Path, note_text: str) -> str:
    """The BASELINE tree's extractor run on the redacted note, so a regression
    in this branch's extractor cannot certify its own output."""
    import subprocess
    import sys

    code = (
        "import sys, yaml; from reflect_kb.cli.entity_store import auto_extract_entities\n"
        "text = sys.stdin.read(); parts = text.split('---', 2)\n"
        "fm = yaml.safe_load(parts[1]) if len(parts) >= 3 else {}\n"
        "print(auto_extract_entities(text, fm or {}).to_yaml())"
    )
    proc = subprocess.run([sys.executable, "-c", code], input=note_text, capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(baseline_tree / "src")}, timeout=300, check=False)
    assert proc.returncode == 0, proc.stderr[-800:]
    return proc.stdout


def _only_redaction_apart(baseline_note: str, branch_note: str) -> bool:
    return redact_secrets is not None and branch_note == redact_secrets(baseline_note).text


def test_reflect_add_legacy_and_secret_notes(behaviour, baseline_tree) -> None:
    baseline, branch = behaviour
    for run in branch["add"]["runs"].values():
        assert run["exit"] == 0, run
    added = branch["add"]["added"]
    assert added, "reflect add wrote nothing"

    # Whitelist buckets for this branch (redaction at capture):
    # - a sidecar may differ from the baseline only when its sibling note
    #   differs from the baseline note by redaction alone AND the sidecar is
    #   exactly the BASELINE extractor's output on that redacted note;
    # - a written id may differ only when it is the id of the redacted bytes
    #   (slug plus hash of title and redacted body), computed here from the
    #   captured note text, never from this branch's own id function alone.
    import re

    from reflect_kb.cli.learnings_cli import generate_document_id, parse_frontmatter

    mask = lambda t: re.sub(r"extracted_at:.*", "extracted_at: <TS>", t)
    verified: set[str] = set()
    baseline_added = baseline["add"]["added"]
    for name, text in added.items():
        if not name.endswith(".entities.yaml"):
            continue
        note_name = name[: -len(".entities.yaml")] + ".md"
        note, baseline_note = added.get(note_name), baseline_added.get(note_name)
        if note is None or baseline_note is None or not _only_redaction_apart(baseline_note, note):
            continue
        if mask(_baseline_extractor_sidecar(baseline_tree, note)).strip() == mask(text).strip():
            verified.add(f"added.{name}")
    expected_ids: set[str] = set()
    for name, text in added.items():
        if name.endswith(".md"):
            fm, body = parse_frontmatter(text)
            if fm and fm.get("title"):
                expected_ids.add(generate_document_id(fm["title"], body))

    def regenerated_sidecar(key: str, old, new) -> bool:
        return key in verified

    def id_of_redacted_bytes(key: str, old, new) -> bool:
        return key.startswith("ids") and isinstance(new, str) and new in expected_ids

    assert_same_as_baseline("add", baseline["add"], branch["add"],
                            allowed=lambda k, o, n: ALLOWED_BEHAVIOUR_DIFF(k, o, n) or regenerated_sidecar(k, o, n)
                            or id_of_redacted_bytes(k, o, n))


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


def _canonical_sidecar(key: str, old, new) -> bool:
    """reflect add now parses an explicit sidecar (after redaction) and writes
    it back through write_sidecar, so the stored sidecar is the canonical
    serialisation of the baseline's, not a byte copy of the extractor's file."""
    if not key.endswith(".entities.yaml") or not isinstance(old, str) or not isinstance(new, str):
        return False
    import re

    from reflect_kb.cli.entity_store import DocumentEntities

    mask = lambda t: re.sub(r"extracted_at:.*\n?", "", t)
    try:
        canonical = DocumentEntities.from_yaml(redact_secrets(old).text if redact_secrets else old).to_yaml()
    except Exception:  # noqa: BLE001, a sidecar the parser rejects is a real diff
        return False
    return mask(canonical).strip() == mask(new).strip()


def _transcript_key_renamed(key: str, old, new) -> bool:
    """The drain now writes the transcript path as source_transcript, because
    source_path is the key a source pin is built from (repo, commit,
    source_path). A written note may differ from the baseline by that one
    rename on the transcript line, on top of the redaction bucket."""
    if not key.startswith("written.") or not key.endswith(".md") or not isinstance(old, str) or not isinstance(new, str):
        return False
    import re

    # The branch writes the transcript under both keys the readers know: the
    # top-level source_transcript and the template's provenance block.
    renamed = re.sub(r'^source_path: ("[^"\n]*\.jsonl")$',
                     r"source_transcript: \1\nprovenance:\n  source_path: \1", old, count=1, flags=re.MULTILINE)
    return renamed != old and redaction_or_classification(key, renamed, new)


def test_extract_writer_with_canned_model_reply(behaviour) -> None:
    baseline, branch = behaviour
    assert branch["extract"]["exit"] == 0, branch["extract"]
    assert branch["extract"]["summary"]["created"] == 1, branch["extract"]["summary"]

    def allowed(k, o, n):
        return ALLOWED_BEHAVIOUR_DIFF(k, o, n) or _canonical_sidecar(k, o, n) or _transcript_key_renamed(k, o, n)

    assert_same_as_baseline("extract", baseline["extract"], branch["extract"], allowed=allowed)


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
