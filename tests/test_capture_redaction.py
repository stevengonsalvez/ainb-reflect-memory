"""The capture gate: a learning note is redacted before it is written.

A transcript that carries a credential must never yield a note that carries
it. Token-shaped fixtures are assembled at runtime so this file never contains
a verbatim secret-shaped literal (GitHub push protection).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


# Real passwords in the corpus: the local test container's password sits in a
# password slot, so the password-key and URL rules redact it by design.
_CORPUS_CREDENTIALS = {
    "docs/setup.md": [
        ("POSTGRES_PASSWORD=reflect_test", "POSTGRES_PASSWORD=<REDACTED:generic_secret>"),
        ("postgresql://postgres:reflect_test@", "postgresql://postgres:<REDACTED:url_password>@"),
    ],
}


@pytest.mark.parametrize("path", _corpus(), ids=lambda p: str(p.relative_to(Path(__file__).resolve().parents[1])))
def test_real_notes_pass_through_capture_redaction_unchanged(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    expected = text
    for raw, clean in _CORPUS_CREDENTIALS.get(str(path.relative_to(Path(__file__).resolve().parents[1])), []):
        assert raw in text, raw
        expected = expected.replace(raw, clean)
    assert redact_secrets(text).text == expected, path


def test_corpus_is_large_enough_to_mean_something() -> None:
    assert len(_corpus()) >= 40, len(_corpus())


# --------------------------------------------------------------------------- #
# The indexed entities, the project-tree copy, ids and dedupe (items 25, 31, 41, 42)
# --------------------------------------------------------------------------- #

class _Engine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def insert_document(self, text, entities_formatted=None, label=None):
        self.calls.append((text, entities_formatted))
        return SimpleNamespace(indexed=True, reason=None)

    shared_store_target = None


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
    # The user's own files are never rewritten (item 5): only the KB copies are clean.
    assert GITHUB_TOKEN in src.read_text(encoding="utf-8")
    assert GITHUB_TOKEN in sidecar.read_text(encoding="utf-8")


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


def test_force_replaces_a_note_whose_secret_was_in_the_title(tmp_path: Path, monkeypatch) -> None:
    """Item 11: the old id is built from the raw title and body, so a pre-gate
    copy whose secret sat in the title is matched and removed."""
    kb = _kb(tmp_path, monkeypatch)
    raw = f"---\ntitle: rotate {GITHUB_TOKEN} nightly\ncategory: ops\nkey_insight: rotate the token\n---\n\nbody\n"
    fm, body = learnings_cli.parse_frontmatter(raw)
    old_id = learnings_cli.generate_document_id(fm["title"], body)
    (kb / "documents" / f"{old_id}.md").write_text(raw, encoding="utf-8")
    src = tmp_path / "note.md"
    src.write_text(raw, encoding="utf-8")
    result = CliRunner().invoke(learnings_cli.cli, ["add", str(src), "--force"])
    assert result.exit_code == 0, result.output
    notes = sorted((kb / "documents").glob("*.md"))
    assert len(notes) == 1 and notes[0].name != f"{old_id}.md", [n.name for n in notes]
    assert GITHUB_TOKEN not in notes[0].read_text(encoding="utf-8")


def test_fleet_import_replaces_a_pre_gate_note_stored_under_its_raw_id(tmp_path: Path, monkeypatch) -> None:
    """Item 11: a note imported before the gate sits under the raw-body id;
    a fresh import computes both ids, removes the leaky file and writes one
    clean copy instead of a clean duplicate beside it."""
    import json

    from reflect_kb import metrics
    from reflect_kb.fleet import importer as importer_mod

    kb = tmp_path / "kb"
    docs = kb / "documents"
    docs.mkdir(parents=True)
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(kb))
    monkeypatch.setenv("REFLECT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(metrics, "METRICS_PATH", tmp_path / "state" / "metrics.jsonl")
    root = tmp_path / "fleet"
    root.mkdir()
    (root / "patterns.jsonl").write_text(
        json.dumps({"title": "Leaky pattern", "description": f"export GH={GITHUB_TOKEN}"}) + "\n")
    raw = next(iter(importer_mod._iter_docs(root, ["patterns"], importer_mod.ImportResult())))
    raw_id = learnings_cli.generate_document_id(raw.title, raw.body)
    (docs / f"{raw_id}.md").write_text(raw.render(raw_id, 1), encoding="utf-8")
    (docs / f"{raw_id}.entities.yaml").write_text("entities: []\n", encoding="utf-8")
    assert GITHUB_TOKEN in (docs / f"{raw_id}.md").read_text(encoding="utf-8")

    result = importer_mod.ingest(root, ["patterns"])
    notes = sorted(docs.glob("*.md"))
    assert len(notes) == 1, [n.name for n in notes]
    assert notes[0].name != f"{raw_id}.md" and GITHUB_TOKEN not in notes[0].read_text(encoding="utf-8")
    assert not (docs / f"{raw_id}.entities.yaml").exists()
    assert result.imported == 1 and result.deduped == 0, result


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



# --------------------------------------------------------------------------- #
# Review round four: pattern gaps, over-redaction and bounded cost
# --------------------------------------------------------------------------- #

_UUID = "3b241101-e2bb-4255-8caf-4136c566a962"
# (text, the credential that must not survive). Assembled at runtime.
PATTERN_GAPS = [
    ("DATABASE_URL=postgresql://app:" + "hunter2pass" + "@db.internal:5432/app", "hunter2pass"),
    ("redis://:" + "s3cretpw" + "@cache:6379", "s3cretpw"),
    ("https://deploy:" + "Tr0ub4dor" + "@git.example.com/org/repo.git", "Tr0ub4dor"),
    ("-----BEGIN ENCRYPTED PRIVATE KEY-----\nMIIFHDBOBgkqhkiG9w0BBQ0w\n-----END ENCRYPTED PRIVATE KEY-----",
     "MIIFHDBOBgkqhkiG9w0BBQ0w"),
    ("-----BEGIN PGP PRIVATE KEY BLOCK-----\nlQOYBFzZ2x0BCADq\n-----END PGP PRIVATE KEY BLOCK-----", "lQOYBFzZ2x0BCADq"),
    ("pasted: -----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAAABG5vbmU\nand the paste stopped",
     "b3BlbnNzaC1rZXktdjEAAAAABG5vbmU"),
    ('{\\"api_key\\": \\"' + "AbCd1234efgh5678ijklMNOP" + '\\"}', "AbCd1234efgh5678ijklMNOP"),
    ("curl -H 'Authorization: Basic " + "dXNlcjpwYXNzd29yZA==" + "'", "dXNlcjpwYXNzd29yZA=="),
    ("authorization: bearer " + "abcdef123456ghijkl", "abcdef123456ghijkl"),
    ("password: " + "correcthorsebattery", "correcthorsebattery"),
    ("DB_PASSWORD=" + "correct-horse-battery-staple", "correct-horse-battery-staple"),
    ("passwd: " + "hunter2x", "hunter2x"),
    ("client_secret: " + "abcdefghij", "abcdefghij"),
    ("API_KEY=" + "ABCD_EFGH_IJKL_MNOP", "ABCD_EFGH_IJKL_MNOP"),
    ("http://hooks.slack.com/services/" + "T000/B000/XXXXsecret", "XXXXsecret"),
    ("https://discord.com/api/webhooks/" + "123456/abcDEF-ghi", "abcDEF-ghi"),
    ("creds: " + "ASIA" + "IOSFODNN7EXAMPLE", "ASIA" + "IOSFODNN7EXAMPLE"),
    ("whsec_" + "abcdefghijklmnopqrstuvwxyz012345", "abcdefghijklmnopqrstuvwxyz012345"),
    ("hf_" + "abcdefghijklmnopqrstuvwxyzABCDEFGH", "abcdefghijklmnopqrstuvwxyzABCDEFGH"),
    ("sk-" + "abc_def_ghi_jkl_mno_pqr_stu", "abc_def_ghi_jkl_mno_pqr_stu"),
    ("HEROKU_API_KEY=" + _UUID, _UUID),
    ("token=https://example.com/cb?access_token=" + "abc123def456ghi", "abc123def456ghi"),
]
NOT_SECRETS = [
    "cache_key=user42:session",
    "the idempotency key: req-2026-09-13-abc1",
    "auth: oauth2-client-credentials-v3",
    "foreign_key=users.id2fk_constraint",
    "token=https://example.com/oauth2/callback",
    "i18n key: settings.page.title2",
    f"idempotencyKey: {_UUID}",
    "key: sha256:" + "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "Use bearer authentication for the API",
    "password = settings.db_password",
    "password: ${DB_PASSWORD}",
    "db_password = os.environ.get('DB_PASSWORD')",
    "export REFLECT_PG_DSN=\"postgresql://USER:PASS@HOST:5432/DBNAME\"",
    "postgresql://app:…@host/db",
    "ssh://git@github.com/org/repo",
]
CREDENTIALS += [text for text, _ in PATTERN_GAPS]
LEGITIMATE += NOT_SECRETS

# --------------------------------------------------------------------------- #
# Review round five: the UUID exemption and the bearer floor
# --------------------------------------------------------------------------- #

# Round four exempted every UUID-shaped value, and rescued only api-key names.
# Plenty of services issue UUID tokens, so these were stored and mirrored in
# the clear. (text, the credential that must not survive.)
UUID_CREDENTIALS = [
    (f"token: {_UUID}", _UUID),
    (f"session_token: {_UUID}", _UUID),
    (f"X-Auth-Token: {_UUID}", _UUID),
    (f"authorization: {_UUID}", _UUID),
    (f"auth: {_UUID}", _UUID),
    (f"refreshToken={_UUID}", _UUID),
    (f"client_secret: {_UUID}", _UUID),
    # The bearer floor: 15 to 23 purely alphabetic characters passed.
    ("Bearer " + "abcdefghijklmno", "abcdefghijklmno"),
    ("Authorization: Bearer " + "abcdefghijklmnop", "abcdefghijklmnop"),
    ("bearer " + "qwertyuiopasdfghjklzxcv", "qwertyuiopasdfghjklzxcv"),
]
# The other direction: a key that names an identifier keeps its UUID, and a
# key that names a credential keeps a value that is plainly not one.
UUID_IDENTIFIERS = [
    f"request_id: {_UUID}",
    f"uuid: {_UUID}",
    f"idempotency_key: {_UUID}",
    f"cache_key: {_UUID}",
    f"correlation_id={_UUID}",
    "auth: oauth2-client-credentials-v3",
    "token=https://example.com/oauth2/callback",
    "Use bearer authentication for the API",
]
CREDENTIALS += [text for text, _ in UUID_CREDENTIALS]
LEGITIMATE += UUID_IDENTIFIERS


@pytest.mark.parametrize(("text", "secret"), UUID_CREDENTIALS, ids=lambda v: str(v)[:40])
def test_round_five_uuid_and_bearer_credentials_are_redacted(text: str, secret: str) -> None:
    from reflect_kb.issues.sanitize import sanitize

    out = redact_secrets(text).text
    assert secret not in out and "<REDACTED:" in out, out
    assert secret not in sanitize(text).text


@pytest.mark.parametrize("text", UUID_IDENTIFIERS)
def test_round_five_identifier_keys_keep_their_uuid(text: str) -> None:
    assert redact_secrets(text).text == text


@pytest.mark.parametrize(("text", "secret"), PATTERN_GAPS, ids=lambda v: str(v)[:40])
def test_round_four_pattern_gaps_are_redacted_in_both_postures(text: str, secret: str) -> None:
    from reflect_kb.issues.sanitize import sanitize

    out = redact_secrets(text).text
    assert secret not in out and "<REDACTED:" in out, out
    assert secret not in sanitize(text).text


@pytest.mark.parametrize("text", NOT_SECRETS)
def test_round_four_identifiers_are_not_redacted(text: str) -> None:
    assert redact_secrets(text).text == text


def test_url_password_keeps_the_scheme_user_and_host() -> None:
    out = redact_secrets("postgresql://app:" + "hunter2pass" + "@db.internal:5432/app").text
    assert out == "postgresql://app:<REDACTED:url_password>@db.internal:5432/app"


def test_escaped_json_redaction_keeps_the_escaped_quotes() -> None:
    import json

    inner = json.dumps({"api_key": "AbCd1234efgh5678ijklMNOP", "tool": "reflect"})
    outer = json.dumps({"message": inner})
    parsed = json.loads(json.loads(redact_secrets(outer).text)["message"])
    assert parsed == {"api_key": "<REDACTED:generic_secret>", "tool": "reflect"}


@pytest.mark.parametrize(
    "text",
    [
        "key_" * 20000,
        "-----BEGIN RSA PRIVATE KEY-----\n" * 8000,
        ("key_" * 64 + " ") * 2000,
        "eyJ-" * 50000,
        "local-part." * 20000,
    ],
    ids=["repeated-keyword-identifier", "unterminated-pem", "many-long-identifiers", "jwt-run", "email-run"],
)
def test_adversarial_inputs_redact_in_bounded_time(text: str) -> None:
    """Item 8: each of these took 8 to 27 seconds before the patterns were
    bounded. The limit is generous so a slow CI box does not flake."""
    import time

    from reflect_kb.issues.sanitize import sanitize

    start = time.monotonic()
    redact_secrets(text)
    sanitize(text)
    assert time.monotonic() - start < 2.0


@pytest.mark.parametrize("confidence", ["high", "medium", "low"])
def test_route_document_never_puts_a_title_secret_in_git_gh_or_the_queue(tmp_path: Path, confidence: str) -> None:
    """Item 2: the file copy was redacted, but the title came from the raw
    frontmatter and reached the commit message, branch, PR title and body
    and the review-queue record."""
    import subprocess

    calls: list[list[str]] = []

    def runner(cmd, cwd=None, check=True):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="https://example.com/pr/1\n" if cmd[0] == "gh" else "abc\n",
                                           stderr="")

    doc = tmp_path / "leak.md"
    doc.write_text(f"---\ntitle: rotate {GITHUB_TOKEN} nightly\nconfidence: {confidence}\n---\n\nbody\n",
                   encoding="utf-8")
    queue = tmp_path / "queue"
    result = write_flow.route_document(doc, team_root=tmp_path / "team", queue_dir=queue, git=runner, gh=runner,
                                       gh_available=lambda: True)
    assert GITHUB_TOKEN not in repr(calls), calls
    assert GITHUB_TOKEN not in result.title and GITHUB_TOKEN not in (result.branch or "")
    assert "<REDACTED:github_token>" in result.title
    for path in queue.glob("*.yaml") if queue.exists() else []:
        assert GITHUB_TOKEN not in path.read_text(encoding="utf-8")
    if confidence == "medium":
        assert any(c[:3] == ["gh", "pr", "create"] for c in calls)


def test_reflect_add_leaves_the_source_file_byte_for_byte(tmp_path: Path, monkeypatch) -> None:
    """Item 5: add used to overwrite the user's source with the redacted text
    and keep no copy. The source stays as it was; the KB copy is clean and
    the output says the source still carries the secret."""
    kb = _kb(tmp_path, monkeypatch)
    engine = _Engine()
    monkeypatch.setattr(learnings_cli, "_get_graph_engine", lambda: engine)
    src = tmp_path / "note.md"
    src.write_text(_note_with_secrets(), encoding="utf-8")
    before = src.read_bytes()
    result = CliRunner().invoke(learnings_cli.cli, ["add", str(src)])
    assert result.exit_code == 0, result.output
    assert src.read_bytes() == before
    assert "still contains the secret" in " ".join(result.output.split())
    note = next((kb / learnings_cli.DOCUMENTS_DIR).glob("*.md")).read_text(encoding="utf-8")
    assert all(secret not in note for secret in SECRETS)
    assert GITHUB_TOKEN not in engine.calls[0][0]
    # Nothing beside the source was created (no backup inside the project tree).
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kb", "note.md"]


def test_add_without_force_removes_the_copy_under_the_unredacted_id(tmp_path: Path, monkeypatch) -> None:
    """Item 6: the leaked copy used to be removed only with --force."""
    kb = _kb(tmp_path, monkeypatch)
    monkeypatch.setattr(learnings_cli, "_get_graph_engine", lambda: _Engine())
    raw = _note_with_secrets()
    frontmatter, raw_body = learnings_cli.parse_frontmatter(raw)
    old_id = learnings_cli.generate_document_id(frontmatter["title"], raw_body)
    docs = kb / learnings_cli.DOCUMENTS_DIR
    (docs / f"{old_id}.md").write_text(raw, encoding="utf-8")
    (docs / f"{old_id}.entities.yaml").write_text("document_id: x\nentities: []\nrelationships: []\n", encoding="utf-8")
    src = tmp_path / "note.md"
    src.write_text(raw, encoding="utf-8")
    result = CliRunner().invoke(learnings_cli.cli, ["add", str(src)])
    assert result.exit_code == 0, result.output
    assert not (docs / f"{old_id}.md").exists() and not (docs / f"{old_id}.entities.yaml").exists()
    assert f"Removed {old_id}.md" in " ".join(result.output.split())
    for path in docs.iterdir():
        assert GITHUB_TOKEN not in path.read_text(encoding="utf-8"), path


class _FailingBatchEngine:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.purged: list[list[str]] = []

    shared_store_target = None

    def local_only(self, text, label):
        return label == "restricted"

    def purge_local_only(self, notes):
        self.events.append("purge")
        self.purged.append(list(notes))
        return len(notes)

    def insert_documents_batch(self, batch):
        self.events.append("batch")
        raise RuntimeError("embedding service unavailable")


def test_reindex_purges_relabelled_notes_even_when_the_batch_fails(tmp_path: Path, monkeypatch) -> None:
    """Item 7: the purge ran only after a successful batch, so a batch error
    left a note relabelled restricted in the shared store."""
    kb = _kb(tmp_path, monkeypatch)
    docs = kb / learnings_cli.DOCUMENTS_DIR
    (docs / "secret-plan-aaaaaa.md").write_text(
        "---\ntitle: secret plan\ncategory: ops\nkey_insight: k\nclassification: restricted\n---\n\nbody\n",
        encoding="utf-8")
    (docs / "open-note-bbbbbb.md").write_text(
        "---\ntitle: open note\ncategory: ops\nkey_insight: k\nclassification: internal\n---\n\nbody\n",
        encoding="utf-8")
    engine = _FailingBatchEngine()
    monkeypatch.setattr(learnings_cli, "_get_graph_engine", lambda: engine)
    result = CliRunner().invoke(learnings_cli.cli, ["reindex"])
    assert result.exit_code == 0, result.output
    assert engine.events == ["purge", "batch"], engine.events
    assert len(engine.purged[0]) == 1 and "secret plan" in engine.purged[0][0]
    assert "Batch indexing error" in result.output
