---
title: Claude Code
description: Install reflect into Claude Code with the native plugin runtime. Prerequisites, the two-layer install, the 13 hooks the manifest wires, how to verify, update and uninstall.
sidebar:
  order: 10
---

Claude Code is the reference harness. The plugin runtime extracts the plugin and auto-wires every hook from the manifest, so there is no hand-editing of `settings.json`. Support is complete: all 13 lifecycle hooks and all 10 skills.

reflect is two layers. Install both, then verify:

```
┌───────────────────────────┐      ┌───────────────────────────┐
│ plugin (step 2)           │      │ reflect-kb CLI (step 1)   │
│ hooks + skills            │─────▶│ recall, index, search     │
│ installed by Claude Code  │      │ installed by uv           │
└───────────────────────────┘      └───────────────────────────┘
```

`claude plugin install` only does the plugin layer. Without the CLI, hooks fire but recall is empty.

## Prerequisites

| Need | Why | Check |
|---|---|---|
| macOS or Linux | hooks are bash and Python, Unix only | |
| Claude Code with plugin support | `claude plugin` subcommands | `claude plugin --help` |
| [uv](https://docs.astral.sh/uv/) | runs every hook (`uv run --script`) and installs the CLI | `uv --version` |
| `claude` on `PATH` | the background drain runs `claude -p` to write learnings | `command -v claude` |
| `bash` 4+, `coreutils` (`timeout`), `jq` | statusline timeline and error badge only; skip if you do not use them | `bash --version` |
| `qmd` on `PATH` (optional) | BM25 leg of hybrid recall; without it recall degrades to the vector leg only | `command -v qmd` |

On macOS, the stock `/bin/bash` is 3.2. For the statusline helpers: `brew install bash coreutils jq`.

## Install

```bash
# 1. Engine: the reflect CLI plus the full GraphRAG stack (CPU torch, skips ~4 GB of CUDA wheels)
uv tool install --force --upgrade --torch-backend cpu \
  'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'

# 2. Plugin: hooks and skills
claude plugin marketplace add stevengonsalvez/ainb-reflect-memory
claude plugin install reflect@ainb-reflect-memory
```

If `uv` rejects `--torch-backend`, update it (`uv self update`) or use the env form: `UV_TORCH_BACKEND=cpu uv tool install --force --upgrade 'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'`. On a GPU box that wants CUDA torch, drop the flag.

Restart Claude Code (or start a new session) so the hooks load.

:::note
`ainb reflect bootstrap` (from the separate agents-in-a-box `ainb` tool, not this repo) automates step 1 and prints the system-tool commands. It is a convenience, not required.
:::

## How hooks get wired

The marketplace entry (`.claude-plugin/marketplace.json`) points at `./plugin`. Claude Code reads `plugin/.claude-plugin/plugin.json`, whose `hooks` block registers each event with a command of the form:

```
uv run --script ${CLAUDE_PLUGIN_ROOT}/<path>
```

`${CLAUDE_PLUGIN_ROOT}` is set by the runtime to the cache copy of the plugin: `~/.claude/plugins/cache/ainb-reflect-memory/reflect/<version>/`. Hooks are `uv run --script` (not bare `uv run`) so uv always treats the file as a self-contained script regardless of the session's working directory.

| Event | Script | Role |
|---|---|---|
| `SessionStart` | `skills/recall/hooks/session_start_recall.py` | injects top-3 learnings as `additionalContext` |
| `SessionStart` | `hooks/reflect-drain-bg.sh` | detached background drain (`timeout: 5`); the only hook path that runs `/reflect` |
| `UserPromptSubmit` | `skills/recall/hooks/user_prompt_submit_recall.py` | prompt-specific recall, deduped per session |
| `PreToolUse` | `hooks/pretooluse_context.py` | bounded policy lookup before risky tools |
| `PermissionRequest` | `hooks/permission_request_reflect.py` | permission-pattern lookup, arms watcher |
| `Notification` | `hooks/notification_reflect.py` | arms permission-decision watcher |
| `PostToolUse` | `hooks/posttooluse_minilearning.py` | arms mini-learning watcher |
| `PostToolUseFailure` | `hooks/posttoolusefailure_minilearning.py` | arms failure-shaped watcher |
| `PreCompact` | `hooks/precompact_reflect.py --auto --verbose` | gates and queues the transcript before compaction |
| `PostCompact` | `hooks/postcompact_bookkeeping.py` | bookkeeping only |
| `SubagentStart` | `hooks/subagent_start_recall.py` | subagent-scoped recall |
| `SubagentStop` | `hooks/subagent_stop_reflect.py` | queues the subagent transcript |
| `Stop` | `hooks/stop_reflect.py` | slot update plus session queue producer |
| `SessionEnd` | `hooks/session_end_reflect.py` | final queue producer and cleanup |

Producers append deduped entries to `~/.reflect/pending_reflections.jsonl` after a `$0` gate. Only the drain runs the LLM. Full event contract: [Hooks reference](/ainb-reflect-memory/reference/hooks/). Drain internals and cost caps: [Drain](/ainb-reflect-memory/concepts/drain/).

The same manifest registers 10 skills: `reflect`, `recall`, `ingest`, `consolidate`, `status`, `errors-ack`, `cost`, `corpus`, `export`, `slots`. They appear namespaced, for example `/reflect:recall`.

## Verify it works

```bash
claude plugin list                          # reflect@ainb-reflect-memory, enabled, version (5.2.5 at time of writing)
claude plugin details reflect@ainb-reflect-memory   # Skills (10), Hooks (13)
reflect --version                           # engine installed and on PATH
```

Smoke-test the recall hook directly. It must exit 0 and print one JSON object (empty `additionalContext` on an empty KB):

```bash
echo '{"session_id":"t1","cwd":"'"$PWD"'","source":"startup"}' \
  | uv run --script ~/.claude/plugins/cache/ainb-reflect-memory/reflect/*/skills/recall/hooks/session_start_recall.py
# {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ""}}
```

Then work normally. After a few sessions:

| Evidence | Where |
|---|---|
| Queue is receiving transcripts | `~/.reflect/pending_reflections.jsonl` |
| Drain ran | `~/.reflect/drain.log` (rotates at 10 MB) |
| Learnings written and indexed | `reflect stats`, `~/.learnings/documents/` |
| Spend | `reflect:cost` skill |
| Something broke | `reflect errors count`, `/reflect:errors-ack` |

`/reflect:status` shows pending reviews, sidecar coverage and GraphRAG health.

If `reflect` is not on `PATH`, the drain prints a `[reflect-kb] CLI not found` warning at session start and recall stays empty. Silence it with `REFLECT_QUIET_INSTALL_WARNING=1` once you have decided not to install.

## Update

```bash
claude plugin update reflect@ainb-reflect-memory     # restart required
uv tool install --force --upgrade --torch-backend cpu \
  'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'
```

The plugin version (manifest, currently 5.2.5) and the engine version (`reflect --version`) are separate streams.

## Optional add-ons

**Per-repo git commit capture.** A commit is the strongest "this was deliberate" signal. The hook lives in `.git/hooks`, which the plugin does not touch, so install it once per repo. It chains any existing post-commit hook and never fails your commit:

```bash
bash ~/.claude/plugins/cache/ainb-reflect-memory/reflect/*/hooks/install_post_commit.sh /path/to/repo
bash ~/.claude/plugins/cache/ainb-reflect-memory/reflect/*/hooks/install_post_commit.sh --uninstall /path/to/repo
```

**Scheduled drain (macOS launchd).** `plugin/launchd/` ships `com.reflect.{drain,idle,forget,synthesis,maintenance}.plist` templates. Each has an INSTALL block in its header comment; replace `{{PLUGIN_ROOT}}` with the cache path above. launchd is macOS only.

**Statusline timeline.** `scripts/reflect_timeline.sh` in the cache copy renders a 4-row activity dashboard. Set `REFLECT_TIMELINE_DISABLE=1` to suppress it.

## Uninstall

```bash
claude plugin uninstall reflect@ainb-reflect-memory   # add --keep-data to keep ~/.claude/plugins/data/<id>/
claude plugin marketplace remove ainb-reflect-memory  # optional
uv tool uninstall reflect-kb                          # removes the engine
```

Your data is not removed. Delete it yourself only if you want the memory gone:

| Path | Contents |
|---|---|
| `~/.learnings/` | the markdown knowledge base and graph cache |
| `~/.reflect/` | queue, drain log, errors, state |
| `~/.cache/qmd/` | BM25 index |

Remove per-repo post-commit hooks with the `--uninstall` command above.

## Limitations

- **`claude_adapter.py install` is refused.** Claude Code installs through `claude plugin install` only. The adapter's `install` exits non-zero with those commands and writes nothing. Older versions copied only each `SKILL.md` and merged a `SessionStart` entry pointing at `~/.claude/skills/recall/hooks/session_start_recall.py`, a script they never deployed, which left a dead hook. If you ran an old adapter, clean up with `python3 plugin/adapters/claude/claude_adapter.py uninstall` (removes its managed skill files and that one hook entry; foreign hooks and hand-written skills stay).
- **Cold recall is slow.** The first recall after a reboot loads local embedding and cross-encoder models (about 11 to 16 s). Hooks pin `HF_HUB_OFFLINE=1` and use `REFLECT_RECALL_TIMEOUT` (default 30 s). Models must be cached once.
- **Capture spends your Claude quota.** The drain runs `claude -p` on Sonnet under caps (16 turns, 300 s, 2M-token poison). Kill switch: `REFLECT_DISABLED=1`. Disable auto-queueing: `REFLECT_AUTO_REFLECT=0`.
- **Hook environment needs `uv` on `PATH`.** Launchers with a trimmed `PATH` (some IDE integrations) can fail to find it.

More failure modes: [Troubleshooting](/ainb-reflect-memory/guides/troubleshooting/). Settings and env vars: [Configuration](/ainb-reflect-memory/reference/configuration/).
