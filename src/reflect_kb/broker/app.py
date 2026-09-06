"""FastAPI application: ``/v1/evidence`` over ``MemoryStore.get_evidence_pack``.

Evidence only. The handler authenticates, scopes the store call to the tenant
claim, then filters every lexical hit through the classification floor and the
source pin resolver before it is returned. Dropped hits are counted in
``meta`` so a caller can tell "nothing matched" from "matches were refused".
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from reflect_kb.classification import may_leave_machine
from reflect_kb.postgres import EvidencePack, EvidencePackQuery, MemoryStore, Tenant

from .auth import AuthError, OIDCVerifier, Principal
from .pinning import SourcePinError, SourceResolver, parse_source_uri, resolve_all

__all__ = ["StoreFactory", "create_app", "filter_pack", "psycopg_store_factory"]

# Yields a MemoryStore for one request and releases it afterwards.
StoreFactory = Callable[[], AbstractContextManager[MemoryStore]]


def psycopg_store_factory(dsn: str) -> StoreFactory:
    """One psycopg connection per request, closed on exit.

    ponytail: connection per request; add a pool if request volume ever makes
    the connect handshake visible in latency.
    """

    @contextmanager
    def _open() -> Iterator[MemoryStore]:
        import psycopg
        from psycopg.rows import dict_row

        # Not autocommit: the request runs inside one transaction so the
        # SET LOCAL workspace binding covers every read and dies with it.
        conn = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)
        try:
            with conn.transaction():
                yield MemoryStore(conn)
        finally:
            conn.close()

    return _open


class EvidenceRequest(BaseModel):
    # Unknown fields are ignored on purpose: a body cannot smuggle a tenant.
    model_config = ConfigDict(extra="ignore")

    query: str = Field(min_length=1, max_length=2000)
    lexical_limit: int = Field(default=10, ge=1)
    entity_limit: int = Field(default=10, ge=1)
    neighborhood_depth: int = Field(default=1, ge=0, le=3)


class DroppedCounts(BaseModel):
    unpinned: int = 0
    unresolvable: int = 0
    classified: int = 0
    # Graph edges whose evidence_memory_id points at a memory that was not
    # itself returned (dropped above, or never a lexical hit): unverified
    # provenance, so the edge goes too.
    unverified_edges: int = 0


class EvidenceMeta(BaseModel):
    returned: int
    dropped: DroppedCounts
    evidence_only: bool = True


class SourcePin(BaseModel):
    repo: str
    sha: str
    path: str
    line_start: int | None = None
    line_end: int | None = None


class LexicalHit(BaseModel):
    memory_id: str
    content: str
    rank: float
    snippet: str
    source_type: str
    source_uri: str
    source: SourcePin


class EntityOut(BaseModel):
    entity_id: str
    canonical_name: str
    entity_type: str
    matched_alias: str | None = None


class GraphEntityOut(BaseModel):
    id: str
    canonical_name: str
    entity_type: str
    aliases: list[str]


class GraphEdgeOut(BaseModel):
    id: str
    source_entity_id: str
    target_entity_id: str
    relation_type: str
    evidence_memory_id: str | None
    weight: float


class GraphOut(BaseModel):
    entities: list[GraphEntityOut]
    edges: list[GraphEdgeOut]


class CitationOut(BaseModel):
    memory_id: str
    source_type: str
    source_uri: str


class EvidenceResponse(BaseModel):
    query: str
    workspace_id: str
    lexical: list[LexicalHit]
    entities: list[EntityOut]
    graph: GraphOut
    citations: list[CitationOut]
    meta: EvidenceMeta


def filter_pack(pack: EvidencePack, resolver: SourceResolver) -> EvidenceResponse:
    """Apply the classification floor and source pinning to a pack.

    Pure: no I/O beyond the resolver. Order matters: a restricted item is
    counted as classified even if it would also fail to pin, so the counts
    tell the operator which guard fired first. Graph edges are kept only when
    their evidence memory survived (or they cite none); entity names pass
    through, they carry no content.
    """
    dropped = DroppedCounts()
    hits: list[LexicalHit] = []
    kept_ids: set[str] = set()
    # Parse first, then resolve every pin in one batch (one git batch-check
    # per repo per request), then assemble.
    parsed: list[tuple[Any, Any]] = []
    for hit in pack.lexical:
        if not may_leave_machine(getattr(hit, "metadata", None)):
            dropped.classified += 1
            continue
        try:
            parsed.append((hit, parse_source_uri(hit.source_uri)))
        except SourcePinError:
            dropped.unpinned += 1
    resolved = resolve_all(resolver, [pin for _, pin in parsed])
    for hit, pin in parsed:
        if not resolved.get(pin, False):
            dropped.unresolvable += 1
            continue
        kept_ids.add(hit.memory_id)
        hits.append(
            LexicalHit(
                memory_id=hit.memory_id,
                content=hit.content,
                rank=hit.rank,
                snippet=hit.snippet,
                source_type=hit.source_type,
                source_uri=str(pin),
                source=SourcePin(**pin.as_dict()),
            )
        )
    citations = [
        CitationOut(memory_id=c.memory_id, source_type=c.source_type, source_uri=str(c.source_uri))
        for c in pack.citations
        if c.memory_id in kept_ids
    ]
    edges: list[GraphEdgeOut] = []
    for e in pack.graph.edges:
        if e.evidence_memory_id is not None and e.evidence_memory_id not in kept_ids:
            dropped.unverified_edges += 1
            continue
        edges.append(
            GraphEdgeOut(
                id=e.id,
                source_entity_id=e.source_entity_id,
                target_entity_id=e.target_entity_id,
                relation_type=e.relation_type,
                evidence_memory_id=e.evidence_memory_id,
                weight=e.weight,
            )
        )
    return EvidenceResponse(
        query=pack.query,
        workspace_id=pack.tenant.workspace_id,
        lexical=hits,
        entities=[
            EntityOut(
                entity_id=e.entity_id,
                canonical_name=e.canonical_name,
                entity_type=e.entity_type,
                matched_alias=e.matched_alias,
            )
            for e in pack.entities
        ],
        graph=GraphOut(
            entities=[
                GraphEntityOut(
                    id=e.id,
                    canonical_name=e.canonical_name,
                    entity_type=e.entity_type,
                    aliases=list(e.aliases),
                )
                for e in pack.graph.entities
            ],
            edges=edges,
        ),
        citations=citations,
        meta=EvidenceMeta(returned=len(hits), dropped=dropped),
    )


def create_app(
    *,
    verifier: OIDCVerifier,
    store_factory: StoreFactory,
    resolver: SourceResolver,
    max_limit: int = 50,
) -> FastAPI:
    app = FastAPI(
        title="reflect Context Broker",
        version="1.0.0",
        description="Read-only, OIDC-authenticated evidence over the reflect memory store.",
        # No interactive docs on a service that requires a token to do anything.
        docs_url=None,
        redoc_url=None,
    )

    def principal(request: Request) -> Principal:
        return verifier.verify(request.headers.get("authorization"))

    @app.exception_handler(AuthError)
    async def _auth_error(_: Request, exc: AuthError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.status == 401 else {}
        return JSONResponse(status_code=exc.status, content={"detail": exc.detail}, headers=headers)

    def _serve(req: EvidenceRequest, who: Principal) -> EvidenceResponse:
        # The tenant is the verified claim. Nothing from the request reaches
        # Tenant(); EvidenceRequest has no tenant field and ignores extras.
        query = EvidencePackQuery(
            tenant=Tenant(workspace_id=who.workspace_id, user_id=who.subject),
            query=req.query,
            lexical_limit=min(req.lexical_limit, max_limit),
            entity_limit=min(req.entity_limit, max_limit),
            neighborhood_depth=req.neighborhood_depth,
        )
        with store_factory() as store:
            store.bind_workspace(who.workspace_id)
            pack = store.get_evidence_pack(query)
        return filter_pack(pack, resolver)

    @app.post("/v1/evidence", response_model=EvidenceResponse)
    def evidence_post(req: EvidenceRequest, who: Principal = Depends(principal)) -> Any:
        return _serve(req, who)

    @app.get("/v1/evidence", response_model=EvidenceResponse)
    def evidence_get(
        q: str = Query(min_length=1, max_length=2000),
        lexical_limit: int = Query(default=10, ge=1),
        entity_limit: int = Query(default=10, ge=1),
        neighborhood_depth: int = Query(default=1, ge=0, le=3),
        who: Principal = Depends(principal),
    ) -> Any:
        req = EvidenceRequest(
            query=q,
            lexical_limit=lexical_limit,
            entity_limit=entity_limit,
            neighborhood_depth=neighborhood_depth,
        )
        return _serve(req, who)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
