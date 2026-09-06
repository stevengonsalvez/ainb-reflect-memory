"""Remove what a note left in the shared nano-graphrag store once its label
puts it above the floor.

The floor (``LearningsGraphEngine._floor_label``) stops a restricted or pii
note from being handed to nano-graphrag. It cannot undo an earlier index of
the same note under a shareable label: that left a full_docs row, its
text_chunks, their chunk vectors, graph nodes and edges sourced from those
chunks, entity vectors for those nodes, and community reports and cached
model answers built over them. ``purge_notes`` deletes all of that by doc id.

A stored document belongs to a note when its content is the note, its body
is the note's body (a relabel changes only the frontmatter), or its title is
the note's title (a relabel with edits). Nodes and edges also sourced from
other documents keep those sources and lose only the purged chunks.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from reflect_kb.frontmatter import split_frontmatter

__all__ = ["purge_notes"]

SEP = "<SEP>"  # nano-graphrag's GRAPH_FIELD_SEP
GRAPH_NAMESPACE = "chunk_entity_relation"
DERIVED_NAMESPACES = ("community_reports", "llm_response_cache")
_KV = "reflect_memory.ng_kv"
_NODES = "reflect_memory.ng_graph_nodes"
_EDGES = "reflect_memory.ng_graph_edges"
_VECTORS = "reflect_memory.ng_vectors"


def _same_note(note: str, content: str) -> bool:
    if content.strip() == note.strip():
        return True
    n, c = split_frontmatter(note), split_frontmatter(content)
    if n.body.strip() and n.body.strip() == c.body.strip():
        return True
    title = (n.mapping or {}).get("title")
    return bool(title) and title == (c.mapping or {}).get("title")


def _without_chunks(attrs: dict[str, Any] | None, chunk_ids: set[str]) -> tuple[dict[str, Any] | None, bool]:
    """(attrs to keep or None when nothing sources the row any more, changed)."""
    attrs = dict(attrs or {})
    source = attrs.get("source_id")
    if not isinstance(source, str) or not source:
        return attrs, False  # no provenance recorded: not ours to remove
    parts = [p for p in source.split(SEP) if p]
    remaining = [p for p in parts if p not in chunk_ids]
    if not remaining:
        return None, True
    if len(remaining) == len(parts):
        return attrs, False
    attrs["source_id"] = SEP.join(remaining)
    return attrs, True


def purge_notes(dsn: str, workspace_id: str, notes: Iterable[str], *, connect: Callable | None = None) -> int:
    """Delete every ng_* row derived from ``notes`` in ``workspace_id``;
    return how many stored documents were removed."""
    import psycopg
    from nano_graphrag._utils import compute_mdhash_id
    from psycopg.types.json import Jsonb

    notes = [n for n in notes if n and n.strip()]
    if not notes:
        return 0
    connect = connect or psycopg.connect
    ws = str(workspace_id)
    with connect(dsn) as conn, conn.transaction(), conn.cursor() as cur:
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
        for node_id, attrs in cur.fetchall():
            kept, changed = _without_chunks(attrs, chunk_ids)
            if kept is None:
                cur.execute(
                    f"delete from {_EDGES} where workspace_id=%s and namespace=%s and (source=%s or target=%s)",
                    (ws, GRAPH_NAMESPACE, node_id, node_id),
                )
                cur.execute(
                    f"delete from {_NODES} where workspace_id=%s and namespace=%s and node_id=%s",
                    (ws, GRAPH_NAMESPACE, node_id),
                )
                cur.execute(
                    f"delete from {_VECTORS} where workspace_id=%s and namespace='entities' and id=%s",
                    (ws, compute_mdhash_id(node_id, prefix="ent-")),
                )
            elif changed:
                cur.execute(
                    f"update {_NODES} set attrs=%s where workspace_id=%s and namespace=%s and node_id=%s",
                    (Jsonb(kept), ws, GRAPH_NAMESPACE, node_id),
                )
        cur.execute(f"select source, target, attrs from {_EDGES} where workspace_id=%s and namespace=%s", (ws, GRAPH_NAMESPACE))
        for source, target, attrs in cur.fetchall():
            kept, changed = _without_chunks(attrs, chunk_ids)
            if kept is None:
                cur.execute(
                    f"delete from {_EDGES} where workspace_id=%s and namespace=%s and source=%s and target=%s",
                    (ws, GRAPH_NAMESPACE, source, target),
                )
            elif changed:
                cur.execute(
                    f"update {_EDGES} set attrs=%s where workspace_id=%s and namespace=%s and source=%s and target=%s",
                    (Jsonb(kept), ws, GRAPH_NAMESPACE, source, target),
                )
        # Community reports and cached model answers are built over the
        # chunks above and carry their text; nano-graphrag rebuilds them on
        # the next insert.
        cur.execute(f"delete from {_KV} where workspace_id=%s and namespace = any(%s)", (ws, list(DERIVED_NAMESPACES)))
    return len(doc_ids)
