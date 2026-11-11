"""Environment compatibility across installed engine and standalone plugin."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from reflect_kb import errors, identity

ROOT = Path(__file__).resolve().parents[1]
STANDALONE = ROOT / "plugin/scripts/identity_env.py"


@pytest.fixture(params=[identity, "standalone"])
def resolver(request):
    if request.param != "standalone":
        return request.param
    spec = importlib.util.spec_from_file_location("standalone_identity", STANDALONE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_environment_precedence_and_removal(resolver, monkeypatch):
    monkeypatch.setenv("REFLECT_STATE_DIR", "/legacy")
    monkeypatch.setenv("PLUGLY_REFLECT_MEMORY_STATE_DIR", "/canonical")
    assert resolver.getenv("REFLECT_STATE_DIR") == "/canonical"
    monkeypatch.setenv("PLUGLY_REFLECT_MEMORY_STATE_DIR", "")
    assert resolver.getenv("REFLECT_STATE_DIR", "/fallback") == ""
    monkeypatch.delenv("PLUGLY_REFLECT_MEMORY_STATE_DIR")
    assert resolver.getenv("REFLECT_STATE_DIR") == "/legacy"
    monkeypatch.delenv("REFLECT_STATE_DIR")
    assert resolver.getenv("REFLECT_STATE_DIR", "/fallback") == "/fallback"


def test_subprocess_aliases_are_pure_and_preserve_empty_override(resolver):
    original = {
        "REFLECT_STATE_DIR": "/legacy",
        "PLUGLY_REFLECT_MEMORY_STATE_DIR": "",
        "REFLECT_DRAIN_MAX": "3",
        "PLUGLY_REFLECT_MEMORY_PG_DSN": "test-dsn",
        "HOME": "/example",
    }
    result = resolver.subprocess_env(original)
    assert result["REFLECT_STATE_DIR"] == ""
    assert result["PLUGLY_REFLECT_MEMORY_DRAIN_MAX"] == "3"
    assert result["REFLECT_PG_DSN"] == "test-dsn"
    assert result["HOME"] == "/example"
    assert original["REFLECT_STATE_DIR"] == "/legacy"
    assert "REFLECT_PG_DSN" not in original


def test_explicit_empty_mapping_does_not_read_process(resolver, monkeypatch):
    monkeypatch.setenv("PLUGLY_REFLECT_MEMORY_STATE_DIR", "/process")
    assert resolver.getenv("REFLECT_STATE_DIR", "/default", environ={}) == "/default"
    assert resolver.subprocess_env({}) == {}


def test_unrelated_variables_keep_existing_meaning(resolver):
    assert resolver.canonical_name("GLOBAL_LEARNINGS_PATH") == "GLOBAL_LEARNINGS_PATH"
    assert resolver.getenv("GLOBAL_LEARNINGS_PATH", environ={"GLOBAL_LEARNINGS_PATH": "/kb"}) == "/kb"


def test_engine_state_uses_canonical_and_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("REFLECT_STATE_DIR", str(tmp_path / "old"))
    monkeypatch.setenv("PLUGLY_REFLECT_MEMORY_STATE_DIR", str(tmp_path / "new"))
    assert errors._state_dir() == tmp_path / "new"
    monkeypatch.delenv("PLUGLY_REFLECT_MEMORY_STATE_DIR")
    assert errors._state_dir() == tmp_path / "old"


def test_standalone_shell_exports_quote_values_without_execution(tmp_path):
    value = f"{tmp_path}/spaced ' directory; $(false)"
    env = {"PATH": os.environ["PATH"], "PLUGLY_REFLECT_MEMORY_STATE_DIR": value}
    # Shell consumes the helper's quoted exports as production hooks do.
    script = 'eval "$("$1" "$2" --shell)"; printf "%s" "$REFLECT_STATE_DIR"'
    result = subprocess.run(["sh", "-c", script, "identity-test", sys.executable, str(STANDALONE)], env=env, capture_output=True, text=True, check=True)
    assert result.stdout == value


def test_helper_copies_stay_identical():
    assert Path(identity.__file__).read_text() == STANDALONE.read_text()


def test_plugin_config_canonical_env_preserves_legacy_layering(monkeypatch):
    import reflect_config as plugin_config

    monkeypatch.setenv("REFLECT_DB_PATH", "/legacy/database.db")
    monkeypatch.setenv("PLUGLY_REFLECT_MEMORY_DB_PATH", "/canonical/database.db")
    baseline = {"storage": {"db_path": "/file/database.db", "artifacts_dir": "/unchanged"}}
    result = plugin_config._apply_env_overrides(baseline)
    assert result["storage"] == {
        "db_path": "/canonical/database.db",
        "artifacts_dir": "/unchanged",
    }
    monkeypatch.delenv("PLUGLY_REFLECT_MEMORY_DB_PATH")
    assert plugin_config._apply_env_overrides(baseline)["storage"]["db_path"] == "/legacy/database.db"


def test_plugin_event_hook_exports_both_runtime_payloads(monkeypatch):
    import reflect_events

    observed = {}
    monkeypatch.setenv("PLUGLY_REFLECT_MEMORY_EVENT", "stale-event")
    monkeypatch.setattr(reflect_events, "hook_for", lambda event: "example-command")
    monkeypatch.setattr(reflect_events.subprocess, "run", lambda *args, **kwargs: observed.update(kwargs))
    reflect_events._run_hook("learning.created", {"id": "test-note"})
    child_env = observed["env"]
    assert child_env["REFLECT_EVENT"] == child_env["PLUGLY_REFLECT_MEMORY_EVENT"] == "learning.created"
    assert child_env["REFLECT_EVENT_PAYLOAD"] == child_env["PLUGLY_REFLECT_MEMORY_EVENT_PAYLOAD"]


def test_standalone_hook_canonical_env_shares_queue_with_legacy(tmp_path):
    import json

    state = tmp_path / "state"
    payload = json.dumps({"session_id": "alias-test", "transcript_path": "/tmp/identity-alias-test.jsonl"})
    env = {key: value for key, value in os.environ.items() if not key.startswith(("REFLECT_", "PLUGLY_REFLECT_MEMORY_"))}
    hook = ROOT / "plugin/hooks/session_end_reflect.py"
    for overrides in (
        {"PLUGLY_REFLECT_MEMORY_STATE_DIR": str(state), "REFLECT_STATE_DIR": str(tmp_path / "unused")},
        {"REFLECT_STATE_DIR": str(state)},
    ):
        result = subprocess.run([sys.executable, str(hook)], input=payload, text=True, capture_output=True, env={**env, **overrides}, timeout=10, check=True)
        assert result.stdout == ""
    queued = (state / "pending_reflections.jsonl").read_text().splitlines()
    assert len(queued) == 1
    assert json.loads(queued[0])["session_id"] == "alias-test"
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize(
    ("legacy", "canonical", "expected"),
    [
        (None, None, 2.0),
        ("malformed", None, 2.0),
        ("1.25", None, 1.25),
        ("1.25", "3.5", 3.5),
        ("1.25", "malformed", 2.0),
        ("1.25", "", 2.0),
    ],
)
def test_skill_tier_threshold_defaults_and_alias_precedence(monkeypatch, legacy, canonical, expected):
    for name, value in (
        ("REFLECT_SKILL_TIER_MIN_SCORE", legacy),
        ("PLUGLY_REFLECT_MEMORY_SKILL_TIER_MIN_SCORE", canonical),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    hook_path = ROOT / "plugin/skills/recall/hooks/session_start_recall.py"
    spec = importlib.util.spec_from_file_location("identity_session_start_hook", hook_path)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    assert hook.skill_tier_min_score() == expected
