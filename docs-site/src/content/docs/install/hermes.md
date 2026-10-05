---
title: Hermes (fleet-lambda)
description: Partial support. Deploy the reflect skills and two shim scripts into ~/.hermes so a fleet-lambda hook can call reflect for shadow recall and capture. What works, what is not wired, and how to verify.
sidebar:
  order: 40
---

Hermes is the fleet-lambda agent harness. Support is **partial by design**: reflect ships the pieces a fleet-lambda hook calls, but it does not own Hermes hook wiring and there is no plugin runtime. Treat this as an integration kit, not a one-command install.

| Works | Does not exist yet |
|---|---|
| Adapter deploys skills, `reflect.toml` and two shim scripts to `~/.hermes/skills/` | Any `hooks.json` or hook registration in Hermes (fleet-lambda owns it) |
| `pre_llm_recall.py` shim: shadow / reflect / bank modes, telemetry, fleet-context block | Session-end, pre-compact, subagent or error hooks (the spec records Hermes as exposing only `pre_llm_call` and `post_llm_call`) |
| `post_llm_capture.py` shim: turn into a queue entry with a correction-priority flag | A drain on Hermes: the adapter deploys no drain script, and the drain needs `claude` |
| `reflect fleet ingest`: import fleet-lambda memory as quarantined learnings | `/reflect:ingest` discovery of `~/.hermes` (no Hermes provider exists) |

## Prerequisites

| Need | Why | Check |
|---|---|---|
| fleet-lambda with a hook that can pipe JSON to a command | calls the shims; wiring is on that side | |
| [uv](https://docs.astral.sh/uv/) | runs the shims and `recall.py` | `uv --version` |
| reflect engine on `PATH` | the recall subprocess searches the KB | `reflect --version` |
| PyYAML | `plugin/adapters/base.py` imports `yaml` | `python3 -c 'import yaml'` |
| `claude` on `PATH` and a way to run the drain | only if you want queued captures turned into learnings | `command -v claude` |

## Install

```bash
uv tool install --force --upgrade --torch-backend cpu \
  'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'

git clone https://github.com/stevengonsalvez/ainb-reflect-memory.git
cd ainb-reflect-memory
uv run --no-project --with pyyaml python plugin/adapters/hermes/hermes_adapter.py install --dry-run
uv run --no-project --with pyyaml python plugin/adapters/hermes/hermes_adapter.py install
```

Flags: `--dry-run`, `--force` (replace a hand-written `SKILL.md` lacking the `managed_by` sentinel), `--home DIR`. There is no `--no-hooks` because the adapter writes no hooks.

Deployed layout:

```
~/.hermes/skills/
├── reflect/
│   ├── SKILL.md                 managed_by: reflect-kb/adapters/hermes
│   ├── reflect.toml             includes [providers.hermes] home_dir = "~/.hermes"
│   └── shim/
│       ├── pre_llm_recall.py
│       └── post_llm_capture.py
├── recall/    SKILL.md  hooks/  scripts/   (recall.py lives here; the shim calls it)
├── status/    SKILL.md  scripts/
├── consolidate/  SKILL.md
└── ingest/       SKILL.md
```

Unlike the Codex and Copilot adapters, the plugin-level `hooks/`, `scripts/`, `assets/` and `references/` are not copied. The drain script `reflect-drain-bg.sh` is therefore not under `~/.hermes`.

The shims are copied without the executable bit. Invoke them as `uv run --script <path>` (or `chmod +x` them).

## How hooks get wired

They do not, from reflect's side. A fleet-lambda hook runs a shim and pipes a JSON envelope on stdin. Both shims always exit 0 and turn any exception into a breadcrumb at `~/.reflect/last-event.json`, so a failure never blocks a turn.

```
fleet-lambda pre_llm_call  ──JSON stdin──▶ pre_llm_recall.py ──▶ recall.py --format fleet-context
fleet-lambda post_llm_call ──JSON stdin──▶ post_llm_capture.py ─▶ ~/.reflect/pending_reflections.jsonl
                                                                    ~/.reflect/hermes-transcripts/
```

### Recall: `pre_llm_recall.py`

Input: `{"prompt": "...", "agent_id": "...", "domain_hint": "...", "session_id": "..."}`. Mode comes from `FLEET_MEMORY_BACKEND`:

| Value | Behaviour |
|---|---|
| `bank` | exit 0 immediately, no output (fleet's own bank owns recall) |
| `shadow` (default) | run recall, log a `fleet_shadow_recall` metric, print nothing |
| `reflect` | run recall, log the metric, print the fleet-context block to stdout |

Recall runs `uv run --script ~/.hermes/skills/recall/scripts/recall.py --format fleet-context --include-quarantined --limit 5 --no-followup --no-gap-log [--domain-hint D] -- "<prompt>"` with `REFLECT_HARNESS=hermes`. The block is capped at 5 items and about 2000 estimated tokens, labelled `fleet-context/v1`, and includes quarantined fleet imports that normal Claude and Codex recall excludes.

| Env var | Default | Purpose |
|---|---|---|
| `FLEET_MEMORY_BACKEND` | `shadow` | mode above |
| `REFLECT_FLEET_TIMEOUT` | `10` | wall-clock seconds for the recall subprocess; on timeout the shim exits 0 with a breadcrumb |
| `REFLECT_RECALL_SCRIPT` | auto | override the `recall.py` path |
| `REFLECT_RECALL_RUNNER` | `uv run --script` | override the command prefix |
| `REFLECT_METRICS_PATH` | `~/.learnings/metrics.jsonl` | where telemetry is appended |

### Capture: `post_llm_capture.py`

Input: `{"last_user_msg": "...", "last_assistant_msg": "...", "transcript_tail": "...", "session_id": "...", "agent_id": "...", "cwd": "..."}`. The shim appends the turn to `~/.reflect/hermes-transcripts/<session>.jsonl`, then enqueues one entry per pending session (later turns extend the same transcript) in `~/.reflect/pending_reflections.jsonl` with `"trigger": "stop"` and `"source": "hermes"`. If the user message matches `no|wrong|actually|stop|don't|should be`, the entry gets `"priority": "high"`. Classification still happens later in `/reflect`. `REFLECT_STATE_DIR` relocates `~/.reflect`.

### Draining the queue

Nothing on a Hermes-only host runs the drain. Entries accumulate until something runs `reflect-drain-bg.sh`: a Claude Code or Codex session on the same machine (the queue is shared), or you, by hand from a checkout:

```bash
bash plugin/hooks/reflect-drain-bg.sh
```

The drain calls `claude -p`, so it needs a working `claude`. That the drain accepts the synthesized Hermes transcripts end to end was not exercised here; the shim writes them in the shape the drain's gate reads.

## Verify it works

Run the shims by hand with invented input. Use a throwaway state dir so you do not touch real data:

```bash
export REFLECT_STATE_DIR=/tmp/hermes-check/state REFLECT_METRICS_PATH=/tmp/hermes-check/metrics.jsonl
SH=~/.hermes/skills/reflect/shim

echo '{"last_user_msg":"no, use bun not npm","last_assistant_msg":"ok","session_id":"s1","agent_id":"a1"}' \
  | uv run --script $SH/post_llm_capture.py
cat $REFLECT_STATE_DIR/pending_reflections.jsonl     # one entry, "source":"hermes","priority":"high"

echo '{"prompt":"how do we run migrations","agent_id":"a1","session_id":"s1"}' \
  | FLEET_MEMORY_BACKEND=shadow REFLECT_FLEET_TIMEOUT=120 uv run --script $SH/pre_llm_recall.py
cat $REFLECT_METRICS_PATH      # {"op":"fleet_shadow_recall","harness":"hermes","hits":...,"latency_ms":...}
```

Shadow mode prints nothing; the metric line is the proof. With an empty KB, `hits` is 0. In reflect mode, a KB with a matching learning prints the fleet-context block. Use a long `REFLECT_FLEET_TIMEOUT` on the first run: a cold `uv` resolve of `recall.py` exceeded the 10 s default in testing, then ran in about 2 s warm.

Telemetry rolls up in:

```bash
reflect metrics stats     # includes fleet shadow recalls, avg hits, latency, tokens
```

## Importing fleet memory

`reflect fleet ingest --root <dir>` reads `patterns.jsonl`, `discoveries.jsonl`, `discoveries-archive.jsonl`, `corrections.md` and `pending-corrections.jsonl` from `<dir>`, writes quarantined learnings, dedupes by content hash, and reindexes once. Use `--dry-run` first, and `reflect fleet status` for ledger counts. Full detail: [Fleet and Hermes import](/ainb-reflect-memory/guides/fleet/).

## Uninstall

```bash
uv run --no-project --with pyyaml python plugin/adapters/hermes/hermes_adapter.py uninstall
```

Removes the five adapter-managed `SKILL.md` files, `~/.hermes/skills/reflect/shim/` and `~/.hermes/skills/reflect/reflect.toml`. Remaining support directories:

```bash
rm -rf ~/.hermes/skills/recall/{hooks,scripts} ~/.hermes/skills/status/scripts
rmdir ~/.hermes/skills/{reflect,recall,status,consolidate,ingest} 2>/dev/null
```

Then remove the hook on the fleet-lambda side. Captured data stays: `~/.reflect/hermes-transcripts/`, queue entries with `"source":"hermes"`, imported learnings under `~/.learnings/documents/`.

## Limitations

- **Integration kit, not turnkey.** The fleet-lambda side (hook config, `FLEET_MEMORY_BACKEND` switch, shim call) lives in a separate repo and is out of scope here.
- **Two hook points only.** Recall maps to `pre_llm_call` and capture to `post_llm_call`. No compaction, session-end, subagent, permission or error hooks.
- **Shadow by default.** Nothing is injected until you set `FLEET_MEMORY_BACKEND=reflect`. Latency and token cost are logged so you can judge a cutover; a p95 under 150 ms was the design target for flipping, not something this repo enforces.
- **No drain, no ingest discovery.** See the table at the top. `[discovery].enabled_providers` in `reflect.toml` lists `hermes`, but no provider module is registered for it, so it is skipped silently.
- **Path anchors not rewritten.** The Hermes adapter, like Copilot, does not rewrite `${CLAUDE_PLUGIN_ROOT}` anchors in `SKILL.md` bodies.
- **Design docs are partly aspirational.** `docs/design/fleet-hermes-adapter.md` is an implementation plan. This page reflects what the code does.

See also: [Fleet and Hermes import](/ainb-reflect-memory/guides/fleet/), [Drain](/ainb-reflect-memory/concepts/drain/), [Troubleshooting](/ainb-reflect-memory/guides/troubleshooting/).
