# Fleet/Hermes Adapter Implementation Plan

## Overview
Port fleet-lambda's memory surface into ainb-reflect-memory: a hermes adapter with hook-driven shadow recall + capture, a `reflect fleet ingest` importer with quarantine, domain-aware recall ranking, and the regression guards that protect the claude/codex adapters. Spec: `docs/design/fleet-hermes-adapter-spec.md`.

## Current State Analysis
- Adapter contract: `AdapterBase` (`plugin/adapters/base.py:281`); codex adapter (`plugin/adapters/codex/codex_adapter.py`) is the physical-deploy model to mirror (no plugin runtime, `_sync_dir` deploy at :506, hook merge at :525). No hermes adapter exists.
- Recall: `plugin/skills/recall/scripts/recall.py`; `--format {markdown,json}` at :3110; final score = `formula()` at :1800-1815 (bounded multiplicative boosts); project-affinity boost (`project_norm` :1927) is the copy template for a domain boost. Recency silently degrades for tz-aware timestamps (`recency_norm` :1962, subtraction :1972-1973, TypeError swallowed :1974 → neutral 0.5).
- Write path: `learnings_cli.py add()` :378 writes docs with content-aware filenames (`generate_document_id` :72 = slug + sha256[:6]); no occurrence counting, no supersedes; subgroups register at :917-922. Bulk ingest must be write-all-then-one-`reindex` (:500) — per-doc `add` fragments graph communities (:554).
- Frontmatter read side understands only `id/title/category/tags/project/created/confidence` (`corpus.py:_parse_frontmatter` :211, `CorpusFilter.matches` :143; `learnings_cli.py` :56). New fields (`domain`, `quarantine`, `authority`, `source_system`, `content_hash`, `supersedes`) parsed nowhere.
- Dedupe template: `src/reflect_kb/issues/dedupe.py` ledger (atomic tmp+replace :194, `record_filed` :164) — port for occurrence counting, but key on sha256(title+body), NOT title-slug (:103 is title-only). Read-modify-write needs the `errors.py:48` flock pattern; atomic replace alone lost-updates increments.
- Tests: `behavioral_kb` fixture (`tests/eval/behavioral/conftest.py:127-267`); A6 shard-bypass template for isolation proofs; M1 for byte-measured invariants; golden set exists (`tests/eval/fixtures/golden_queries.yaml`, 20 queries, 4 classes) with metrics in `harness.py:59-301` — but NO snapshot diff/gate exists, and NO eval/proof runs in CI (`ci.yml:80` ignores tests/eval).
- Telemetry: `write_metric()` (`src/reflect_kb/metrics.py:40`) → `~/.learnings/metrics.jsonl`, best-effort, rotated. `metrics_stats._bucket` :100 aggregates only `op=="recall"`.
- Hook envelope: `user_prompt_submit_recall.py` emit gated on `REFLECT_HARNESS` (:528); copilot precedent (:516) shows a harness can ignore hook stdout — hermes shadow mode sidesteps this v1 (emits nothing).

## Desired End State
- `reflect fleet ingest --root ~/.clan/learnings` imports patterns/discoveries/corrections as quarantined, typed, deduped learnings; re-run is idempotent.
- `reflect recall "query" --domain-hint personal --format fleet-context` returns authority-labeled injection block ≤2000 tokens; quarantined docs excluded from claude/codex-scope recall.
- `plugin/adapters/hermes/` installs shim scripts a fleet-lambda hook can call; shadow mode logs recall telemetry, injects nothing.
- Behavioral proofs F1–F3 pass; golden snapshot diff gate exists and runs in CI; recency tz bug fixed with regression test.

### Key Discoveries:
- Content-hash filenames give free idempotency — importer rides `generate_document_id` (`learnings_cli.py:72`).
- Domain boost is a one-term addition at `recall.py:1815` + threading through `recall()` :2692 and both rerank call sites (:2812, :2964); alphas at :113-135; declarative knob in `plugin/reflect.toml [recall.boost]` (line 64).
- BANK parity numbers to encode: TOP_K=5, MIN_SCORE_NORM=0.5, MAX_TOKENS_APPROX=2000 (fleet-lambda bank_lookup.py).
- Non-TTY `click.confirm` silently aborts on id-collision (prior learning): importer must pass `--force`-equivalent internally and fail loudly, never prompt.

## What We're NOT Doing
- fleet-lambda side changes (bank_lookup shim PR, FLEET_MEMORY_BACKEND switch) — separate repo, after this ships.
- Convex sync of any kind.
- Journal/HOT-memory/skill-doc import (importer v1 = patterns, discoveries, corrections; others stretch).
- Semantic near-dup merge, H1/H2 skill signals, promotion writes (Fleet-owned).
- Live hermes fleet enablement — this repo only ships the pieces; whole-fleet shadow rollout is an ops step after merge.

## Implementation Approach
Wave 1 builds the two independent foundations (recall hardening; importer). Wave 2 threads the new frontmatter through recall. Wave 3 ships the adapter and the regression guards in parallel. Build agents run on opus; each phase is verified by a sonnet agent running the phase's success criteria before the next dependent wave starts.

## Phase 1: Recall hardening — tz-aware recency fix
<!-- wave: 1 | depends_on: [] | files: [plugin/skills/recall/scripts/recall.py, src/reflect_kb/recall/recall.py, tests/test_recency_norm.py] -->

### Overview
Fix silent recency degradation: tz-aware `archived_at` (`+00:00` offsets) currently raises TypeError inside `recency_norm` and falls back to neutral 0.5, killing recency ranking for those docs.

### Changes Required:
#### 1. recency_norm normalization
**File**: `plugin/skills/recall/scripts/recall.py` (:1962-1979) and, if present, the mirrored `src/reflect_kb/recall/recall.py`
**Changes**: after `fromisoformat`, strip tzinfo (convert to UTC then `replace(tzinfo=None)`), mirroring `_coerce_datetime` (:1211). Keep the except-fallback for genuinely malformed strings.

#### 2. Regression test
**File**: `tests/test_recency_norm.py` (new)
**Changes**: unit tests: naive ts, `Z`-suffixed, `+00:00`-offset, `+05:30`-offset, malformed → assert offset-aware inputs produce real recency (recent≈1.0, 90d-old <0.5), malformed → 0.5. Import via the same path-insert convention existing unit tests use.

### Success Criteria:
#### Automated Verification:
- [ ] `uv run pytest -q tests/test_recency_norm.py`
- [ ] `uv run pytest -q tests --ignore=tests/eval` (no regressions)
- [ ] `grep -n "tzinfo" plugin/skills/recall/scripts/recall.py` shows normalization in recency_norm
#### Manual Verification:
- [ ] none

---

## Phase 2: Fleet importer with quarantine + occurrence ledger
<!-- wave: 1 | depends_on: [] | files: [src/reflect_kb/fleet/__init__.py, src/reflect_kb/fleet/importer.py, src/reflect_kb/fleet/ledger.py, src/reflect_kb/cli/fleet_cli.py, src/reflect_kb/cli/learnings_cli.py, tests/test_fleet_importer.py] -->

### Overview
`reflect fleet ingest`: import fleet-lambda JSONL artifacts (patterns.jsonl, discoveries.jsonl + archive, corrections ledgers) as markdown learnings with typed frontmatter, quarantine=true, content_hash dedupe, occurrence counting, and supersedes support. Write files then trigger ONE reindex.

### Changes Required:
#### 1. Importer core
**File**: `src/reflect_kb/fleet/importer.py` (new)
**Changes**: readers for patterns/discoveries/corrections JSONL shapes (tolerant, per-line try/except, report skipped); each entry → md doc via the add-path conventions (`generate_document_id`); frontmatter: `title, category, key_insight, tags, source_system: fleet, source_kind, source_path, content_hash (sha256 title+body), authority: advisory, domain (heuristic from tags/agent, default coding), quarantine: true, workflow_state, supersedes (optional)`. Discoveries-archive → `status: archived`. Entity sidecar via existing `auto_extract_entities`. Never `click.confirm` — internal force + loud non-zero exit on failure (non-TTY safe).
#### 2. Occurrence ledger
**File**: `src/reflect_kb/fleet/ledger.py` (new)
**Changes**: port issues ledger shape (`issues/dedupe.py:135-203`): `~/.reflect/fleet-ledger.json` mapping `content_hash → {doc_id, count, first_seen, last_seen}`; atomic tmp+replace AND `fcntl.flock` around read-modify-write (errors.py:48 pattern); repeat hash → increment count, bump `occurrences` frontmatter in doc, emit `promotion_candidate` metric at count ≥3 via `write_metric`.
#### 3. CLI group
**File**: `src/reflect_kb/cli/fleet_cli.py` (new); register in `src/reflect_kb/cli/learnings_cli.py` (:917-922)
**Changes**: `reflect fleet ingest --root PATH [--kinds patterns,discoveries,corrections] [--dry-run] [--no-reindex]`; summary table (imported/deduped/skipped/errors); calls `reindex` once at end unless `--no-reindex`. `reflect fleet status` prints ledger stats.
#### 4. Tests
**File**: `tests/test_fleet_importer.py` (new)
**Changes**: tmp KB + fixture JSONL: import → files exist with full frontmatter; re-import → zero new files, counts increment; 3rd occurrence → promotion_candidate metric line; malformed line skipped with count; discoveries-archive gets archived status.

### Success Criteria:
#### Automated Verification:
- [ ] `uv run pytest -q tests/test_fleet_importer.py`
- [ ] `uv run reflect fleet ingest --root tests/fixtures/... --dry-run` exits 0 with summary (use test fixture dir)
- [ ] `uv run pytest -q tests --ignore=tests/eval`
#### Manual Verification:
- [ ] none

---

## Phase 3: Domain-aware + quarantine-aware recall, fleet-context format
<!-- wave: 2 | depends_on: [1, 2] | files: [plugin/skills/recall/scripts/recall.py, src/reflect_kb/recall/corpus.py, plugin/reflect.toml] -->

### Overview
Recall understands the new frontmatter: quarantined docs excluded unless `--include-quarantined`; `--domain-hint` soft boost; `--format fleet-context` renderer emitting authority-labeled sections under BANK-parity budget.

### Changes Required:
#### 1. Frontmatter parsing
**File**: `src/reflect_kb/recall/corpus.py` (`_parse_frontmatter` :211, `_entry_from_doc` :239, `CorpusFilter` :118-164) and the doc-loading path in `plugin/skills/recall/scripts/recall.py`
**Changes**: parse `domain, quarantine, authority, source_system, occurrences`; `CorpusFilter` gains `include_quarantined: bool = False` (default excludes quarantine=true).
#### 2. Domain boost
**File**: `plugin/skills/recall/scripts/recall.py`
**Changes**: `DOMAIN_ALPHA = _env_alpha("RECALL_DOMAIN_ALPHA", 0.2)` near :113-135; `domain_norm(hint, lrn)` mirroring `project_norm` (:1927); add `bounded_boost(domain_norm(...), DOMAIN_ALPHA)` to `formula()` at :1815; thread `domain_hint` through `rerank_with_scores` (:1772), `rerank` (:1735), `recall()` (:2692), both call sites (:2812, :2964); argparse `--domain-hint` + `--include-quarantined` near :3139. Authority weight: map `authority` to a small bounded boost (law/promoted > advisory > archived) in the same formula.
#### 3. fleet-context renderer
**File**: `plugin/skills/recall/scripts/recall.py`
**Changes**: `--format fleet-context` → `render_fleet_context()` next to `render_markdown` (:2310): header `## Reflect Recall (fleet memory, advisory)`, sections by authority label, per-item source path + score, hard cap: top-5 items, ≤2000 tokens via `_est_tokens` (:366), contract comment `fleet-context/v1`.
#### 4. Config
**File**: `plugin/reflect.toml`
**Changes**: `[recall.boost] domain_affinity_alpha = 0.2`; `[providers.hermes]` block (home_dir `~/.hermes`); add `"hermes"` to `[discovery].enabled_providers`.

### Success Criteria:
#### Automated Verification:
- [ ] `uv run pytest -q tests --ignore=tests/eval`
- [ ] recall CLI: `--domain-hint x --format fleet-context` on a seeded tmp KB returns v1-labeled block; quarantined doc absent by default, present with `--include-quarantined` (scripted in a new unit test or verified via Phase 5 proofs)
- [ ] `python -c` tomllib parse of plugin/reflect.toml succeeds
#### Manual Verification:
- [ ] none

---

## Phase 4: Hermes adapter + shadow shim + telemetry
<!-- wave: 3 | depends_on: [3] | files: [plugin/adapters/hermes/hermes_adapter.py, plugin/adapters/hermes/shim/pre_llm_recall.py, plugin/adapters/hermes/shim/post_llm_capture.py, plugin/adapters/tests/test_hermes_adapter.py, src/reflect_kb/metrics_stats.py] -->

### Overview
`plugin/adapters/hermes/`: AdapterBase subclass (codex pattern) deploying skills + shim scripts into `~/.hermes/`; shim scripts are what a fleet-lambda hook calls. Shadow mode default: compute recall, log telemetry, emit NOTHING.

### Changes Required:
#### 1. Adapter
**File**: `plugin/adapters/hermes/hermes_adapter.py` (new)
**Changes**: subclass `AdapterBase` (base.py:281): `HARNESS_DIR=".hermes"`, `HARNESS_LABEL="Hermes"`, unique `POINTER_MANAGED_BY`; `augment_plan`/`execute_extra` deploy PLUGIN_SKILLS content, `reflect.toml`, and `shim/` scripts via `_sync_dir` (codex_adapter.py:506 pattern) into `~/.hermes/skills/reflect/`; no hooks.json merge (fleet-lambda owns hook wiring); module-level wrappers + `run_cli` main like codex (:428-466). `--dry-run` supported via base.
#### 2. Shadow recall shim
**File**: `plugin/adapters/hermes/shim/pre_llm_recall.py` (new)
**Changes**: stdin JSON `{prompt, agent_id?, domain_hint?, session_id?}`; mode from `FLEET_MEMORY_BACKEND` env (`bank`→exit 0 immediately; `shadow` default → run recall subprocess `--format fleet-context --domain-hint … --include-quarantined --max-tokens 2000 --limit 5`, `write_metric("fleet_shadow_recall", hits=…, tokens=…, latency_ms=…, agent=…)`, print NOTHING; `reflect`→print the block to stdout). Hard `REFLECT_FLEET_TIMEOUT` (default 10s) wall clock; ANY exception → exit 0 silent + error breadcrumb (test_hooks_silent_fail contract). Sets `REFLECT_HARNESS=hermes` for child.
#### 3. Capture shim
**File**: `plugin/adapters/hermes/shim/post_llm_capture.py` (new)
**Changes**: stdin JSON `{transcript_tail | last_user_msg + last_assistant_msg, session_id, agent_id}`; enqueue to `~/.reflect/pending_reflections.jsonl` (same shape stop_reflect.py writes) for the bg-drain to process; correction-signal cheap heuristic (trigger words) only sets a priority field — classification stays in /reflect. Exit 0 always.
#### 4. Telemetry aggregation
**File**: `src/reflect_kb/metrics_stats.py`
**Changes**: `_bucket` (:100) gains `fleet_shadow_recall` branch: count, avg hits, avg latency, token histogram — surfaces in `reflect metrics stats`.
#### 5. Adapter tests
**File**: `plugin/adapters/tests/test_hermes_adapter.py` (new)
**Changes**: mirror test_codex_adapter structural + `--dry-run --home tmp` tests; shim tests via subprocess (test_hooks_silent_fail.py:39-108 pattern): shadow mode emits nothing + writes metric; bank mode exits instantly; broken stdin → exit 0; timeout honored.

### Success Criteria:
#### Automated Verification:
- [ ] `uv run pytest -q plugin/adapters/tests/test_hermes_adapter.py`
- [ ] `uv run pytest -q plugin/tests plugin/adapters/tests` (no adapter regressions; registry-parity test untouched — hermes deliberately absent from hooks registry, wiring is fleet-lambda's)
- [ ] `python plugin/adapters/hermes/hermes_adapter.py install --dry-run --home /tmp/x` reports plan, touches nothing
#### Manual Verification:
- [ ] none (live hermes wiring is fleet-lambda work, out of scope)

---

## Phase 5: Regression guards — proofs F1–F3 + golden snapshot diff + CI
<!-- wave: 3 | depends_on: [2, 3] | files: [tests/eval/behavioral/proofs/proof_F1_fleet_ingest_isolation.py, tests/eval/behavioral/proofs/proof_F2_domain_boost_ranking.py, tests/eval/behavioral/proofs/proof_F3_quarantine_enforced.py, tests/eval/snapshot_diff.py, .github/workflows/ci.yml] -->

### Overview
The claude/codex protection layer: three behavioral proofs + the missing baseline-vs-current diff gate + first CI wiring for the deterministic subset.

### Changes Required:
#### 1. Proofs (behavioral_kb fixture; A6 template for isolation, M1 for measured invariants)
**Files**: `tests/eval/behavioral/proofs/proof_F1_fleet_ingest_isolation.py`, `proof_F2_domain_boost_ranking.py`, `proof_F3_quarantine_enforced.py` (new)
**Changes**: F1 — seed KB, snapshot recall top-5 for fixed queries, run fleet ingest of fixture JSONL (quarantined), assert identical top-5 (quarantine isolates). F2 — two docs equal but domain coding vs personal; `--domain-hint personal` ranks personal first; no hint → tie broken by base score only. F3 — quarantined doc never in default recall ids, present with `--include-quarantined`; byte-check fleet-context output ≤2000 est tokens, ≤5 items.
#### 2. Snapshot diff gate
**File**: `tests/eval/snapshot_diff.py` (new)
**Changes**: run harness metrics on golden_queries.yaml against a seeded KB, compare to `results/baseline.json`: fail if R@5 drops >0.05 or any exact-class query loses a relevant hit from top-5; `--update-baseline` flag regenerates. Runnable standalone + as pytest.
#### 3. CI wiring
**File**: `.github/workflows/ci.yml`
**Changes**: new job `fleet-guards` (non-blocking `continue-on-error: true` first iteration): slim install + run proofs F1–F3 and unit tests for fleet/recall additions. Heavy [graph]+model+qmd golden-diff job documented but gated behind `workflow_dispatch` (first heavy job — don't block PRs on a 420MB model download yet).

### Success Criteria:
#### Automated Verification:
- [ ] `uv run pytest -q tests/eval/behavioral/proofs/proof_F1* proof_F2* proof_F3*` (via run_proofs.py or direct pytest, env permitting; skipif honored on slim env)
- [ ] `uv run python tests/eval/snapshot_diff.py --help` exits 0
- [ ] `ci.yml` parses (actionlint or yaml load)
#### Manual Verification:
- [ ] Review CI job output on the PR run

---

## Testing Strategy
- Unit: recency tz matrix, importer idempotency/ledger/flock, renderer budget caps.
- Behavioral: F1–F3 proofs (real engine, hermetic KB, deterministic).
- Regression: full existing suite `pytest tests --ignore=tests/eval` green at every phase; adapter suite green; snapshot diff vs baseline.
- Verification agents (sonnet) run each phase's Automated Verification list + spot-read the diff; failures bounce back to the opus builder.

## Performance Considerations
Shadow shim: subprocess recall per turn — measure latency in telemetry (p95 target <150ms is a gate for the LATER fleet flip, not this repo's CI). Importer: one reindex per run, not per doc.

## Migration Notes
Importer is read-only toward fleet-lambda files; quarantine keeps all imported content out of existing recall until explicitly lifted (future `reflect fleet unquarantine --kind patterns` — out of scope v1).

## References
- Spec: `docs/design/fleet-hermes-adapter-spec.md`
- Explorer findings: adapter (`plugin/adapters/base.py:281`, codex_adapter.py:506,525), recall (`recall.py:1800-1815, 1962-1979, 2692, 3110`), ingest (`learnings_cli.py:72,378,917`, `issues/dedupe.py:135-203`, `errors.py:48`), tests (`conftest.py:127-267`, `harness.py:59-301`, `ci.yml:80`)
- BANK parity constants: fleet-lambda `bank_lookup.py` TOP_K=5, MIN_SCORE_NORM=0.5, MAX_TOKENS_APPROX=2000
