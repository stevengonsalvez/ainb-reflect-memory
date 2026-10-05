---
title: Recall pipeline
description: "End to end recall in reflect: hooks, query building, vector, BM25, graph and temporal arms, RRF, cross-encoder, MMR, budgets, and what gets injected or skipped."
sidebar:
  order: 5
---

Recall turns "what is the agent about to do" into a few lines of prior learnings in its context. It is one Python script (`recall.py`) wrapped by three hooks. This page follows one recall from trigger to injected text and gives the defaults as they are in the code.

:::note[Prefer to click through it?]
There is an interactive version of this pipeline: [Recall walkthrough](/ainb-reflect-memory/interactive/recall-walkthrough/). For the optional pieces and their knobs see [Retrieval features](/ainb-reflect-memory/concepts/retrieval-features/).
:::

## The pipeline at a glance

<div class="rp-diagram">
<svg viewBox="0 0 680 734" style="color:var(--sl-color-gray-2);--rp-text:var(--sl-color-white);--rp-muted:var(--sl-color-gray-2);--rp-line:var(--sl-color-gray-4);--rp-accent:var(--sl-color-text-accent);--rp-arm:#4f9d6a;--rp-gate:#c9a227;--rp-ok:#4f9d6a;--rp-box-bg:var(--sl-color-gray-6);--rp-core-bg:var(--sl-color-gray-5);--rp-hook-bg:var(--sl-color-accent-low);--rp-arm-bg:color-mix(in srgb,#4f9d6a 14%,var(--sl-color-gray-7));--rp-gate-bg:color-mix(in srgb,#c9a227 14%,var(--sl-color-gray-7));--rp-out-bg:color-mix(in srgb,#4f9d6a 18%,var(--sl-color-gray-7));width:100%;height:auto;max-width:680px" font-family="var(--sl-font)" role="img" aria-labelledby="rp-t rp-d">
<title id="rp-t">reflect recall pipeline</title>
<desc id="rp-d">Stages of a recall from hook trigger to injected context: persona lookup, scope, cache, four parallel arms (vector, BM25, graph, temporal), RRF fusion, cross-encoder and embeddings, bounded-boost scoring, filters, OOD gate, MMR, token budget, injection.</desc>
<style>
.rp-box{stroke-width:1.4}
.rp-hook{fill:var(--rp-hook-bg);stroke:var(--rp-accent)}
.rp-core{fill:var(--rp-core-bg);stroke:var(--rp-line)}
.rp-step{fill:var(--rp-box-bg);stroke:var(--rp-line)}
.rp-arm{fill:var(--rp-arm-bg);stroke:var(--rp-arm)}
.rp-gate{fill:var(--rp-gate-bg);stroke:var(--rp-gate)}
.rp-out{fill:var(--rp-out-bg);stroke:var(--rp-ok)}
.rp-group{fill:none;stroke:var(--rp-line);stroke-width:1.2;stroke-dasharray:6 4}
.rp-arrow{stroke:currentColor;stroke-width:1.6;opacity:.75}
.rp-head{fill:currentColor;opacity:.75}
.rp-title{fill:var(--rp-text);font-size:13px;font-weight:700}
.rp-sub{fill:var(--rp-muted);font-size:11.5px}
.rp-glabel{fill:var(--rp-muted);font-size:11.5px;font-style:italic}
</style>
<defs><marker id="rp-ah" markerWidth="9" markerHeight="7" refX="8" refY="3.5" orient="auto"><path d="M0,0 L9,3.5 L0,7 Z" class="rp-head"/></marker></defs>
<rect x="2" y="228" width="676" height="112" rx="10" class="rp-group"/>
<text x="16" y="247" class="rp-glabel">4 arms in parallel (4 threads); a failed arm returns []</text>
<line x1="112" y1="70" x2="112" y2="90" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="340" y1="70" x2="340" y2="90" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="568" y1="70" x2="568" y2="90" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="340" y1="124" x2="340" y2="146" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="218" y1="175" x2="234" y2="175" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="446" y1="175" x2="462" y2="175" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="340" y1="204" x2="340" y2="228" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="340" y1="340" x2="340" y2="360" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="170" y1="394" x2="170" y2="416" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="510" y1="394" x2="510" y2="416" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="170" y1="474" x2="170" y2="496" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="510" y1="474" x2="510" y2="496" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="340" y1="554" x2="340" y2="574" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="167" y1="603" x2="175" y2="603" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="336" y1="603" x2="344" y2="603" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="505" y1="603" x2="513" y2="603" class="rp-arrow" marker-end="url(#rp-ah)"/>
<line x1="340" y1="632" x2="340" y2="654" class="rp-arrow" marker-end="url(#rp-ah)"/>
<rect x="6" y="8" width="212" height="62" rx="8" class="rp-box rp-hook"/>
<text x="112.0" y="28.0" class="rp-title" text-anchor="middle">SessionStart hook</text>
<text x="112.0" y="43.0" class="rp-sub" text-anchor="middle">query: project, branch, commits</text>
<text x="112.0" y="58.0" class="rp-sub" text-anchor="middle">limit 3, 1500 chars, overlap 0.2</text>
<rect x="234" y="8" width="212" height="62" rx="8" class="rp-box rp-hook"/>
<text x="340.0" y="28.0" class="rp-title" text-anchor="middle">UserPromptSubmit hook</text>
<text x="340.0" y="43.0" class="rp-sub" text-anchor="middle">query: the prompt (min 12 chars)</text>
<text x="340.0" y="58.0" class="rp-sub" text-anchor="middle">fetch 9, keep 3 new, 1500 chars</text>
<rect x="462" y="8" width="212" height="62" rx="8" class="rp-box rp-hook"/>
<text x="568.0" y="28.0" class="rp-title" text-anchor="middle">SubagentStart hook</text>
<text x="568.0" y="43.0" class="rp-sub" text-anchor="middle">query: agent type, cwd, task</text>
<text x="568.0" y="58.0" class="rp-sub" text-anchor="middle">limit 3, 1500 chars, 5 s cap</text>
<rect x="6" y="90" width="668" height="34" rx="8" class="rp-box rp-core"/>
<text x="340.0" y="111.0" class="rp-title" text-anchor="middle">recall.py QUERY  (hook subprocess timeout 30 s)</text>
<rect x="6" y="146" width="212" height="58" rx="8" class="rp-box rp-step"/>
<text x="112.0" y="164.0" class="rp-title" text-anchor="middle">0. persona lookup (O3)</text>
<text x="112.0" y="179.0" class="rp-sub" text-anchor="middle">aggregate question: answer from</text>
<text x="112.0" y="194.0" class="rp-sub" text-anchor="middle">project_persona, then stop</text>
<rect x="234" y="146" width="212" height="58" rx="8" class="rp-box rp-step"/>
<text x="340.0" y="164.0" class="rp-title" text-anchor="middle">1. scope (R15, A6)</text>
<text x="340.0" y="179.0" class="rp-sub" text-anchor="middle">branch or project shard,</text>
<text x="340.0" y="194.0" class="rp-sub" text-anchor="middle">else pooled ~/.learnings</text>
<rect x="462" y="146" width="212" height="58" rx="8" class="rp-box rp-step"/>
<text x="568.0" y="164.0" class="rp-title" text-anchor="middle">2. cache (R9)</text>
<text x="568.0" y="179.0" class="rp-sub" text-anchor="middle">exact sha1, fuzzy Jaccard 0.85</text>
<text x="568.0" y="194.0" class="rp-sub" text-anchor="middle">1 h TTL; hit skips arms + CE</text>
<rect x="6" y="256" width="161" height="70" rx="8" class="rp-box rp-arm"/>
<text x="86.5" y="280.0" class="rp-title" text-anchor="middle">vector</text>
<text x="86.5" y="295.0" class="rp-sub" text-anchor="middle">reflect search</text>
<text x="86.5" y="310.0" class="rp-sub" text-anchor="middle">--mode naive</text>
<rect x="175" y="256" width="161" height="70" rx="8" class="rp-box rp-arm"/>
<text x="255.5" y="280.0" class="rp-title" text-anchor="middle">BM25</text>
<text x="255.5" y="295.0" class="rp-sub" text-anchor="middle">qmd search</text>
<text x="255.5" y="310.0" class="rp-sub" text-anchor="middle">-c learnings</text>
<rect x="344" y="256" width="161" height="70" rx="8" class="rp-box rp-arm"/>
<text x="424.5" y="280.0" class="rp-title" text-anchor="middle">graph (R1)</text>
<text x="424.5" y="295.0" class="rp-sub" text-anchor="middle">reflect search</text>
<text x="424.5" y="310.0" class="rp-sub" text-anchor="middle">--mode local</text>
<rect x="513" y="256" width="161" height="70" rx="8" class="rp-box rp-arm"/>
<text x="593.5" y="280.0" class="rp-title" text-anchor="middle">temporal (R5)</text>
<text x="593.5" y="295.0" class="rp-sub" text-anchor="middle">date-window scan,</text>
<text x="593.5" y="310.0" class="rp-sub" text-anchor="middle">only on a date phrase</text>
<rect x="6" y="360" width="668" height="34" rx="8" class="rp-box rp-core"/>
<text x="340.0" y="381.0" class="rp-title" text-anchor="middle">3. RRF fusion, k = 60  (per-arm floors first, off by default)</text>
<rect x="6" y="416" width="326" height="58" rx="8" class="rp-box rp-step"/>
<text x="169.0" y="434.0" class="rp-title" text-anchor="middle">4a. cross-encoder (R2)</text>
<text x="169.0" y="449.0" class="rp-sub" text-anchor="middle">top 20 via reflect rerank</text>
<text x="169.0" y="464.0" class="rp-sub" text-anchor="middle">MiniLM-L-6-v2 logits</text>
<rect x="348" y="416" width="326" height="58" rx="8" class="rp-box rp-step"/>
<text x="511.0" y="434.0" class="rp-title" text-anchor="middle">4b. embeddings (R3)</text>
<text x="511.0" y="449.0" class="rp-sub" text-anchor="middle">top 20 via reflect embed</text>
<text x="511.0" y="464.0" class="rp-sub" text-anchor="middle">all-mpnet-base-v2 vectors</text>
<rect x="6" y="496" width="668" height="58" rx="8" class="rp-box rp-core"/>
<text x="340.0" y="514.0" class="rp-title" text-anchor="middle">5. score = sigmoid(CE logit) x bounded boosts</text>
<text x="340.0" y="529.0" class="rp-sub" text-anchor="middle">confidence, recency, tags, proof,</text>
<text x="340.0" y="544.0" class="rp-sub" text-anchor="middle">project, domain, authority, speculative</text>
<rect x="6" y="574" width="161" height="58" rx="8" class="rp-box rp-gate"/>
<text x="86.5" y="592.0" class="rp-title" text-anchor="middle">6. filters</text>
<text x="86.5" y="607.0" class="rp-sub" text-anchor="middle">quarantine,</text>
<text x="86.5" y="622.0" class="rp-sub" text-anchor="middle">--confidence</text>
<rect x="175" y="574" width="161" height="58" rx="8" class="rp-box rp-gate"/>
<text x="255.5" y="592.0" class="rp-title" text-anchor="middle">7. OOD gate (R7)</text>
<text x="255.5" y="607.0" class="rp-sub" text-anchor="middle">best of top 5 below</text>
<text x="255.5" y="622.0" class="rp-sub" text-anchor="middle">min-overlap: nothing</text>
<rect x="344" y="574" width="161" height="58" rx="8" class="rp-box rp-gate"/>
<text x="424.5" y="592.0" class="rp-title" text-anchor="middle">8. MMR (R3)</text>
<text x="424.5" y="607.0" class="rp-sub" text-anchor="middle">lambda 0.7,</text>
<text x="424.5" y="622.0" class="rp-sub" text-anchor="middle">top LIMIT</text>
<rect x="513" y="574" width="161" height="58" rx="8" class="rp-box rp-gate"/>
<text x="593.5" y="592.0" class="rp-title" text-anchor="middle">9. budget (R4)</text>
<text x="593.5" y="607.0" class="rp-sub" text-anchor="middle">--max-tokens (0 = off),</text>
<text x="593.5" y="622.0" class="rp-sub" text-anchor="middle">then --max-chars</text>
<rect x="6" y="654" width="668" height="72" rx="8" class="rp-box rp-out"/>
<text x="340.0" y="671.5" class="rp-title" text-anchor="middle">10. inject as additionalContext</text>
<text x="340.0" y="686.5" class="rp-sub" text-anchor="middle">SessionStart adds a token-economics footer</text>
<text x="340.0" y="701.5" class="rp-sub" text-anchor="middle">UserPromptSubmit dedupes per session</text>
<text x="340.0" y="716.5" class="rp-sub" text-anchor="middle">any failure or empty result: empty context, exit 0</text>
</svg>
</div>

[Standalone SVG](/ainb-reflect-memory/diagrams/recall-pipeline.svg) (follows your OS light/dark setting).

Everything after the arms is local: fusion, scoring, gates and MMR run in `recall.py`; the cross-encoder and embeddings are two small subprocess calls to the `reflect` engine (served by a warm model daemon when available). Every stage past the primary vector arm is a booster, not a blocker: on any failure it returns nothing and the pipeline continues with what it has.

Source: [`plugin/skills/recall/scripts/recall.py`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/plugin/skills/recall/scripts/recall.py), function `recall()`.

## Entry points

Four callers run `recall.py`. Each builds its own query and passes its own limits.

| Caller | Fires on | Query | Limit and size | Other flags |
|---|---|---|---|---|
| `session_start_recall.py` | `SessionStart` | project + branch + commit tags (see below) | `--limit 3`, `--max-chars 1500` | `--min-overlap 0.2`, `--max-tokens 0`, `--no-gap-log`, `--no-followup` |
| `user_prompt_submit_recall.py` | `UserPromptSubmit` | the prompt text | fetches `--limit 9`, keeps 3 not-yet-injected, `--max-chars 3000`, then fits up to 3 whole learnings into 1500 chars (a learning that does not fit is dropped, not cut, and is not marked injected) | passes `--session-id`; no min-overlap, no gap/followup suppression |
| `subagent_start_recall.py` | `SubagentStart` | `subagent <type> \| cwd <cwd> \| agent_id <id> \| <task prompt>` | `--limit 3`, `--max-chars 1500` | `--no-gap-log`, `--no-followup`, 5 s timeout |
| `/reflect:recall` skill | you type it | your query | `--limit 10`, `--max-chars 2000` | all flags available |

Hook subprocesses time out at 30 s (`REFLECT_RECALL_TIMEOUT`); the subagent hook at 5 s (`REFLECT_SUBAGENT_RECALL_TIMEOUT`). The SessionStart and UserPromptSubmit hooks set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` (via `setdefault`) because a cold model load that hits the network blows the timeout; models must already be cached under `~/.reflect/models/`.

Env overrides for the callers: `REFLECT_RECALL_LIMIT` and `REFLECT_RECALL_MAX_CHARS` (explicit recall defaults), `REFLECT_RECALL_MIN_OVERLAP` and `REFLECT_RECALL_MAX_TOKENS` (SessionStart), `REFLECT_SUBAGENT_RECALL_LIMIT`, `REFLECT_SUBAGENT_RECALL_MAX_CHARS`, `REFLECT_SUBAGENT_CONTEXT` (replaces the recall result with a fixed string).

The hooks are wired in [`plugin/.claude-plugin/plugin.json`](https://github.com/stevengonsalvez/ainb-reflect-memory/blob/main/plugin/.claude-plugin/plugin.json) for Claude Code, and in `codex-hooks.json` and `copilot-hooks.json` for the other harnesses. See [Hooks reference](/ainb-reflect-memory/reference/hooks/).

:::caution[Copilot]
Copilot ignores the output of its `userPromptSubmitted` hook, so on that harness the UserPromptSubmit script only runs for its capture side effects. Recall text reaches the model through SessionStart and SubagentStart only. This is documented in the hook source.
:::

`pretooluse_context.py` is not recall. It matches deterministic policy rules from `~/.reflect/policy-rules.jsonl` (or `REFLECT_POLICY_FILE`) and never queries the KB.

### How the SessionStart query is built

`build_query()` in `session_start_recall.py`:

1. Project name: basename of `git remote get-url origin` (minus `.git`), else the cwd basename.
2. Branch, unless it is `main`, `master` or detached: slashes, underscores and hyphens become spaces (`feat/foo-bar` becomes `foo bar`).
3. Up to 3 tokens from the last 5 commit subjects (3+ chars, conventional-commit words and filler dropped, ranked by frequency).
4. Up to 3 more tokens from the last 5 records of `~/.reflect/commits.jsonl` (written by the post-commit hook), if present.
5. Words are lowercase-deduplicated in order. The commit tokens are also passed as `--tags`, which feeds the tag boost.

So the query is a bag of keywords, not a sentence. That suits the BM25 arm and the embedding arm equally.

## Stage by stage

### 0. Persona short-circuit (O3)

If the query looks like an aggregate question ("what testing style do we use?") and the project has a high-confidence field in the `project_persona` table, `recall()` returns that single line and stops. Closed-domain queries or a miss fall through. Any DB problem is swallowed.

### 1. Scope: which KB gets read (R15, A6)

`resolve_kb_root()` picks the KB root, first match wins:

1. `$GLOBAL_LEARNINGS_PATH` already set: use it untouched (eval and test harness contract).
2. `--global` or `RECALL_GLOBAL=1`: the pooled KB, `~/.learnings` (override root with `RECALL_LEARNINGS_ROOT`).
3. Current project plus current non-trunk branch: `~/.learnings/shards/<project>/branches/<branch>/`.
4. Current project, trunk or detached: `~/.learnings/shards/<project>/`.
5. Anything else, or a shard with no `documents/*.md` and no vector index: the pooled KB.

`--all-branches` / `RECALL_ALL_BRANCHES=1` skips the branch level. Branch comes from `RECALL_BRANCH`, else `git rev-parse --abbrev-ref HEAD`; slashes become `__`.

:::caution[Shards are read-only in this repo]
A search of `src/` and `plugin/` finds no code that writes `shards/`. Capture and ingest write the pooled `~/.learnings`. Unless you populate shards yourself, step 5 applies and every recall reads the pooled KB across all projects; branch isolation is then not in effect, and the project-affinity boost (below) is what keeps same-project notes ahead.
:::

### 2. Cache (R9)

Cache files live in `~/.reflect/recall_cache/` (`$REFLECT_STATE_DIR`).

- **Exact tier**: key is `sha1(version | query | mode | fetched_limit)`, with the shard folded in. `fetched_limit = max(limit * 2, 10)`, so limit 1 and limit 3 share an entry (SessionStart relies on this: its probe and its main recall hit the same file).
- **Fuzzy tier**: if the exact lookup misses, the stopword-filtered token set of the query is compared (Jaccard) against an index of up to 200 recent entries; best match at or above 0.85 wins. Queries with fewer than 2 meaningful tokens never fuzzy-match.
- **Validity**: 1 h TTL (`--cache-ttl`), and invalid once `~/.learnings/nano_graphrag_cache` is newer than the file.
- **What a hit skips**: the arms, the cross-encoder and the embeddings. Scoring, filters, OOD gate, MMR and budget are re-applied on every call, so tags, `--confidence` and `--limit` can differ between calls sharing a fetch.

### 3. Candidate arms

Run in a 4-worker thread pool. Each returns at most `fetched_limit` learnings, or `[]` on any error.

| Arm | Command | Timeout | Runs when |
|---|---|---|---|
| Vector | `reflect search QUERY --mode naive --format json` | 60 s | always (the primary arm) |
| BM25 | `qmd search QUERY -c learnings` | 10 s | `qmd` is on `$PATH` |
| Graph (R1) | `reflect search QUERY --mode local --format json` | 60 s | `RECALL_GRAPH_ARM` not `0` and mode is not already `local` |
| Temporal (R5) | scan `documents/*.md` for notes timestamped inside a parsed date window | n/a, capped at 5000 files | `RECALL_TEMPORAL_ARM` not `0` and the query contains a date phrase |

Only if the vector arm fails and no other arm returned anything does `recall()` give up with an error (silent, shown only with `REFLECT_RECALL_DEBUG=1`).

Note: `reflect search` accepts `--limit` but the engine call (`LearningsGraphEngine.search`) does not pass it on, so the vector and graph arms return whatever the engine's default `QueryParam` yields. The limit is enforced later, after MMR.

Date phrases are extracted by a stdlib regex pass in `temporal_extraction.py`: `yesterday`, `N days ago`, `last week/month/year`, `last <weekday>`, `last 3 days`, ISO dates, `in march`, `march 2024`, `last sprint` (14 days), `recently`, and `before/until/since/after <anchor>` modifiers. A phrase with no resolvable date ("before the rewrite") returns nothing.

### 4. Fusion (RRF)

Reciprocal rank fusion over the four lists: `score(doc) = sum of 1 / (60 + rank)` across arms (`RRF_K = 60`). Documents are matched by frontmatter `id` (else a hash of the first 256 chars). No score normalisation is needed, which is why it suits arms whose native scores are incomparable.

Before fusion, each arm may drop candidates below its own query-term-coverage floor (R12). All four floors default to 0, which is off. See [Retrieval features](/ainb-reflect-memory/concepts/retrieval-features/#per-arm-floors-r12).

### 5. Cross-encoder and embeddings (R2, R3)

For the top 20 fused candidates, two subprocess calls run concurrently, so added latency is the slower of the two:

- `reflect rerank QUERY`: `cross-encoder/ms-marco-MiniLM-L-6-v2` scores each (query, first 2000 chars) pair. Output is a raw logit per candidate.
- `reflect embed QUERY`: unit-normalised `all-mpnet-base-v2` vectors for the query and the same 20 candidates, used by MMR.

Both need `sentence-transformers` (the `[graph]` extra of `reflect-kb`). On a slim install each returns `available: false` and recall silently continues without it.

### 6. Scoring

```
score = sigmoid(ce_logit) x confidence x recency x tags x proof
        x project x domain x authority x speculative
```

Each factor is `1 + alpha * (norm - 0.5)`, with `norm` clamped to 0..1, so it stays inside `[1 - alpha/2, 1 + alpha/2]` and the neutral value (norm 0.5) is exactly 1.0. Candidates past the top 20 get a cross-encoder component of `1e-6`, so they sort below every scored one. Without cross-encoder scores the boost product is the whole score.

| Factor | Env | Default alpha | Swing | Norm |
|---|---|---|---|---|
| confidence | `RECALL_CONFIDENCE_ALPHA` | 0.2 | +/- 10% | `(confidence_num - 0.3) / 0.6`; HIGH 0.9, MEDIUM 0.6, LOW 0.3 |
| recency | `RECALL_RECENCY_ALPHA` | 0.2 | +/- 10% | linear decay over 365 days, floor 0.1, 0.5 if undated (see note) |
| tags | `RECALL_TAG_ALPHA` | 0.2 | +/- 10% | fraction of query tags the note carries; 0.5 with no query tags |
| proof count | `RECALL_PROOF_ALPHA` | 0.1 | +/- 5% | `0.5 + ln(proof_count)/10` |
| project | `RECALL_PROJECT_ALPHA` | 0.2 | up to +10% | 1.0 for same project, else neutral |
| domain | `RECALL_DOMAIN_ALPHA` | 0.2 | up to +10% | 1.0 only if `--domain-hint` equals the note's `domain` |
| authority | `RECALL_AUTHORITY_ALPHA` | 0.1 | +/- 5% | law/promoted 1.0, advisory 0.5, archived 0.0 |
| speculative | `RECALL_SPECULATIVE_ALPHA` | 0.2 | down to -10% | 0.0 if tagged `speculative`, else neutral |

Alphas are clamped to 0..2; a malformed value falls back to the default. Recency reads the `<!-- archived: ISO -->` header in the note body (written by the ingest archive step), not frontmatter; a note without that header counts as undated and gets the neutral 0.5. The project boost is skipped (neutral) when the corpus is already a single-project shard.

### 7. Filters, gate, MMR, budget

Before any of this (after fusion, before rerank), `filter_superseded()` drops notes retired by frontmatter (`superseded_by` set, `status` superseded or archived), notes whose id sits in `archived/` or `documents/.forgotten/`, and notes whose ledger row has `is_latest = 0`. `REFLECT_RECALL_INCLUDE_SUPERSEDED=1` turns it off. A missing ledger is fine; ledger ids differ from note ids, so a ledger-only retirement with no `artifact_path` cannot always be linked to a file.

Applied in this order after scoring:

1. **Quarantine**: fleet-imported notes with `quarantine` set are dropped unless `--include-quarantined` (implied by `--format fleet-context`).
2. **Confidence**: `--confidence HIGH|MEDIUM|LOW|ANY` (hooks use `ANY`).
3. **OOD gate (R7)**: if the best of the top 5 notes covers less than `--min-overlap` of the query's content terms, the result is emptied and flagged `ood_gated`. Coverage is the fraction of the query's stopword-filtered tokens (3+ chars) found in the note text. `0` disables it. Defaults: `recall.py` 0.0, SessionStart 0.2.
4. **MMR (R3)**: keep the top note, then repeatedly pick `argmax(lambda * rel - (1 - lambda) * max_sim_to_selected)`, with `lambda = 0.7` and `rel` the rerank score divided by the window max. Similarity is cosine between the `reflect embed` vectors. Notes outside the 20-note window only fill leftover slots. With no embeddings, this is plain `[:limit]`.
5. **Token budget (R4)**: with `--max-tokens N > 0`, keep notes in order until the estimate (characters / 4) would exceed N. The first note is always kept. Default 0 (off).

### 8. Render and inject

`render_markdown()` builds one block. `--max-chars` is a budget on the rendered entries; when the next entry would exceed it the block ends with `- _(...N more truncated)_`.

```markdown
## Prior learnings relevant to `billing-svc payments proto` - 2 learnings, ~320 tok injected, est ~3880 tok saved
- **[lrn-0f3a9c]** Regenerate gRPC clients after editing payments.proto - ⚒ D:3000 → R:180 (-94%)
  How to apply: Run `make proto-gen` after any .proto edit; CI gates on it.
- **[lrn-7b21de]** Pin the retry budget per client, not per call - ⚖ D:1200 → R:140 (-88%)
  How to apply: Set it in the client constructor so retries cannot compound.

memory: 2 learnings, ~320 tok injected, est ~3880 tok saved
```

The sample is invented, and the real output separates the header counts and the economics with a long dash instead of the hyphen shown here. Per row: the glyph comes from the active mode's `learning_types` (`work_emoji`), `D` is estimated discovery cost, `R` is the read cost of the stored note, and the percentage is the saving. The final `memory:` footer is added by the SessionStart hook only. `RECALL_ECONOMICS=0` removes the economics, byte-for-byte the pre-economics format. With `--field rule` each hit is just that one frontmatter field.

The block is handed back as hook JSON:

| Harness | Envelope |
|---|---|
| Claude Code, Codex | `{"hookSpecificOutput": {"hookEventName": "SessionStart" or "UserPromptSubmit", "additionalContext": "..."}}` |
| Copilot (`REFLECT_HARNESS=copilot`) | `{"additionalContext": "..."}` (the exact shape Copilot expects is not confirmed in the source) |

On UserPromptSubmit the hook also dedupes: ids found in the output (`[lrn-...]`) are compared with `~/.reflect/session-injected/<session_id>.json`, already-seen notes are dropped, and the new ids are saved. SessionStart does not record its ids there, so a note injected at boot can appear again on the first matching prompt. Notes whose id does not start with `lrn-` are not deduped.

## What is skipped, and when

| Situation | Result |
|---|---|
| SessionStart with cwd equal to `$HOME` | empty context |
| SessionStart without `uv` or `recall.py` | only the slots and conventions blocks, if enabled |
| UserPromptSubmit with a prompt under 12 characters | no recall at all |
| Best hit covers less than `--min-overlap` of the query terms | empty (on by default at SessionStart only) |
| Persona hit | one persona line, no learnings |
| `reflect` CLI not on `$PATH` | empty, silent |
| Vector arm fails and no other arm has hits | empty, silent (`REFLECT_RECALL_DEBUG=1` prints the reason) |
| Recall subprocess exceeds its timeout | empty |
| Any uncaught hook exception | empty context, exit 0, breadcrumb in `~/.reflect/last-event.json` |
| UserPromptSubmit and every result already injected this session | empty |

Hooks never block the session: they always exit 0 with valid JSON.

An empty final result from a real prompt (UserPromptSubmit or `/reflect:recall`) is appended to `~/.reflect/knowledge-gaps.jsonl` as a knowledge gap (`RECALL_GAP_LOG=0` disables). SessionStart and SubagentStart suppress this because their queries are synthetic. Infrastructure errors are not logged as gaps.

## SessionStart is tiered

SessionStart does more than one recall:

```
cwd == $HOME?  ──yes──▶  empty
      │ no
      ▼
Tier 0  slots (REFLECT_SLOTS, opt-in) + conventions pointer (REFLECT_TIERED_INJECT, opt-in)
        prepended to whatever follows, never suppress it
      ▼
Tier 1  skills index (REFLECT_TIERED_INJECT, opt-in): strong hit ──▶ inject skills, stop
      ▼
probe   recall.py --limit 1 --confidence HIGH --format json
        fresh (archived within 30 days) and score above 0.8 ──▶ inject that one note, stop
      ▼
Tier 2  recall.py --limit 3 (the full pipeline above)
```

Two details worth knowing, both from `session_start_recall.py`:

- The probe (R11) is not behind a flag. It always runs first. Its "score" is derived from the note's confidence tier (HIGH 1.0, MEDIUM 0.7, LOW 0.4), and `--confidence HIGH` already limits it to HIGH notes, so in practice a HIGH-confidence top hit whose `<!-- archived -->` header is at most 30 days old short-circuits the boot. A note with no such header has unknown age and never qualifies. The probe warms the cache, so the Tier 2 call that follows reuses the fetch.
- The skills tier and slots are opt-in env flags (`REFLECT_TIERED_INJECT`, `REFLECT_SLOTS`), both off by default. See [Retrieval features](/ainb-reflect-memory/concepts/retrieval-features/#tiered-inject-r10-and-short-circuit-r11).

## Where recall spends time

The code comments give these figures; treat them as orders of magnitude, not guarantees.

| Cost | Source |
|---|---|
| Cold load of the embedding model and cross-encoder in a fresh process: roughly 11 to 16 s | hook comments in `session_start_recall.py` |
| Cross-encoder on 20 candidates once loaded: about 50 ms on CPU | `cross_encoder.py` docstring |
| In-process fallback holds about 3.5 GB RSS with torch | `cross_encoder.py` comment |
| Cache hit: skips the arms, cross-encoder and embeddings | `recall()` |

To avoid paying the cold load per recall, `reflect_kb/model_daemon.py` keeps both models warm behind a unix socket, auto-spawned on first use and shared by every KB on the box. It exits after `REFLECT_IDLE_TIMEOUT` seconds idle (default 1800). `REFLECT_NO_DAEMON=1` forces in-process loading. `REFLECT_EMBED_MODEL` and `REFLECT_CE_MODEL` swap the models; changing the embedding model requires a reindex because similarity must live in the index's space.

Related state, all under `~/.reflect/` unless `REFLECT_STATE_DIR` is set: `recall_cache/`, `recall_log.jsonl` (one line per recall: query, mode, count, cache tier, economics), `knowledge-gaps.jsonl`, `recent-searches.json` (follow-up diagnostic), `session-injected/`.

## Next

- [Retrieval features](/ainb-reflect-memory/concepts/retrieval-features/): every optional feature with default, key and cost.
- [Index and storage](/ainb-reflect-memory/concepts/index-and-storage/): what the arms read.
- [Configuration reference](/ainb-reflect-memory/reference/configuration/): the TOML and env surface.
- [Troubleshooting](/ainb-reflect-memory/guides/troubleshooting/): empty recall, slow recall.
