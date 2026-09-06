"""The plugin's stdlib redactor and the engine's redact_secrets stay
identical: same tables, same output on the repository's markdown corpus, on
the over-redaction cases and on the credentials, with the vendored path
called explicitly (pytest's pythonpath includes src, so nothing else would
ever run it)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "plugin" / "scripts"))
import secret_redact  # noqa: E402

engine = pytest.importorskip("reflect_kb.issues.sanitize")


def _cases():
    spec = importlib.util.spec_from_file_location("capture_cases", ROOT / "tests" / "test_capture_redaction.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


CASES = _cases()
FAKE_TOKEN = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"
JSON_LINE = json.dumps({"api_key": "AbCd1234efgh5678ijklMNOP", "db_password": "s3cr3tP4ssw0rdValue9",
                        "tool": "reflect", "token_count": 123456789012, "hook": "https://hooks.slack.com/services/T/B/x"})


def test_tables_and_rules_are_identical() -> None:
    mine = [(p.pattern, r, k) for p, r, k in secret_redact._SECRET_PATTERNS]
    theirs = [(p.pattern, r, k) for p, r, k in engine._SECRET_PATTERNS]
    assert mine == theirs
    assert secret_redact._GENERIC_SECRET_RE.pattern == engine._GENERIC_SECRET_RE.pattern
    assert secret_redact._CAPTURE_EXEMPT_KEYS == engine._CAPTURE_EXEMPT_KEYS


@pytest.mark.parametrize("case", CASES.LEGITIMATE + CASES.CREDENTIALS + [JSON_LINE], ids=lambda c: c[:40])
def test_vendored_and_engine_agree_on_every_case(case: str) -> None:
    assert secret_redact._redact_local(case) == engine.redact_secrets(case).text


@pytest.mark.parametrize("path", CASES._corpus(), ids=lambda p: str(p.relative_to(ROOT)))
def test_vendored_and_engine_agree_on_the_corpus(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert secret_redact._redact_local(text) == engine.redact_secrets(text).text


def test_the_vendored_path_runs_when_the_engine_is_absent(monkeypatch) -> None:
    monkeypatch.setattr(secret_redact, "_engine_redact_secrets", None)
    sample = f"export GH={FAKE_TOKEN}\n{JSON_LINE}\n"
    out = secret_redact.redact_secrets_text(sample)
    assert out == secret_redact._redact_local(sample)
    assert FAKE_TOKEN not in out and "AbCd1234" not in out
    assert json.loads(out.splitlines()[1])["api_key"] == "<REDACTED:generic_secret>"
