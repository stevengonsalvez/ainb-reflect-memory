# Specification: Fleet/Hermes Adapter — Reflect Memory Parity

**Generated from:** Discussion #14 + issue #6 (+comment 4868867674) + fleet-lambda code verification (f/default-cx33 @ 45730a5)
**Interview date:** 2026-07-02 (user AFK — recommended options adopted, marked ASSUMED)
**Version:** 1.0

## Executive Summary

Port fleet-lambda's memory surface (BANK retrieval, discoveries, corrections, HOT memory, journals) into ainb-reflect-memory as a first-class **hermes adapter** with parity to the claude/codex/copilot adapters. Everything works through hooks — hooks store AND retrieve; no instruction-only flows. Recall becomes personal-assistant-aware: fleet/non-coding knowledge is tagged by domain and source, ranked with soft boosts from a query-time domain hint, never hard-filtered.

## Decisions (ASSUMED — Stevie veto window open)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | Adapter home | `plugin/adapters/hermes/` in reflect repo; fleet-hooks shrinks to thin shim calling `reflect` CLI | Prior rule: never bake fleet customizations into runtime repos; matches claude/codex adapter layout |
| D2 | Cutover | Shadow → flip via `FLEET_MEMORY_BACKEND=bank\|reflect\|shadow` | BANK indexer unversioned; telemetry doesn't exist yet — shadow mode creates it |
| D3 | Domain model | One index + domain tags, soft rank boost, no hard filter | Shards kill cross-domain graph edges (person↔repo↔meeting); personal assistant needs those |
| D4 | Corrections | Dual: reflect hook captures durable learning; fleet detector keeps governance debt; dedupe by content_hash | Issue #6 acceptance test 7 forbids moving detector into Reflect |

## Objectives

### Primary Goals
- Hermes sessions get hook-driven memory capture (corrections, mini-learnings, session distillation) identical in kind to claude adapter.
- Hermes sessions get hook-driven recall injection replacing BANK, with authority labels, after Fleet law.
- Non-coding (personal-assistant) knowledge is first-class: tagged, ranked, recallable with domain awareness.
- Historical fleet artifacts imported with typed metadata (severity, lifecycle, authority, supersession).

### Success Metrics
- Parity gate: hit@5 ≥ BANK on committed golden set, ≤2000 injected tokens (BANK's real budget: TOP_K=5, MIN_SCORE_NORM=0.5, MAX_TOKENS_APPROX=2000), p95 shim latency <150ms.
- Zero governance regressions: law-before-memory ordering test green; reflect failure exits 0.
- Every imported item carries source_path, source_kind, content_hash, source timestamp, authority label.

## Scope

### In Scope
- `plugin/adapters/hermes/` (recall hook, capture hook, config)
- `reflect fleet ingest` importer (patterns, discoveries+archive, corrections ledgers, MEMORY.md snapshots, journals)
- Domain/source tagging schema + ranking boost in recall
- Shadow-mode telemetry (recall_telemetry → reflect.db + metrics.jsonl)
- Replay/parity harness + golden set
- fleet-lambda side: thin shim PR (bank_lookup → reflect subprocess), backend switch env

### Out of Scope (deferred)
- Convex knowledge tables beyond sync-state + events (greenfield on both sides — verified nothing calls /api/knowledge/sync today)
- Skill-proposal subsystem
- Moving correction detector / inbox / ACP metrics into Reflect (forbidden by issue #6)
- Semantic near-dup merge (v2; v1 = content_hash + supersedes frontmatter)

## Technical Requirements

### Hook parity matrix (claude adapter → hermes adapter)

Hermes exposes only two plugin hook points (`pre_llm_call`, `post_llm_call` — verified in fleet-hooks plugin.yaml). Adapter multiplexes:

| Claude adapter hook | Purpose | Hermes equivalent | Mechanism |
|---|---|---|---|
| user_prompt_submit_recall | recall injection | `pre_llm_call` (every turn) | staged recall, first-turn full + subsequent delta; domain hint from agent profile/inbox kind |
| posttooluse_minilearning | capture tool-failure lessons | `post_llm_call` | scan last turn for failure/correction signals |
| stop_reflect / session_end_reflect | session distillation | **no hook point** | reflect-drain-bg daemon tails Hermes transcript/journal files (same pattern as codex adapter) — OPEN Q1 |
| precompact_reflect | pre-compaction preservation | **no hook point** | drain daemon; Hermes compaction cadence TBD — OPEN Q1 |
| subagent_start_recall | scoped recall for subagents | Hermes agent spawn | pass agent_id → domain hint; v2 |

### Storage & tagging schema

Canonical root `~/.learnings` (override `GLOBAL_LEARNINGS_PATH`). Four legacy roots become import sources only: `~/.clan/learnings/`, `~/.hermes/self-improving/`, `~/.claude/global-learnings/`, repo `.clan/`.

Frontmatter additions (all fleet-ingested + hermes-captured docs):

```yaml
source_system: fleet | hermes | claude | codex
source_kind: pattern | discovery | correction | journal | hot_memory | skill
source_path: ~/.clan/learnings/patterns.jsonl
content_hash: sha256:...
supersedes: lrn-fleet-...        # optional
authority: advisory | debt | candidate | promoted | law
domain: coding | ops | comms | personal | governance
agent_id: motoko                  # capturing agent, optional
workflow_state: open | triaged | promoted | archived
```

### Recall ranking (personal-assistant aware)

```
score = hybrid(bm25, vector, graph)
      × domain_boost      (1.3 same-domain as query hint, 1.0 otherwise)
      × authority_weight  (law/promoted > advisory > archived)
      × recency_arm       (freshness FEATURE for discoveries — replaces
                           discovery_context last-20 first-turn blast)
```

- Domain hint sources, in priority order: explicit `--domain-hint`, Hermes agent profile, active inbox item kind, entity overlap fallback.
- Soft boost only — a strongly relevant coding memory still surfaces in a personal query and vice versa.
- Hard filters reserved for privacy: `domain: personal` excluded from team-KB export/shards and Convex payloads by default.
- Injection block format: authority-labeled sections (advisory memory / correction evidence / promotion candidates / graph neighbors), injected AFTER Fleet law, ≤2000 tokens.

### Corrections flow (D4)

```
user corrects Hermes agent
   │
   ├─▶ hermes adapter post_llm_call ─▶ durable learning (recallable, authority=advisory)
   └─▶ fleet correction_detector    ─▶ ~/.hermes/self-improving/pending-corrections.jsonl
                                        (debt: triage, 3-strike HOT promotion — unchanged)
merge: content_hash → one canonical doc, two provenance records
recall of a correction NEVER satisfies/closes debt (issue #6 invariant)
```

### Integrations
- fleet-lambda: one PR — bank_lookup gains `FLEET_MEMORY_BACKEND` branch (bank | shadow | reflect); shim calls `reflect recall --format fleet-context/v1` (versioned contract).
- Convex: unchanged except `knowledgeSyncState` heartbeat + recall telemetry via existing `/api/events`. All knowledge tables deferred.

### Performance / reliability
- p95 shim latency <150ms (subprocess spawn included; if breached, D1 escalates to in-process contract module — the "Both" option).
- Reflect failure → exit 0, empty context, heartbeat event flags degradation (fail-open must not be fail-silent).
- UTC-aware timestamps normalized at ingest (live tz crash in recall.py rerank is the precedent).

### Security
- Redact journals/corrections before index + before any Convex event (existing redaction path; extend deny-list to env-like keys).
- Imported fleet text treated as untrusted data: strip instruction-shaped headers, cap authority of imported items at `advisory` unless Fleet promotion gate raises it.

## Risks & Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| BANK baseline unversioned (indexer outside git) | parity gate against moving target | High | Phase 0 vendors snapshot of ~/.clan/learnings/scripts/bank/ into replay harness |
| Golden set can't be mined (no injection ledger exists) | gate delayed | Certain | Shadow mode IS the telemetry; curate golden set from 1–2 weeks of shadow logs |
| Subprocess-per-turn latency | hook budget blown | Medium | measure in shadow; escalate to in-process contract if >150ms p95 |
| Hermes lacks session-end/compact hooks | capture gaps | Medium | drain daemon on transcripts (proven pattern, codex adapter); OPEN Q1 |
| Domain mis-tagging pollutes personal recall | wrong memories surface | Medium | soft boost limits damage; `reflect fleet retag` CLI + telemetry review |
| Dual correction capture duplicates | noisy KB | Medium | content_hash dedupe + provenance merge; weekly consolidate pass |

## Implementation Phases (priority order)

0. **Groundwork**: fix recall tz crash + CI smoke test; canonical-root decision recorded; vendor BANK indexer snapshot; define parity numbers in repo.
1. **Hermes adapter skeleton**: pre_llm_call recall in SHADOW mode + post_llm_call capture; fleet-lambda shim PR with backend switch (default `bank`).
2. **Importer**: `reflect fleet ingest` for patterns → discoveries(+archive, status=archived) → corrections ledgers → MEMORY.md snapshot → journals. Typed metadata, dedupe, supersedes. PLUS (from wololo coverage map): occurrence counter on content_hash emitting `promotion_candidate` (1b), `anti_pattern` kind (5b), `skill_signal` H3 detection from correction occurrence data (6b), multi-writer-safe ingest (10).
3. **Domain-aware recall**: tagging schema, domain boost, authority weights, freshness arm; retire discovery first-turn blast in reflect path. PLUS: typed-edge parity audit vs wololo graph (owns/depends_on/caused/supersedes ownership+dependency queries), per-kind decay half-life tuning (30d discoveries / evergreen exempt), anti-pattern surfacing in injection block.
4. **Parity + flip**: golden set from shadow logs; replay harness; flip `FLEET_MEMORY_BACKEND=reflect` on one profile; BANK rollback retained.
5. **Telemetry surfacing**: knowledgeSyncState heartbeat + /api/events recall telemetry.
6. **Retire BANK** after parity, law-ordering, rollback tests green across all profiles.

## Wololo Agent-Intelligence Coverage Map

Source: getwololo.dev/docs/agent-intelligence + 8 deep dives (reflection-loop, knowledge-graph, journals, gossip, research-loop, skills-evolution, graphrag, distributed-knowledge). Verdict per capability: **IN** = Reflect owns, **OUT** = stays Fleet, **SPLIT** = Reflect stores/signals, Fleet decides/acts.

| # | Wololo capability | Verdict | Reflect today | Action |
|---|---|---|---|---|
| 1a | Correction capture (log before next reply, 4 triggers) | **IN** | ✅ posttooluse_minilearning, stop_reflect, /reflect | hermes adapter post_llm_call (Phase 1) |
| 1b | Occurrence counting → 3-strike HOT promotion | **SPLIT** | ⚠️ partial (confirmed-count rerank; no promotion signal) | NEW: occurrence counter on content_hash; emit `promotion_candidate` record; Fleet gate promotes to HOT |
| 1c | M1–M8 mutation protocol (stuck-state escape) | **OUT** | n/a — live behavioral law, not memory | Reflect only indexes mutation declarations from journals as learnings |
| 2 | Knowledge graph (entities, typed edges, sidecars, structural search) | **IN** | ✅ entity_store, graph_engine, graph_links, nano-graphrag, sidecars | Parity check: typed edges (owns/depends_on/caused/supersedes) + per-entity pages vs per-learning sidecars — gap audit Phase 3 |
| 3a | Journal authoring protocol (boot/during/handover/50-line read) | **OUT** | n/a — worktree discipline = governance | — |
| 3b | Journal archival + indexing + recall | **IN** | ⚠️ drain exists; work_journal kind in spec | Phase 2 importer |
| 4a | Gossip transport (epidemic broadcast, Lamport, version vectors, CRDT merge) | **OUT** | n/a — fleet infra | Reflect ingests the merged JSONL result only |
| 4b | Discovery storage + freshness-ranked recall | **IN** | ✅ recall + temporal arm | Phase 2/3; replaces tail-20 first-turn blast |
| 4c | G-Counter occurrence max-merge | **SPLIT** | ❌ | maps onto 1b occurrence counter; merge by max on ingest |
| 5a | Research loop (PROPOSAL.md gate, blind review, accept/reject) | **OUT** | n/a — pure workflow governance | — |
| 5b | Research outcomes as knowledge (accepted patterns + REJECTED anti-patterns) | **IN** | ⚠️ no anti-pattern kind | NEW: `kind: anti_pattern` — negative knowledge must rank in recall ("don't do X" surfacing) |
| 6a | Skills lifecycle (approval, build, Velma score gate, adoption, 30-day monitor) | **OUT** | n/a | — |
| 6b | Skill-need detection signals H1–H4 | **SPLIT** | ❌ | NEW: Reflect owns the data for H3 (same correction 3+) and H1/H2 (journal patterns/wishes) → emit `skill_signal` records; Fleet runs lifecycle. H3 in Phase 2; H1/H2 v2 |
| 7 | GraphRAG retrieval (entity walks, 3-way merge+rank) | **IN** | ✅ hybrid + graph + MMR + cross-encoder | Parity check: ownership/dependency query types over typed edges |
| 8a | HOT tier (MEMORY.md, SOUL/IDENTITY etc., always-loaded, <8k) | **OUT** | index snapshots only (spec Q3) | Fleet owns load + eviction-immunity |
| 8b | WARM tier (daily-log tails at session start) | **SPLIT** | ✅ staged recall | Target: relevance recall replaces blind tails; Fleet may keep thin tail during transition |
| 8c | BANK tier (on-demand search) | **IN** | ✅ core competency | Phase 1–4 = the cutover |
| 8d | ARCHIVE tier (explicit recall only) | **IN** | ✅ archived status + rank penalty | — |
| 9 | Retrieval stack (vec+BM25 70/30, sqlite-vec/QMD, temporal decay 30d, MMR, cited bundles) | **IN** | ✅ parity or better (QMD, cross-encoder, MMR, citations) | Tune decay half-life per kind (wololo: 30d + evergreen exemption; reflect rerank: ~60d) |
| 10 | Multi-writer safety (7 agents appending concurrently) | **SPLIT** | ⚠️ single-writer assumption | fcntl-style locking + content_hash idempotent ingest; CRDT semantics stay Fleet-side |

### Coverage opinion (summary)
- Retrieval stack: Reflect at parity or ahead (cross-encoder rerank + staged recall exceed wololo's described pipeline).
- Real gaps exposed by wololo docs, now in scope: **occurrence-count promotion signal (1b)**, **anti-pattern kind (5b)**, **skill_signal detection (6b, H3 first)**, **typed-edge parity audit (2)**, **multi-writer ingest safety (10)**.
- Firmly out, resist scope creep: M1–M8 protocol, research-loop workflow, gossip transport/CRDT internals, skills lifecycle gates, HOT-tier loading, journal authoring protocol. All are law/behavior, not memory.
- Boundary restated: **Reflect = remember, rank, signal. Fleet = enforce, promote, act.** Wololo's seven subsystems split cleanly: 3 memory (graph, retrieval, tiers) → Reflect; 3 governance (research, skills lifecycle, mutation) → Fleet; reflection loop is the one true split (capture+count in Reflect, promotion in Fleet).

### Residual — handled by NEITHER side in v1

| Gap | Risk | Plan |
|---|---|---|
| Semantic near-dup merge (reworded lesson, different content_hash) | duplicate advice in recall | v2; supersedes + consolidate pass mitigates |
| H1/H2 skill signals (journal command-pattern / "wish I had" mining) | skill needs detected only via H3 corrections | v2 after journals indexed |
| WARM tier (daily-log session-start tails) | no reflect equivalent during transition | decide at Phase 4 flip; Fleet may keep thin tail |
| Subagent-scoped recall | spawned agents get generic recall | v2 |
| Typed-edge parity (owns/depends_on/caused/supersedes query shapes) | ownership/dependency queries may underperform wololo graph | Phase 3 audit |
| Mid-session propagation | new learning reaches other live sessions only on next recall query, not push | accepted; gossip remains the push channel |

## Validation Strategy (interviewed 2026-07-12, Stevie decisions)

### Fleet validation environment — WHOLE-FLEET SHADOW DAY 1 (Stevie override of canary recommendation)
All hermes agents on this machine run BANK injection + reflect shadow recall simultaneously from first deploy. Max telemetry volume; accepted blast radius on shim bugs. Mitigation: shim is fail-open (exit 0, empty context), hard wall-clock timeout, and a kill switch (`FLEET_MEMORY_BACKEND=bank` reverts fleet-wide instantly).

### Regression guard for claude/codex adapters — ALL FOUR mechanisms
1. **Golden recall snapshot diff** (CI, pre-merge): fixed golden query set (coding + personal), top-k snapshot committed as baseline; fail on rank-shift beyond threshold in claude/codex scopes.
2. **Behavioral proof suite** (CI, every PR): extend tests/eval/behavioral/proofs/ with proof_F1_fleet_ingest_isolation, proof_F2_domain_boost_ranking, proof_F3_quarantine_enforced.
3. **reflect-verify tmux smoke** (post-merge, this machine): drain clearing, ingest alive, claude + codex hook smoke.
4. **Recall-followup-rate monitor**: baseline the reflect:cost followup rate before hermes ingest; alert if it degrades >5pp after.

### Parity gate judge — auto thresholds + Stevie spot-check
Gate = ALL of: hit@5 ≥ BANK on golden set · injected tokens ≤ 2000 · p95 shim latency < 150ms · 7 days whole-fleet shadow with zero hook errors · Stevie reviews a 20-prompt side-by-side (BANK vs reflect injection) diff report and approves the flip.

### Blast-radius containment — QUARANTINE until parity
Every fleet/hermes-ingested doc gets `quarantine: true` frontmatter. Claude/codex recall excludes quarantined docs; hermes shadow recall sees everything. Quarantine lifted per-kind (patterns → discoveries → corrections → journals) only after the golden-snapshot diff stays clean for that kind. Hermes work physically cannot regress claude/codex recall during build-out.

## Open Questions

- [ ] Q1: Does Hermes expose session-end/compaction hook points beyond pre/post_llm_call? If yes, wire directly; if no, drain daemon confirmed.
- [ ] Q2: Domain taxonomy final list — coding|ops|comms|personal|governance enough? Per-agent domains (motoko=personal)?
- [ ] Q3: Should HOT memory (MEMORY.md) recall inject as pinned block in Hermes (like BANK today) or rely on Fleet's own session_rules? (Spec assumes Fleet keeps injecting HOT; reflect only indexes snapshots.)
- [ ] Q4: p95 latency budget confirmation — 150ms acceptable for Hermes turn cadence?

---
*Generated via /interview; user AFK — four architecture decisions adopted from recommendations, veto window open.*
