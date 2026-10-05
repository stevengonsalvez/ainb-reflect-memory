---
title: Codex CLI
description: Install reflect into Codex CLI with the python adapter or the native plugin manifest. What gets copied, the 10 hooks written to hooks.json, how to verify, uninstall, and the limits of Codex support.
sidebar:
  order: 20
---

Codex CLI has lifecycle hooks, so reflect recalls at session start and per prompt, and queues transcripts for capture, the same as on Claude Code. Support is good but narrower: 10 of the 13 hooks, and 5 of the 10 skills when installed by the adapter. Capture still needs the `claude` CLI.

There are two install routes. Pick one, not both (you would get every hook twice).

| Route | Status |
|---|---|
| **A. Adapter** (`codex_adapter.py`) | Documented in the repo, covered by the adapter test suite. Install and uninstall verified in a sandboxed `$HOME`. |
| **B. Native Codex plugin** (`codex plugin add`) | Installs cleanly (verified on `codex-cli 0.154.0`). Hook firing in a live Codex session was not verified. |

## Prerequisites

| Need | Why | Check |
|---|---|---|
| macOS or Linux | hooks are bash and Python | |
| Codex CLI with hooks enabled | `hooks` feature; the adapter targets the Codex hook set that landed around 0.129 | `codex features list \| grep hooks` |
| [uv](https://docs.astral.sh/uv/) | runs every hook and installs the CLI | `uv --version` |
| reflect engine on `PATH` | recall and indexing | `reflect --version` |
| `claude` on `PATH` | the drain runs `claude -p` to turn queued transcripts into learnings | `command -v claude` |
| PyYAML for the adapter (route A) | `plugin/adapters/base.py` imports `yaml`; stock `python3` without it fails with `ModuleNotFoundError: No module named 'yaml'` | `python3 -c 'import yaml'` |

Install the engine first (both routes):

```bash
uv tool install --force --upgrade --torch-backend cpu \
  'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'
```

## Route A: adapter

```bash
git clone https://github.com/stevengonsalvez/ainb-reflect-memory.git
cd ainb-reflect-memory

# preview, writes nothing
uv run --no-project --with pyyaml python plugin/adapters/codex/codex_adapter.py install --dry-run
# install (or: pip install pyyaml, then python3 plugin/adapters/codex/codex_adapter.py install)
uv run --no-project --with pyyaml python plugin/adapters/codex/codex_adapter.py install
```

`--no-project` keeps uv from creating a `.venv` inside the checkout.

| Flag | Effect |
|---|---|
| `--dry-run` | print the plan, change nothing |
| `--no-hooks` | copy skills and scripts, leave `hooks.json` alone (older Codex builds without hooks; then drive `/reflect` and `/recall` by hand) |
| `--no-bg-drain` | skip the `SessionStart` drain entry (see the drain caveat below) |
| `--force` | overwrite a hand-written `SKILL.md` that lacks the adapter's `managed_by` sentinel |
| `--home DIR` | treat `DIR` as `$HOME` (testing) |

The adapter is idempotent. Re-run it after pulling a newer checkout: the copy under `~/.codex/skills/` does not update itself.

### What it deploys

Codex has no plugin runtime that extracts the plugin tree for the adapter route, so the adapter copies it:

```
~/.codex/
├── hooks.json                     merged: reflect entries only added, yours kept
└── skills/
    ├── reflect/
    │   ├── SKILL.md               full content + managed_by: reflect-kb/adapters/codex
    │   ├── hooks/                 plugin-level hook scripts (drain, queue, policy, subagent)
    │   ├── scripts/  assets/  references/
    │   └── reflect.toml           plugin defaults
    ├── recall/        SKILL.md  hooks/  scripts/
    ├── status/        SKILL.md  scripts/
    ├── consolidate/   SKILL.md
    └── ingest/        SKILL.md
```

In each copied `SKILL.md` the adapter rewrites `${CLAUDE_PLUGIN_ROOT}/plugin/...` anchors to the real `~/.codex/skills/...` paths, since Codex does not set that variable.

## How hooks get wired (route A)

The adapter merges these into `~/.codex/hooks.json` using the same nested `{matcher, hooks:[{type, command}]}` shape Claude uses. Commands are rendered with the resolved `~/.codex` path at install time:

| Event | Command (under `~/.codex/skills/`) |
|---|---|
| `SessionStart` | `uv run recall/hooks/session_start_recall.py` |
| `SessionStart` | `(nohup reflect/hooks/reflect-drain-bg.sh >/dev/null 2>&1 &)` with `timeout: 5` |
| `UserPromptSubmit` | `uv run recall/hooks/user_prompt_submit_recall.py` |
| `PreToolUse` | `uv run reflect/hooks/pretooluse_context.py` |
| `PermissionRequest` | `uv run reflect/hooks/permission_request_reflect.py` |
| `PostToolUse` | `uv run reflect/hooks/posttooluse_minilearning.py` |
| `PreCompact` | `uv run reflect/hooks/precompact_reflect.py --auto --verbose` |
| `PostCompact` | `uv run reflect/hooks/postcompact_bookkeeping.py` |
| `SubagentStart` | `uv run reflect/hooks/subagent_start_recall.py` |
| `SubagentStop` | `uv run reflect/hooks/subagent_stop_reflect.py` |
| `Stop` | `uv run reflect/hooks/stop_reflect.py` |

Not wired on Codex: `Notification`, `PostToolUseFailure`, `SessionEnd`. Those exist in the Claude and Copilot manifests only. `plugin/hooks/registry.py` is the source of truth; a parity test keeps the three manifests in line with it and the adapter has its own test suite under `plugin/adapters/tests/`.

Recall hooks emit the default envelope, `{"hookSpecificOutput": {"hookEventName": ..., "additionalContext": ...}}` (the Copilot-only variant is selected by `REFLECT_HARNESS=copilot`, which the Codex adapter does not set). `PreCompact` emits nothing, because Codex's schema rejects `PreCompact` output; the queue is its only effect.

## Route B: native Codex plugin

The repo ships `plugin/.codex-plugin/plugin.json`, which points at `./codex-hooks.json` (same 10 events, `${PLUGIN_ROOT}` paths) and `./skills/` (all 10 skills). Codex reads the marketplace file at `.claude-plugin/marketplace.json`:

```bash
codex plugin marketplace add stevengonsalvez/ainb-reflect-memory
codex plugin add reflect@ainb-reflect-memory
codex plugin list      # reflect@ainb-reflect-memory   installed, enabled   5.2.5
```

Verified: against a local checkout and a scratch `CODEX_HOME`, `marketplace add` registered `ainb-reflect-memory` and `plugin add` installed 5.2.5 into `$CODEX_HOME/plugins/cache/ainb-reflect-memory/reflect/5.2.5`. Not verified: that Codex then fires the hooks from `codex-hooks.json` in a real session. If you take this route, confirm with the checks below before relying on it.

## Verify it works

```bash
ls ~/.codex/skills/recall/hooks/session_start_recall.py     # deployed (route A)
jq '.hooks | keys' ~/.codex/hooks.json                      # 10 events, route A
```

Smoke-test the recall hook directly (exit 0, one JSON object):

```bash
echo '{"session_id":"t1","cwd":"'"$PWD"'","source":"startup"}' \
  | uv run --script ~/.codex/skills/recall/hooks/session_start_recall.py
# {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ""}}
```

Then run a short Codex session that includes a correction ("no, use X not Y") and end it:

| Evidence | Where |
|---|---|
| Transcript queued | new line in `~/.reflect/pending_reflections.jsonl` with `"harness"` set |
| Drain ran | `~/.reflect/drain.log` |
| Learning landed | `reflect stats`, `~/.learnings/documents/` |
| Hook errors | `reflect errors count` |

## Uninstall

Route A:

```bash
uv run --no-project --with pyyaml python plugin/adapters/codex/codex_adapter.py uninstall
```

This deletes the five adapter-managed `SKILL.md` files (only those carrying the `managed_by` sentinel) and removes reflect's entries from `hooks.json`, leaving other hooks alone. It does **not** delete the copied support directories, and leaves an empty `{}` if `hooks.json` held only reflect. To finish:

```bash
rm -rf ~/.codex/skills/reflect/{hooks,scripts,assets,references} ~/.codex/skills/reflect/reflect.toml
rm -rf ~/.codex/skills/recall/{hooks,scripts} ~/.codex/skills/status/scripts
rmdir ~/.codex/skills/{reflect,recall,status,consolidate,ingest} 2>/dev/null
```

Route B: `codex plugin remove reflect@ainb-reflect-memory`, then `codex plugin marketplace remove ainb-reflect-memory`.

Your knowledge base (`~/.learnings/`, `~/.reflect/`) is never touched. Remove the engine with `uv tool uninstall reflect-kb`.

## Limitations

- **Capture needs `claude`.** `reflect-drain-bg.sh` shells out to `claude -p /reflect <transcript>`. With no `claude` on `PATH` the drain logs a warning and exits 0 (Codex startup never blocks), but queued transcripts are not converted. Recall still works against existing learnings; run `/reflect` by hand to capture. `--no-bg-drain` removes the drain entry entirely, so on its own the queue is never consumed. The queue at `~/.reflect/pending_reflections.jsonl` is shared across harnesses, so a Claude Code session on the same machine will drain Codex entries.
- **Fewer hooks.** No `Notification`, `PostToolUseFailure` or `SessionEnd` on Codex, so permission-decision and failure-shaped mini-learnings are not armed from those events, and session-end queueing relies on `Stop`.
- **Fewer skills via the adapter.** The adapter installs `reflect`, `recall`, `status`, `consolidate`, `ingest`. The `cost`, `errors-ack`, `corpus`, `export` and `slots` skills come only with the native route (or use the matching `reflect` CLI commands).
- **Frozen copy.** Under route A the deployed scripts do not follow plugin upgrades; re-run the adapter after updating the checkout. Only `SKILL.md` bodies get their path anchors rewritten, not other copied files.
- **No Codex status line.** Codex has only a built-in item picker, so the reflect statusline timeline does not apply.
- **Older Codex without hooks.** Use `--no-hooks` and run `/reflect` and `/recall` manually each session.

See also: [Hooks reference](/ainb-reflect-memory/reference/hooks/), [Drain](/ainb-reflect-memory/concepts/drain/), [Troubleshooting](/ainb-reflect-memory/guides/troubleshooting/).
