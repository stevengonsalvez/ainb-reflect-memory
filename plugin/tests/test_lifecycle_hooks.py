"""Synthetic payload tests for new Reflect lifecycle hooks."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
HOOKS = PLUGIN_ROOT / "hooks"


def _run(
    hook: str,
    payload: dict | str,
    state_dir: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "REFLECT_STATE_DIR": str(state_dir)}
    if extra_env:
        env.update(extra_env)
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOKS / hook)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=20,
    )


def _policy_file(tmp_path: Path, *rules: dict) -> Path:
    path = tmp_path / "policy.jsonl"
    path.write_text("\n".join(json.dumps(rule) for rule in rules) + "\n")
    return path


def test_subagent_start_injects_scoped_context_when_recall_available(tmp_path):
    result = _run(
        "subagent_start_recall.py",
        {"session_id": "s", "agent_type": "reviewer", "prompt": "check tests"},
        tmp_path,
        extra_env={"REFLECT_SUBAGENT_CONTEXT": "- [lrn-demo] Run focused tests first."},
    )
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    assert "Run focused tests" in body["hookSpecificOutput"]["additionalContext"]


def test_subagent_stop_queues_subagent_transcript(tmp_path):
    result = _run(
        "subagent_stop_reflect.py",
        {
            "session_id": "parent",
            "agent_id": "agent-1",
            "agent_type": "reviewer",
            "agent_transcript_path": "/tmp/agent.jsonl",
            "cwd": "/repo",
        },
        tmp_path,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {}
    entry = json.loads((tmp_path / "pending_reflections.jsonl").read_text().strip())
    assert entry["trigger"] == "subagent_stop"
    assert entry["scope"] == "subagent"
    assert entry["agent_type"] == "reviewer"


def test_subagent_stop_allows_multiple_transcripts_for_same_parent(tmp_path):
    for idx in (1, 2):
        result = _run(
            "subagent_stop_reflect.py",
            {
                "session_id": "parent",
                "agent_id": f"agent-{idx}",
                "agent_type": "reviewer",
                "agent_transcript_path": f"/tmp/agent-{idx}.jsonl",
                "cwd": "/repo",
            },
            tmp_path,
        )
        assert result.returncode == 0

    lines = (tmp_path / "pending_reflections.jsonl").read_text().splitlines()
    assert len(lines) == 2
    assert {json.loads(line)["transcript_path"] for line in lines} == {
        "/tmp/agent-1.jsonl",
        "/tmp/agent-2.jsonl",
    }


def test_pretooluse_emits_context_for_matching_policy(tmp_path):
    policy = _policy_file(
        tmp_path,
        {
            "scope": "pretool",
            "tool": "Bash",
            "pattern": "npm test",
            "context": "Use pnpm test in this repository.",
            "confidence": "HIGH",
        },
    )
    result = _run(
        "pretooluse_context.py",
        {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "npm test"}},
        tmp_path,
        extra_env={"REFLECT_POLICY_FILE": str(policy)},
    )
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert "pnpm test" in body["hookSpecificOutput"]["additionalContext"]


def test_pretooluse_denies_exact_high_confidence_policy(tmp_path):
    policy = _policy_file(
        tmp_path,
        {
            "scope": "pretool",
            "tool": "Bash",
            "pattern": "rm -rf",
            "decision": "deny",
            "message": "No destructive deletes.",
            "confidence": "HIGH",
        },
    )
    result = _run(
        "pretooluse_context.py",
        {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "rm -rf build"}},
        tmp_path,
        extra_env={"REFLECT_POLICY_FILE": str(policy)},
    )
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "destructive" in body["hookSpecificOutput"]["permissionDecisionReason"]


def test_permission_request_arms_watcher_and_denies_policy(tmp_path):
    policy = _policy_file(
        tmp_path,
        {
            "scope": "permission",
            "tool": "Bash",
            "pattern": "curl",
            "decision": "deny",
            "message": "No network calls here.",
            "confidence": "HIGH",
        },
    )
    result = _run(
        "permission_request_reflect.py",
        {
            "session_id": "perm-1",
            "tool_name": "Bash",
            "tool_input": {"command": "curl https://example.com", "description": "network"},
        },
        tmp_path,
        extra_env={"REFLECT_POLICY_FILE": str(policy)},
    )
    assert result.returncode == 0
    body = json.loads(result.stdout)
    assert body["hookSpecificOutput"]["decision"]["behavior"] == "deny"
    assert (tmp_path / "permission-armed" / "perm-1.json").exists()


def test_posttoolusefailure_arms_mini_learning(tmp_path):
    result = _run(
        "posttoolusefailure_minilearning.py",
        {
            "sessionId": "fail-1",
            "toolName": "Bash",
            "toolInput": {"command": "pytest"},
            "toolResult": {"stderr": "failed"},
        },
        tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    armed = json.loads((tmp_path / "armed" / "fail-1.json").read_text())
    assert armed["tool"] == "Bash"
    assert armed["reason"] == "failure"


def test_session_end_queues_final_transcript(tmp_path):
    result = _run(
        "session_end_reflect.py",
        {"session_id": "end-1", "transcript_path": "/tmp/session.jsonl"},
        tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    entry = json.loads((tmp_path / "pending_reflections.jsonl").read_text().strip())
    assert entry["trigger"] == "session_end"
    assert entry["scope"] == "session"


def test_postcompact_bookkeeping_never_queues_or_injects(tmp_path):
    injected = tmp_path / "session-injected" / "compact-1.json"
    injected.parent.mkdir(parents=True)
    injected.write_text("{}")
    result = _run(
        "postcompact_bookkeeping.py",
        {"session_id": "compact-1", "trigger": "auto"},
        tmp_path,
        extra_env={"REFLECT_POSTCOMPACT_RESET_DEDUPE": "1"},
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert not injected.exists()
    assert not (tmp_path / "pending_reflections.jsonl").exists()


def test_error_occurred_records_breadcrumb(tmp_path):
    result = _run(
        "error_occurred_reflect.py",
        {"sessionId": "err-1", "errorType": "tool", "message": "boom"},
        tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    event = json.loads((tmp_path / "errors.jsonl").read_text().strip())
    assert event["session_id"] == "err-1"
    assert event["kind"] == "tool"


def test_notification_accepts_copilot_camel_case_session_id(tmp_path):
    result = _run(
        "notification_reflect.py",
        {
            "sessionId": "copilot-1",
            "notificationType": "permission_prompt",
            "message": "Copilot needs your permission to use Bash",
        },
        tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert (tmp_path / "permission-armed" / "copilot-1.json").exists()


def test_new_lifecycle_hooks_survive_malformed_json(tmp_path):
    json_stdout_hooks = {
        "subagent_start_recall.py",
        "subagent_stop_reflect.py",
    }
    for hook in (
        "subagent_start_recall.py",
        "subagent_stop_reflect.py",
        "pretooluse_context.py",
        "permission_request_reflect.py",
        "posttoolusefailure_minilearning.py",
        "session_end_reflect.py",
        "postcompact_bookkeeping.py",
        "error_occurred_reflect.py",
    ):
        result = _run(hook, "not json at all", tmp_path / hook)
        assert result.returncode == 0, hook
        if hook in json_stdout_hooks:
            json.loads(result.stdout or "{}")
        else:
            assert result.stdout == "", hook
