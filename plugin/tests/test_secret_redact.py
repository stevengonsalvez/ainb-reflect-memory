"""The plugin's stdlib redactor stays identical to the engine's, and the drain's
LLM-bound slice and bounded input carry no credential."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN / "scripts"))
import secret_redact

TRANSCRIPT = _PLUGIN.parent / "tests" / "fixtures" / "transcripts" / "recorded-session.jsonl"
FAKE_TOKEN = "ghp_" + "abcdefghijklmnopqrstuvwxyz0123456789"


def test_vendored_table_matches_the_engine() -> None:
    engine = pytest.importorskip("reflect_kb.issues.sanitize")
    mine = [(p.pattern, r, k) for p, r, k in secret_redact._SECRET_PATTERNS]
    theirs = [(p.pattern, r, k) for p, r, k in engine._SECRET_PATTERNS]
    assert mine == theirs
    assert secret_redact._GENERIC_SECRET_RE.pattern == engine._GENERIC_SECRET_RE.pattern
    assert secret_redact._CAPTURE_EXEMPT_KEYS == engine._CAPTURE_EXEMPT_KEYS
    sample = f"export GITHUB_TOKEN={FAKE_TOKEN}\nAKIAIOSFODNN7EXAMPLE\nkey_insight: keep_the_keychain_token\n"
    assert secret_redact._redact_local(sample) == engine.redact_secrets(sample).text


def test_local_fallback_redacts_without_the_engine(monkeypatch) -> None:
    monkeypatch.setattr(secret_redact, "_engine_redact_secrets", None)  # resolved at import: absent here
    out = secret_redact.redact_secrets_text(f"token {FAKE_TOKEN} and AKIAIOSFODNN7EXAMPLE")
    assert FAKE_TOKEN not in out and "AKIAIOSFODNN7EXAMPLE" not in out
    assert "<REDACTED:github_token>" in out and "<REDACTED:aws_key>" in out


def test_slice_and_bounded_input_are_redacted(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REFLECT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REFLECT_DB_PATH", str(tmp_path / "reflect.db"))
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(tmp_path / "kb"))
    for name in ("reflect_config", "reflect_db", "reflect_cascade"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    cascade = importlib.import_module("reflect_cascade")
    assert FAKE_TOKEN in TRANSCRIPT.read_text()
    prep = cascade.prepare(str(TRANSCRIPT), out_path=str(tmp_path / "slice.txt"))
    assert prep.slice_path
    slice_text = Path(prep.slice_path).read_text()
    assert FAKE_TOKEN not in slice_text and "<REDACTED:github_token>" in slice_text
    bounded = cascade.bound_transcript(str(TRANSCRIPT), out_path=str(tmp_path / "bounded.txt"), max_chars=4000)
    assert FAKE_TOKEN not in Path(bounded["path"]).read_text()


def test_raw_transcript_fallback_hands_the_writer_the_bounded_view(tmp_path: Path) -> None:
    """With the cascade off the writer would read the raw transcript; the hook
    hands it `reflect_cascade.py bound`'s view instead, unconditionally: the
    dialogue rendered, <private> spans stripped, secrets redacted."""
    import json
    import os
    import subprocess

    aws = "AKIAIOSFODNN7EXAMPLE"
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----"
    transcript = tmp_path / "s.jsonl"
    rows = [
        {"type": "user", "message": {"role": "user", "content": f"deploy fails, token is {FAKE_TOKEN} "
                                                               "<private>my home address is 1 Main St</private>"}, "uuid": "u1",
         "timestamp": "2026-08-11T10:00:00Z", "sessionId": "s"},
        {"type": "assistant", "message": {"role": "assistant", "model": "m", "content": [
            {"type": "text", "text": f"no, the aws key {aws} expired; also rotate {pem}"}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}, "uuid": "a1", "timestamp": "2026-08-11T10:00:01Z", "sessionId": "s"},
    ]
    transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    stub = tmp_path / "bin" / "claude"
    stub.parent.mkdir()
    # The prompt names the file the writer is told to read; copy it out before
    # the hook removes it, then answer with a clean envelope.
    stub.write_text(
        "#!/usr/bin/env bash\n"
        "target=$(printf '%s' \"$2\" | sed -n 's/^Process the transcript at: //p' | head -1)\n"
        'printf "%s" "$target" > "$REFLECT_TEST_TARGET_PATH"; cp "$target" "$REFLECT_TEST_TARGET_COPY"\n'
        "echo '{\"type\":\"result\",\"is_error\":false,\"result\":\"nothing to capture\",\"num_turns\":1,"
        "\"total_cost_usd\":0.001,\"usage\":{\"input_tokens\":10,\"output_tokens\":2}}'\n")
    stub.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    (state / "pending_reflections.jsonl").write_text(json.dumps({
        "session_id": "s", "transcript_path": str(transcript), "trigger": "stop", "cwd": "/",
        "scope": "session", "harness": "claude", "ts": "2026-08-11T10:01:00Z"}) + "\n")
    env = {**os.environ, "REFLECT_STATE_DIR": str(state), "REFLECT_DRAIN_DRY_RUN": "0",
           "REFLECT_DRAIN_CLAUDE_BIN": str(stub), "REFLECT_DRAIN_DEBOUNCE_SEC": "0",
           "REFLECT_DRAIN_CASCADE": "0", "REFLECT_QUOTA_GATE": "0", "REFLECT_DRAIN_SKIP_REINDEX": "1",
           "REFLECT_QUIET_INSTALL_WARNING": "1", "REFLECT_DRAIN_MAX": "1", "REFLECT_DRAIN_WRITER": "agentic",
           "REFLECT_DRAIN_CWD": str(tmp_path / "cwd"), "GLOBAL_LEARNINGS_PATH": str(tmp_path / "kb"),
           "REFLECT_TEST_TARGET_PATH": str(tmp_path / "target.path"),
           "REFLECT_TEST_TARGET_COPY": str(tmp_path / "target.copy")}
    (tmp_path / "cwd").mkdir()
    subprocess.run(["bash", str(_PLUGIN / "hooks" / "reflect-drain-bg.sh")], env=env, capture_output=True,
                   text=True, timeout=180, check=False)
    log = (state / "drain.log").read_text()
    assert "bounded input: raw transcript (" in log and "redacted copy" not in log, log
    target = (tmp_path / "target.path").read_text()
    assert target and target != str(transcript), f"writer was pointed at {target!r}"
    handed = (tmp_path / "target.copy").read_text()
    for secret in (FAKE_TOKEN, aws, "MIIEpAIBAAKCAQEA", "1 Main St"):
        assert secret not in handed, secret
    assert "<REDACTED:" in handed and "[private content removed]" in handed
    assert "    OK turns=1" in log



def test_cascade_fails_closed_without_the_redactor(tmp_path, monkeypatch) -> None:
    """Item 10: an install that does not ship secret_redact.py must not slice
    or bound anything (the hook then keeps the entry queued), instead of
    sending the raw text silently."""
    monkeypatch.setenv("REFLECT_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("REFLECT_DB_PATH", str(tmp_path / "reflect.db"))
    monkeypatch.setenv("GLOBAL_LEARNINGS_PATH", str(tmp_path / "kb"))
    for name in ("reflect_config", "reflect_db", "reflect_cascade"):
        if name in sys.modules:
            importlib.reload(sys.modules[name])
    cascade = importlib.import_module("reflect_cascade")
    monkeypatch.setitem(sys.modules, "secret_redact", None)  # import fails
    prep = cascade.prepare(str(TRANSCRIPT), out_path=str(tmp_path / "slice.txt"))
    assert prep.action == "reflect" and prep.reason == "redactor-unavailable"
    assert not prep.slice_path and not (tmp_path / "slice.txt").exists()
    bounded = cascade.bound_transcript(str(TRANSCRIPT), out_path=str(tmp_path / "bounded.txt"), max_chars=4000)
    assert bounded["path"] == "" and "error" in bounded
    assert not (tmp_path / "bounded.txt").exists()


def test_default_writer_with_cascade_off_takes_the_agentic_path_on_the_bounded_view(tmp_path: Path) -> None:
    """The bounded view of a raw transcript is not a signal slice: with the
    cascade off and the writer unset (the default, extract) the agentic
    writer runs on it, and the stub sees the /reflect prompt naming that view,
    never the extract writer's tool-free argv; the view carries no secret."""
    import json
    import os
    import subprocess

    transcript = tmp_path / "s.jsonl"
    rows = [
        {"type": "user", "message": {"role": "user", "content": f"deploy fails, token is {FAKE_TOKEN}"}, "uuid": "u1",
         "timestamp": "2026-08-11T10:00:00Z", "sessionId": "s"},
        {"type": "assistant", "message": {"role": "assistant", "model": "m", "content": [
            {"type": "text", "text": "no, never hardcode it; the root cause was the env leaking the token"}],
            "usage": {"input_tokens": 1, "output_tokens": 1}}, "uuid": "a1", "timestamp": "2026-08-11T10:00:01Z", "sessionId": "s"},
    ]
    transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    stub = tmp_path / "bin" / "claude"
    stub.parent.mkdir()
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'printf "%s\\0" "$@" > "$REFLECT_TEST_ARGV"\n'
        "target=$(printf '%s' \"$2\" | sed -n 's/^Process the transcript at: //p' | head -1)\n"
        '[ -n "$target" ] && cp "$target" "$REFLECT_TEST_TARGET_COPY"\n'
        "echo '{\"type\":\"result\",\"is_error\":false,\"result\":\"nothing to capture\",\"num_turns\":1,"
        "\"total_cost_usd\":0.001,\"usage\":{\"input_tokens\":10,\"output_tokens\":2}}'\n")
    stub.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    (state / "pending_reflections.jsonl").write_text(json.dumps({
        "session_id": "s", "transcript_path": str(transcript), "trigger": "stop", "cwd": "/",
        "scope": "session", "harness": "claude", "ts": "2026-08-11T10:01:00Z"}) + "\n")
    env = {**os.environ, "REFLECT_STATE_DIR": str(state), "REFLECT_DRAIN_DRY_RUN": "0",
           "REFLECT_DRAIN_CLAUDE_BIN": str(stub), "REFLECT_DRAIN_DEBOUNCE_SEC": "0",
           "REFLECT_DRAIN_CASCADE": "0", "REFLECT_QUOTA_GATE": "0", "REFLECT_DRAIN_SKIP_REINDEX": "1",
           "REFLECT_QUIET_INSTALL_WARNING": "1", "REFLECT_DRAIN_MAX": "1",
           "REFLECT_DRAIN_CWD": str(tmp_path / "cwd"), "GLOBAL_LEARNINGS_PATH": str(tmp_path / "kb"),
           "REFLECT_TEST_ARGV": str(tmp_path / "argv.bin"),
           "REFLECT_TEST_TARGET_COPY": str(tmp_path / "target.copy")}
    env.pop("REFLECT_DRAIN_WRITER", None)
    (tmp_path / "cwd").mkdir()
    subprocess.run(["bash", str(_PLUGIN / "hooks" / "reflect-drain-bg.sh")], env=env, capture_output=True,
                   text=True, timeout=180, check=False)
    log = (state / "drain.log").read_text()
    assert "bounded input: raw transcript (" in log, log
    argv = (tmp_path / "argv.bin").read_bytes().split(b"\0")[:-1]
    assert b"--tools" not in argv, "the extract writer ran on the bounded view"
    assert argv[0] == b"-p" and b"Headless drain rules" in argv[1]
    handed = (tmp_path / "target.copy").read_text()
    assert FAKE_TOKEN not in handed and "<REDACTED:github_token>" in handed
    assert "    OK turns=1" in log
