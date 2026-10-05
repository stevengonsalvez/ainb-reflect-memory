---
title: OKF vs reflect
description: Design note comparing Open Knowledge Format v0.2 with reflect, what to borrow, how to measure it, and how PR #47 chose a different path than first recommended.
sidebar:
  order: 1
---

:::note[Draft, tied to unmerged PR #47]
This note is distilled from `research/2026-09-24_09-44-37_okf-vs-reflect.md` and `explainers/okf-vs-reflect.html` on the `stevengonsalvez/okf` branch (research pinned to `main` at `48968dd`). The profile it led to is [proposed in PR #47](/ainb-reflect-memory/reference/okf-profile/) and is not on `main`. Figures marked "author measurement" come from the author's live KB and cannot be reproduced from the repository.
:::

## Decision

**Adopt OKF as an interchange projection and storage-key profile. Keep reflect's own retrieval engine. Borrow four OKF ideas.**

| Question | Answer |
|---|---|
| Is OKF a replacement for recall? | No. OKF is a file format. Serving, search and query are declared non-goals in its spec. |
| Can reflect notes be OKF? | Yes, cheaply. OKF conformance is a parseable frontmatter block plus a non-empty `type` (SPEC section 11). |
| Should the agent browse `index.md` files instead of hook injection? | Not on current evidence. Nothing shows index browsing working at thousands of notes. Run the benchmark below first. |

## What each system is

```
 OKF: agent pulls                          reflect: hook pushes
┌───────┐  ┌──────────┐  ┌──────────┐     ┌────────┐  ┌───────────────────┐
│ Agent │─▶│ index.md │─▶│ concept  │     │ Prompt │─▶│ hook: vector + BM25│
└───────┘  │ per dir  │  │ .md x2-3 │     └────────┘  │ + graph + date    │
  4-6 tool └──────────┘  └──────────┘                 │ RRF, rerank, MMR  │
  turns (estimate)                                    └─────────┬─────────┘
                                                      <= 1,500 chars injected,
                                                      0 agent turns
```

OKF's unit is the concept: one `.md` file, id equal to its path, a bundle being a directory tree distributed as a git repo. Consumption is progressive disclosure through per-directory `index.md`. reflect's unit is the learning: one `.md` file with frontmatter, an `id` field, an entity sidecar, and a recall pipeline that injects results before the model reads the prompt.

## Parallels and differences

| Concern | OKF v0.2 | reflect (`main`) | Stronger |
|---|---|---|---|
| Unit | concept `.md` + YAML | learning `.md` + YAML | tie |
| Required keys | `type` only | `reflect add`: `title`, `category`, `key_insight` | OKF is looser |
| Identity | file path | `id` field, but several id schemes ([see KB format](/ainb-reflect-memory/reference/kb-format/#ids-and-file-names)) | OKF is simpler |
| Relations | untyped markdown links | 14 typed relationship kinds in the sidecar, with `tcommit`, `tvalid`, `tvalid_end` clocks | reflect |
| Supersession | `status: deprecated` plus prose | DB `is_latest` and `superseded_by_learning_id`; frontmatter `superseded_by` is not used by the ranker | reflect, with a gap |
| Trust | `generated` and `verified` actors, derived tiers | `confidence_num`, `proof_count`, `authority`, `quarantine` | OKF has clearer semantics |
| Freshness | `stale_after`, an absolute instant | `forget_after` plus recency boost | tie |
| Provenance | `sources[]` and per-claim footnotes | `provenance{}` and DB source quote and hash; the drain writer writes no `provenance` block | OKF |
| Discovery | `index.md` browsing | hybrid search, rerank; staged index, timeline, hydrate | reflect |
| History | `log.md` prose | `learning_history` table | reflect |
| Distribution | git bundle, vendor-neutral | custom deterministic tarball (`plugin/scripts/kb_export.py`) | OKF |
| Executable knowledge | attested computation (approved SQL plus a checker) | none; PreToolUse policy rules are the nearest | OKF, not applicable to reflect |
| Capture pipeline | not specified | hooks, then drain, then single-shot writer | reflect |
| Evals | none published | golden-query harness, LOCOMO pilot, behavioural proofs | reflect |

OKF external facts (as of the research date): v0.1 on 2026-06-12, v0.2 on 2026-07-25, one maintainer, open issues unanswered, sample bundles of about 78 files in total, no JSON Schema or validator upstream.

## Retrieval accuracy and tokens

These are hypotheses until the benchmark runs. Each states what would falsify it.

| | reflect hook injection | OKF index browsing |
|---|---|---|
| Delivered per query | at most about 375 tokens (1,500 chars) per prompt, 0 agent turns | about 5k to 6k tokens over 4 to 6 tool turns (estimate) |
| Strength | keyword, vector and graph search; works without the agent choosing to look | zero infrastructure; costs tokens only when the agent decides to look |
| Weakness | local model load; golden-set p50 12.8 s; injects on every prompt | no keyword or vector search; recall depends on one-line descriptions; every tool turn re-sends context |
| Evidence | golden set (20 queries): recall at 5 of 1.0, MRR 0.9375, p50 12.773 s (`tests/eval/results/baseline.json`) | none published |
| Falsified if | | an index-browsing agent reaches recall at 5 of 0.95 or more using 1.5k tokens or less per query |

The token estimate assumes a flat root index would cost about 25 tokens per entry (about 195k for roughly 7.8k notes, so navigation must be hierarchical), then a three-level walk of three indexes of about 50 entries plus 2 to 3 concept reads. Both numbers are inference. The hook caps are in `user_prompt_submit_recall.py` (`USER_PROMPT_MAX_CHARS = 1500`) and `session_start_recall.py` (3 results, 1500 chars). The LOCOMO pilot (J = 0.80 on 50 questions, run on 4.1.0, see [Benchmarks](/ainb-reflect-memory/evals/benchmarks/)) is a small sample.

## How to measure

Three arms, same questions, tokens counted where they reach the model, using the existing `tests/eval` harness.

| Arm | Setup |
|---|---|
| R0 | reflect recall as it runs today |
| O1 | Sonnet agent with read-only tools browsing an OKF export of the same KB |
| O2 | reflect recall over a KB exported to OKF and re-imported; passes only if results match R0 |

Datasets: the 20 golden queries, LOCOMO `conv-26` (50 questions, about $15 per arm), and about 30 real prompts sampled from `recall_log.jsonl` with hand-labelled relevant ids. Metrics: recall at 5, MRR, noise rate, LOCOMO J; tokens delivered per query (for O1, input plus output over every tool turn); tool calls; p50 and p95 latency; dollars per query and per learning written.

Fix these measurement defects first or the numbers mislead:

| Defect | Evidence |
|---|---|
| `recall_log.jsonl` logs `injected_tokens` before the hook truncates to 1,500 chars, so the logged figure is not what the model receives | `recall.py` sets `record["injected_tokens"] = economics.get("read_tokens", 0)`; the hook truncates at `USER_PROMPT_MAX_CHARS` |
| The drain writer sets no `discovery_tokens`, so the "tokens saved" economics fall back to per-type defaults | `drain_extract.render_md` emits no such key; defaults live in `DISCOVERY_CATEGORY_AVERAGES` |
| Recall fires on `<task-notification>` system text. Author measurement: 176 of 677 recalls in 30 days (26%) | no handling for it exists in the recall hooks on `main` (searched) |

## What to lift

Independent of the integration option, in rough priority order:

1. **Backfill and normalize `type`.** Author measurement: 1,669 of a 3,000-note sample (56%) had no `type`, and the rest used two casings. This alone makes notes OKF-conformant.
2. **`stale_after` as an absolute alias of `forget_after`.**
3. **Trust tiers from `generated` and `verified`**, mapped onto `authority` and `quarantine`; record `verified: human:<id>` when a user confirms a correction.
4. **Per-claim source footnotes** in drain output, and fill the missing `provenance` block.
5. **Generated `index.md` per shard** as a zero-infrastructure browse surface for harnesses without hooks (reuses the index stage of staged recall).
6. **Skip attested computation.** It is built around BigQuery SQL and does not match what reflect stores.

Going the other way, reflect's typed relations and supersession model address OKF's most requested open issues (#16, #22, #11, #13 upstream), which could be proposed upstream.

## Options considered

Criteria and weights: retrieval accuracy 0.25, token efficiency 0.25, interoperability 0.20, maintenance drag 0.15, exposure to a young single-maintainer spec 0.15. Scores are 1 to 5; D's accuracy and token scores are untested guesses.

| Option | Idea | Weighted (of 5) |
|---|---|---|
| A | OKF as export/import format plus validator; storage and retrieval unchanged | 4.65 |
| B | Store notes as OKF directly (`documents/` becomes a bundle, path as id, `index.md` everywhere) | 4.10 |
| C | Borrow ideas only, no OKF export or import | 4.20 |
| D | Replace hook injection with OKF-style browsing | 2.50 |

The research recommended A. Its work list was: `reflect export --format okf` (mapping `description` from `key_insight`, `status` from DB status with superseded becoming deprecated plus a successor link, `stale_after` from `forget_after`, `sources[]` from provenance, typed relations as an extension key plus a Related section), an OKF provider for `reflect ingest` that maps trust tier to `confidence_num` and quarantines unverified external concepts, `reflect okf lint`, and an export-then-import round trip that must reproduce the golden-query results (arm O2).

## Where PR #47 landed

The PR description says it "makes OKF reflect's core storage format without a separate export step". That is closer to a limited form of option B than to A: reflect's own notes become OKF concepts on write, without path-as-id or bundles.

| Research recommendation | PR #47 |
|---|---|
| Add `type`, `description`, `generated`, `sources`, `stale_after` | Done at write time by every writer, as extension-safe additions |
| Keep reflect fields as extension keys (allowed by SPEC section 11) | Done; no reflect key is rewritten except `type` casing |
| Rule S9 versus `verified` | Resolved by reword: S9 means "no telemetry in frontmatter", with `status`, `verified`, `stale_after` mutable through one function |
| Export, ingest provider, lint, `index.md`, `log.md` | Not included |
| Backfill existing notes | Listed as a follow-up |
| Round-trip proof and benchmark arms | Not included |

The resolved S9 question is the one the research left open (write `verified` to frontmatter, or only to the DB and the export layer). The cost is that frontmatter is no longer strictly immutable. The research warned that native bundles conflict with id schemes and shard layout and that the spec is still moving; the PR avoids that by not adopting path identity.

## Open questions

- Directory layout for any future export: by `learning_type`, by category, or by shard. It decides how useful `index.md` browsing would be.
- Pin exports to v0.2 and version the mapping, since upstream shipped a breaking timestamp change without a version bump (issue #24).
- Whether to contribute typed relations upstream, given no maintainer replies so far.

## Next

1. Merge or revise PR #47, then write the backfill with a dry run and a backup.
2. Fix the three measurement defects.
3. Run arms R0, O1, O2 before claiming any efficiency result.
