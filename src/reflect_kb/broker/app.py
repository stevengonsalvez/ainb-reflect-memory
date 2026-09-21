"""FastAPI application: ``/v1/evidence`` over ``MemoryStore.get_evidence_pack``.

Evidence only. The handler authenticates, scopes the store call to the tenant
claim, then filters every lexical hit through the classification floor and the
source pin resolver before it is returned. Graph edges and entities ride out
only on a hit that survived. ``meta.dropped.total`` tells a caller "nothing
matched" from "matches were refused"; the per-reason split stays in the server
log. The resolver sees repos beyond the caller's workspace, so a per-reason
count said which files exist there; the total only narrows that, since
``returned`` still shows whether a planted pin resolved (scoping the resolver
per workspace closes it).
"""

from __future__ import annotations

import logging
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

_log = logging.getLogger(__name__)


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
    # One number on purpose: see the module docstring. The reasons are logged.
    total: int = 0


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

    Pure: no I/O beyond the resolver. The contract is that everything returned
    traces to a pinned, resolved hit:

    * a lexical hit is kept when it passes the floor and its pin resolves;
    * a graph edge is kept when its evidence memory is a kept hit and both
      endpoints pass the floor (an edge citing no memory has no provenance);
    * an entity, in ``entities`` or ``graph.entities``, is kept only when a
      kept edge touches it. The schema links entities to memories through
      edges alone, so a kept edge is the only way a kept hit references one.

    Refusals are counted per reason for the server log (a restricted item is
    counted as classified even if it would also fail to pin); the response
    carries only their total. Graph context trimmed for lack of a link to a
    kept hit is not a refusal and is not counted: most neighbourhoods carry
    some, and counting it would make "total > 0" mean nothing.
    """
    reasons = {"classified": 0, "unpinned": 0, "unresolvable": 0, "refused_edges": 0}
    hits: list[LexicalHit] = []
    kept_ids: set[str] = set()
    # Parse first, then resolve every pin in one batch (one git batch-check
    # per repo per request), then assemble.
    parsed: list[tuple[Any, Any]] = []
    refused_ids: set[str] = set()
    for hit in pack.lexical:
        if not may_leave_machine(getattr(hit, "metadata", None)):
            reasons["classified"] += 1
            refused_ids.add(hit.memory_id)
            continue
        try:
            parsed.append((hit, parse_source_uri(hit.source_uri)))
        except SourcePinError:
            reasons["unpinned"] += 1
            refused_ids.add(hit.memory_id)
    resolved = resolve_all(resolver, [pin for _, pin in parsed])
    for hit, pin in parsed:
        if not resolved.get(pin, False):
            reasons["unresolvable"] += 1
            refused_ids.add(hit.memory_id)
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
    classified_entities = sum(1 for e in pack.graph.entities if not may_leave_machine(getattr(e, "metadata", None)))
    reasons["classified"] += classified_entities
    visible = {e.id for e in pack.graph.entities if may_leave_machine(getattr(e, "metadata", None))}
    edges: list[GraphEdgeOut] = []
    referenced: set[str] = set()
    trimmed = 0
    for e in pack.graph.edges:
        if not may_leave_machine(getattr(e, "metadata", None)):
            reasons["classified"] += 1
            continue
        if e.evidence_memory_id in refused_ids:
            reasons["refused_edges"] += 1
            continue
        # A null evidence id is not in kept_ids: an edge citing no memory has
        # no pin and is withheld on purpose.
        if e.evidence_memory_id not in kept_ids or not {e.source_entity_id, e.target_entity_id} <= visible:
            trimmed += 1
            continue
        referenced.update((e.source_entity_id, e.target_entity_id))
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
    graph_entities = [e for e in pack.graph.entities if e.id in referenced]
    entities = [e for e in pack.entities if e.entity_id in referenced]
    trimmed += len(pack.graph.entities) - classified_entities - len(graph_entities) + len(pack.entities) - len(entities)
    total = sum(reasons.values())
    if total:
        _log.info("evidence refused workspace=%s returned=%d %s", pack.tenant.workspace_id, len(hits),
                  " ".join(f"{k}={v}" for k, v in reasons.items()))
    if trimmed:
        _log.debug("graph context trimmed workspace=%s unlinked=%d", pack.tenant.workspace_id, trimmed)
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
            for e in entities
        ],
        graph=GraphOut(
            entities=[
                GraphEntityOut(
                    id=e.id,
                    canonical_name=e.canonical_name,
                    entity_type=e.entity_type,
                    aliases=list(e.aliases),
                )
                for e in graph_entities
            ],
            edges=edges,
        ),
        citations=citations,
        meta=EvidenceMeta(returned=len(hits), dropped=DroppedCounts(total=total)),
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
        # Every store call binds the token's tenant with SET LOCAL inside its
        # own transaction (MemoryStore._scoped); there is no separate bind.
        with store_factory() as store:
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
