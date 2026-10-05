---
title: Hooks reference
description: Every lifecycle hook the reflect plugin registers for Claude Code, Codex and Copilot, what each script does, timeouts, output shapes, and the always-exit-0 contract, verified against plugin/hooks.
sidebar:
  order: 1
---

The plugin wires reflect into a harness through command hooks. Each hook is a small script that either **injects recall context** before the model acts, **arms a watcher** for a cheap no-LLM capture, or **queues a transcript** for the background drain. No hook runs an LLM. The only component that spends model tokens is the detached drain (`reflect-drain-bg.sh`), launched from `SessionStart`.

Source of truth for the event map is [`plugin/hooks/registry.py`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/plugin/hooks/registry.py); a parity test (`plugin/tests/test_hook_registry_parity.py`) keeps the three manifests in sync with it. For the concepts behind the roles below see [Capture](/ainb-reflect-memory/concepts/capture/), [Drain](/ainb-reflect-memory/concepts/drain/) and [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/).

## Roles

```text
 inject            arm                 queue                    drain
 ------            ---                 -----                    -----
 SessionStart      PostToolUse         PreCompact               SessionStart
 UserPromptSubmit  PostToolUseFailure  Stop                     (reflect-drain-bg.sh,
 SubagentStart     Notification        SessionEnd                detached, only
 PreToolUse*       PermissionRequest*  SubagentStop              component that
                   errorOccurred                                 runs claude -p)
   |                  |                   |                        |
   v                  v                   v                        v
 additionalContext  armed/<sid>.json    pending_reflections.jsonl  learnings .md
 (stdout JSON)      (next prompt        (deduped, $0 gate)         + reindex
                     reads it)
 * only acts when a matching policy rule exists (see PreToolUse below)
```

| Role | Hooks | Contract |
|---|---|---|
| Recall injectors | `SessionStart`, `UserPromptSubmit`, `SubagentStart`, `PreToolUse` | Emit `additionalContext` before the model or tool acts. |
| Signal armers | `PostToolUse`, `PostToolUseFailure`, `Notification`, `PermissionRequest`, `errorOccurred` | Write small breadcrumb files. The next `UserPromptSubmit` consumes them. Never run `/reflect`. |
| Queue producers | `PreCompact`, `Stop`, `SessionEnd`, `SubagentStop` | Run the `$0` enqueue gate, then append deduped work to `pending_reflections.jsonl`. Never run `/reflect`. |
| Drain consumer | `SessionStart` (`reflect-drain-bg.sh`) | Sole consumer of the queue. Detached, always exits 0. |
| Bookkeeping | `PostCompact` | Log only. No injection, no queue append, no drain. |

## Event matrix

Which event each harness fires for each script. A dash means that harness does not wire the hook.

| Script | Claude Code | Codex | Copilot |
|---|---|---|---|
| `skills/recall/hooks/session_start_recall.py` | `SessionStart` | `SessionStart` | `sessionStart` |
| `hooks/reflect-drain-bg.sh` | `SessionStart` | `SessionStart` | `sessionStart` |
| `skills/recall/hooks/user_prompt_submit_recall.py` | `UserPromptSubmit` | `UserPromptSubmit` | `userPromptSubmitted` |
| `hooks/notification_reflect.py` | `Notification` | - | `notification` |
| `hooks/pretooluse_context.py` | `PreToolUse` | `PreToolUse` | `preToolUse` |
| `hooks/permission_request_reflect.py` | `PermissionRequest` | `PermissionRequest` | `permissionRequest` |
| `hooks/posttooluse_minilearning.py` | `PostToolUse` | `PostToolUse` | `postToolUse` |
| `hooks/posttoolusefailure_minilearning.py` | `PostToolUseFailure` | - | `postToolUseFailure` |
| `hooks/precompact_reflect.py --auto --verbose` | `PreCompact` | `PreCompact` | `preCompact` |
| `hooks/postcompact_bookkeeping.py` | `PostCompact` | `PostCompact` | - |
| `hooks/subagent_start_recall.py` | `SubagentStart` | `SubagentStart` | `subagentStart` |
| `hooks/subagent_stop_reflect.py` | `SubagentStop` | `SubagentStop` | `subagentStop` |
| `hooks/stop_reflect.py` | `Stop` | `Stop` | `agentStop` |
| `hooks/session_end_reflect.py` | `SessionEnd` | - | `sessionEnd` |
| `hooks/error_occurred_reflect.py` | - | - | `errorOccurred` |

Totals: Claude Code 13 events (14 commands), Codex 10 events (11 commands), Copilot 13 events (14 commands). `SessionStart` carries two commands everywhere (recall, then the drain launcher).

### Where each manifest lives

| Harness | Manifest | Path variable | Notes |
|---|---|---|---|
| Claude Code | `plugin/.claude-plugin/plugin.json` (`hooks` block) | `${CLAUDE_PLUGIN_ROOT}` | Auto-wired by `/plugin install`. Every entry is `matcher: ""` and runs `uv run --script <script>`. |
| Codex | `plugin/codex-hooks.json`, referenced by `plugin/.codex-plugin/plugin.json` | `${PLUGIN_ROOT}` | The Codex adapter also merges the same 10 events into `~/.codex/hooks.json`, pointing at `~/.codex/skills/...`. |
| Copilot | `plugin/copilot-hooks.json` (adapter writes `~/.copilot/hooks/reflect.json`) | `${PLUGIN_ROOT}` | Flat `[{type, command}]` entries with `version: 1`, camelCase event names, and `REFLECT_HARNESS=copilot` prefixed on every `uv run` command. |
| Hermes | none | n/a | No hook autowiring. The adapter deploys two shim scripts; the fleet-lambda side decides when to call them. See [Hermes shims](#hermes-shims). |

`plugin/plugin.json` (top level) is a metadata-only manifest with no `hooks` key. The `claude` adapter (`plugin/adapters/claude/claude_adapter.py`) only merges a single `SessionStart` recall entry into `~/.claude/settings.json`, and only when the plugin runtime does not already own reflect (it checks `~/.claude/plugins/installed_plugins.json`), to avoid firing a second copy.

## Per-hook reference

### Recall injectors

| Hook | What it does | Output | Internal bound |
|---|---|---|---|
| `session_start_recall.py` | Builds a query from cwd, git branch and recent commits, then injects the top 3 learnings (any confidence, reranked, max 1500 chars) plus a one-line token-economics footer. Skips entirely when cwd is `$HOME`. Applies an out-of-domain gate (min query-term overlap `0.2`). Optional tiers: memory slots (`REFLECT_SLOTS`), CONVENTIONS pointer and skills tier (`REFLECT_TIERED_INJECT`). Pins `RECALL_BRANCH` to the current worktree branch before recalling. | `additionalContext` | recall subprocess 30 s (`REFLECT_RECALL_TIMEOUT`) |
| `user_prompt_submit_recall.py` | Uses the prompt itself as the query. Skips prompts under 12 chars. Injects up to 3 learnings (max 1500 chars) not already injected this session (dedupe set in `session-injected/<sid>.json`). Before recalling it also **consumes armed watchers**: a correction-shaped prompt after an armed tool failure writes a low-confidence mini-learning; a permission-decision reply (`yes always`, `no never`, `only for X`, plain approve or deny) after an armed permission prompt writes a `source: permission-pattern` learning. Armed state older than 10 minutes is ignored. | `additionalContext` | recall subprocess 30 s (`REFLECT_RECALL_TIMEOUT`) |
| `subagent_start_recall.py` | Query is `subagent <type> \| cwd <cwd> \| agent_id \| <task>`. Injects up to 3 learnings (max 1500 chars) headed `Prior learnings for subagent`. | `additionalContext` | recall subprocess 5 s (`REFLECT_SUBAGENT_RECALL_TIMEOUT`) |
| `pretooluse_context.py` | **Not** broad recall. Reads deterministic rules from the [policy file](#policy-rules-file). A matching HIGH-confidence `deny` rule blocks the tool call; matching `context`/`allow` rules inject up to 3 short lines (`Reflect tool context:`). With no rules (the default) it prints nothing. | `permissionDecision: deny` or `additionalContext` or empty | none (local file read) |

Both recall hooks set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` (via `setdefault`) so a cold model load never makes a network round trip that blows the timeout.

### Signal armers

| Hook | Arms when | Writes | Output |
|---|---|---|---|
| `posttooluse_minilearning.py` | The tool failed (non-zero `exit_code`/`returncode`, `is_error`, non-empty `error`, or Bash with empty stdout and non-empty stderr), **or** a loop of identical calls is detected, **or** a Bash test run regresses from 0 to N failures. Also records per-session loop, test-outcome and TodoWrite state; a confirmed fix (failures N to 0) and each TodoWrite completion write a learning directly. | `armed/<sid>.json` (reason `failure`, `loop` or `test-regression`) | empty |
| `posttoolusefailure_minilearning.py` | Always, on the explicit failure event. | `armed/<sid>.json` (reason `failure`) | empty |
| `notification_reflect.py` | The notification is a permission prompt (`notification_type == permission_prompt`, or a message matching the known phrasings, e.g. `needs your permission to use Bash`). Idle or input-needed notifications do not arm. | `permission-armed/<sid>.json` | empty |
| `permission_request_reflect.py` | Always arms the permission watcher; then, if a matching HIGH-confidence `allow` or `deny` policy rule exists, emits that decision. Otherwise prints nothing and the normal approval flow proceeds. | `permission-armed/<sid>.json` | empty or `decision` |
| `error_occurred_reflect.py` | Copilot `errorOccurred` event. | appends to `errors.jsonl` | empty |

All armed payloads are truncated to 500 chars and pass through secret scrubbing (`Authorization`/`X-Api-Key` headers, `token=`/`api_key=`/`password=` values, `sk-`, `sk-ant-`, `ghp_`, `ghs_`, `xox*`, `AKIA*` shapes). `posttooluse_minilearning.py` also strips `<private>` spans first.

### Queue producers

All four append one JSON line to `$REFLECT_STATE_DIR/pending_reflections.jsonl` with `ts`, `session_id`, `transcript_path`, `trigger`, `cwd`. They first call the `$0` gate (`plugin/scripts/reflect_gate.py`), which skips reflect-on-reflect transcripts, transcripts with no correction, approval or knowledge signal, and anything already queued or processed. A gate error fails open (the entry is queued).

| Hook | `trigger` | Behavior |
|---|---|---|
| `precompact_reflect.py` | `auto` or `manual` (from the event) | Modes: `--auto` (gate and enqueue), `--remind` (no-op on disk), `--log-only`. The plugin wires `--auto --verbose`. Honors `REFLECT_AUTO_REFLECT=0` (no-op). Pure side-effect hook: emits no stdout, because the transcript is about to be compacted and Codex's schema rejects PreCompact output. |
| `stop_reflect.py` | `stop` | Fires every agent turn. Skips if the `session_id` is already queued (PreCompact got there first). Also sweeps stale loop, test-outcome and todo state files (6 h TTL). With `REFLECT_SLOTS` on, runs a deterministic slot-reflect pass (pending TODOs, tool counts, touched files into memory slots), even when the gate skips the transcript. |
| `session_end_reflect.py` | `session_end` | Final enqueue via the shared `enqueue_reflection` helper (adds `harness` and `scope`). |
| `subagent_stop_reflect.py` | `subagent_stop` | Queues the subagent transcript (`agent_transcript_path`, else `transcript_path`) with `scope: subagent`, `agent_id`, `agent_type`, `parent_session_id`. Does not dedupe by session. Prints `{}` (Codex documents JSON stdout for `SubagentStop`). |

### Drain launcher

```json
{
  "type": "command",
  "command": "(nohup ${CLAUDE_PLUGIN_ROOT}/hooks/reflect-drain-bg.sh >/dev/null 2>&1 &) >/dev/null 2>&1",
  "timeout": 5
}
```

The `( ... &)` wrapper detaches the drain, so the harness returns immediately and the 5 s timeout (Copilot: `timeoutSec: 5`) only covers the launch. The script always exits 0 and all its output goes to `~/.reflect/drain.log` (rotates at 10 MB). Cost controls (turn cap, wall-clock cap, token-budget poison, debounce, daily cap, quota gate) are all env vars; see [drain env vars](/ainb-reflect-memory/reference/configuration/#drain). The writer path defaults to the single-shot `extract` writer since 5.2.5; set `REFLECT_DRAIN_WRITER=agentic` for the legacy multi-turn loop.

### Bookkeeping

`postcompact_bookkeeping.py` writes one forensics log line. If `REFLECT_POSTCOMPACT_RESET_DEDUPE=1` it also deletes `session-injected/<sid>.json`, so the next prompt may re-inject learnings the compaction just discarded.

## Timeouts

Only the drain launcher declares a timeout in any manifest. Every other command uses the harness default. The real latency bounds are inside the scripts:

| Hook | Bound | Override |
|---|---|---|
| `session_start_recall.py`, `user_prompt_submit_recall.py` | 30 s per recall subprocess | `REFLECT_RECALL_TIMEOUT` |
| `subagent_start_recall.py` | 5 s | `REFLECT_SUBAGENT_RECALL_TIMEOUT` |
| drain launcher | 5 s (launch only) | edit manifest |
| all armers, producers, `pretooluse_context.py` | local file I/O, no subprocess | n/a |

On timeout or any recall failure the recall hooks inject an empty string rather than stalling the session.

## Exit-code and output contract

Verified by running each script with empty stdin, garbage stdin and a realistic payload under a throwaway `HOME` and `REFLECT_STATE_DIR`: every lifecycle hook exited `0` with empty stderr.

| Rule | Mechanism |
|---|---|
| Always exit 0 | Each script wraps its body in a top-level handler (`except BaseException`, then `sys.exit(0)`). |
| Never raise into the session | On error the handler writes `last-event.json` (hook, kind, scrubbed detail) and a line in `logs/<hook>.log`, then exits 0. |
| Valid JSON or nothing | Recall hooks print a JSON envelope (empty `additionalContext` on failure). Armers and producers print nothing. `subagent_stop_reflect.py` prints `{}`. |
| Logs are private | `logs/<hook>.log` is developer-only, not shown to the user. Secrets are scrubbed before any write. |

:::caution
Two documented-silent exceptions exist in the code today:

- `precompact_reflect.py --verbose` (which the plugin wires) writes one line to **stderr** (`[precompact_reflect] mode=auto trigger=auto`). Exit code is still 0.
- Bad arguments exit `2` (argparse), and a malformed numeric env var read at import time (`REFLECT_RECALL_MIN_OVERLAP`, `REFLECT_RECALL_MAX_TOKENS` in the SessionStart hook; `REFLECT_SUBAGENT_RECALL_TIMEOUT` in the SubagentStart hook) raises before the silent-fail wrapper exists, giving a traceback and exit `1`. Keep those variables numeric.
:::

`reflect-drain-bg.sh` prints a `reflect-kb CLI not found` notice to stderr when `reflect` is not on `PATH` (suppress with `REFLECT_QUIET_INSTALL_WARNING=1`). In the shipped wiring this is discarded by the `>/dev/null 2>&1` in the launch command.

## Output shapes by harness

| Harness | Envelope for injected context |
|---|---|
| Claude Code, Codex | `{"hookSpecificOutput": {"hookEventName": "<Event>", "additionalContext": "..."}}` |
| Copilot (`REFLECT_HARNESS=copilot`) | `{"additionalContext": "..."}` |

The Copilot envelope is a best-documented guess; the source marks it `TODO(copilot-envelope)` because Copilot's `sessionStart` output shape is not documented. `PreToolUse` deny uses `permissionDecision: "deny"` with `permissionDecisionReason`; `PermissionRequest` uses `decision: {behavior: "allow"|"deny", message}`. The stdin readers accept both snake_case (Claude, Codex) and camelCase (Copilot) keys through `plugin/scripts/hook_input.py`.

## Policy rules file

`pretooluse_context.py` and `permission_request_reflect.py` read JSONL rules from `$REFLECT_POLICY_FILE`, then `$REFLECT_STATE_DIR/policy-rules.jsonl`, then `$REFLECT_STATE_DIR/permission-policy.jsonl`. None ship by default, so both hooks are inert until you add rules.

| Field | Values | Default | Meaning |
|---|---|---|---|
| `scope` | `any`, `pretool`, `permission` | `any` | Which hook may use the rule. |
| `tool` | tool name or `*` | `*` | Case-insensitive match on the tool name. |
| `pattern` | substring | empty (matches all) | Case-insensitive substring of the Bash command, or of the JSON-serialized tool input. |
| `decision` | `deny`, `allow`, `context` | `context` (pretool) | `deny` blocks; `allow` and `context` inject (pretool) or allow (permission). |
| `confidence` | `HIGH`, other | `HIGH` | Only `HIGH` rules act. |
| `message` / `context` | string | built-in text | Shown on deny, or injected as context. |

```json
{"scope": "pretool", "tool": "Bash", "pattern": "rm -rf /", "decision": "deny", "message": "Blocked by project policy."}
```

## State files written by hooks

Paths are relative to `$REFLECT_STATE_DIR` (default `~/.reflect`).

| File | Written by | Read by |
|---|---|---|
| `pending_reflections.jsonl` | `PreCompact`, `Stop`, `SessionEnd`, `SubagentStop`, Hermes capture shim | the drain |
| `armed/<sid>.json` | `PostToolUse`, `PostToolUseFailure` | `UserPromptSubmit` |
| `permission-armed/<sid>.json` | `Notification`, `PermissionRequest` | `UserPromptSubmit` |
| `session-injected/<sid>.json` | `UserPromptSubmit` | `UserPromptSubmit`, reset by `PostCompact` |
| `errors.jsonl` | `errorOccurred` (Copilot) | nothing in this repo (breadcrumb sink) |
| `last-event.json` | any hook on error | status line |
| `logs/<hook>.log` | every hook (for example `logs/precompact_reflect.log`) | developers |
| `drain.log`, `drain-cost.jsonl`, `retry-count.jsonl`, `poison-reflections.jsonl`, `drain.lock.d/` | the drain | the drain; `reflect cost` reads `drain-cost.jsonl` |

Mini-learnings written by `UserPromptSubmit` and the TodoWrite capture land in `$REFLECT_LEARNINGS_DIR`, default `~/.learnings/documents/`.

## Status surfaces

Hooks report health through files, never through the session:

| Surface | Source | Shows |
|---|---|---|
| `last-event.json` | the silent-fail handler in every hook | the most recent hook error; a status line can render it as a `recall failed` or `reflect failed` warning. Nothing in this repo reads it, so rendering is up to your status line script. |
| `errors.json` | drain poison, parser crashes, `reflect-maintenance-watch.sh` | an unacked-error badge (`reflect errors count`), cleared with `reflect:errors-ack`. |
| `plugin/scripts/reflect_timeline.sh` | local logs (`recall_log.jsonl`, `drain.log`, `errors.json`, ingest log, session JSONL) | a 4-row, 2-hour sparkline dashboard (rows REC, MEM, ING, DRN, TOK, UNC, CHR, OUT, THR, AGT, COM, ERR) for your status line. 10 s cache; opt out with `REFLECT_TIMELINE_DISABLE=1`. |

See the [hook timeline](/ainb-reflect-memory/interactive/hook-timeline/) for a walkthrough of what fires when.

## Hooks that are not lifecycle events

These ship in `plugin/hooks/` but are not registered by any manifest.

| Script | Trigger | Purpose |
|---|---|---|
| `post_commit.sh` | git `post-commit`, installed per repo by `install_post_commit.sh` (chains any existing hook; `--uninstall` removes it) | Records the commit SHA, branch, subject and files, links it to the active session (`CLAUDE_SESSION_ID`, else `REFLECT_SESSION_ID`), flags merge-conflict resolutions, demotes learnings of reverted commits. Always exits 0 and silences its output. |
| `idle_reflect.sh` | launchd `com.reflect.idle` every 300 s (macOS) | Enqueues sessions whose transcript has been quiet for `REFLECT_IDLE_THRESHOLD_SEC` with `trigger: idle`. Resulting learnings are tagged `speculative` and down-ranked in recall. |
| `reflect-maintenance-watch.sh` | launchd `com.reflect.maintenance` every 300 s | Read-only watchdog: appends drain and ingest malfunctions to `errors.json`. |
| `launchd/com.reflect.drain.plist` | every 600 s | Optional timer-based drain cadence (sets `REFLECT_DRAIN_DEBOUNCE_SEC=0`). |
| `launchd/com.reflect.synthesis.plist`, `com.reflect.forget.plist` | hourly | Auto-triggered synthesis pass; TTL forget sweep. |

The launchd plists are macOS templates with a `{{PLUGIN_ROOT}}` placeholder you substitute with `sed` before `launchctl load`.

## Hermes shims

Hermes (fleet-lambda) has no hook runtime. `plugin/adapters/hermes/hermes_adapter.py install` copies the skills plus two shims to `~/.hermes/skills/reflect/shim/`; fleet-lambda calls them with a JSON envelope on stdin. Both always exit 0 and write a `last-event.json` breadcrumb on failure.

| Shim | Stdin | Behavior |
|---|---|---|
| `pre_llm_recall.py` | `{prompt, agent_id, domain_hint, session_id}` | `FLEET_MEMORY_BACKEND` selects the mode: `bank` exits immediately; `shadow` (default) runs recall and logs telemetry but prints nothing; `reflect` prints the fleet-context block. Recall is bounded by `REFLECT_FLEET_TIMEOUT` (default 10 s). |
| `post_llm_capture.py` | `{last_user_msg, last_assistant_msg, transcript_tail, session_id, agent_id}` | Appends to `pending_reflections.jsonl`; a correction word in the user message (no, wrong, actually, stop, don't, should be) tags the entry `priority: high`. |

## Disabling hooks

| Goal | How |
|---|---|
| Stop the drain spending tokens | `REFLECT_DISABLED=1` (drain, idle sweep and maintenance watch all exit immediately). |
| Stop PreCompact queueing | `REFLECT_AUTO_REFLECT=0`. |
| Stop all hooks | Disable the plugin in the harness. `REFLECT_DISABLED` does **not** gate the recall hooks or the other queue producers (`Stop`, `SessionEnd`, `SubagentStop`); only the drain family checks it. |
| Run the drain by hand, no spend | `REFLECT_DRAIN_DRY_RUN=1`. |

All variables are documented in [Configuration](/ainb-reflect-memory/reference/configuration/#environment-variables). If hooks misbehave, start with [Troubleshooting](/ainb-reflect-memory/guides/troubleshooting/).
