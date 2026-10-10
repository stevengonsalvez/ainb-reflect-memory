---
title: APM (Agent Package Manager)
description: An extra install channel for Codex, Copilot and Cursor through Microsoft APM. How the package is generated from plugin/, what it wires, what it does not do (CLI, timers, post-commit hook, KB bootstrap), and what is and is not verified.
sidebar:
  order: 50
---

[APM](https://github.com/microsoft/apm) (Microsoft Agent Package Manager) can drop reflect's skills and hooks into several agent harnesses from one package. It is an **extra channel for Codex, Copilot and Cursor**. It does not replace the Claude Code plugin, and it only does the agent wiring, not the machine setup.

## What APM does and does not do

| | APM channel |
|---|---|
| Installs the 10 skills | Yes |
| Wires lifecycle hooks (recall at session start and per prompt, capture on stop) | Yes: Claude 13 events, Codex 10, Copilot 13, Cursor 10 |
| Bundles the Python hook runtime (`scripts/`, `hooks/`, assets) | Yes, under `.apm/hooks/rt/` |
| Keeps your own hooks and settings, removes only its own on uninstall | Yes (tested) |
| Installs the `reflect-kb` CLI (`uv tool install`) | **No** |
| Installs the launchd timers | **No** |
| Installs the per-repo git post-commit hook | **No** |
| Bootstraps the knowledge base (`~/.learnings`, index build) | **No** |
| Fires hooks inside a live Codex, Copilot or Cursor session | **Not verified** |

APM packages cannot ship install scripts, so the machine layer stays manual. Without the `reflect` CLI on `PATH` the hooks fail open: they exit 0 and recall and capture quietly do nothing useful. Install the engine first, exactly as on the other pages:

```bash
uv tool install --force --upgrade --torch-backend cpu \
  'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'
```

:::caution
Claude Code users should keep `claude plugin install` plus `ainb reflect bootstrap` (see [Claude Code](/ainb-reflect-memory/install/claude-code/)). Use APM for Claude only if you specifically want APM to manage it. Pick **one** route per harness: the APM channel, the [Codex adapter](/ainb-reflect-memory/install/codex/) or the [Copilot adapter](/ainb-reflect-memory/install/copilot/). Combining them wires every hook twice.
:::

## Install

The package is generated from `plugin/` at build time and is not committed, so there is no `apm install owner/repo` shortcut yet. Build it from a checkout:

```bash
uv tool install apm-cli==0.33.0          # the version this channel is tested against
git clone https://github.com/stevengonsalvez/ainb-reflect-memory.git
cd ainb-reflect-memory
python3 scripts/build_apm_package.py     # writes build/apm/reflect (stdlib only, offline)
apm install -g build/apm/reflect --target codex,copilot,cursor
```

`-g` installs at user scope (`~/.codex`, `~/.copilot`, `~/.cursor`, `~/.agents`). Add `claude` to `--target` only if you want APM to manage Claude too. The generator is idempotent: re-run it after pulling, then run `apm install -g build/apm/reflect` again. The APM copy does not update itself.

### Why a generator

Pointed at `plugin/` as-is, APM wires zero hooks (the Copilot root manifest wins and has none) or wires them but drops `scripts/`, which leaves every hook failing open with no error. So the generator lays the package out on purpose:

```text
build/apm/reflect/
  apm.yml
  .apm/skills/<10 skills>/
  .apm/hooks/claude-hooks.json    13 events
  .apm/hooks/codex-hooks.json     10 events
  .apm/hooks/copilot-hooks.json   13 events, REFLECT_HARNESS=copilot kept
  .apm/hooks/cursor-hooks.json    10 events, camelCase
  .apm/hooks/rt/                  the whole runtime (scripts/, hooks/, skills/, assets/, ...)
```

The hook files are derived from the plugin's own manifests, so the event set cannot drift from the native install.

## Where things land

| Target | Hooks | Count |
|---|---|---|
| Claude | merged into `~/.claude/settings.json` | 13 |
| Codex | merged into `~/.codex/hooks.json` | 10 |
| Copilot | `~/.copilot/hooks/reflect-copilot-hooks.json` | 13 |
| Cursor | merged into `~/.cursor/hooks.json` | 10 |

Skills go to `~/.agents/skills/` (the shared directory APM uses for non-Claude targets) and `~/.claude/skills/` when Claude is targeted. The runtime is copied to `~/.<target>/hooks/reflect/.apm/hooks/rt/`, and hook commands carry the resolved absolute path. For Claude, APM writes the matcher as `"*"` rather than `""`.

## Verify it works

```bash
jq '.hooks | keys | length' ~/.codex/hooks.json               # 10 (plus any hooks of your own)
jq '.hooks | keys' ~/.cursor/hooks.json                       # camelCase names
ls ~/.agents/skills                                           # the 10 skills
ls ~/.codex/hooks/reflect/.apm/hooks/rt/scripts/hook_input.py # runtime was deployed
```

The repo's test (`tests/test_apm_package.py`) does this automatically against a throwaway project and a fake `$HOME`, and CI runs it with `apm-cli==0.33.0` on every change to `plugin/`.

## Uninstall

```bash
apm uninstall -g _local/reflect
```

This removes the reflect hooks, skills and the copied runtime, and leaves your own hooks and settings intact (checked in the test).

## Cursor

Cursor documents camelCase event names (`sessionStart`, `beforeSubmitPrompt`, `stop`). For a shared hook file APM writes PascalCase into `.cursor/hooks.json`, which Cursor would not recognise. The generator therefore authors `cursor-hooks.json` with camelCase names, and APM passes it through unchanged. APM prints a "hook event casing mismatch" warning for it; that warning is APM's own PascalCase assumption and can be ignored. Three Claude events have no Cursor equivalent and are not wired: `Notification`, `PermissionRequest` and `PostCompact`.

What is confirmed is the wiring: the right ten events land in `~/.cursor/hooks.json` with working paths. What is not confirmed is behaviour inside Cursor. The recall hooks reply in Claude's `additionalContext` shape, while Cursor documents `additional_context` for `sessionStart`, and per Cursor's docs most of its events carry `conversation_id` where reflect's capture hooks look for `session_id`. Treat Cursor support as experimental until it has been run in a live session.

## Known limits

- **Live firing is unverified.** The tests prove the right files and paths exist and the hook scripts can be found. Nobody has yet watched an APM-installed hook fire in a real Codex, Copilot or Cursor session.
- **Capture still needs the `claude` CLI**, same as every non-Claude harness.
- **File-name routing is deprecated in APM.** Per-agent hook files (`codex-hooks.json` and friends) work in 0.33.0 but APM has marked the routing for replacement. The CI pin exists so a newer APM cannot break this silently.
- **Skills location.** Skills go to `~/.agents/skills`, not `~/.codex/skills` as the Codex adapter does. Whether Codex and Copilot read that directory is not confirmed here.
- **No drain self-healing.** The drain script's stale-copy delegation only applies to the Codex adapter's layout, so under APM it runs in place.
