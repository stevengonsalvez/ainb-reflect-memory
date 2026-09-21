"""Remove what a note left in the shared nano-graphrag store once its label
puts it above the floor.

The floor (``LearningsGraphEngine._floor_label``) stops a restricted or pii
note from being handed to nano-graphrag. It cannot undo an earlier index of
the same note under a shareable label: that left a full_docs row, its
text_chunks, their chunk vectors, graph nodes and edges sourced from those
chunks, entity vectors for those nodes, and community reports and cached
model answers built over them. ``purge_notes`` deletes all of that by doc id.

A stored document belongs to a note when it is the note, or the note with
only its ``classification`` changed: same body, same frontmatter apart from
that one key. A title alone is not an identity (an unrelated note can share
it), so a relabel that also edits the note is not matched here.

A graph node or edge also sourced from another document cannot keep its row:
nano-graphrag merges every source's description into one string (and may
summarise it), so the purged note's text cannot be cut back out. Such a row
is deleted with its entity vector, its edges and every community report that
covers it; the remaining sources' documents and chunks stay.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from typing import Any

from reflect_kb.frontmatter import split_frontmatter

__all__ = ["purge_notes"]

SEP = "<SEP>"  # nano-graphrag's GRAPH_FIELD_SEP
GRAPH_NAMESPACE = "chunk_entity_relation"
_KV = "reflect_memory.ng_kv"
_NODES = "reflect_memory.ng_graph_nodes"
_EDGES = "reflect_memory.ng_graph_edges"
_VECTORS = "reflect_memory.ng_vectors"


def _identity(text: str) -> tuple[str, str] | None:
    """(frontmatter without classification, body) or None when the block is
    absent or malformed, in which case only the exact text matches."""
    fm = split_frontmatter(text)
    if fm.mapping is None:
        return None
    rest = {k: v for k, v in fm.mapping.items() if k != "classification"}
    return json.dumps(rest, sort_keys=True, default=str), fm.body.strip()


def _same_note(note: str, content: str) -> bool:
    if content.strip() == note.strip():
        return True
    ours = _identity(note)
    return ours is not None and ours == _identity(content)


def _sourced_by(attrs: dict[str, Any] | None, chunk_ids: set[str]) -> bool:
    """True when the row's recorded provenance names a purged chunk. A row
    with no provenance is not ours to remove."""
    source = (attrs or {}).get("source_id")
    return isinstance(source, str) and any(p in chunk_ids for p in source.split(SEP) if p)


def _covers(report: Any, nodes: set[str], edges: set[tuple[str, ...]], chunk_ids: set[str]) -> bool:
    report = report if isinstance(report, dict) else {}
    return bool(
        nodes & set(report.get("nodes") or ())
        or edges & {tuple(sorted(e)) for e in report.get("edges") or () if isinstance(e, (list, tuple))}
        or chunk_ids & set(report.get("chunk_ids") or ())
    )


def _forget_clusters(cur: Any, ws: str, stale: set[str]) -> None:
    """Drop the deleted communities from surviving nodes' ``clusters``. Local
    search looks up the report of every cluster a node lists and raises
    KeyError on one that is gone; global search reads the same membership."""
    from psycopg.types.json import Jsonb

    cur.execute(
        f"select node_id, attrs from {_NODES} where workspace_id=%s and namespace=%s and attrs ? 'clusters'",
        (ws, GRAPH_NAMESPACE),
    )
    for node_id, attrs in cur.fetchall():
        try:
            clusters = json.loads(attrs["clusters"])
        except (TypeError, ValueError):
            continue
        kept = [c for c in clusters if not (isinstance(c, dict) and str(c.get("cluster")) in stale)]
        if len(kept) != len(clusters):
            cur.execute(
                f"update {_NODES} set attrs=%s where workspace_id=%s and namespace=%s and node_id=%s",
                (Jsonb({**attrs, "clusters": json.dumps(kept)}), ws, GRAPH_NAMESPACE, node_id),
            )


def purge_notes(dsn: str, workspace_id: str, notes: Iterable[str], *, connect: Callable | None = None) -> int:
    """Delete every ng_* row derived from ``notes`` in ``workspace_id``;
    return how many stored documents were removed."""
    from nano_graphrag._utils import compute_mdhash_id

    from reflect_kb.postgres.dsn import connect_secure

    notes = [n for n in notes if n and n.strip()]
    if not notes:
        return 0
    ws = str(workspace_id)
    conn = connect_secure(dsn, what="the shared-store DSN", connect=connect)
    with conn, conn.transaction(), conn.cursor() as cur:
        # Bind the tenant for FORCE RLS; every statement below is also
        # scoped by workspace_id explicitly.
        cur.execute("select set_config('app.current_workspace', %s, true)", (ws,))
        cur.execute(f"select key, value->>'content' from {_KV} where workspace_id=%s and namespace='full_docs'", (ws,))
        doc_ids = [key for key, content in cur.fetchall() if any(_same_note(n, content or "") for n in notes)]
        if not doc_ids:
            return 0
        cur.execute(
            f"select key from {_KV} where workspace_id=%s and namespace='text_chunks' "
            "and value->>'full_doc_id' = any(%s)",
            (ws, doc_ids),
        )
        chunk_ids = {row[0] for row in cur.fetchall()}
        cur.execute(f"delete from {_KV} where workspace_id=%s and namespace='full_docs' and key = any(%s)", (ws, doc_ids))
        if chunk_ids:
            cur.execute(
                f"delete from {_KV} where workspace_id=%s and namespace='text_chunks' and key = any(%s)",
                (ws, list(chunk_ids)),
            )
            cur.execute(
                f"delete from {_VECTORS} where workspace_id=%s and namespace='chunks' and id = any(%s)",
                (ws, list(chunk_ids)),
            )

        cur.execute(f"select node_id, attrs from {_NODES} where workspace_id=%s and namespace=%s", (ws, GRAPH_NAMESPACE))
        nodes = sorted(node_id for node_id, attrs in cur.fetchall() if _sourced_by(attrs, chunk_ids))
        if nodes:
            cur.execute(
                f"delete from {_EDGES} where workspace_id=%s and namespace=%s and (source = any(%s) or target = any(%s))",
                (ws, GRAPH_NAMESPACE, nodes, nodes),
            )
            cur.execute(
                f"delete from {_NODES} where workspace_id=%s and namespace=%s and node_id = any(%s)",
                (ws, GRAPH_NAMESPACE, nodes),
            )
            cur.execute(
                f"delete from {_VECTORS} where workspace_id=%s and namespace='entities' and id = any(%s)",
                (ws, [compute_mdhash_id(n, prefix="ent-") for n in nodes]),
            )
        cur.execute(f"select source, target, attrs from {_EDGES} where workspace_id=%s and namespace=%s", (ws, GRAPH_NAMESPACE))
        removed_edges: set[tuple[str, ...]] = set()
        for source, target, attrs in cur.fetchall():
            if _sourced_by(attrs, chunk_ids):
                removed_edges.add(tuple(sorted((source, target))))
                cur.execute(
                    f"delete from {_EDGES} where workspace_id=%s and namespace=%s and source=%s and target=%s",
                    (ws, GRAPH_NAMESPACE, source, target),
                )

        # A community report is written over its nodes' and edges'
        # descriptions and its sub-communities' reports, so one that covers a
        # removed node or edge, or a purged chunk, carries the text. The rest
        # stay: nano-graphrag rebuilds reports only when a new document
        # arrives, so dropping all of them left global search empty after
        # every relabel.
        cur.execute(f"select key, value from {_KV} where workspace_id=%s and namespace='community_reports'", (ws,))
        stale = {key for key, report in cur.fetchall() if _covers(report, set(nodes), removed_edges, chunk_ids)}
        if stale:
            cur.execute(
                f"delete from {_KV} where workspace_id=%s and namespace='community_reports' and key = any(%s)",
                (ws, sorted(stale)),
            )
            _forget_clusters(cur, ws, stale)
        # Cached model answers are keyed by prompt hash and name no source, so
        # which ones quote the note is unknowable. The cache is only
        # memoisation: dropping it costs recomputation, never data.
        cur.execute(f"delete from {_KV} where workspace_id=%s and namespace='llm_response_cache'", (ws,))
    return len(doc_ids)
