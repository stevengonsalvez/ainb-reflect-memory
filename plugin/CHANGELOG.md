# Changelog

All notable changes to the **reflect** plugin. Format follows
[Keep a Changelog](https://keepachangelog.com/); this project uses semantic
versioning.

## [5.2.3] - 2026-07-18 - Single-shot extraction writer (linear cost)

Minor, shipped as a patch. Adds an alternative drain "writer" behind a flag.
**Opt-in: `REFLECT_DRAIN_WRITER` defaults to `agentic` (the existing loop); set
it to `extract` to use the new single-shot path.** It ships opt-in rather than
default while the corpus-write integration bakes (two review rounds found and
fixed data-loss defects in it).

**The problem.** The writer was the `claude -p "/reflect"` subprocess the drain
spawned, run as an agentic loop: read slice, search corpus, read template,
write .md, write sidecar, run `reflect add`, summarize, each an assistant turn.
The API has no memory, so every turn re-sent the whole growing conversation.
Cost therefore grew ~quadratically with turns (measured: 20 turns = 6.8M tokens
= $4.42; one uncapped run = 242M tokens = $110). On real backlog transcripts the
writer exhausted the 16-turn cap mid-workflow and wrote **nothing**, at ~$1.2 a
failure.

**The fix.** `extract` mode does ONE tool-free `claude -p` call
(`--allowedTools "" --max-turns 1`): the model reads the slice (which already
carries the belief-revision block) and emits a JSON action list, and
`drain_extract.py` executes it deterministically: CREATE via `reflect add`
(the same canonical corpus write), UPDATE/DELETE via `reflect_cascade.py
revise`. No file I/O or CLI calls burn model turns. Cost is linear in slice
size, and a single turn cannot partial_max_turns.

**Measured, real transcripts** (the class that failed agentic): 216KB and 654KB
sessions each captured 2 learnings in 1 turn at ~84K tokens / ~$0.37, versus the
agentic path's ~$1.2 and zero capture. Draining the backlog drops from a ~$200
estimate to roughly $15-20.

The writer path is selected by `REFLECT_DRAIN_WRITER`; extract only runs when a
slice exists and the trigger is not skill_refresh, else it falls back to
agentic. All downstream cost/outcome/chunk-hash bookkeeping is unchanged.

Hardening (found in review before merge): a run that captured nothing but
reported per-note write failures is now scored a failure, so the transcript
stays queued instead of being dropped; the entity sidecar carries the
`description` field validate_sidecar requires, so entities and relationships are
no longer rejected; UPDATE/DELETE target ids are restricted to the ids the
slice's revision block actually listed, so an untrusted transcript cannot retire
an arbitrary learning; the model-controlled causal-link type is whitelisted to
the closed enum (a raw value was a frontmatter-injection vector); the note and
its sidecar share one id; extract-written notes also record a learnings row
keyed on the transcript so `record-chunk` provenance links them; and the model
call is given a timeout below the entry budget so the reflect-add pass is not
SIGTERMed mid-index.

## [5.2.2] - 2026-07-16 - Drain now actually captures: skill paths + turn budget

Patch. Follows 5.2.1, which restored skill registration but left the drain
running without capturing. Two defects, both proven on real drain runs.

**Skill asset paths never resolved.** Five SKILL.md files cited their assets,
scripts, and references with bare relative paths (`assets/learning_template.md`)
that resolve against the skill's own directory. Those directories live at the
plugin root, so the paths pointed nowhere. A model reading them does not error,
it goes hunting: every drain run burned about three turns guessing
`plugin/skills/reflect/assets/`, falling back to `find`, then reading the real
path. Two skills (export, cost) were worse, invoking scripts at
`${CLAUDE_PLUGIN_ROOT}/scripts/...`, which is missing the `plugin/` segment and
never ran. Rewrote 18 references to `${CLAUDE_PLUGIN_ROOT}/plugin/...`; the one
genuinely skill-local path (recall's own `scripts/recall_stages.py`) was left
as is. A test now checks that every cited path resolves against one of the two
legitimate bases.

**Turn budget was below the floor.** `--max-turns` counts assistant messages,
not tool calls, so the 8-turn cap allowed only about four tool calls. The
writer's minimum honest workflow (read slice, search corpus to dedupe, read
template, write the note, write the entity sidecar, `reflect add`, summarize)
is about seven, so every real run hit max_turns and wrote nothing while still
costing around $0.60. Raised the default to 16, which completes with headroom:
a measured run wrote its learning in 12 turns. Still overridable via
`REFLECT_DRAIN_MAX_TURNS`.

Together these make the drain capture end to end at stock config: a real run
at the new default recorded `ok` in 12 turns with a learning written to the
corpus. The path fix alone does not fit under 8 turns, and the cap alone still
wastes turns hunting, so both ship together.

**Wall-clock cap raised too.** `REFLECT_DRAIN_TIMEOUT` went 180s to 300s
alongside the turn bump. If the wall-clock cap sits below the turn budget's
worst case, a run that should stop cleanly at max_turns instead gets SIGTERMed
mid-write and quarantined to the poison file. The measured 16-turn write ran
about 111s, so 300s keeps turns the binding limit.

**Codex path resolution.** The reflect/corpus skills anchor their resources on
`${CLAUDE_PLUGIN_ROOT}`, which the Claude runtime sets but Codex does not. The
Codex adapter now rewrites those anchors to the installed umbrella layout when
it copies each SKILL.md, so a Codex drain resolves the same files instead of
hunting. Covered by an adapter test that installs and checks every rewritten
path resolves across all installed skills. Known limitation: only SKILL.md
bodies are rewritten, so a copied non-SKILL resource file that itself cites the
anchor keeps it literal under Codex (today only a human-facing hooks/README.md
does, which the model never dereferences). Copilot and Hermes accept the
destination argument but do not rewrite yet; the same treatment applies.

## [5.2.1] - 2026-07-15 - Drain outage: skills never registered

Patch. Packaging + observability. The drain had captured **nothing since
04 Jul**: it ran on schedule, exited clean, and logged `outcome: ok,
tokens: 0` every time. Two independent defects.

**The outage.** 5.1.0 dropped the `skills` array from the root
`.claude-plugin/plugin.json`, on the theory that skills auto-discover. They
only auto-discover at `<plugin-root>/skills/`, and reflect keeps its skills at
`<plugin-root>/plugin/skills/`. The plugin therefore registered **Skills (0)**
while its 13 hooks kept loading normally, so recall and enqueue looked healthy
while every `claude -p "/reflect ..."` the drain issued came back
`Unknown command: /reflect`. Restored the array (all 10 skills; the pre-5.1.0
array listed only 7 and predated the `reflect-status` -> `status` rename).

**The reason it hid for 11 days.** An unresolved command makes `claude -p`
exit 0 with no turns. The drain scored that `ok`, and `ok` drops the entry from
the queue, so each no-op run discarded its transcript unharvested.

A zero-turn run is now `fail_unknown_command`. It is treated as an
install-level fault rather than a transcript-level one: the drain aborts the
run and leaves the queue **fully intact**, with no per-entry retry bump. That
distinction matters. Charging the fault to each entry's retry budget would
archive the whole queue to `poison-reflections.jsonl` within 3 drains (roughly
40 minutes at the default debounce), and for `skill_refresh` entries, whose
retry key is a long-lived `SKILL.md` path that is never reset, it would kill
that skill's refresh permanently.

Both paths are covered by tests that drive the real drain against a stub
`claude` and fail against the old code, including a five-consecutive-drain
outage that must not consume the queue. No drain prompt or command form
changed: `/reflect` was correct all along.

Also aligns all four plugin manifests on one version, with a parity test.

## [5.2.0] — 2026-07-10 — Persistent model daemon (recall RAM fix)

Minor. Engine. Every `reflect search/embed/rerank` used to cold-boot torch
and load both neural models per process (~3.5 GB RSS, 10–30 s); session-start
recall fans several out at once across parallel sessions, OOM-ing 8 GB boxes.
Models now load once in an auto-spawned unix-socket daemon; CLI calls are
millisecond clients with a locked in-process fallback.

### Added

- `reflect_kb.model_daemon`: stdlib-only unix-socket daemon serving
  embed/rerank, auto-spawned on first use, one per (user, model pair, TMPDIR).
  Serial, spawn-race safe (spawn flock), exit unlink gated on socket-inode
  ownership, busy is never mistaken for dead.
- Env knobs: `REFLECT_NO_DAEMON=1` (always in-proc),
  `REFLECT_IDLE_TIMEOUT` (daemon idle-exit seconds, default 1800, 0 = never),
  `REFLECT_DAEMON_TIMEOUT` (client per-request seconds, default 120).
- Single-flight flock caps concurrent in-process model loads at one when the
  daemon is unavailable; released on load failure so a degraded process can't
  starve the box.

### Changed

- Warm recall: embed/rerank round-trip ~0.2 s vs 7.5 s cold; 4 parallel
  recalls share one ~0.2 GB-RSS daemon instead of 4 × 3.5 GB cold boots.
- nano-graphrag's async embedding path no longer blocks the event loop
  (socket/encode moved to a worker thread); daemon-served vectors are
  float32 for dtype parity with in-process encoding.
- Model-name defaults live in `model_daemon` as the single source shared by
  the daemon key, client guard, and both loaders.

No hook or skill interface changes. Upgrade the engine to activate:
`uv tool install --upgrade --torch-backend cpu
'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'` —
Claude Code plugin updates via the marketplace ref; Codex / Copilot users
re-run `python3 plugin/adapters/codex/codex_adapter.py install` /
`python3 plugin/adapters/copilot/copilot_adapter.py install` from the updated
checkout (hooks/skills unchanged this release, so re-running is optional).

## [5.1.1] — 2026-07-06 — CPU torch install guidance

Patch. Docs only. The `[graph]` extra pulls `sentence-transformers → torch`,
which defaults to the CUDA build (~5GB of `nvidia-*` wheels) and fails on small
disks with `No space left on device`. reflect embeds on the CPU, so every
documented install command now pins `--torch-backend cpu` (~1.5GB).

### Changed
- Install hints across `plugin/README.md`, the two "reflect CLI missing"
  messages in `reflect-drain-bg.sh`, and the `status`/`ingest` skill docs now
  use `uv tool install --torch-backend cpu …`; a GPU escape-hatch note is added.
- Dropped the obsolete `llvmlite`/`nano-graphrag --no-deps` workaround note —
  the reflect-kb dep floors (`llvmlite>=0.44`, `numba>=0.61`) make plain
  `[graph]` resolve on py3.11–3.14. No hook behavior changes (hint text only).

## [5.0.4] — 2026-06-28 — Drain watchdog and timeout unjam

Patch. Background drain could appear jammed when heavyweight `/reflect`
writer calls timed out or hit `max_turns`; stale harness-installed hook copies
could also keep retrying terminal entries after source was fixed.

### Fixed
- Timeout/no-output drain calls are quarantined after the configured timeout
  retry budget instead of staying at the head of the queue.
- `terminal_reason=max_turns` is treated as terminal partial progress and
  removed from the queue, preventing repeated re-spend on giant sessions.
- Retryable failures rotate to the queue tail so one bad transcript cannot
  block younger entries.
- Codex installs overwrite stale physical copies of the drain hook.

### Added
- `reflect-maintenance-watch.sh` and `com.reflect.maintenance.plist` surface
  stale ingest, stale drain locks, long-running drains, and missing launchd
  timers through `reflect errors`, which the statusline ERR row already reads.
- Structural tests for the maintenance launchd template and watchdog behavior.

## [5.0.3] — 2026-06-22 — Slash commands resolve (drop double `reflect:` prefix)

Patch. Manual reflect slash commands were unreachable: the statusline badge
and docs said `/reflect:errors-ack`, but the command registered as
`/reflect:reflect-errors-ack` → "Unknown command".

### Fixed
- Skill `name:` fields carried a redundant `reflect:` prefix (e.g.
  `name: reflect:errors-ack`). Claude namespaces plugin skills as
  `reflect:<name>`, so the prefix doubled to `reflect:reflect-errors-ack`.
  Dropped the prefix on all 7 affected skills (recall, ingest, consolidate,
  cost, errors-ack, export, slots) → they now resolve as the documented
  `/reflect:<name>` (matches the statusline badge). Verified via
  `claude plugin details` (no `reflect:reflect-*` remain).

## [5.0.2] — 2026-06-22 — Hooks register on whole-repo install; recall healed

Patch. After the standalone-repo restructure, **no reflect lifecycle hooks
fired** in real Claude sessions (recall, drain, mini-learning, reflect-enqueue,
pre-compact). Skills still loaded via auto-discovery, masking it.

### Fixed
- **Hooks not registered.** The marketplace installs reflect as a whole-repo
  clone, so `CLAUDE_PLUGIN_ROOT` resolves to the repo/version-root, and Claude
  reads hooks only from `<root>/.claude-plugin/plugin.json` — which didn't exist
  at the root (the hooks manifest was nested under `plugin/`). Added a root
  `.claude-plugin/plugin.json` declaring all skills + hooks with
  `${CLAUDE_PLUGIN_ROOT}/plugin/...` paths so the lifecycle hooks register and
  fire. Verified in real tmux sessions across 3 repos (all 5 hooks fire; recall
  injects matching learnings).
- **Recall starved by empty shards.** `resolve_kb_root` now falls back to the
  global KB when a per-project/branch shard has no docs/index (sharding design
  unchanged; `RECALL_GLOBAL` still an override).
- **Cold model-load timeout.** Recall hooks pin `HF_HUB_OFFLINE`/
  `TRANSFORMERS_OFFLINE` (models cached locally) and use a configurable
  `REFLECT_RECALL_TIMEOUT` (default 30s) so the ~16s cold load no longer blows
  the old 10s timeout and return empty.

## [5.0.1] — 2026-06-20 — Hook hardening: force `uv --script`

Patch. All lifecycle hooks now invoke `uv run --script` instead of `uv run`,
across the Claude (`.claude-plugin/plugin.json`), Codex (`codex-hooks.json`),
and Copilot (`copilot-hooks.json`) manifests.

### Fixed
- Hooks no longer fail with `No requires-python value found in the workspace` /
  exit 2 when the session's working directory contains a `pyproject.toml` that
  lacks `requires-python`. `uv run <script>` could drop into project/workspace
  mode and ignore the script's own PEP 723 metadata; `--script` forces uv to
  treat the file as a self-contained script every time, independent of cwd.

## [5.0.0] — 2026-06-20 — Standalone repo + tagged releases

Major bump marking the plugin's move to its own repository
(`stevengonsalvez/ainb-reflect-memory`) and the switch to **tagged, pinned
releases**. Functionally a continuation of 4.1.x — no runtime behavior change —
but the install lineage and source-of-truth changed, hence the major.

### Changed
- Plugin now lives in `stevengonsalvez/ainb-reflect-memory` (`plugin/` subdir),
  no longer under the `agents-in-a-box` monorepo.
- The `agents-in-a-box` marketplace entry for `reflect` redirects here via a
  GitHub source pinned to a release tag (`source.ref`), so existing
  `reflect@agents-in-a-box` installs keep working — they just resolve to the
  pinned tag instead of a stale local path.

### Added
- **Automated releases** (`.github/workflows/release.yml`): merging a
  `plugin.json` version bump to `main` auto-tags `vX.Y.Z`, cuts a GitHub
  Release, and propagates the new tag into the `agents-in-a-box`
  marketplace `reflect.source.ref` (direct push).

## [4.1.0] — 2026-06-17 — Recall upgrade (57 ports)

Minor bump for the recall-upgrade campaign (PR #248): **57 features ported** from
Hindsight, ByteRover, agentmemory, and claude-mem into the recall layer, each
shipped with a real-engine behavioral proof
(`reflect-kb/tests/eval/behavioral/proofs/`). Additive and backward-compatible —
every new arm/signal is gated by a `RECALL_*`/`REFLECT_*` env knob that defaults
off or to the pre-4.1 behavior, and the DB schema migration is additive
(`CREATE TABLE IF NOT EXISTS` on first connection). No existing 4.0 install
breaks; the retrieval improvements are live out of the box.

### Added — retrieval / inject
- **Graph-expansion arm** (R1, `RECALL_GRAPH_ARM`), **cross-encoder rerank** (R2,
  `RECALL_CROSS_ENCODER`, lazy `sentence-transformers` — degrades to the legacy
  formula if absent), **MMR diversity** (R3, `RECALL_MMR`), **token-budget
  retrieval** (R4, `REFLECT_RECALL_MAX_TOKENS`), **temporal arm + query-date
  parsing** (R5/R6, `RECALL_TEMPORAL`), **OOD relevance gate** (R7,
  `--min-overlap`), **bounded multiplicative boosts** (R8, `RECALL_*_ALPHA`),
  **fuzzy cache tier** (R9), **3-tier hierarchical inject + forced-grounding
  short-circuit** (R10/R11, `REFLECT_TIERED_INJECT`), **per-arm calibrated
  thresholds** (R12, `reflect calibrate-thresholds`), **auto-skill-refresh +
  per-skill staleness** (R13/R14), **per-project sharding** (R15), **project
  affinity** (R16), **skills index** (R20), **staged 3-layer recall** (M1).
- See **`docs/retrieval-features.md`** for each feature with a worked example and
  the counterfactual.

### Added — storage / signals / consolidation / open-domain
- Storage: structured drain fields (S1), typed causal links (S2), numeric
  confidence (S3), provenance/proof-count (S4), belief revision (S5), history
  snapshots (S6), chunk-hash dedup (S7), doc→chunk→learning grouping (S8),
  volatile-signals sidecar (S9), write-validate-retry (S10).
- Signals: cross-turn contradiction (SG1), git-event capture (SG2, post-commit
  hook), idle trigger (SG3, launchd), test-outcome (SG4), tool-loop detect
  (SG5), knowledge-gap (SG6), todo-completion (SG7), permission capture (SG8).
- claude-mem: writer breaker (M2), quota-aware abort (M3), pluggable modes (M4),
  commit verification (M5), privacy stripping (M6), corpus Q&A (M7,
  `/reflect:corpus`), token economics (M8).
- agentmemory: pinned slots (A1), bitemporal edges (A2), per-row TTL (A3),
  followup-rate diagnostic (A4), synthetic no-LLM compression (A5), branch-aware
  isolation (A6).
- Open-domain: observations layer (O1), conventions doc (O2), persona fields
  (O3). Consolidation: semantic dedup (C1), auto-consolidation (C2), graph
  maintenance (C3), lifecycle events (C4), KB export/import (C5).

### Infrastructure
- **DB schema**: 14 new `reflect.db` tables (auto-migrated, additive).
- **New `reflect.db` is migrated transparently** on first connection — no manual
  migration step for existing installs.
- **Pre-merge review fixes**: depth-aware `<private>` stripping (M6 nested-tag
  leak), corpus date-filter validation (M7), R14 staleness applied to the inject
  tier, `validate_sidecar` degrades as a library instead of `sys.exit`.

### Activation notes (features that need a one-time wiring step)
- **SG2 git-event capture** needs the post-commit hook installed per repo (see
  Install below). **SG3 idle** and the other launchd timers load via their
  `launchd/*.plist` INSTALL blocks. **S8** document-grouping persists once the
  drain calls the chunk-record step.

## [4.0.0] — 2026-05-31 — Cost rearchitecture

Major bump for the drain cost rearchitecture (W1–W5). Triggered by an incident:
a single background `/reflect` drain run burned **41.5M tokens in 9.6 min
(~$713)** for **zero net-new learnings** — it handed a 123K-token transcript to
an Opus agent that roamed for 223 turns, the same transcript was reflected 16×,
and the daily cap was blown 20→61. A 30-day backfill showed reflect was burning
~1.2B tokens / 446 runs / ~$7k, almost all on Opus.

> Note: `4.0.0` was previously sketched for the "universal cross-harness
> install" effort (see `docs/design-records/2026-04-23-v4-universal-install-spec.md`);
> that effort now targets **5.0.0**.

### Added
- **`reflect cost` CLI** (`scripts/reflect_cost.py`) — drain spend by
  day/transcript/model/outcome with the cached-vs-uncached token split,
  outlier flagging, and an approximate per-model $ estimate.
- **`/reflect:cost` sub-skill** (`skills/cost/`) — slash-command wrapper over
  the cost reporter: parses a window (default 1 day), renders the
  cached/uncached/io split by outcome+model+transcript, and falls back to the
  backfill when historical events predate the v4 token envelope.
- **`scripts/backfill_costs.py`** — reconstruct the cost timeline from existing
  `~/.claude/projects` logs into a separate `drain-cost-backfill.jsonl`.
- **Enqueue skip-gate + dedup** (`scripts/reflect_gate.py`) — $0 regex over a
  transcript's dialogue: skips reflect-on-reflect / no-signal / clean sessions
  and anything already queued or processed.
- **Cascade** (`scripts/reflect_cascade.py`) — gate + slice the transcript to
  just signal-bearing windows (~10× smaller) before `/reflect`.
- **graphml self-heal** (`scripts/graphml_repair.py`) — validate + repair the
  doubled-close-tag corruption that caused the incident's rabbit hole; run
  before reindex.
- **`scripts/regate_backlog.py`** — re-gate the existing pending queue (a real
  dry-run collapsed 114 entries → 13).
- **Weekly Opus synthesis** (`scripts/reflect_synthesis.py`) + **launchd
  templates** (`launchd/com.reflect.{drain,synthesis}.plist`).
- **Cost-event envelope** — `drain-cost.jsonl` now records tokens, cost, turns,
  model, and the input/output/cache_read/cache_creation split per run.

### Changed
- **Default drain model is now `sonnet`** (was Opus / unset) via
  `REFLECT_DRAIN_MODEL`. Opus is reserved for rare escalation + weekly synthesis.
- **Hard caps tightened**: `--max-turns` 25 → **8**, per-entry timeout 600s →
  **180s**, plus a **post-hoc token-budget poison** (`REFLECT_DRAIN_TOKEN_MAX`,
  default 2M) so a completed-but-expensive run can never be retried.
- **Atomic `mkdir` lock** replaces the check-then-write PID file (the race that
  let concurrent spawns each pass the daily cap). Daily cap now **sums the
  `entries` field** so $0 skips never consume budget.
- **Debounce** (`REFLECT_DRAIN_DEBOUNCE_SEC`, default 600) collapses a burst of
  session starts to one drain.
- **Neutral cwd** (`REFLECT_DRAIN_CWD`, default `$HOME`) — the drain no longer
  runs `/reflect` inside whatever repo triggered it.
- Producers (`precompact_reflect.py`, `stop_reflect.py`) run the gate before
  enqueue.
- `DRY_RUN` is now side-effect-free (no longer triggers a real reindex).

### Removed / Retired
- **SessionStart surfacer** (`sessionstart_drain_reflections.py`) retired to a
  no-op. It was never wired in `plugin.json`; the background drainer is the sole
  queue consumer (ends the dual-consumer queue pollution).

### Added (env vars)
`REFLECT_DRAIN_TIMEOUT` · `REFLECT_DRAIN_TOKEN_MAX` · `REFLECT_DRAIN_MODEL` ·
`REFLECT_DRAIN_DEBOUNCE_SEC` · `REFLECT_DRAIN_CWD` · `REFLECT_DRAIN_CASCADE` ·
`REFLECT_DISABLED` (kill switch) · `REFLECT_DRAIN_SKIP_REINDEX` (tests).

### Deferred
- Full sqlite-queue migration — W2 dedup (path + processed-set) and W4
  signal-hash already deliver idempotency in practice; the sqlite rewrite is
  robustness polish at the highest migration risk, tracked as a follow-up.

## [3.6.0] and earlier

See `docs/design-records/` for frozen plans (v3.2 single-PR, v4 universal
install) that informed earlier evolution.
