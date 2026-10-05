---
title: Universal install and team KB (v4 spec)
description: The April 2026 spec for a standalone, cross-harness reflect with Nix packaging and a shared team KB, compared item by item with what shipped as the 5.0 standalone repo.
sidebar:
  order: 13
---

:::note[Design record, mostly superseded]
Source: `plugin/docs/design-records/2026-04-23-v4-universal-install-spec.md` (an interview-derived spec, also kept in agents-in-a-box as `plans/reflect-v4-universal-install-spec.md`). The spec was written for a "v4" release; by the time the work landed, version 4.0.0 had been used by the [cost rearchitecture](/ainb-reflect-memory/design/cost-rearchitecture/), so this effort shipped as **5.0.0**. The "Shipped" column below was checked against this repo at `main`.
:::

## What the spec wanted

Extract reflect from the toolkit into a standalone, cross-harness tool with one deterministic install path, two knowledge bases (a personal one that stays local and a team one shared through git) and quality gates on what gets shared.

```text
reflect-kb (the tool)                    team-kb (the content, one per team)
  CLI, recall, skills, hooks   ──reads──▶   documents/*.md + .entities.yaml
  harness adapters                          pre-commit + CI schema validation
  Nix flake + pipx fallback                 CODEOWNERS

personal KB at ~/.learnings/  (never in a repo)
```

## Spec versus shipped

| Spec item | Shipped? | Notes |
|---|---|---|
| Standalone repo for the tool | **Yes** | `stevengonsalvez/ainb-reflect-memory`, 5.0.0 (2026-06-20); tagged releases cut by `release.yml` |
| Python package with the CLI, `recall.py`, skills, hooks, schema | **Yes** | `src/reflect_kb/`, `plugin/`, `schemas/frontmatter.schema.json` |
| Nix flake as the primary install | **Partly** | `flake.nix` exists, but it still contains `lib.fakeHash` placeholders for a packaged dependency, so it has not been proven to build. Not documented as an install path |
| `pipx` as the fallback | **Changed** | the documented install is `uv tool install --upgrade --torch-backend cpu 'git+https://github.com/stevengonsalvez/ainb-reflect-memory.git[graph]'` |
| `reflect adapter install <harness>` | **No** | the command does not exist. Adapters are scripts: `python3 plugin/adapters/codex/codex_adapter.py install`, and the same for `copilot`; Claude Code installs as a plugin from the marketplace; a Hermes adapter was added later |
| Claude Code full support; Codex and Copilot "slash-command only, no hooks" | **Reversed** | the spec's own banner records it: Codex and Copilot gained real hook systems in 2026, and both adapters now wire reflect hooks (SessionStart recall, drain, queue producers). Only Copilot's per-prompt recall stays manual |
| Two-tier KB with a configurable team KB path | **Partly** | `write_flow.py` implements the routing (below); there is no `reflect team clone/init/sync` command and no `reflect share` command in the CLI |
| Confidence-gated write flow | **Library only** | `src/reflect_kb/write_flow.py` routes by confidence: HIGH commits and pushes to team-kb main, MED branches and opens a draft PR via `gh`, LOW writes a pointer to `~/.learnings/review-queue/`, missing counts as MED. It is covered by `tests/test_write_flow.py` but no CLI command calls it |
| Frontmatter schema plus pre-commit validation | **Yes (schema)** | `schemas/frontmatter.schema.json` and `scripts/validate_frontmatter.py` (with a test); there is no team-KB template repo here |
| JSONL metrics and an opt-in dashboard | **Partly** | `src/reflect_kb/metrics.py` appends to `~/.learnings/metrics.jsonl` (rotated at 10 MB) and `reflect metrics stats` reads it; the dashboard POST client from the spec is not in the CLI |
| MCP server wrapper | Deferred then, **not built** | |
| Cursor support, Windows native | Out of scope | |

## Decisions the spec recorded

| Decision | Rationale as recorded | Outcome |
|---|---|---|
| Nix primary, pipx fallback | Nix fixes the broken nano-graphrag transitive chain (graspologic, numba, llvmlite) | the same problem was solved differently: a small networkx shim (`graspologic_shim.py`) plus version floors in the `[graph]` extra (commit `1b3b8ec`) |
| Two repos, personal KB local | tool and content have different cadences | tool repo done; team-KB repo template not built |
| Hybrid write flow by confidence | matches the existing confidence field | implemented as a library |
| Rebuild indexes locally on clone, do not commit them | index files churn and do not merge | holds: the markdown is the source of truth and `reflect reindex` rebuilds |
| Claude gets hooks, others manual | hook parity "is a rabbit hole" | reversed, see above |

## Open questions the spec left, and where they stand

- Auth for private team-KB repos (PAT or SSH): not addressed in the code.
- Naming (`reflect-kb` vs alternatives): the PyPI-style package name is `reflect-kb`, the repo is `ainb-reflect-memory`, the plugin is `reflect`.
- Embedding model bump strategy: the embedder is swappable with `REFLECT_EMBED_MODEL`, but vectors are dimension-specific and a swap requires `reflect reindex --force`. There is no automatic migration.

## What to read for the current install

[Claude Code install](/ainb-reflect-memory/install/claude-code/), [Codex](/ainb-reflect-memory/install/codex/), [Copilot](/ainb-reflect-memory/install/copilot/), [Hermes](/ainb-reflect-memory/install/hermes/), and the [Configuration reference](/ainb-reflect-memory/reference/configuration/).
