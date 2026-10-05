---
title: Lifecycle and control plane (v3.2 plan)
description: The April 2026 plan to make reflect's SQLite database the single control plane, with an explicit learning lifecycle, provenance, supersession, pluggable indexers and recall feedback, and which parts exist in the code today.
sidebar:
  order: 14
---

:::note[Design record, schema shipped, service layer did not]
Source: `plugin/docs/design-records/2026-04-23-v3.2-single-pr-plan.md` (a plan dated 2026-04-23, status "Planned"; also kept in agents-in-a-box as `plans/reflect-v3.2-single-pr.md`). Checked against `plugin/scripts/reflect_db.py`, `plugin/scripts/domain/` and `plugin/scripts/providers/`. The plan was meant to land as one PR; its goals arrived piecemeal through 4.x.
:::

## Intent

Turn "a good script collection" into one coherent subsystem:

- SQLite (`reflect.db`) is the **single operational control plane**;
- every learning has an explicit, enforced **lifecycle state**;
- discovery output from Claude, Codex, Copilot and Gemini **normalizes** into one shape;
- indexing goes through **backend interfaces** instead of ad hoc GraphRAG calls;
- recall **logs usage and feedback**;
- tests cover schema, migration, lifecycle, indexing, recall and end-to-end flows.

One architectural rule: only service-layer methods change lifecycle state; skills, hooks and CLI wrappers call services and never transition state themselves.

## Lifecycle

```text
detected ─▶ proposed ─▶ approved ─▶ materialized ─▶ indexed ─▶ recalled
                │            │                                    │
                ▼            ▼                                    ▼
             rejected      reverted                          superseded
```

Plan rules: every transition emits an event row; indexing is idempotent; revert never silently deletes provenance; supersession links old and new rows.

## Plan versus code

| Plan item | In the code today |
|---|---|
| Lifecycle enum | **Yes.** `domain/enums.py` `LearningStatus`: detected, pending (v3.1 compatibility), proposed, approved, materialized, indexed, recalled, superseded, reverted, rejected, plus `archived` (added later for the per-row TTL forget sweep, A3). A `CHECK` constraint on `learnings.status` enforces the values |
| Provenance on learnings | **Yes.** `source_tool`, `source_provider`, `source_kind`, `source_path`, `source_quote` and its hash, `content_hash`, `source_memory_ids`, `session_id`, `thread_id`, `proof_count`, `commit_hash` |
| Supersession | **Yes.** `supersedes_learning_id`, `superseded_by_learning_id`, `is_latest`, plus a `learning_history` table (S6) |
| Privacy level | **Yes.** `privacy_level` CHECK of internal, restricted, secret_redacted; `<private>` tags are stripped before prompts (M6) |
| Recall feedback columns | **Schema yes.** `recall_count`, `helpful_count`, `ignored_count`, `stale_count`, `last_recalled_at`, and a `recall_events` table with `rank`, `feedback`, `followup`. Nothing reads helpful or ignored counts to rerank; see [Retrieval roadmap](/ainb-reflect-memory/design/retrieval-roadmap/) phase 6 |
| `index_jobs` and `artifacts` tables | **Yes.** `index_jobs` has an `idempotency_key` and `add_index_job` dedups on it; `IndexBackend` is `graphrag` or `qmd` |
| Event idempotency | **Yes.** unique index on `events.idempotency_key` |
| Provider normalization | **Yes.** `scripts/providers/{claude,codex,copilot,gemini}.py` emit one discovered-memory shape with a content hash and source path |
| Service layer owns transitions (`services/`) | **No.** There is no `services/` package. `reflect_db.update_learning_status` is the shared function, and hooks and scripts call database helpers directly. The "only services transition state" rule is a convention, not a structure |
| Indexer interface (`indexers/base.py`, `graphrag.py`, `qmd.py`) | **No.** Indexing still shells out to `reflect reindex` (nano-graphrag) and `qmd update` / `qmd embed`; the backend enum exists but there is no pluggable class hierarchy |
| `artifacts/` package, `doctor` CLI | **Partly.** No `artifacts/` package. A `doctor` subcommand exists inside the `reflect_db.py` command line (`init`, `stats`, `events`, `history`, `contradictions`, `doctor`), not as a separate module |
| Single PR | **No.** Delivered across 3.x to 4.x (v3.2 never shipped as one PR) |

## What this record is still good for

- The **vocabulary** used across the code: lifecycle states, proposal types (`learning`, `agent_update`, `knowledge_note`, `skill_update`), artifact types (`knowledge_note`, `entity_sidecar`, `episode_note`, `archived_memory`), index job states.
- The **rationale** for putting state in SQLite with migrations in `reflect_db.py` rather than YAML files (the v2 state, imported once by `migrate_v2.py`, now archived).
- A concrete shape for the unbuilt pieces if someone wants to finish them: service layer, indexer interface, feedback-driven rerank.

See [Index and storage](/ainb-reflect-memory/concepts/index-and-storage/) for how storage works today and [KB format](/ainb-reflect-memory/reference/kb-format/) for the note and sidecar formats.
