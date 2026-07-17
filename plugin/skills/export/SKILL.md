---
name: reflect:export
description: |
  Export or import the reflect knowledge base as a deterministic,
  git-friendly tarball — the learnings markdown corpus plus the reflect.db
  rows in one snapshot. Use to back up the KB, move it to another machine,
  or restore it: `export` writes the tarball, `import` restores it (with an
  optional GraphRAG reindex). Answers "snapshot my reflect KB", "move my
  learnings to another machine", or "restore my knowledge base from a backup".
version: "1.0.0"
user-invocable: true
triggers:
  - reflect:export
  - reflect export
  - reflect import
  - export knowledge base
  - import knowledge base
  - backup reflect kb
  - restore reflect kb
allowed-tools:
  - Read
  - Bash
---

# /reflect:export — Knowledge-base snapshot (export / import)

Snapshots the reflect knowledge base — the `~/.learnings` markdown corpus plus
the `reflect.db` rows — into a single deterministic, git-friendly tarball, and
restores it on the same or another machine. Two scripts back this:
`kb_export.py` writes the tarball; `kb_import.py` restores it (re-running the
GraphRAG index by default).

## When to Use

- "Back up / snapshot my reflect knowledge base."
- "Move my learnings + reflect.db to another machine."
- "Restore the KB from a tarball I exported earlier."

## Resolve the scripts

Locate the export/import scripts robustly across deploy layouts (prefer the
running plugin, else the newest cached version):

```bash
# Resolve kb_export.py.
EXPORT_PY=""
for cand in \
  "${CLAUDE_PLUGIN_ROOT:-}/plugin/scripts/kb_export.py" \
  $(ls -t "$HOME"/.claude/plugins/cache/*/reflect/*/plugin/scripts/kb_export.py "$HOME"/.claude/plugins/cache/*/reflect/*/scripts/kb_export.py 2>/dev/null); do
  if [ -n "$cand" ] && [ -f "$cand" ]; then EXPORT_PY="$cand"; break; fi
done
if [ -z "$EXPORT_PY" ]; then
  echo "kb_export.py not found — install/update the reflect plugin:"
  echo "  claude plugin update reflect@ainb-reflect-memory"
  exit 1
fi
# kb_import.py ships alongside kb_export.py.
IMPORT_PY="$(dirname "$EXPORT_PY")/kb_import.py"
```

## Export

```bash
# Positional: the output tarball path. Optional --db / --learnings override
# the defaults (reflect.db from config; learnings home ~/.learnings).
python3 "$EXPORT_PY" kb.tar
# python3 "$EXPORT_PY" kb.tar --db /path/to/reflect.db --learnings /path/to/.learnings
```

Prints how many documents and DB rows (across how many tables) it wrote.

## Import

```bash
# Positional: the tarball produced by the export above. Restores onto this
# machine and re-runs the GraphRAG index by default.
python3 "$IMPORT_PY" kb.tar
# --db / --learnings  : target reflect.db / learnings home (default: config)
# --no-reindex        : skip the GraphRAG reindex after restore
# --force             : overwrite a non-empty target (default: refuse)
python3 "$IMPORT_PY" kb.tar --force
```

Prints how many documents and DB rows it restored. By default it refuses to
overwrite a non-empty target — pass `--force` to replace it.
