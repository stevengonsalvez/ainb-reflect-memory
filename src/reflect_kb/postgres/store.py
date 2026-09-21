"""MemoryStore — the typed helper layer over a Postgres connection.

This is the trusted server/worker path. It talks to Postgres through a DB-API
style connection (psycopg 3 in production) and returns typed records. It does
**no** LLM work: no embeddings, no extraction, no answer synthesis. It stores,
scopes, and retrieves — the brain stays in the client.

Safety model on this path is *explicit tenant scoping*: every query carries the
workspace id as a bound parameter (see ``sql.py``; the tests pin this down).
Row-Level Security is the independent guard for the *other* access path —
direct Supabase/PostgREST clients authenticating with a JWT — and is defined in
the migration. The two are defense in depth, not a single point of failure.

Connection contract
-------------------
``conn`` must yield **mapping rows** (e.g. psycopg's ``dict_row`` factory) so
``MemoryItem.from_row`` and friends can read columns by name::

    import psycopg
    from psycopg.rows import dict_row
    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row)
    store = MemoryStore(conn)

On a psycopg connection every write runs in its own transaction, committed
when the call returns, so the connection must be idle when a write starts: a
write inside a transaction the caller opened would commit only with that
transaction and leave the tenant bound until it ends, so it raises
:class:`CallerTransactionError` instead. A read may run inside the caller's
transaction (the broker wraps a request in one); it runs in a savepoint that
is rolled back afterwards, which drops the tenant binding again. On a plain
DB-API connection the store calls ``conn.commit()`` after each call. It never
closes the connection; the caller owns its lifecycle.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from . import sql
from .errors import ReflectMemoryError
from .models import (
    Citation,
    Edge,
    Entity,
    EntityHit,
    EvidenceHit,
    EvidencePack,
    EvidencePackQuery,
    GraphNeighborhood,
    InsertMemoryInput,
    MemoryItem,
    SearchMemoryInput,
    SearchResult,
    UpsertEdgeInput,
    UpsertEntityInput,
)

__all__ = ["CallerTransactionError", "MemoryStore"]


class CallerTransactionError(ReflectMemoryError):
    """A store write was called inside a transaction the caller opened."""


class MemoryStore:
    """Typed CRUD + search over the reflect memory substrate."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    # ----------------------------------------------------------------- #
    # low-level execution helpers
    # ----------------------------------------------------------------- #

    def _fetchone(self, sql_text: str, params: Sequence[Any]) -> dict | None:
        with self._conn.cursor() as cur:
            cur.execute(sql_text, params)
            return cur.fetchone()

    def _fetchall(self, sql_text: str, params: Sequence[Any]) -> list[dict]:
        with self._conn.cursor() as cur:
            cur.execute(sql_text, params)
            return list(cur.fetchall())

    def _commit(self) -> None:
        commit = getattr(self._conn, "commit", None)
        if callable(commit):
            commit()

    @contextmanager
    def _scoped(self, tenant: Any, *, read_only: bool = False) -> Iterator[None]:
        """Run the enclosed statements with ``app.current_workspace`` bound to
        ``tenant`` for this transaction only (``SET LOCAL``, is_local=true).

        Under FORCE RLS (migration 0003) an owner role that is not BYPASSRLS
        is subject to the policies, so an unbound write fails; binding per
        call keeps the documented worker DSN working while the explicit
        ``workspace_id`` parameters remain the first layer. The binding is
        transaction-local on purpose, so a pooled connection never carries a
        workspace into its next user (an unbound GUC denies every row, the
        fail-closed default).

        On an idle connection the call runs in its own transaction, committed
        (or rolled back on an exception) when it returns. Inside a transaction
        the caller opened, ``conn.transaction()`` is only a savepoint: nothing
        would commit and the binding would outlive the call. A write there
        raises :class:`CallerTransactionError`; a read runs in a savepoint
        that is rolled back after the rows are fetched, which restores the
        caller's binding exactly.
        """
        workspace_id = str(tenant.workspace_id)
        transaction = getattr(self._conn, "transaction", None)
        if not callable(transaction):
            self._set_local(workspace_id)
            try:
                yield
            finally:
                self._commit()
            return
        import psycopg

        status = self._conn.info.transaction_status
        if status == psycopg.pq.TransactionStatus.IDLE:
            with transaction():
                self._set_local(workspace_id)
                yield
            return
        if not read_only or status != psycopg.pq.TransactionStatus.INTRANS:
            what = "read" if read_only else "write"
            raise CallerTransactionError(
                f"MemoryStore {what} called on a connection that is {status.name}, inside a transaction "
                "the caller opened: a write would not commit and the tenant binding would outlive the "
                "call. Commit or roll back first, or pass an idle connection."
            )
        with transaction():
            self._set_local(workspace_id)
            yield
            raise psycopg.Rollback()

    def _set_local(self, workspace_id: str) -> None:
        with self._conn.cursor() as cur:
            cur.execute("select set_config('app.current_workspace', %s, true)", (workspace_id,))

    # ----------------------------------------------------------------- #
    # writes
    # ----------------------------------------------------------------- #

    def insert_memory(self, inp: InsertMemoryInput) -> MemoryItem:
        """Insert (or idempotently refresh) a memory item; return the row."""
        sql_text, params = sql.insert_memory(inp)
        with self._scoped(inp.tenant):
            row = self._fetchone(sql_text, params)
        assert row is not None  # RETURNING always yields a row
        return MemoryItem.from_row(row)

    def upsert_entity(self, inp: UpsertEntityInput) -> Entity:
        sql_text, params = sql.upsert_entity(inp)
        with self._scoped(inp.tenant):
            row = self._fetchone(sql_text, params)
        assert row is not None
        return Entity.from_row(row)

    def upsert_edge(self, inp: UpsertEdgeInput) -> Edge:
        sql_text, params = sql.upsert_edge(inp)
        with self._scoped(inp.tenant):
            row = self._fetchone(sql_text, params)
        assert row is not None
        return Edge.from_row(row)

    # ----------------------------------------------------------------- #
    # reads
    # ----------------------------------------------------------------- #

    def search_memory(self, inp: SearchMemoryInput) -> list[SearchResult]:
        """Ranked full-text search within the tenant."""
        sql_text, params = sql.search_memory(inp)
        with self._scoped(inp.tenant, read_only=True):
            rows = self._fetchall(sql_text, params)
        return [
            SearchResult(
                item=MemoryItem.from_row(r),
                rank=float(r["rank"]),
                snippet=r["snippet"],
            )
            for r in rows
        ]

    def lookup_entities(self, tenant, query: str, limit: int = 10) -> list[EntityHit]:
        """Fuzzy entity lookup by canonical name / alias within the tenant."""
        sql_text, params = sql.search_entities(tenant, query, limit)
        with self._scoped(tenant, read_only=True):
            rows = self._fetchall(sql_text, params)
        return [
            EntityHit(
                entity_id=str(r["id"]),
                canonical_name=r["canonical_name"],
                entity_type=r["entity_type"],
                matched_alias=r.get("matched_alias"),
            )
            for r in rows
        ]

    def neighborhood(self, tenant, entity_id: str, depth: int = 1) -> GraphNeighborhood:
        """Entities + edges within ``depth`` hops of ``entity_id`` (same tenant)."""
        sql_text, params = sql.entity_neighborhood(tenant, entity_id, depth)
        with self._scoped(tenant, read_only=True):
            edge_rows = self._fetchall(sql_text, params)
            edges = [Edge.from_row(r) for r in edge_rows]

            # Hydrate the entities touched by those edges (plus the seed), all
            # tenant-scoped, so the caller gets full entity records not just ids.
            entity_ids = {entity_id}
            for e in edges:
                entity_ids.add(e.source_entity_id)
                entity_ids.add(e.target_entity_id)
            entities: list[Entity] = []
            if entity_ids:
                ent_sql, ent_params = sql.entities_by_ids(tenant, sorted(entity_ids))
                entities = [Entity.from_row(r) for r in self._fetchall(ent_sql, ent_params)]

        return GraphNeighborhood(entities=entities, edges=edges)

    # ----------------------------------------------------------------- #
    # evidence pack — pure retrieval, no synthesis
    # ----------------------------------------------------------------- #

    def get_evidence_pack(self, q: EvidencePackQuery) -> EvidencePack:
        """Assemble an evidence pack for a query: lexical hits + entity matches
        + a graph neighborhood + citations. The server returns *evidence only*;
        the local agent synthesizes the final answer from it.
        """
        tenant = q.tenant

        # 1. lexical hits
        with self._scoped(tenant, read_only=True):
            lexical_rows = self._fetchall(
                *sql.search_pinned_memory(
                    SearchMemoryInput(tenant=tenant, query=q.query, limit=q.lexical_limit)
                )
            )
        lexical = [
            EvidenceHit(
                memory_id=str(r["id"]),
                content=r["content"],
                rank=float(r["rank"]),
                snippet=r["snippet"],
                source_type=r["source_type"],
                source_uri=r.get("source_uri"),
                # The classification floor reads this on the live path; an
                # empty dict would pass every hit.
                metadata=r.get("metadata") or {},
            )
            for r in lexical_rows
        ]

        # 2. entity matches
        entity_hits = self.lookup_entities(tenant, q.query, q.entity_limit)

        # 3. graph neighborhood around the top entity match (if any)
        graph = GraphNeighborhood(entities=[], edges=[])
        if entity_hits and q.neighborhood_depth > 0:
            graph = self.neighborhood(tenant, entity_hits[0].entity_id, q.neighborhood_depth)

        # 4. citations — every lexical hit is a citable source
        citations = [
            Citation(
                memory_id=h.memory_id,
                source_type=h.source_type,
                source_uri=h.source_uri,
            )
            for h in lexical
        ]

        return EvidencePack(
            query=q.query,
            tenant=tenant,
            lexical=lexical,
            entities=entity_hits,
            graph=graph,
            citations=citations,
        )
