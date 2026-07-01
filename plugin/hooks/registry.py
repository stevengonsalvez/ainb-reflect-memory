"""Reflect hook registry.

This is the small source of truth for the Reflect-managed lifecycle hooks.
Manifest tests use it to prevent drift between Claude, Codex, Copilot, and the
adapter-generated hook files.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class HookSpec:
    canonical: str
    behavior: str
    script: str | None
    claude: str | None
    codex: str | None
    copilot: str | None
    drains: bool = False
    queues: bool = False
    lookup: bool = False


HOOKS: tuple[HookSpec, ...] = (
    HookSpec(
        "SessionStart.recall",
        "startup recall injection",
        "skills/recall/hooks/session_start_recall.py",
        "SessionStart",
        "SessionStart",
        "sessionStart",
        lookup=True,
    ),
    HookSpec(
        "SessionStart.drain",
        "detached queue drain",
        "hooks/reflect-drain-bg.sh",
        "SessionStart",
        "SessionStart",
        "sessionStart",
        drains=True,
    ),
    HookSpec(
        "UserPromptSubmit",
        "prompt recall and watcher completion",
        "skills/recall/hooks/user_prompt_submit_recall.py",
        "UserPromptSubmit",
        "UserPromptSubmit",
        "userPromptSubmitted",
        lookup=True,
    ),
    HookSpec(
        "Notification",
        "permission watcher arm",
        "hooks/notification_reflect.py",
        "Notification",
        None,
        "notification",
    ),
    HookSpec(
        "PreToolUse",
        "narrow policy context",
        "hooks/pretooluse_context.py",
        "PreToolUse",
        "PreToolUse",
        "preToolUse",
        lookup=True,
    ),
    HookSpec(
        "PermissionRequest",
        "permission policy lookup and watcher arm",
        "hooks/permission_request_reflect.py",
        "PermissionRequest",
        "PermissionRequest",
        "permissionRequest",
        lookup=True,
    ),
    HookSpec(
        "PostToolUse",
        "mini-learning watcher arm",
        "hooks/posttooluse_minilearning.py",
        "PostToolUse",
        "PostToolUse",
        "postToolUse",
    ),
    HookSpec(
        "PostToolUseFailure",
        "explicit failure watcher arm",
        "hooks/posttoolusefailure_minilearning.py",
        "PostToolUseFailure",
        None,
        "postToolUseFailure",
    ),
    HookSpec(
        "PreCompact",
        "silent transcript queue producer",
        "hooks/precompact_reflect.py",
        "PreCompact",
        "PreCompact",
        "preCompact",
        queues=True,
    ),
    HookSpec(
        "PostCompact",
        "bookkeeping only",
        "hooks/postcompact_bookkeeping.py",
        "PostCompact",
        "PostCompact",
        None,
    ),
    HookSpec(
        "SubagentStart",
        "subagent-scoped recall injection",
        "hooks/subagent_start_recall.py",
        "SubagentStart",
        "SubagentStart",
        "subagentStart",
        lookup=True,
    ),
    HookSpec(
        "SubagentStop",
        "subagent transcript queue producer",
        "hooks/subagent_stop_reflect.py",
        "SubagentStop",
        "SubagentStop",
        "subagentStop",
        queues=True,
    ),
    HookSpec(
        "Stop",
        "slot update and queue fallback",
        "hooks/stop_reflect.py",
        "Stop",
        "Stop",
        "agentStop",
        queues=True,
    ),
    HookSpec(
        "SessionEnd",
        "final cleanup and queue producer",
        "hooks/session_end_reflect.py",
        "SessionEnd",
        None,
        "sessionEnd",
        queues=True,
    ),
    HookSpec(
        "errorOccurred",
        "error breadcrumb",
        "hooks/error_occurred_reflect.py",
        None,
        None,
        "errorOccurred",
    ),
)


def expected_events(harness: str) -> set[str]:
    return {
        event
        for spec in HOOKS
        for event in [getattr(spec, harness)]
        if event is not None
    }


def expected_scripts(harness: str) -> set[str]:
    return {
        spec.script
        for spec in HOOKS
        if getattr(spec, harness) is not None and spec.script is not None
    }


def specs_for_event(harness: str, event: str) -> tuple[HookSpec, ...]:
    return tuple(spec for spec in HOOKS if getattr(spec, harness) == event)
