---
title: OKF v0.2 profile (proposed)
description: How reflect notes become Open Knowledge Format v0.2 concepts under PR #47 - derived keys, writers, lifecycle edits, and what is not included.
sidebar:
  order: 5
---

:::note[Proposed, lands with PR #47]
Nothing on this page exists on `main`. It documents [PR #47](https://github.com/stevengonsalvez/ainb-reflect-memory/pull/47) ("store learnings as OKF v0.2 concepts", open at the time of writing) from the code on the `stevengonsalvez/okf` branch. Behaviour may change in review. The shipped format is in [KB on-disk format](/ainb-reflect-memory/reference/kb-format/).
:::

[Open Knowledge Format](https://github.com/GoogleCloudPlatform/open-knowledge-format) (OKF) v0.2 is a markdown-plus-frontmatter convention for agent knowledge. Its only required key is `type`. The profile makes every note reflect writes a valid OKF concept by adding a few derived keys, while every reflect key stays in place as an OKF extension key. Retrieval is unchanged.

```
 writers                       profile                     note on disk
┌──────────────┐   fm + body  ┌──────────────────┐        ┌────────────────────┐
│ drain        │─────────────▶│ okf_additions()  │───────▶│ type, title,       │
│ hooks        │              │ derive missing   │        │ description,       │
│ reflect add  │              │ OKF keys only    │        │ generated, sources,│
│ fleet, share │              └──────────────────┘        │ stale_after        │
└──────────────┘                                          │ + every reflect key│
                                                          └────────────────────┘
```

## What is in the PR

| Piece | Path (on the PR branch) |
|---|---|
| Stdlib-only derivation and conformance checks | `src/reflect_kb/okf_profile.py` |
| Byte-identical vendored copy for hooks | `plugin/scripts/okf_profile.py` |
| Note text surgery, parsing, lifecycle edits | `src/reflect_kb/okf.py` |
| Writers wired to the profile | drain, skill, mini and permission hooks, `reflect add`, fleet importer, team share |
| `reflect serve` archive and restore stamp `status` | `src/reflect_kb/serve.py` |
| Schema and template updates | `schemas/frontmatter.schema.json`, `plugin/assets/learning_template.md` |
| Tests | `tests/test_okf_profile.py` |

The vendored copy exists because plugin hooks run under `uv run --script` with no dependencies and cannot import `reflect_kb` or PyYAML. A test fails whenever the two files differ. The profile module is Python 3.9 compatible on purpose, since the drain runs under the system `python3`.

## Conformance

OKF's rule (SPEC section 11) is that a concept is a frontmatter mapping with a non-empty `type`. The profile follows it exactly.

| Function | Returns |
|---|---|
| `okf_problems(fm)` | `[]` when conformant, else reasons: `frontmatter is not a mapping` or ``missing non-empty `type` ``. |
| `okf_warnings(fm)` | Soft deviations from the optional families. These never make a note non-conformant. |
| `is_okf_conformant(fm)` | `not okf_problems(fm)`. |

Warnings cover: a `sources` entry without `resource`; `generated.by` missing or `generated.at` without an offset; a `verified` event without `by` and an offset `at`; `status` outside `draft | stable | deprecated`; `stale_after` without an offset. These are library functions only; the PR adds no CLI command that calls them.

## Derived keys

`okf_additions(fm, body, actor, now)` returns only the keys a note is missing. It never edits a reflect key. The single exception is the casing of `type`.

| Key | Derived from | Rule |
|---|---|---|
| `type` | existing `type` | Missing or blank becomes `learning`. `LEARNING` or `Observation` collapse to lowercase. Any other value is treated as a producer-defined OKF type and left alone. |
| `title` | `title`, else `name`, first body heading, `id`, then `untitled` | Only when blank. Single line, capped at 200 characters. |
| `description` | `key_insight`, else the body's first sentence, else the title | Only when blank. Single line, capped at 200 characters. |
| `generated` | the writer | `{by: <actor>, at: <instant>}`. `at` comes from `created`, else `captured_at`, else the source file mtime, else it is omitted (never the wall clock, so re-normalizing yields identical bytes). Kept as-is when `generated.by` already exists. |
| `sources` | `source_path` or `provenance.source_path`, else `session_id` | `[{id: source, resource: <path>}]`, or `[{id: session, resource: "session:<id>"}]`. Only when no `sources` key exists. |
| `stale_after` | `forget_after` | Only when `stale_after` is blank. `forget_after` is kept for back-compat. |

Details that matter when reading output:

- Legacy naive timestamps (`2026-04-24`, bare datetimes) are read as UTC, so derived instants always carry an offset (`2026-04-24T00:00:00Z`). Existing `created` and `forget_after` values are not rewritten.
- `one_line` collapses runs of 3 or more hyphens to one, so a summary can never look like a `---` fence to a text-splitting parser.
- Actors follow OKF section 7: `<producer>/<version>`. Version comes from the plugin manifest (`.claude-plugin/plugin.json` or `plugin.json`) or `reflect_kb.__version__`, and is `unknown` when unreadable.

| Writer | Actor producer | Mechanism |
|---|---|---|
| Drain single-shot writer | `reflect-drain` | `okf_additions` appended to the rendered frontmatter |
| `/reflect` project notes (`output_generator.py`) | `reflect-skill` | `to_okf` on the frontmatter dict |
| Mini-learning and permission-reply hooks | `reflect-mini` | `to_okf`, rendered by `render_frontmatter` (a fallback renderer keeps `type: learning` if the module cannot be imported) |
| `reflect add` | `reflect-cli` | `normalize_note` on the stored copy |
| Fleet importer | `reflect-fleet-import` | `to_okf` before `yaml.safe_dump` |
| Team share (`write_flow._copy_into_team`) | `reflect-share` | `normalize_note` on the team copy; the local source is not rewritten |

## Example

Input note (invented):

```yaml
---
type: LEARNING
id: lrn-hooks-exit-zero-abc123
created: 2026-04-24
confidence: high
title: "Hooks must exit 0"
key_insight: "Hooks must always exit 0. Otherwise the harness surfaces errors."
forget_after: 2026-12-31
source_path: /tmp/session.jsonl
---
```

After `reflect add` (the `type` line is replaced in place; the other additions are appended; body and every other line are untouched):

```yaml
---
type: learning
id: lrn-hooks-exit-zero-abc123
created: 2026-04-24
confidence: high
title: "Hooks must exit 0"
key_insight: "Hooks must always exit 0. Otherwise the harness surfaces errors."
forget_after: 2026-12-31
source_path: /tmp/session.jsonl
description: "Hooks must always exit 0. Otherwise the harness surfaces errors."
generated: {by: reflect-cli/5.2.5, at: "2026-04-24T00:00:00Z"}
sources: [{id: source, resource: "/tmp/session.jsonl"}]
stale_after: "2026-12-31T00:00:00Z"
---
```

This output was produced by running the branch's `okf.normalize_note` on the input.

## Safe rewriting

`normalize_note` and `set_lifecycle_fields` edit only the lines of the keys being set. Each rewrite is re-parsed and compared with the intended result before it is returned.

| Case | Behaviour |
|---|---|
| No OKF key missing | Returned unchanged. `reflect add` then copies bytes and file mode verbatim. |
| No frontmatter | A fresh block is derived from the body and prepended. |
| Duplicate keys, flow-style frontmatter, unterminated, glued or BOM-prefixed fence | `ValueError`. `reflect add` stores the note verbatim and prints a warning. Team share falls back to a verbatim copy. |
| CRLF notes | Line endings preserved. |
| Idempotence | Normalizing twice yields the same bytes. |

## Parser change

The PR changes every note reader (`reflect add`, `reflect serve`, fleet import, team share, entity extraction) to split frontmatter on `---` delimiter lines instead of any `---` text. A value such as `title: cost --- benefit` used to truncate the block. Consequences:

- The opening fence may carry a YAML comment (`--- # note`).
- Notes whose closing fence is glued to the last value (written by `/reflect` without PyYAML before the fix) are still read, but never rewritten in place.
- The generated doc id can change for a note that was previously misparsed, leaving a duplicate beside the old copy. The PR's changelog entry says to run `reflect reindex` after upgrading and dedupe such pairs.

## Lifecycle edits (Rule S9 carve-out)

Rule S9 keeps per-query telemetry out of note frontmatter. The PR rewords it to "no telemetry in frontmatter" and allows event-driven edits to three keys, only through `okf.set_lifecycle_fields`:

| Key | Values | Notes |
|---|---|---|
| `status` | `draft`, `stable`, `deprecated`; absent means stable | Observation notes keep their own `active` or `retired` and are never touched by the serve stamp. |
| `verified` | one `{by, at}` mapping or a non-empty list, stored as a list | `by` is an actor (`human:<id>`, `process:<id>`, `<producer>/<version>`); `at` needs an explicit offset. |
| `stale_after` | ISO 8601 instant with offset | A bare date raises `ValueError`. |

Pass `None` to remove a key; the call is a no-op when nothing would change. The volatile ranking fields (`recall_count`, `importance`, and so on) stay out of notes and remain in the `learning_signals` table.

### Archive and restore in `reflect serve`

```
 archive:  documents/<id>.md ──move pair──▶ archived/<id>.md
                                            status: deprecated
                                            archived/<stem>.okf-prior-status = prior value
 restore:  archived/<id>.md ──move pair──▶ documents/<id>.md
                                            status = prior value, marker deleted
```

- The stamp runs after the move. If it fails, the archive still stands and the failure goes to the reflect error sink as `okf_status_stamp_failed`.
- A note already `deprecated`, or carrying a `status` outside OKF's vocabulary, is left untouched and gets no marker, so restore leaves it untouched too.
- The marker file lives in `archived/` only and is not a `*.md`, so archive listings ignore it.

## Schema changes

`schemas/frontmatter.schema.json` gains optional `type`, `description`, `resource`, `sources`, `generated`, `verified`, `status`, `stale_after`. Required keys stay `title`, `created`, `confidence`, so pre-profile notes keep validating. A conditional keeps `status` to `draft | stable | deprecated`, except for `type: observation` where `active | retired` is also allowed.

## Not in the PR

| Item | State |
|---|---|
| `reflect export --format okf` | not implemented |
| Generated `index.md` or `log.md` per directory, root `okf_version` | not implemented |
| OKF bundle provider for `/reflect:ingest` | not implemented |
| A `reflect okf lint` command | not implemented (conformance checks exist as library functions) |
| Backfill of existing notes (the PR describes about 56% of a 3,000-note sample as lacking `type`) | listed as a follow-up in the PR; needs a dry run and a backup first |
| Trust tiers derived from `generated` and `verified` | not implemented; the profile only writes the keys |

Why these gaps and how the PR departs from the original recommendation is in [OKF vs reflect](/ainb-reflect-memory/design/okf-vs-reflect/).

## Verification state

The PR description reports `pytest tests --ignore=tests/eval` at 374 passed and 18 skipped, the same 15 pre-existing failures in `plugin/tests` as `main`, and import checks on Python 3.9 and 3.10. These are the author's reported results, not re-run for this page. The branch tests cover, among others: vendored-copy equality, YAML round-trip of rendered values, every writer producing a conformant note, `reflect add` preserving bytes and mode, CRLF survival, archive and restore round trips, and notes with `---` inside values.
