"""Remove what a note left in the shared store once its label puts it above
the floor.

The floor (``LearningsGraphEngine._floor_label``) stops a restricted or pii
note from being handed to nano-graphrag, and ``mirror_note`` refuses to write
the same note to the broker's tables. Neither can undo an earlier write of
the same note under a shareable label. That write left, in one database:

* nano-graphrag's derived store, a full_docs row, its text_chunks, their
  chunk vectors, graph nodes and edges sourced from those chunks, entity
  vectors for those nodes, and community reports and cached model answers
  built over them;
* the broker's tables, a memory_items row (pinned and shareable, so
  ``search_pinned_memory`` serves its full content) with the entities and
  edges the mirror derived from it.

``purge_notes`` deletes both halves in one transaction, because ``reindex``
skips a note above the floor before mirroring it, so neither half ever heals
itself.

A stored document belongs to a note when it is the note, or the note with
only its ``classification`` changed: same body, same frontmatter apart from
that one key. A title alone is not an identity (an unrelated note can share
it), so a relabel that also edits the note is not matched here.

A graph node or edge also sourced from another document cannot keep its row:
nano-graphrag merges every source's description into one string (and may
summarise it), so the purged note's text cannot be cut back out. Such a row
is deleted with its entity vector, its edges and every community report that
covers it, and the surviving sources' documents are reopened for the next
reindex (see ``_reopen_surviving_documents``).
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
# The broker's tables, written by reflect_kb.postgres.mirror.
_ITEMS = "reflect_memory.memory_items"
_MIRROR_ENTITIES = "reflect_memory.entities"
_MIRROR_EDGES = "reflect_memory.edges"
# Shortest entity name and description read as the words of the note that
# produced the row (an acronym is three, a description is a phrase).
_WORD = 3
_PHRASE = 12


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


def _other_sources(attrs: dict[str, Any] | None, chunk_ids: set[str]) -> set[str]:
    """The chunks a removed row was also built from, the purged ones apart.
    Those chunks belong to notes that stay shareable."""
    source = (attrs or {}).get("source_id")
    if not isinstance(source, str):
        return set()
    return {p for p in source.split(SEP) if p and p not in chunk_ids}


def _matching(forms: dict[int, list[str]], content: str | None) -> set[int]:
    """Which notes the stored text is a copy of, given the forms each note can
    have been stored in."""
    text = content or ""
    return {i for i, variants in forms.items() if any(_same_note(v, text) for v in variants)}


def _pins(notes: list[str]) -> set[str]:
    """The ``repo@sha:path`` pins the notes carry. A pinned memory_items row
    is the one the broker serves, and the pin survives an edit to the body,
    so it matches a note that was relabelled and rewritten in one go, which
    content identity alone cannot."""
    from reflect_kb.pinning import pinned_source_uri

    found = set()
    for note in notes:
        fm = split_frontmatter(note)
        pin = pinned_source_uri(fm.mapping) if fm.mapping else None
        if pin:
            found.add(pin)
    return found


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


def _forget_sources(cur: Any, ws: str, gone: set[str]) -> None:
    """Drop the reopened chunks from surviving nodes' and edges' provenance.
    Local search reads every chunk a node names and sorts the rows by their
    content, so a name with no row behind it raises; the next reindex writes
    the name back when it re-extracts the chunk. A row whose only source was
    reopened keeps an empty ``source_id``, which nano-graphrag's splitter
    reads as no chunks at all, the same as a node it has not seen yet."""
    from psycopg.types.json import Jsonb

    for table, key_cols in ((_NODES, ("node_id",)), (_EDGES, ("source", "target"))):
        cols = ", ".join(key_cols)
        cur.execute(
            f"select {cols}, attrs from {table} where workspace_id=%s and namespace=%s and attrs ? 'source_id'",
            (ws, GRAPH_NAMESPACE),
        )
        for row in cur.fetchall():
            keys, attrs = row[:-1], row[-1]
            source = attrs.get("source_id")
            if not isinstance(source, str):
                continue
            kept = [p for p in source.split(SEP) if p and p not in gone]
            if len(kept) == len([p for p in source.split(SEP) if p]):
                continue
            where = " and ".join(f"{c}=%s" for c in key_cols)
            cur.execute(
                f"update {table} set attrs=%s where workspace_id=%s and namespace=%s and {where}",
                (Jsonb({**attrs, "source_id": SEP.join(kept)}), ws, GRAPH_NAMESPACE, *keys),
            )


def _reopen_surviving_documents(cur: Any, ws: str, chunks: set[str], purged_docs: set[str]) -> None:
    """Delete the chunks of still-shareable notes that also fed a removed node
    or edge, and the stored documents those chunks belong to, so the next
    reindex extracts them again.

    nano-graphrag inserts only documents it has not stored yet ("All docs are
    already in the storage") and, within those, only chunks it has not stored
    yet, so a node removed here stays lost for as long as the surviving
    note's rows stand. Re-extracting the node from those chunks instead would
    need model calls inside this transaction and would still have to rebuild
    the entity vectors and community reports the new descriptions invalidate;
    reopening the chunks hands that work back to the one place that already
    does it. ``reindex`` purges before it inserts, so it heals the graph
    inside the same command; ``reflect add`` prints the instruction to run
    one.

    Only the chunks that fed a removed row are reopened, so a long note's
    untouched chunks are not re-embedded. The reach is still as wide as the
    sharing: a note indexed without a sidecar contributes to the single
    ``knowledge_entry`` placeholder node (``LearningsGraphEngine``), which
    every other sidecar-less note also feeds, so purging one of those
    reopens all of them. Leaving them would be worse: the node is gone
    either way, and only a document nano-graphrag has not stored is ever
    extracted again.
    """
    if not chunks:
        return
    cur.execute(
        f"select key, value->>'full_doc_id' from {_KV} where workspace_id=%s and namespace='text_chunks' "
        "and key = any(%s)",
        (ws, sorted(chunks)),
    )
    rows = [(key, doc_id) for key, doc_id in cur.fetchall() if doc_id and doc_id not in purged_docs]
    if not rows:
        return
    keys = sorted({key for key, _ in rows})
    docs = sorted({doc_id for _, doc_id in rows})
    # The chunk vectors go with the chunks: a naive-rag hit whose chunk is
    # gone has no text to return. Both come back under the same ids on the
    # next insert, which hashes the chunk's content.
    cur.execute(f"delete from {_KV} where workspace_id=%s and namespace='text_chunks' and key = any(%s)", (ws, keys))
    cur.execute(f"delete from {_VECTORS} where workspace_id=%s and namespace='chunks' and id = any(%s)", (ws, keys))
    cur.execute(f"delete from {_KV} where workspace_id=%s and namespace='full_docs' and key = any(%s)", (ws, docs))
    _forget_sources(cur, ws, set(keys))


def _purge_derived(cur: Any, ws: str, notes: list[str]) -> set[int]:
    """Delete every ng_* row derived from ``notes``; return which notes had
    one."""
    cur.execute(f"select key, value->>'content' from {_KV} where workspace_id=%s and namespace='full_docs'", (ws,))
    matched: set[int] = set()
    doc_ids: list[str] = []
    forms = {i: [note] for i, note in enumerate(notes)}  # the graph stores the note as the caller has it
    for key, content in cur.fetchall():
        hit = _matching(forms, content)
        if hit:
            matched |= hit
            doc_ids.append(key)
    if not doc_ids:
        return matched
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
    node_rows = cur.fetchall()
    nodes = sorted(node_id for node_id, attrs in node_rows if _sourced_by(attrs, chunk_ids))
    removed_nodes = set(nodes)
    # The other sources of everything removed here, read before the rows go.
    survivors: set[str] = set()
    for node_id, attrs in node_rows:
        if node_id in removed_nodes:
            survivors |= _other_sources(attrs, chunk_ids)
    cur.execute(
        f"select source, target, attrs from {_EDGES} where workspace_id=%s and namespace=%s",
        (ws, GRAPH_NAMESPACE),
    )
    edge_rows = cur.fetchall()
    if nodes:
        from nano_graphrag._utils import compute_mdhash_id

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
    removed_edges: set[tuple[str, ...]] = set()
    for source, target, attrs in edge_rows:
        if source in removed_nodes or target in removed_nodes:
            survivors |= _other_sources(attrs, chunk_ids)  # already deleted with the node
        elif _sourced_by(attrs, chunk_ids):
            removed_edges.add(tuple(sorted((source, target))))
            survivors |= _other_sources(attrs, chunk_ids)
            cur.execute(
                f"delete from {_EDGES} where workspace_id=%s and namespace=%s and source=%s and target=%s",
                (ws, GRAPH_NAMESPACE, source, target),
            )

    # A community report is written over its nodes' and edges' descriptions
    # and its sub-communities' reports, so one that covers a removed node or
    # edge, or a purged chunk, carries the text. The rest stay: nano-graphrag
    # rebuilds reports only when a new document arrives, so dropping all of
    # them left global search empty after every relabel.
    cur.execute(f"select key, value from {_KV} where workspace_id=%s and namespace='community_reports'", (ws,))
    stale = {key for key, report in cur.fetchall() if _covers(report, removed_nodes, removed_edges, chunk_ids)}
    if stale:
        cur.execute(
            f"delete from {_KV} where workspace_id=%s and namespace='community_reports' and key = any(%s)",
            (ws, sorted(stale)),
        )
        _forget_clusters(cur, ws, stale)
    # Cached model answers are keyed by prompt hash and name no source, so
    # which ones quote the note is unknowable. The cache is only memoisation:
    # dropping it costs recomputation, never data.
    cur.execute(f"delete from {_KV} where workspace_id=%s and namespace='llm_response_cache'", (ws,))
    _reopen_surviving_documents(cur, ws, survivors, set(doc_ids))
    return matched


def _purge_mirror(cur: Any, ws: str, notes: list[str]) -> set[int]:
    """Delete the rows the mirror wrote for ``notes`` into the broker's
    tables; return which notes had one.

    The memory_items row is the leak the floor cannot close on its own: it
    keeps the note's full content, its pin and its old shareable label, so
    ``search_pinned_memory`` serves it. A row is the note's when its content
    is the note (in either form the mirror may have stored it, see below) or
    when it carries the note's pin, which an edit to the body leaves alone.

    The edges go with it: the evidence FK is ``on delete set null``, which
    would otherwise leave the note's relation types behind. So do the
    entities that carry the note's words: an endpoint of one of those edges,
    a row whose canonical name is a phrase of the note, or one whose stored
    description is. The entity table keeps no provenance, so those are the
    only handles on an entity the note contributed without a relationship,
    and they take entities another note also described. That is the safe
    direction: a deleted entity comes back on the next reindex, which
    re-mirrors every shareable note unconditionally, while a kept one goes
    on being served under the label the note no longer has.
    """
    from reflect_kb.issues.sanitize import redact_secrets

    # The mirror redacts before it writes, so a note whose file still carries
    # a secret (one written before the capture gate) is stored in its
    # redacted form. Both forms are matched, or that note would keep its row.
    forms = {i: [note, redact_secrets(note).text] for i, note in enumerate(notes)}
    words = "\n".join(form for variants in forms.values() for form in variants).lower()
    pins = _pins(notes)
    cur.execute(f"select id, content, source_uri from {_ITEMS} where workspace_id=%s", (ws,))
    matched: set[int] = set()
    item_ids: list[Any] = []
    for item_id, content, source_uri in cur.fetchall():
        hit = _matching(forms, content)
        if hit:
            matched |= hit
            item_ids.append(item_id)
        elif source_uri and source_uri in pins:
            item_ids.append(item_id)  # the note, rewritten in the same edit
    if not item_ids:
        return matched
    cur.execute(
        f"select source_entity_id, target_entity_id from {_MIRROR_EDGES} "
        "where workspace_id=%s and evidence_memory_id = any(%s)",
        (ws, item_ids),
    )
    victims = {entity_id for row in cur.fetchall() for entity_id in row}
    cur.execute(
        f"select id, canonical_name, metadata->>'description' from {_MIRROR_ENTITIES} where workspace_id=%s",
        (ws,),
    )
    for entity_id, name, description in cur.fetchall():
        named = (name or "").strip().lower()
        described = (description or "").strip().lower()
        # Both are compared only when they are long enough to be the note's
        # words: a one-letter name reads as part of every note, and the
        # mirror stores boilerplate descriptions ("Document category") that
        # say nothing about which note produced the row.
        if (len(named) >= _WORD and named in words) or (len(described) >= _PHRASE and described in words):
            victims.add(entity_id)
    if victims:
        # The endpoint FKs cascade, so this also takes the edges that touch
        # them, whichever note supplied the evidence.
        cur.execute(f"delete from {_MIRROR_ENTITIES} where workspace_id=%s and id = any(%s)", (ws, sorted(victims)))
    cur.execute(f"delete from {_MIRROR_EDGES} where workspace_id=%s and evidence_memory_id = any(%s)", (ws, item_ids))
    cur.execute(f"delete from {_ITEMS} where workspace_id=%s and id = any(%s)", (ws, item_ids))
    return matched


def purge_notes(dsn: str, workspace_id: str, notes: Iterable[str], *, connect: Callable | None = None) -> int:
    """Delete every row ``notes`` left in ``workspace_id``, in nano-graphrag's
    derived store and in the broker's tables alike; return how many of
    ``notes`` were found in either."""
    from reflect_kb.postgres.dsn import connect_secure

    notes = [n for n in notes if n and n.strip()]
    if not notes:
        return 0
    ws = str(workspace_id)
    conn = connect_secure(dsn, what="the shared-store DSN", connect=connect)
    with conn, conn.transaction(), conn.cursor() as cur:
        # Bind the tenant for FORCE RLS; every statement below is also
        # scoped by workspace_id explicitly. Both halves run in the one
        # transaction: a note left in the broker's tables because the graph
        # half failed is the leak this whole module exists to close.
        cur.execute("select set_config('app.current_workspace', %s, true)", (ws,))
        purged = _purge_derived(cur, ws, notes)
        purged |= _purge_mirror(cur, ws, notes)
    return len(purged)
