"""Shared helpers for Reflect lifecycle hooks.

The hook scripts are deliberately small and silent-fail. This module holds
only dependency-free helpers used by the newer lifecycle hooks so behavior
stays consistent across Claude/Codex/Copilot payload shapes.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Iterable


_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLUGIN_ROOT / "scripts"))

try:
    from hook_input import (  # type: ignore
        get_agent_id,
        get_agent_transcript_path,
        get_agent_type,
        get_cwd,
        get_parent_session_id,
        get_prompt,
        get_session_id,
        get_tool_input,
        get_tool_name,
        get_tool_response,
        get_transcript_path,
        get_turn_id,
    )
except ImportError:  # pragma: no cover - deployed-layout fallback
    def _first_present(data: dict, keys: tuple[str, ...], default: Any) -> Any:
        if not isinstance(data, dict):
            return default
        for key in keys:
            if key in data:
                return data[key]
        return default

    def get_session_id(data, default=""):
        return _first_present(data, ("session_id", "sessionId"), default)

    def get_transcript_path(data, default=""):
        return _first_present(data, ("transcript_path", "transcriptPath"), default)

    def get_cwd(data, default=""):
        return _first_present(data, ("cwd",), default)

    def get_turn_id(data, default=""):
        return _first_present(data, ("turn_id", "turnId"), default)

    def get_prompt(data, default=""):
        return _first_present(data, ("prompt", "userPrompt", "user_prompt"), default)

    def get_tool_name(data, default=""):
        return _first_present(data, ("tool", "tool_name", "toolName"), default)

    def get_tool_input(data, default=None):
        if default is None:
            default = {}
        return _first_present(data, ("tool_input", "toolInput", "input"), default)

    def get_tool_response(data, default=None):
        if default is None:
            default = {}
        return _first_present(data, ("tool_response", "response", "toolResult"), default)

    def get_agent_id(data, default=""):
        return _first_present(data, ("agent_id", "agentId", "subagent_id", "subagentId"), default)

    def get_agent_type(data, default=""):
        return _first_present(data, ("agent_type", "agentType", "subagent_type", "subagentType"), default)

    def get_agent_transcript_path(data, default=""):
        return _first_present(data, ("agent_transcript_path", "agentTranscriptPath"), default)

    def get_parent_session_id(data, default=""):
        return _first_present(data, ("parent_session_id", "parentSessionId"), default)

try:
    from silent_fail import forensics_log, scrub_secrets, write_last_event  # type: ignore
except ImportError:  # pragma: no cover - broken install fallback
    def forensics_log(*_args, **_kwargs):
        pass

    def scrub_secrets(text: str) -> str:
        return text

    def write_last_event(**_kwargs):
        pass


def state_dir() -> Path:
    return Path(os.environ.get("REFLECT_STATE_DIR", str(Path.home() / ".reflect")))


def read_stdin_json() -> dict[str, Any]:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def write_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")


def queue_file() -> Path:
    return state_dir() / "pending_reflections.jsonl"


def transcript_already_queued(path: Path, *, session_id: str = "", transcript_path: str = "") -> bool:
    if not path.exists():
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if session_id and entry.get("session_id") == session_id:
                    return True
                if transcript_path and entry.get("transcript_path") == transcript_path:
                    return True
    except OSError:
        return False
    return False


def enqueue_reflection(
    *,
    trigger: str,
    data: dict[str, Any],
    transcript_path: str,
    scope: str = "session",
    extra: dict[str, Any] | None = None,
    dedupe_session: bool = True,
) -> bool:
    if not transcript_path:
        return False
    qf = queue_file()
    session_id = str(get_session_id(data) or get_parent_session_id(data) or "").strip()
    dedupe_session_id = session_id if dedupe_session else ""
    if transcript_already_queued(qf, session_id=dedupe_session_id, transcript_path=transcript_path):
        return False
    try:
        from reflect_gate import should_enqueue  # type: ignore

        ok, _reason = should_enqueue(transcript_path, qf, state_dir() / "drain-cost.jsonl")
        if not ok:
            return False
    except Exception:
        pass

    entry: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id or "unknown",
        "transcript_path": transcript_path,
        "trigger": trigger,
        "cwd": get_cwd(data, os.getcwd()),
        "harness": os.environ.get("REFLECT_HARNESS", "unknown"),
        "scope": scope,
    }
    if extra:
        entry.update({k: v for k, v in extra.items() if v not in ("", None)})
    write_jsonl(qf, entry)
    return True


def emit_additional_context(event_name: str, additional_context: str) -> None:
    if os.environ.get("REFLECT_HARNESS") == "copilot":
        print(json.dumps({"additionalContext": additional_context}))
    else:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": event_name,
                        "additionalContext": additional_context,
                    }
                }
            )
        )


def emit_pretool_deny(message: str) -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": message,
                }
            }
        )
    )


def emit_permission_decision(behavior: str, message: str = "") -> None:
    decision: dict[str, str] = {"behavior": behavior}
    if message:
        decision["message"] = message
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PermissionRequest",
                    "decision": decision,
                }
            }
        )
    )


def _policy_paths() -> Iterable[Path]:
    explicit = os.environ.get("REFLECT_POLICY_FILE")
    if explicit:
        yield Path(explicit).expanduser()
    yield state_dir() / "policy-rules.jsonl"
    yield state_dir() / "permission-policy.jsonl"


def load_policy_rules() -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = []
    for path in _policy_paths():
        if not path.exists():
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        rule = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rule, dict):
                        rules.append(rule)
        except OSError:
            continue
    return rules


def serialize_tool_input(tool_input: Any) -> str:
    if isinstance(tool_input, dict):
        command = tool_input.get("command")
        if isinstance(command, str):
            return command
    try:
        return json.dumps(tool_input, sort_keys=True)
    except TypeError:
        return str(tool_input)


def matching_policy_rules(
    data: dict[str, Any],
    *,
    scope: str,
) -> list[dict[str, Any]]:
    tool = str(get_tool_name(data) or "").lower()
    text = serialize_tool_input(get_tool_input(data)).lower()
    matches: list[dict[str, Any]] = []
    for rule in load_policy_rules():
        rule_scope = str(rule.get("scope", "any")).lower()
        if rule_scope not in ("any", scope.lower()):
            continue
        rule_tool = str(rule.get("tool", "*")).lower()
        if rule_tool not in ("*", tool):
            continue
        pattern = str(rule.get("pattern", "")).lower()
        if pattern and pattern not in text:
            continue
        matches.append(rule)
    return matches


def high_confidence(rule: dict[str, Any]) -> bool:
    return str(rule.get("confidence", "HIGH")).upper() == "HIGH"
