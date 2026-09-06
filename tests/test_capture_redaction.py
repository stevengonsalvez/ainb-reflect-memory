"""The capture gate: a learning note is redacted before it is written.

A transcript that carries a credential must never yield a note that carries
it. Token-shaped fixtures are assembled at runtime so this file never contains
a verbatim secret-shaped literal (GitHub push protection).
"""

from __future__ import annotations

from pathlib import Path

import pytest
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


def test_team_kb_copy_and_sidecar_are_redacted(tmp_path: Path) -> None:
    doc = tmp_path / "leak.md"
    doc.write_text(_note_with_secrets(), encoding="utf-8")
    sidecar = tmp_path / "leak.entities.yaml"
    sidecar.write_text(
        "document_id: leak\nentities:\n"
        f"  - name: deploy token\n    type: credential\n    description: \"was {GITHUB_TOKEN}\"\n"
        f"  - name: aws key\n    type: credential\n    description: \"was {AWS_KEY}\"\n"
        f"  - name: pem\n    type: credential\n    description: |\n      {PEM_BLOCK.replace(chr(10), chr(10) + '      ')}\n"
        "relationships: []\n",
        encoding="utf-8",
    )
    team_root = tmp_path / "team"
    staged = write_flow._copy_into_team(doc, team_root)
    assert len(staged) == 2
    for path in staged:
        copied = path.read_text(encoding="utf-8")
        for secret in SECRETS:
            assert secret not in copied, path
    assert "<REDACTED:github_token>" in staged[1].read_text(encoding="utf-8")


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


# --------------------------------------------------------------------------- #
# Capture posture: never lose a legitimate value (items 26 and 39)
# --------------------------------------------------------------------------- #

LEGITIMATE = [
    "auth_method: certificate_based",
    "secret_name: my-app-db-credentials",
    "api_key_env: ANTHROPIC_API_KEY",
    "token_count: 123456789012",
    "key_takeaway: SomethingLongValue",
    "task-abcdefghijklmnopqrstuvwxyz",
    "key_path: /home/u/.ssh/id_rsa.pub",
    "token_file: ~/.config/gh/hosts.yml",
    "cache_key: user-123-profile-v2",
    "sort_key: created_at_desc",
    "s3_key: exports/2026/report.csv",
    "primary_key: user_id_bigint",
    "secret_manager: aws-secrets-manager",
    "password_manager: 1password-cli",
    "- key: ANTHROPIC_API_KEY",
    "token = request.headers.get('Authorization')",
    "api_key = settings.anthropic_api_key",
    "const apiKey = process.env.X",
]
CREDENTIALS = [
    "password: Tr0ub4dor3xyzabcdefgh",
    "api_key=AbCd3fGh1jK2LmN0pQrStUv",
    'DB_PASSWORD: "x9Lm2qR8vT4wY7zA1bC3"',
    # Item 13: all-caps seeds and keys, 12 to 15 character values, hyphenated
    # credentials with short mixed segments.
    "totp_secret: JBSWY3DPEHPK3PXP",
    "api_key: DEADBEEFCAFEBABE1234",
    "licence_key: AB12C-DE34F-GH56I-JK78L",
    "password: Tr0ub4dor3xy",
    "auth_token=x9Lm2qR8vT4w",
    "secret_key: gh-Ab12Cd34-Ef56Gh78",
]
LEGITIMATE += [
    # The other direction of item 13: identifiers, versions and words that
    # share a shape with the credentials above.
    "api_key_env: GH_TOKEN_ENV",
    "token_kind: DEADBEEF",
    "key_name: REGISTRY-HOST",
    "secret_version: v1.2.3-beta.4",
    "auth_provider: 2fa-required",
    "password_hint: correcthorsebatterystaple",
]


def test_capture_rule_keeps_every_legitimate_value_and_drops_credentials() -> None:
    from reflect_kb.issues.sanitize import sanitize

    kept = [c for c in LEGITIMATE if redact_secrets(c).text != c]
    assert kept == [], f"capture over-redacted: {kept}"
    leaked = [c for c in CREDENTIALS if redact_secrets(c).text == c]
    assert leaked == [], f"capture missed a credential: {leaked}"
    # The publish posture stays strict on the same shared pattern list.
    assert "<REDACTED:generic_secret>" in sanitize("secret_name: my-app-db-credentials").text


def test_redacted_json_still_parses_and_delimiters_survive() -> None:
    """Item 12: the value's quotes stay around the placeholder, and the
    webhook rule stops at a closing delimiter, so a redacted JSON cell or
    markdown link is still well formed in both postures."""
    import json

    from reflect_kb.issues.sanitize import sanitize

    cell = json.dumps({"api_key": "AbCd1234efgh5678ijklMNOP", "hook": "https://hooks.slack.com/services/T0/B0/x1y2z3",
                       "note": "see (https://hooks.slack.com/services/T0/B0/x1y2z3), then [x]"})
    for posture in (lambda t: redact_secrets(t).text, lambda t: sanitize(t).text):
        out = posture(cell)
        parsed = json.loads(out)
        assert parsed["api_key"] == "<REDACTED:generic_secret>"
        assert parsed["hook"] == "<REDACTED:slack_webhook>"
        assert parsed["note"] == "see (<REDACTED:slack_webhook>), then [x]"
    assert redact_secrets("password: 'Tr0ub4dor3xyzabcdefgh'").text == "password: '<REDACTED:generic_secret>'"
    assert redact_secrets("password: Tr0ub4dor3xyzabcdefgh").text == "password: <REDACTED:generic_secret>"


def test_generic_rule_matches_the_json_form_a_transcript_uses() -> None:
    """Transcripts are JSONL, so the key carries a closing quote before the
    separator and the value an opening one; both postures must match it."""
    from reflect_kb.issues.sanitize import sanitize

    line = ('{"api_key": "AbCd1234efgh5678ijklMNOP", "db_password": "s3cr3tP4ssw0rdValue9", '
            '"tool": "reflect", "token_count": 123456789012, "key_path": "~/.ssh/id_ed25519"}')
    out = redact_secrets(line).text
    assert "AbCd1234" not in out and "s3cr3tP4" not in out
    assert '"tool": "reflect"' in out and "123456789012" in out and "~/.ssh/id_ed25519" in out
    assert out.count("<REDACTED:generic_secret>") == 2
    assert "AbCd1234" not in sanitize(line).text and "s3cr3tP4" not in sanitize(line).text


def _corpus() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    out: list[Path] = []
    for folder in ("tests/samples", "tests/e2e/fixture-kb", "tests/compat/fixtures", "plugin/references",
                   "plugin/skills", "docs", "plugin/docs"):
        out += [p for p in (root / folder).rglob("*.md") if p.is_file()]
    out += [root / "README.md"]
    return [p for p in out if "secret" not in p.name and "transcript" not in p.name]


@pytest.mark.parametrize("path", _corpus(), ids=lambda p: str(p.relative_to(Path(__file__).resolve().parents[1])))
def test_real_notes_pass_through_capture_redaction_unchanged(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert redact_secrets(text).text == text, path


def test_corpus_is_large_enough_to_mean_something() -> None:
    assert len(_corpus()) >= 40, len(_corpus())


# --------------------------------------------------------------------------- #
# The indexed entities, the project-tree copy, ids and dedupe (items 25, 31, 41, 42)
# --------------------------------------------------------------------------- #

class _Engine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def insert_document(self, text, entities_formatted=None):
        self.calls.append((text, entities_formatted))


def _kb(tmp_path: Path, monkeypatch) -> Path:
    kb = tmp_path / "kb"
    (kb / learnings_cli.DOCUMENTS_DIR).mkdir(parents=True)
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    monkeypatch.setattr(learnings_cli, "_sync_qmd", lambda: None)
    return kb


def test_explicit_sidecar_is_redacted_before_it_is_parsed_and_indexed(tmp_path: Path, monkeypatch) -> None:
    kb = _kb(tmp_path, monkeypatch)
    engine = _Engine()
    monkeypatch.setattr(learnings_cli, "_get_graph_engine", lambda: engine)
    src = tmp_path / "note.md"
    src.write_text(_note_with_secrets(), encoding="utf-8")
    sidecar = tmp_path / "note.entities.yaml"
    sidecar.write_text(
        "document_id: note\nentities:\n  - name: deploy token\n    type: credential\n"
        f"    description: \"value was {GITHUB_TOKEN}\"\nrelationships: []\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(learnings_cli.cli, ["add", "--force", str(src), "--entities", str(sidecar)])
    assert result.exit_code == 0, result.output
    text, entities = engine.calls[0]
    assert GITHUB_TOKEN not in text and entities and GITHUB_TOKEN not in entities
    written = list((kb / learnings_cli.DOCUMENTS_DIR).glob("*.entities.yaml"))
    assert len(written) == 1 and GITHUB_TOKEN not in written[0].read_text(encoding="utf-8")
    # The project-tree copies the skill wrote are rewritten clean in place.
    assert GITHUB_TOKEN not in src.read_text(encoding="utf-8")
    assert GITHUB_TOKEN not in sidecar.read_text(encoding="utf-8")


def test_force_replaces_the_note_stored_under_its_unredacted_id(tmp_path: Path, monkeypatch) -> None:
    kb = _kb(tmp_path, monkeypatch)
    monkeypatch.setattr(learnings_cli, "_get_graph_engine", lambda: _Engine())
    raw = _note_with_secrets()
    frontmatter, raw_body = learnings_cli.parse_frontmatter(raw)
    old_id = learnings_cli.generate_document_id(frontmatter["title"], raw_body)
    docs = kb / learnings_cli.DOCUMENTS_DIR
    (docs / f"{old_id}.md").write_text(raw, encoding="utf-8")  # a note added before the gate existed
    (docs / f"{old_id}.entities.yaml").write_text("document_id: x\nentities: []\nrelationships: []\n", encoding="utf-8")
    src = tmp_path / "note.md"
    src.write_text(raw, encoding="utf-8")
    result = CliRunner().invoke(learnings_cli.cli, ["add", "--force", str(src)])
    assert result.exit_code == 0, result.output
    notes = sorted(p.name for p in docs.glob("*.md"))
    assert len(notes) == 1 and notes[0] != f"{old_id}.md", notes
    assert GITHUB_TOKEN not in (docs / notes[0]).read_text(encoding="utf-8")
    assert not (docs / f"{old_id}.entities.yaml").exists()
    _, clean_body = learnings_cli.parse_frontmatter((docs / notes[0]).read_text(encoding="utf-8"))
    assert notes[0] == learnings_cli.generate_document_id(frontmatter["title"], clean_body) + ".md"


def test_reflect_add_round_trips_non_ascii_bytes(tmp_path: Path, monkeypatch) -> None:
    kb = _kb(tmp_path, monkeypatch)
    monkeypatch.setattr(learnings_cli, "_get_graph_engine", lambda: _Engine())
    body = "caf\u00e9 \u2014 na\u00efve r\u00e9sum\u00e9\n"
    src = tmp_path / "note.md"
    src.write_bytes(("---\ntitle: unicode\ncategory: c\nkey_insight: k\n---\n" + body).encode("utf-8"))
    result = CliRunner().invoke(learnings_cli.cli, ["add", "--force", str(src)])
    assert result.exit_code == 0, result.output
    written = next((kb / learnings_cli.DOCUMENTS_DIR).glob("*.md"))
    assert written.read_bytes() == src.read_bytes()


def test_fleet_dedupe_re_redacts_a_leaky_existing_note(tmp_path: Path, monkeypatch) -> None:
    import json

    from reflect_kb import metrics
    from reflect_kb.fleet import importer as importer_mod

    kb = tmp_path / "kb"
    (kb / "documents").mkdir(parents=True)
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    monkeypatch.setenv("REFLECT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(metrics, "METRICS_PATH", tmp_path / "state" / "metrics.jsonl")
    root = tmp_path / "fleet"
    root.mkdir()
    (root / "patterns.jsonl").write_text(
        json.dumps({"title": "Leaky pattern", "description": f"export GH={GITHUB_TOKEN}"}) + "\n")
    first = importer_mod.ingest(root, ["patterns"])
    assert first.imported == 1, first.error_details
    note = next((kb / "documents").glob("*.md"))
    assert GITHUB_TOKEN not in note.read_text(encoding="utf-8")
    # The id is derived from the redacted body: importing again dedupes onto it.
    note.write_text(note.read_text(encoding="utf-8") + f"\nleaked later: {GITHUB_TOKEN}\n", encoding="utf-8")
    second = importer_mod.ingest(root, ["patterns"])
    assert second.deduped == 1 and second.imported == 0, second
    assert GITHUB_TOKEN not in note.read_text(encoding="utf-8")

