"""Mirror a learning note into the shared store (Mode 2).

nano-graphrag's Postgres adapters populate the ng_* tables; the broker reads
memory_items, entities and edges. Until this module nothing in the shipping
pipeline wrote those three, so the broker served nothing outside
scripts/seed.py. ``mirror_note`` is called by ``reflect add`` and
``reflect reindex`` whenever REFLECT_PG_DSN and REFLECT_WORKSPACE_ID are set:

* the note becomes one memory_items row via ``InsertMemoryInput.from_note``
  (idempotent on the per-tenant content hash, pinned when the frontmatter
  carries repo, commit and source_path, refused by the classification floor
  when it is restricted or pii);
* every sidecar entity becomes an entities row and every relationship an
  edges row whose evidence is the memory item, both carrying the note's
  classification so the floor applies to them too;
* every statement runs bound to the workspace (MemoryStore binds per call).

The mirror never fails ``reflect add``: the caller reports a MirrorError and
the local note stays the source of truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from reflect_kb.postgres.dsn import connect_secure
from reflect_kb.postgres.errors import ValidationError
from reflect_kb.postgres.models import (
    InsertMemoryInput,
    Tenant,
    UpsertEdgeInput,
    UpsertEntityInput,
)
from reflect_kb.postgres.store import MemoryStore

__all__ = ["MirrorError", "MirrorResult", "mirror_note"]


class MirrorError(RuntimeError):
    """The shared store could not be written; the local note is untouched."""


@dataclass
class MirrorResult:
    memory_id: str | None = None
    entities: int = 0
    edges: int = 0
    skipped: str | None = None
    notes: list[str] = field(default_factory=list)


def mirror_note(
    dsn: str,
    workspace_id: str,
    *,
    content: str,
    frontmatter: Mapping[str, Any],
    doc_entities: Any = None,
    source_type: str = "learning_note",
    connect: Any = None,
) -> MirrorResult:
    """Write one note (and its sidecar entities and relationships) into the
    shared store bound to ``workspace_id``. ``connect`` is psycopg.connect
    unless a test injects one."""
    tenant = Tenant(workspace_id=workspace_id)
    try:
        inp = InsertMemoryInput.from_note(tenant, frontmatter, content, source_type=source_type)
    except ValidationError as exc:
        return MirrorResult(skipped=str(exc))
    if connect is None:
        import psycopg
        from psycopg.rows import dict_row

        def connect(d):
            return psycopg.connect(d, row_factory=dict_row, autocommit=False)

    result = MirrorResult()
    try:
        # The TLS judgement is made on the open connection; a plain remote
        # DSN is a MirrorError here like every other failure, never an
        # exception that escapes to reflect add.
        conn = connect_secure(dsn, what="REFLECT_PG_DSN", connect=lambda d, **_: connect(d))
    except Exception as exc:
        raise MirrorError(f"could not connect to the shared store: {exc}") from exc
    try:
        store = MemoryStore(conn)
        item = store.insert_memory(inp)
        result.memory_id = item.id
        label = inp.metadata.get("classification")
        ids: dict[str, str] = {}
        for ent in getattr(doc_entities, "entities", None) or []:
            name = str(getattr(ent, "name", "") or "").strip()
            etype = str(getattr(ent, "type", "") or "concept").strip() or "concept"
            if not name:
                continue
            try:
                row = store.upsert_entity(UpsertEntityInput(
                    tenant=tenant, canonical_name=name, entity_type=etype,
                    metadata={"classification": label, "description": str(getattr(ent, "description", "") or "")},
                ))
            except ValidationError as exc:
                result.notes.append(f"entity {name!r} skipped: {exc}")
                continue
            ids[name] = row.id
            result.entities += 1
        for rel in getattr(doc_entities, "relationships", None) or []:
            src, dst = ids.get(str(getattr(rel, "source", ""))), ids.get(str(getattr(rel, "target", "")))
            rtype = str(getattr(rel, "type", "") or "related_to")
            if not (src and dst):
                continue
            strength = getattr(rel, "strength", 5)
            try:
                weight = max(0.0, min(1.0, float(strength) / 10.0))
            except (TypeError, ValueError):
                weight = 0.5
            try:
                store.upsert_edge(UpsertEdgeInput(
                    tenant=tenant, source_entity_id=src, target_entity_id=dst, relation_type=rtype,
                    evidence_memory_id=item.id, weight=weight, metadata={"classification": label},
                ))
            except ValidationError as exc:
                result.notes.append(f"edge {rtype!r} skipped: {exc}")
                continue
            result.edges += 1
    except MirrorError:
        raise
    except Exception as exc:
        raise MirrorError(f"shared store write failed: {exc}") from exc
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
    return result
