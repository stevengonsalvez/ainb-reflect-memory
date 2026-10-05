---
title: GitHub Copilot CLI
description: Install reflect into GitHub Copilot CLI with the python adapter. The native reflect.json hooks drop-in, the 13 camelCase events, verification, uninstall, and what is and is not confirmed.
sidebar:
  order: 30
---

GitHub Copilot CLI has a native hook system, so the adapter wires the same recall, queue, policy and error hooks as the other harnesses, in Copilot's own config format. Support is broad (13 hooks, no `postCompact`) but rests on the adapter only: there is no working native-plugin route, and I could not run a live Copilot CLI to confirm behaviour. Adapter output below is verified in a sandboxed `$HOME`; the live-CLI claims come from the repo's own docs and are labelled as such.

## Prerequisites

| Need | Why | Check |
|---|---|---|
| macOS or Linux | hooks are bash and Python | |
| GitHub Copilot CLI with hooks | hook system GA Feb 2026 per the adapter docs | `copilot --version` |
| [uv](https://docs.astral.sh/uv/) | runs every hook and installs the CLI | `uv --version` |
| reflect engine on `PATH` | recall and indexing | `reflect --version` |
| `claude` on `PATH` | the drain runs `claude -p` to write learnings | `command -v claude` |
| PyYAML | `plugin/adapters/base.py` imports `yaml`; stock `python3` without it fails | `python3 -c 'import yaml'` |

## Install

```bash
# engine
uv tool install --force --upgrade --torch-backend cpu \
  'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'

# adapter
git clone https://github.com/stevengonsalvez/ainb-reflect-memory.git
cd ainb-reflect-memory
uv run --no-project --with pyyaml python plugin/adapters/copilot/copilot_adapter.py install --dry-run
uv run --no-project --with pyyaml python plugin/adapters/copilot/copilot_adapter.py install
```

`--no-project` stops uv creating a `.venv` in the checkout. If you prefer, `pip install pyyaml` and run `python3 plugin/adapters/copilot/copilot_adapter.py install`.

| Flag | Effect |
|---|---|
| `--dry-run` | print the plan, change nothing |
| `--no-hooks` | skills and scripts only, no `reflect.json` |
| `--no-bg-drain` | skip the `sessionStart` drain entry (see limitations) |
| `--force` | overwrite a hand-written `SKILL.md` lacking the `managed_by` sentinel |
| `--home DIR` | treat `DIR` as `$HOME` (testing) |

Re-run after updating the checkout; the deployed copy does not self-update.

:::note
`plugin/plugin.json` carries only `name`, `version`, `description`, `author`, `license` and `keywords`: no `hooks` or `skills` field. `plugin/copilot-hooks.json` is not referenced by any manifest; the test suite uses it as a parity fixture. A Copilot plugin install therefore wires nothing by itself. Use the adapter.
:::

## What it deploys

```
~/.copilot/
├── hooks/
│   └── reflect.json              the one file the adapter owns, written whole
└── skills/
    ├── reflect/   SKILL.md  hooks/  scripts/  assets/  references/  reflect.toml
    ├── recall/    SKILL.md  hooks/  scripts/
    ├── status/    SKILL.md  scripts/
    ├── consolidate/  SKILL.md
    └── ingest/       SKILL.md
```

Each `SKILL.md` is the full plugin content plus `managed_by: reflect-kb/adapters/copilot`.

## How hooks get wired

Copilot loads every `*.json` in `~/.copilot/hooks/` and combines them, so the adapter writes exactly one file, `reflect.json`, and never edits anyone else's. The format differs from Claude and Codex on every axis:

| | Claude / Codex | Copilot |
|---|---|---|
| Config | one shared `settings.json` / `hooks.json` | drop-in directory, one file per owner |
| Event names | `SessionStart` | `sessionStart` |
| Nesting | `{matcher, hooks:[{...}]}` | flat `[{type, command}]` plus top-level `"version": 1` |
| Timeout key | `timeout` | `timeoutSec` |
| Hook stdin keys | snake_case | camelCase |

Every `uv run` command is prefixed `REFLECT_HARNESS=copilot`, which makes the recall hooks emit `{"additionalContext": ...}` and makes the shared stdin reader accept camelCase keys. The drain command is a `( ... &)` subshell and carries no prefix. Example, as generated:

```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [
      { "type": "command",
        "command": "REFLECT_HARNESS=copilot uv run ~/.copilot/skills/recall/hooks/session_start_recall.py" },
      { "type": "command",
        "command": "(nohup ~/.copilot/skills/reflect/hooks/reflect-drain-bg.sh >/dev/null 2>&1 &) >/dev/null 2>&1",
        "timeoutSec": 5 }
    ]
  }
}
```

(Paths are written fully resolved, not with `~`.) The 13 events:

| Copilot event | Script (under `~/.copilot/skills/`) | Role |
|---|---|---|
| `sessionStart` | `recall/hooks/session_start_recall.py` | top-3 recall as `additionalContext` |
| `sessionStart` | `reflect/hooks/reflect-drain-bg.sh` | detached drain |
| `userPromptSubmitted` | `recall/hooks/user_prompt_submit_recall.py` | prompt recall, deduped |
| `preToolUse` | `reflect/hooks/pretooluse_context.py` | policy lookup |
| `permissionRequest` | `reflect/hooks/permission_request_reflect.py` | permission lookup, arms watcher |
| `notification` | `reflect/hooks/notification_reflect.py` | arms watcher |
| `postToolUse` | `reflect/hooks/posttooluse_minilearning.py` | arms mini-learning watcher |
| `postToolUseFailure` | `reflect/hooks/posttoolusefailure_minilearning.py` | failure watcher |
| `preCompact` | `reflect/hooks/precompact_reflect.py --auto --verbose` | gate and queue |
| `subagentStart` | `reflect/hooks/subagent_start_recall.py` | subagent recall |
| `subagentStop` | `reflect/hooks/subagent_stop_reflect.py` | queue subagent transcript |
| `agentStop` | `reflect/hooks/stop_reflect.py` | slot update, session queue |
| `sessionEnd` | `reflect/hooks/session_end_reflect.py` | final queue |
| `errorOccurred` | `reflect/hooks/error_occurred_reflect.py` | error breadcrumb |

`postCompact` is intentionally not wired: Copilot has no such event, and on the other harnesses it is bookkeeping only (it never drains or recalls), so nothing is lost.

## Verify it works

```bash
jq '.version, (.hooks | keys)' ~/.copilot/hooks/reflect.json   # 1 and 13 event names
ls ~/.copilot/skills                                            # consolidate ingest recall reflect status
```

Smoke-test a recall hook with the Copilot env (exit 0, plain `additionalContext` object):

```bash
echo '{"sessionId":"t1","cwd":"'"$PWD"'"}' \
  | REFLECT_HARNESS=copilot uv run --script ~/.copilot/skills/recall/hooks/user_prompt_submit_recall.py
# {"additionalContext": ""}
```

Then run a Copilot session with a correction in it:

| Evidence | Where |
|---|---|
| Transcript queued | new line in `~/.reflect/pending_reflections.jsonl`, `"harness": "copilot"` |
| Drain ran | `~/.reflect/drain.log` |
| Learning landed | `reflect stats`, `~/.learnings/documents/` |
| Hook failures | `reflect errors count` |

## Uninstall

```bash
uv run --no-project --with pyyaml python plugin/adapters/copilot/copilot_adapter.py uninstall
```

Removes `~/.copilot/hooks/reflect.json` (and the `hooks/` dir if empty; foreign `*.json` siblings are untouched) and the five adapter-managed `SKILL.md` files. The copied support directories remain:

```bash
rm -rf ~/.copilot/skills/reflect/{hooks,scripts,assets,references} ~/.copilot/skills/reflect/reflect.toml
rm -rf ~/.copilot/skills/recall/{hooks,scripts} ~/.copilot/skills/status/scripts
rmdir ~/.copilot/skills/{reflect,recall,status,consolidate,ingest} 2>/dev/null
```

Your knowledge base is untouched. Remove the engine with `uv tool uninstall reflect-kb`.

## Limitations

- **Per-prompt recall: repo says yes, I could not confirm.** The adapter README and architecture doc state that `userPromptSubmitted` `additionalContext` reaches the model on Copilot CLI 1.0.66, and that the `sessionStart` and `userPromptSubmitted` envelopes were checked against the live binary. A docstring in `user_prompt_submit_recall.py` still says the output is ignored; it predates that validation. On older builds `/recall` is the manual fallback.
- **Headless `-p` runs are unconfirmed.** An earlier doc claimed `sessionStart` context is not injected in headless mode. Nothing in this repo confirms or refutes that.
- **Capture needs `claude`.** The drain runs `claude -p`. Without it, queued transcripts are not converted; recall still works and `/reflect` works by hand. `--no-bg-drain` removes the drain entry, so alone it leaves the queue unconsumed. The queue is shared across harnesses, so a Claude Code session on the same machine drains Copilot entries.
- **Fewer skills.** The adapter installs `reflect`, `recall`, `status`, `consolidate`, `ingest`. `cost`, `errors-ack`, `corpus`, `export`, `slots` are not installed; use the `reflect` CLI equivalents.
- **Path anchors not rewritten.** Unlike Codex, the Copilot adapter does not rewrite `${CLAUDE_PLUGIN_ROOT}` anchors inside `SKILL.md` bodies yet, so a skill that cites them may point at paths that do not exist.
- **Frozen copy.** Re-run the adapter after upgrading.

See also: [Hooks reference](/ainb-reflect-memory/reference/hooks/), [Drain](/ainb-reflect-memory/concepts/drain/), [Troubleshooting](/ainb-reflect-memory/guides/troubleshooting/).
