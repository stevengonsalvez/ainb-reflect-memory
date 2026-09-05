"""The capture gate: a learning note is redacted before it is written.

A transcript that carries a credential must never yield a note that carries
it. Token-shaped fixtures are assembled at runtime so this file never contains
a verbatim secret-shaped literal (GitHub push protection).
"""

from __future__ import annotations

from pathlib import Path

import yaml
from click.testing import CliRunner

from reflect_kb import write_flow
from reflect_kb.cli import learnings_cli
from reflect_kb.issues.sanitize import redact_secrets

GITHUB_TOKEN = "gh" + "p_" + "abcdefghijklmnopqrstuvwxyz0123456789"
AWS_KEY = "AKIA" + "IOSFODNN7" + "EXAMPLE"
PEM_BLOCK = (
    "-----BEGIN RSA PRIVATE KEY-----\n"
    "MIIEowIBAAKCAQEAfakefakefakefakefakefakefakefake\n"
    "-----END RSA PRIVATE KEY-----"
)
SECRETS = (GITHUB_TOKEN, AWS_KEY, PEM_BLOCK)


def _note_with_secrets() -> str:
    fm = {
        "title": "Deploy failed until the token was rotated",
        "category": "debugging-sessions",
        "key_insight": "Rotate_the_deploy_token_before_retrying",
        "created": "2026-09-05",
        "confidence": "high",
        "commit": "3f2a9c1d4e5b6a7f8091a2b3c4d5e6f708192a3b",
    }
    body = (
        "## Problem\n"
        f"The deploy script exported GITHUB_TOKEN={GITHUB_TOKEN} and\n"
        f"AWS_ACCESS_KEY_ID={AWS_KEY} into the shell.\n\n"
        "## Solution\n"
        f"The leaked key material was:\n{PEM_BLOCK}\n"
        "Rotate it and read the token from the keychain instead.\n"
    )
    return f"---\n{yaml.safe_dump(fm, sort_keys=True)}---\n\n{body}"


def test_redact_secrets_strips_all_three_and_keeps_provenance() -> None:
    res = redact_secrets(_note_with_secrets())
    for secret in SECRETS:
        assert secret not in res.text
    assert res.redactions["github_token"] == 1
    assert res.redactions["aws_key"] == 1
    assert res.redactions["private_key"] == 1
    # Secrets only: the commit sha and the insight survive.
    assert "3f2a9c1d4e5b6a7f8091a2b3c4d5e6f708192a3b" in res.text
    assert "Rotate_the_deploy_token_before_retrying" in res.text


def test_redact_secrets_leaves_clean_text_untouched() -> None:
    text = "key_insight: use spawn_blocking for sync code\nsha: abcdef0123456789abcdef01\n"
    res = redact_secrets(text)
    assert res.text == text
    assert res.total_redactions == 0


def test_reflect_add_writes_a_redacted_note(tmp_path: Path, monkeypatch) -> None:
    kb = tmp_path / "kb"
    (kb / learnings_cli.DOCUMENTS_DIR).mkdir(parents=True)
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    monkeypatch.setattr(learnings_cli, "_sync_qmd", lambda: None)

    def _no_engine():
        raise RuntimeError("graph engine not available in this test")

    monkeypatch.setattr(learnings_cli, "_get_graph_engine", _no_engine)

    src = tmp_path / "transcript-learning.md"
    src.write_text(_note_with_secrets(), encoding="utf-8")

    result = CliRunner().invoke(learnings_cli.cli, ["add", "--force", str(src)])
    assert result.exit_code == 0, result.output

    written = list((kb / learnings_cli.DOCUMENTS_DIR).glob("*.md"))
    assert len(written) == 1
    note = written[0].read_text(encoding="utf-8")
    for secret in SECRETS:
        assert secret not in note
    assert "<REDACTED:github_token>" in note
    assert "<REDACTED:aws_key>" in note
    assert "<REDACTED:private_key>" in note
    # Nothing in the KB directory carries a secret (sidecar included).
    for path in (kb / learnings_cli.DOCUMENTS_DIR).iterdir():
        for secret in SECRETS:
            assert secret not in path.read_text(encoding="utf-8")


def test_reflect_add_rejects_unknown_classification(tmp_path: Path, monkeypatch) -> None:
    kb = tmp_path / "kb"
    (kb / learnings_cli.DOCUMENTS_DIR).mkdir(parents=True)
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    src = tmp_path / "note.md"
    src.write_text(
        "---\ntitle: t\ncategory: c\nkey_insight: k\nclassification: top-secret\n---\nbody\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(learnings_cli.cli, ["add", "--force", str(src)])
    assert result.exit_code == 2
    assert not list((kb / learnings_cli.DOCUMENTS_DIR).iterdir())


def test_team_kb_copy_is_redacted(tmp_path: Path) -> None:
    doc = tmp_path / "leak.md"
    doc.write_text(_note_with_secrets(), encoding="utf-8")
    team_root = tmp_path / "team"
    staged = write_flow._copy_into_team(doc, team_root)
    copied = staged[0].read_text(encoding="utf-8")
    for secret in SECRETS:
        assert secret not in copied
    # The local source of truth is never rewritten by the team copy step.
    assert GITHUB_TOKEN in doc.read_text(encoding="utf-8")


def test_reflect_add_redacts_an_explicit_sidecar(tmp_path: Path, monkeypatch) -> None:
    kb = tmp_path / "kb"
    (kb / learnings_cli.DOCUMENTS_DIR).mkdir(parents=True)
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    monkeypatch.setattr(learnings_cli, "_sync_qmd", lambda: None)
    monkeypatch.setattr(learnings_cli, "_get_graph_engine", lambda: (_ for _ in ()).throw(RuntimeError("no engine")))

    src = tmp_path / "note.md"
    src.write_text(_note_with_secrets(), encoding="utf-8")
    sidecar = tmp_path / "note.entities.yaml"
    sidecar.write_text(
        "document_id: note\nentities:\n  - name: deploy token\n    type: credential\n"
        f"    description: \"value was {GITHUB_TOKEN}\"\nrelationships: []\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        learnings_cli.cli, ["add", "--force", str(src), "--entities", str(sidecar)]
    )
    assert result.exit_code == 0, result.output
    written = list((kb / learnings_cli.DOCUMENTS_DIR).glob("*.entities.yaml"))
    assert len(written) == 1
    assert GITHUB_TOKEN not in written[0].read_text(encoding="utf-8")


def test_fleet_ingest_redacts_imported_artifacts(tmp_path: Path, monkeypatch) -> None:
    import json

    from reflect_kb.fleet import importer as importer_mod

    kb = tmp_path / "kb"
    (kb / "documents").mkdir(parents=True)
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    monkeypatch.setenv("REFLECT_STATE_DIR", str(tmp_path / "state"))
    from reflect_kb import metrics

    monkeypatch.setattr(metrics, "METRICS_PATH", tmp_path / "state" / "metrics.jsonl")
    root = tmp_path / "fleet"
    root.mkdir()
    (root / "patterns.jsonl").write_text(
        json.dumps({"title": "Leaky pattern", "description": f"export GH={GITHUB_TOKEN} and {AWS_KEY}"})
        + "\n"
    )
    result = importer_mod.ingest(root, ["patterns"])
    assert result.imported == 1, result.error_details
    for path in (kb / "documents").rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert GITHUB_TOKEN not in text and AWS_KEY not in text, path
