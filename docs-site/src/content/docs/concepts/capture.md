---
title: Capture
description: What the lifecycle hooks record during a session, which events write notes directly, which only queue a transcript, and where everything lands on disk.
sidebar:
  order: 2
---

Capture is what hooks do while you work. Hooks cannot run a model, so they do two cheap things: write a small note when an event is unambiguous, and queue the transcript path for the [drain](/ainb-reflect-memory/concepts/drain/) when it might hold something worth a model call.

```
 event ──▶ hook ──┬─▶ deterministic signal ──▶ note in ~/.learnings/documents/   (direct write)
                  ├─▶ arm a watcher ──▶ next prompt confirms ──▶ note             (two-phase)
                  └─▶ enqueue gate ──▶ ~/.reflect/pending_reflections.jsonl       (for the drain)
```

Every hook is silent-fail: it catches everything, writes `~/.reflect/last-event.json` plus a forensics log, and exits 0. Capture hooks print nothing to stdout, except where a harness expects a reply (`SubagentStop` prints `{}`) or a policy rule decides (`PreToolUse`, `PermissionRequest`).

## What gets captured

| Signal | Hook | Result | Confidence | Source tag |
|---|---|---|---|---|
| Tests go from N failures to 0 (pytest, jest, cargo, go) | `PostToolUse` | Note `lrn-test-fix-<ms>-<sid8>.md` written immediately | high | `test-outcome` |
| Tests go from 0 to N failures | `PostToolUse` | Arms the watcher (reason `test-regression`) | none yet | n/a |
| Same tool call 3 times in a row, or A,B,A,B oscillation | `PostToolUse` | Arms the watcher (reason `loop`) | none yet | n/a |
| Tool call failed (non-zero exit, `is_error`, error field, Bash stderr with empty stdout) | `PostToolUse`, `PostToolUseFailure` | Arms the watcher (reason `failure`) | none yet | n/a |
| Next prompt reads as a correction ("use X instead", "no, don't use", "should have", "instead of X, use") | `UserPromptSubmit` | Note `lrn-mini-<ts>-<sid8>.md`, then clears the watcher | low | `posttooluse-minilearning` |
| A `TodoWrite` item moves to `completed` | `PostToolUse` | Note `lrn-todo-done-<ms>-<sid8>.md` with duration and files touched | medium | `todo-completion` |
| Reply to a permission prompt ("yes always", "no never", "only for X", plain yes/no) | `Notification` or `PermissionRequest` arms, `UserPromptSubmit` writes | Note `lrn-perm-<ts>-<sid8>.md` | high for always, never, scoped. Medium for one-off allow or deny | `permission-pattern` |
| Compaction, turn end, session end, subagent end, idle session | `PreCompact`, `Stop`, `SessionEnd`, `SubagentStop`, idle sweep | Transcript path queued, if the gate passes | decided by the drain | n/a |

Direct writes are cheap and narrow on purpose. They record what happened, with the failing command or the test runner as evidence. The drain is what extracts durable rules.

### Two-phase capture

A tool failure alone is not a lesson. The `PostToolUse` hook only writes `~/.reflect/armed/<session_id>.json` (tool, truncated input and response, timestamp, reason). The next `UserPromptSubmit` hook reads it. If the prompt matches a correction pattern, it writes the low-confidence note and deletes the file. If not, the watcher lingers and is dropped once it is older than 10 minutes. The permission watcher at `~/.reflect/permission-armed/<session_id>.json` is single-shot: a prompt that is not a permission reply clears it.

Test and loop state is per session under `$REFLECT_STATE_DIR` (`test-state/`, `loops/`, `todo-state/`). It expires after 6 hours and is swept by the `Stop` hook.

When a session has several failing test runs and then goes green, the session's earlier hook-written notes are promoted one confidence tier and marked `validated: true`. Test-fix notes are already high and are skipped.

## Queue producers

| Hook | Script | Trigger value | Notes |
|---|---|---|---|
| `PreCompact` | `precompact_reflect.py --auto --verbose` | the harness `trigger` (`auto`, `manual`) | Pure side effect, no stdout. `REFLECT_AUTO_REFLECT=0` disables the enqueue |
| `Stop` | `stop_reflect.py` | `stop` | Skips if the session id is already queued (long sessions enqueue at `PreCompact` first) |
| `SessionEnd` | `session_end_reflect.py` | `session_end` | Claude Code and Copilot only |
| `SubagentStop` | `subagent_stop_reflect.py` | `subagent_stop` | Queues the subagent transcript with `agent_id`, `agent_type`, `parent_session_id`; does not dedupe by session |
| Idle sweep | `hooks/idle_reflect.sh` via `reflect_gate.py --idle-sweep` | `idle` | A timer, not a hook. See below |

A queue entry is one JSON line in `~/.reflect/pending_reflections.jsonl`:

```json
{"ts": "2026-03-04T09:12:00Z", "session_id": "9f1c...", "transcript_path": "/path/to/session.jsonl",
 "trigger": "stop", "cwd": "/path/to/repo", "harness": "claude", "scope": "session"}
```

`harness` and `scope` come from the shared helper used by `SessionEnd` and `SubagentStop`. The `PreCompact` and `Stop` entries carry the first five fields.

`PostCompact` does bookkeeping only: no recall, no queue, no drain.

### The enqueue gate

Before any line is appended, `reflect_gate.should_enqueue` runs a $0 regex check over the transcript dialogue. Tool output is ignored because it is full of "error" and "fixed" noise.

| Verdict | Reason code |
|---|---|
| Skip | `dup-already-queued`: the path is already in the queue |
| Skip | `dup-already-processed`: the cost ledger has a terminal outcome for this path |
| Skip | `reflect-on-reflect`: the transcript is itself a `/reflect` run (machine markers in the opening records) |
| Skip | `no-signal`: no pattern matched anywhere in the first 600,000 characters of dialogue |
| Enqueue | `has-signal`: any signal, including LOW |

The gate fails open. A read or parse error enqueues, because dropping a real lesson costs more than one wasted model call, and the drain's own caps bound that cost.

### Signal patterns

`signal_detector.py` classifies lines of dialogue by regex. The same detector drives the drain's slicer.

| Confidence | Examples of what matches | Kinds |
|---|---|---|
| HIGH | never, don't, wrong, shouldn't, always, must, "I already told you", root cause was, fixed by, decided to, turns out | correction, prohibition, requirement, frustration, explicit rule, root cause, fix confirmed, decision, breakthrough |
| MEDIUM | perfect, exactly, that's right, keep doing, "spent 3 hours", undocumented, make sure to | approval, positive feedback, continuation, time investment, docs vs reality, env gotcha |
| LOW | maybe, perhaps, seems like, interesting, discovered, switched to | suggestion, observation, tentative, implicit success, tool discovery |

Each signal also gets a category: Code Style, Architecture, Process, Domain, Tools, Security, New Skill, or Unknown. Patterns can be extended per mode (`REFLECT_MODE`, `plugin/skills/reflect/references/modes/`).

### Idle sweep

Sessions that go quiet never fire `Stop` or `PreCompact`. `idle_reflect.sh` walks transcript mtimes under `~/.claude/projects` and queues sessions quiet for at least 600 seconds but younger than 24 hours, up to 5 per sweep. Their entries carry `trigger: idle`, and the drain tells the writer to tag every learning `speculative` and cap confidence at MEDIUM, since the session may resume. Recall down-ranks that tag.

The sweep ships as a launchd template (`plugin/launchd/com.reflect.idle.plist`), so it is a macOS convenience. Tunables: `REFLECT_IDLE_THRESHOLD_SEC`, `REFLECT_IDLE_MAX_AGE_SEC`, `REFLECT_IDLE_MAX_PER_SWEEP`, `REFLECT_IDLE_PROJECTS_ROOT`, `REFLECT_IDLE_DISABLED=1`.

## Other capture paths

| Path | What it records | Opt in |
|---|---|---|
| Git post-commit | SHA, branch, subject, changed files, linked to the active session, appended to `$REFLECT_STATE_DIR/commits.jsonl`. A merge with conflict markers is flagged. A `git revert` demotes the reverted commit's session learnings in the ledger | Per repo: `plugin/hooks/install_post_commit.sh [REPO_DIR]` (chains an existing hook, `--uninstall` reverses) |
| Memory slots | At `Stop`, a regex pass over the transcript updates `pending_items` (TODO lines, unfinished todos), `session_patterns` (command and error counts), and `project_context` (files touched) | `REFLECT_SLOTS=1` |
| Policy rules | `PreToolUse` and `PermissionRequest` read `REFLECT_POLICY_FILE`, `~/.reflect/policy-rules.jsonl`, and `~/.reflect/permission-policy.jsonl`. A matching high-confidence `deny` rule blocks the call. Other matches inject up to three context lines | Create the rules file |
| Error breadcrumbs | Copilot `errorOccurred` appends to `~/.reflect/errors.jsonl` | Copilot only |
| `/reflect:ingest` | Sweeps existing memory files from other tools into the KB with an approval table | Manual skill |

## Privacy

Two filters run before anything is written to a note or handed to a model. Both are best effort, not a security boundary.

- `strip_private` removes `<private>...</private>` spans and harness wrapper tags such as `system-reminder`, leaving a `[private content removed]` marker. An unclosed tag strips to the end of the text.
- `scrub_secrets` masks `Authorization` and `X-*-Key` headers, `Bearer` tokens, `token=`, `api_key=`, `password=` pairs, and prefixes for OpenAI, Anthropic, GitHub, Slack, and AWS keys as `***REDACTED***`.

## Where captures land

| Path | Written by | Contents |
|---|---|---|
| `~/.learnings/documents/lrn-*.md` | Direct-write hooks | Notes. `REFLECT_LEARNINGS_DIR` overrides the directory. They are picked up by the next `reflect reindex` |
| `~/.reflect/pending_reflections.jsonl` | Queue producers | Pending transcripts |
| `~/.reflect/armed/`, `permission-armed/` | Arming hooks | One JSON file per session |
| `~/.reflect/test-state/`, `loops/`, `todo-state/` | `PostToolUse` | Per-session state, 6 hour TTL |
| `~/.reflect/last-event.json`, `~/.reflect/logs/` | All hooks | Last error breadcrumb and forensics log |
| `$REFLECT_STATE_DIR/commits.jsonl` | Post-commit hook | One line per commit |

Notes written by hooks carry only `id`, `confidence`, `source`, `session_id`, and a capture time in frontmatter. They have no entity sidecar and no `title`, `category`, or `key_insight`, so `reflect add` would reject them. They stay in `documents/` and `reflect reindex` indexes any note that has frontmatter, with heuristic entity extraction. Field-level detail is in [KB format](/ainb-reflect-memory/reference/kb-format/).

## Turning capture off

| Control | Effect |
|---|---|
| `REFLECT_DISABLED=1` | Hard no-op for the drain, the idle sweep, and the maintenance watcher. Hooks still run |
| `REFLECT_AUTO_REFLECT=0` | `PreCompact` stops enqueueing |
| `REFLECT_IDLE_DISABLED=1` | Idle sweep no-op |
| Remove or disable the plugin | Removes all hooks |

Events, matchers, and payload fields for each hook are in the [Hooks reference](/ainb-reflect-memory/reference/hooks/).
