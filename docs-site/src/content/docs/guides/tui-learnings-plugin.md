---
title: ainb TUI learnings plugin
description: Browse, search and graph the reflect knowledge base from inside the ainb terminal UI, read-only.
sidebar:
  order: 20
---

`learnings` is the read-only memory browser plugin for the [ainb](https://github.com/stevengonsalvez/agents-in-a-box) terminal UI. reflect writes the knowledge base; this plugin only reads it. It has three tabs (Browse, Search, Graph) and never writes notes or re-indexes.

Prefer a browser? The web equivalent, which can also curate, is the [memory browser](/ainb-reflect-memory/guides/memory-browser/).

```
 reflect (hooks, /reflect, reflect add)        learnings (ainb TUI)
 ──────────────────────────────────────        ────────────────────
 capture + index  ──▶  ~/.learnings  ──read──▶  Browse | Search | Graph
                       ~/.cache/qmd
```

![The learnings Browse tab: notes listed by id with confidence and a filter chip bar](/ainb-reflect-memory/img/tui-learnings-browse.png)

## Open it

| From | How |
|---|---|
| ainb home / session list | `m` |
| ainb slash palette | `/recall` or `/memory` (aliases) |
| shell, no UI | `ainb learnings search <query>` (see [Headless search](#headless-search)) |

:::note
`/recall` in the ainb palette opens this screen. It is not the `reflect:recall` skill that runs inside Claude Code, Codex or Copilot; for that see [Recall pipeline](/ainb-reflect-memory/concepts/recall-pipeline/).
:::

The plugin is lazy-spawned: it stays dormant until you open the screen or run the CLI, and idles out after 600 seconds.

## What it reads

| Artifact | Default path | Used by |
|---|---|---|
| Learning notes (`*.md`, YAML frontmatter) | `~/.learnings/documents/learnings` | Browse, Detail |
| Entity sidecars (`<id>.entities.yaml`) | next to each note | Graph (typed edges) |
| nano-graphrag cache | `~/.learnings/nano_graphrag_cache` | Graph community view |
| qmd index | `~/.cache/qmd/index.sqlite`, collection `learnings` | Search |

Search shells out to the `qmd` binary. The plugin does not open the sqlite file itself. Sidecars and the graph cache are read directly from disk.

:::caution[Default notes path does not match current reflect]
The plugin default `learnings_dir` is `~/.learnings/documents/learnings`, the older layout. Current `reflect add` writes notes flat into `~/.learnings/documents/` (see [KB format](/ainb-reflect-memory/reference/kb-format/)), and the plugin scans one directory without recursing. On a fresh install Browse can therefore show zero notes. Point the plugin at the real directory:

```toml
# ~/.agents-in-a-box/config/config.toml
[plugins.learnings]
learnings_dir = "~/.learnings/documents"
```

Per-project shards (`~/.learnings/shards/<project>/documents`) are separate directories; set `learnings_dir` to one of them to browse that shard.
:::

## Browse

Lists every note by id with its confidence, above a chip bar (`scope`, `conf`, `category`, `source`, `project`).

| Key | Action |
|---|---|
| `↑` `↓` / `j` `k` | Move selection |
| `f` | Cycle the `scope` chip through all values, then back to `*` |
| `⏎` | Open the Detail pane |
| `Backspace` / `Esc` | Close Detail |

Only the `scope` chip is interactive. The other four chips render `*` and cannot be changed yet.

The Detail pane shows the title, `key_insight`, the Markdown body, the entity list, typed relationships as `source --type--> target`, and a provenance line (`source_tool`, `source_path`, confidence). While Detail is open every other key is swallowed so the list behind it cannot move.

## Search

![The Search tab with a query typed in the search box](/ainb-reflect-memory/img/tui-learnings-search.png)

`/` jumps to Search from any tab and focuses the query box. Type, then `⏎` to run it.

- Printable keys, including `j`, `k` and `g`, type into the box. Result selection uses the arrow keys only.
- `Backspace` deletes a character and clears stale results.
- Each query runs twice on a worker thread, so the pane never blocks:
  1. BM25 via `qmd search --json` (no LLM, near-instant) paints first.
  2. Semantic via `qmd query --json -C 20` (LLM rerank, slow when cold) swaps in when it lands. A subtle "refining" indicator shows in between.
- If the semantic pass fails after BM25 painted, the BM25 hits stay on screen.
- A search is cut off at an 8 second ceiling and shows "search timed out". Superseded or timed-out `qmd` children are killed.
- `⏎` on a hit opens the same Detail pane. A hit with no matching local note does nothing.

This is the same retrieval family the reflect hooks run automatically at session start. Here you drive it by hand.

### Headless search

```bash
ainb learnings search "redis connection pooling"        # semantic
ainb learnings search rust async --bm25                 # BM25 only, no LLM
ainb learnings search clap -k 5                         # top 5 (default 10)
ainb learnings search clap --format json                # id, score, title, file
```

Text output is one line per hit: score, id, title. Exit code 2 on usage errors or when the plugin is not staged.

## Graph

`g` focuses the Graph tab from anywhere, except while the Search box has focus (there `g` types into the query). The tab has three views over the same typed entity graph.

### 1. Entity neighbourhood (default)

Pick an entity from the list to see its outgoing typed edges as `entity --type--> neighbour`, aggregated across all notes' `relationships[]`.

![Graph tab, neighbourhood view with typed edges](/ainb-reflect-memory/img/tui-learnings-graph-neighbourhood.png)

### 2. Community clusters

`c` toggles to the nano-graphrag community view: one row per cluster from `kv_store_community_reports.json`, with title, member count and impact rating.

![Graph tab, community cluster view](/ainb-reflect-memory/img/tui-learnings-community.png)

### 3. Radial ego map

`v` swaps in a spatial local graph drawn on the character grid. The selected entity sits in the centre and its neighbours fan out on a ring as boxed nodes, joined by edges with arrowheads on directed relationships. Layout is deterministic (no physics, no randomness), so the same KB draws the same map.

Edge label colours: `solves` green, `caused_by` and `causes` red, `requires` blue. Everything else is grey. Only `relates_to` is undirected (no arrowhead); every other type is directed. reflect sidecars can carry more relationship types than the four coloured ones (`enables`, `prevents`, `supersedes`, `uses` and others, see [KB format](/ainb-reflect-memory/reference/kb-format/)); those render grey with an arrowhead.

The map shows the centre plus 1 hop by default (`h` toggles 2 hops). Ring 1 is capped at 15 nodes, ordered by edge strength then name; the overflow folds into one `[+N more]` node that `e` expands.

| Centred on one entity | After `⏎` recentre on a neighbour |
|---|---|
| ![Map centred on one entity](/ainb-reflect-memory/img/tui-learnings-map-centre.png) | ![Map recentred on a neighbour](/ainb-reflect-memory/img/tui-learnings-map-recentred.png) |

| Capped: `nodes:15 (+3)` | Expanded with `e`: `nodes:18` |
|---|---|
| ![A hub with 15 neighbours and a +3 more node](/ainb-reflect-memory/img/tui-learnings-map-overflow-cap.png) | ![The same hub after pressing e](/ainb-reflect-memory/img/tui-learnings-map-expanded.png) |

No whole-KB view and no force-directed layout exist; the ego map is intentionally cheap and deterministic.

### Graph keymap

| Key | Action |
|---|---|
| `g` | Focus Graph |
| `↑` `↓` / `j` `k` | Move selection (neighbourhood and community lists) |
| `c` | Toggle neighbourhood and community clusters |
| `v` | Toggle the radial map |
| `↑` `↓` | In map: move across rings |
| `←` `→` | In map: orbit within a ring |
| `⏎` / click | In map: recentre on the selected node (animated) |
| `h` | In map: toggle 1 or 2 hops |
| `e` | In map: expand `[+N more]` |
| `o` | In map: open the notes behind the entity (picker if several, then Detail) |
| `Backspace` | In map: leave the map. In text views: release graph focus. |

`Tab`, `/` and `g` always work, so you can leave a focused graph. `Esc` is reserved by the host.

## Configuration

User values live in `config.toml` under `[plugins.learnings]` and are injected at plugin init.

| Key | Default | Meaning |
|---|---|---|
| `learnings_dir` | `~/.learnings/documents/learnings` | Notes and sidecars (Browse, Graph). Not recursive. |
| `graph_cache` | `~/.learnings/nano_graphrag_cache` | graphml and community JSON |
| `qmd_index` | `~/.cache/qmd/index.sqlite` | qmd sqlite index |
| `qmd_collection` | `learnings` | qmd collection name |

Any directory with the same note and sidecar layout works, so you can point the plugin at a different store.

## Capabilities

The plugin manifest asks for the minimum:

| Capability | Value |
|---|---|
| `read_paths` | `~/.learnings`, `~/.cache/qmd` |
| `spawn_subprocess` | `qmd`, `learnings` |
| `write_plugin_data` | true (UI state only) |
| `event_bus` | true (refresh snapshots) |
| `cli_namespaces` | `learnings` (powers `ainb learnings search`) |

## Setup order

1. Install and run reflect so the KB exists: see [Install](/ainb-reflect-memory/install/claude-code/) for your harness. `ainb reflect bootstrap` installs the `reflect-kb[graph]` engine through `uv` and prints any missing system tools.
2. Make sure `qmd` is on `PATH` and its `learnings` collection is indexed. `reflect reindex` rebuilds the GraphRAG index and, when `qmd` is installed, runs `qmd update` and `qmd embed`. Without a qmd index, Search returns nothing while Browse still works.
3. Fix `learnings_dir` if Browse is empty (see the caution above).
4. Press `m` in ainb.

Related: [Architecture](/ainb-reflect-memory/concepts/architecture/), [Index and storage](/ainb-reflect-memory/concepts/index-and-storage/), [Troubleshooting](/ainb-reflect-memory/guides/troubleshooting/).
