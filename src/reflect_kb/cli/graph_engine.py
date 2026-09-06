"""GraphRAG engine wrapping nano-graphrag for Global Learnings.

Uses all-mpnet-base-v2 for local embeddings (no API key needed) and a
passthrough LLM that returns pre-extracted entities from sidecar files
instead of calling an external API.

For search queries, uses only_need_context=True so the calling Claude
session can synthesize results itself.
"""

import logging
import os
import shutil
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Install graspologic shim BEFORE any nano-graphrag import.
# This avoids the broken transitive dependency chain:
# graspologic -> hyppo -> numba -> llvmlite (Python <3.10 only)
from reflect_kb.cli.graspologic_shim import install_shim as _install_graspologic_shim

_install_graspologic_shim()

from reflect_kb.cli.entity_store import COMPLETION_DELIMITER

logger = logging.getLogger(__name__)

# Embedding model shared by indexing (nano-graphrag) and `reflect embed`
# (R3: recall's MMR diversity step) — similarity must live in ONE space.
# Override with REFLECT_EMBED_MODEL to use a stronger retrieval embedder
# (e.g. BAAI/bge-large-en-v1.5, 1024-d). The embedding dimension is derived
# from the loaded model at index time, so any sentence-transformers model
# works; a model swap requires a fresh reindex (vectors are dim-specific).
# The name lives in model_daemon (single source shared with the daemon key
# and the daemon's own loader) and is re-exported here for callers.
from reflect_kb.model_daemon import EMBEDDING_MODEL_NAME

# Cap inputs so giant chunks don't waste tokenizer time (mirrors
# cross_encoder._MAX_CANDIDATE_CHARS). 2000 chars ≈ 512 tokens, the window
# of mpnet and the bge/gte/e5 family alike.
_MAX_EMBED_CHARS = 2000

# Minimal placeholder entity for docs without sidecars.
# Ensures nano-graphrag's insert() doesn't abort before persisting
# full_docs and text_chunks (which happens when extraction returns None).
_PLACEHOLDER_ENTITY = (
    '("entity"<|>"knowledge_entry"<|>"learning"'
    '<|>"A knowledge base document entry")\n'
    f"{COMPLETION_DELIMITER}"
)


class GraphEngineError(Exception):
    pass


@dataclass(frozen=True)
class InsertStatus:
    """What insert_document did: indexed, or skipped with the reason."""

    indexed: bool
    reason: str = ""

    def __bool__(self) -> bool:
        return self.indexed


class LearningsGraphEngine:
    """Wrapper around nano-graphrag for the Global Learnings knowledge base."""

    def __init__(
        self,
        cache_dir: str | Path,
        pg_dsn: str | None = None,
        workspace_id: str | None = None,
    ):
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._graph = None
        self._model = None
        self._pending_entities: str | None = None
        self._entity_queue: deque = deque()
        # Opt-in shared Postgres backend (reflect_kb.postgres). When BOTH a DSN
        # and a workspace id resolve, nano-graphrag's graph / vectors / community
        # reports live in shared Postgres instead of per-machine local files, so
        # the store is the same across machines. Default keeps local-file behavior.
        # Trigger is REFLECT_PG_DSN ONLY — not the generic DATABASE_URL, which
        # usually points at an unrelated DB.
        self._pg_dsn = pg_dsn or os.environ.get("REFLECT_PG_DSN")
        self._workspace_id = workspace_id or os.environ.get("REFLECT_WORKSPACE_ID")

    def _load_embedding_model(self):
        """Lazy-load the sentence transformer model (in-process fallback path).

        Loading torch + the model costs ~3.5 GB RSS — the single-flight lock
        serializes concurrent cold boots across reflect processes so parallel
        session-start recalls can't stack up and OOM the box. Held for the
        process lifetime on success (that's the RAM cap), released on any
        load failure so a degraded process can't starve everyone else. The
        fast path (model daemon, see reflect_kb.model_daemon) never reaches
        this."""
        if self._model is None:
            import importlib.util
            import sys

            # Already-imported (or test-injected) module counts as available;
            # find_spec alone rejects spec-less fakes in sys.modules. A None
            # entry means "explicitly absent" (import raises ImportError).
            if "sentence_transformers" in sys.modules:
                available = sys.modules["sentence_transformers"] is not None
            else:
                try:
                    available = (
                        importlib.util.find_spec("sentence_transformers")
                        is not None
                    )
                except (ImportError, ValueError):
                    available = False
            if not available:  # slim build — fail before taking the lock
                raise GraphEngineError(
                    "sentence-transformers not installed. "
                    "Run: uv pip install sentence-transformers"
                )
            from reflect_kb.model_daemon import (
                acquire_singleflight,
                release_singleflight,
            )

            acquire_singleflight()
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(EMBEDDING_MODEL_NAME)
            except ImportError:
                release_singleflight()  # a failed load must not hold the cap
                raise GraphEngineError(
                    "sentence-transformers not installed. "
                    "Run: uv pip install sentence-transformers"
                )
            except Exception:
                release_singleflight()
                raise
        return self._model

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed texts with the same all-mpnet-base-v2 model used for indexing.

        Returns unit-normalized vectors (dot product == cosine similarity),
        one per input text in input order, as plain float lists so they are
        JSON-serializable for the `reflect embed` CLI. R3: recall's MMR
        diversity step uses these so its similarity measure matches the
        index's embedding space.

        Tries the persistent model daemon first (models already warm, ms
        round-trip); any daemon failure falls back to loading the model
        in-process. Raises GraphEngineError when sentence-transformers is
        missing (slim build) — callers degrade silently.
        """
        if not texts:
            return []
        from reflect_kb.model_daemon import daemon_embed

        # Truncate client-side so the daemon needs no policy (and giant
        # chunks don't cross the socket just to be sliced on arrival).
        capped = [t[:_MAX_EMBED_CHARS] for t in texts]
        vectors = daemon_embed(capped)
        if vectors is not None:
            return vectors
        model = self._load_embedding_model()
        vectors = model.encode(capped, normalize_embeddings=True)
        return [[float(x) for x in vec] for vec in vectors]

    def _get_embedding_func(self):
        """Create nano-graphrag compatible async embedding function."""
        import asyncio

        import numpy as np
        from nano_graphrag._utils import wrap_embedding_func_with_attrs

        from reflect_kb.model_daemon import daemon_embed

        engine = self

        # Derive the embedding dimension from the loaded model so a swapped
        # REFLECT_EMBED_MODEL (e.g. bge-large = 1024-d) indexes correctly
        # instead of being forced into mpnet's 768. Prefer the daemon (one
        # tiny embed call, cached per engine) so query-time search never
        # loads the model here; fall back to the in-process model. The
        # getter was renamed in newer sentence-transformers, so fall back
        # across both names.
        if getattr(self, "_embed_dim", None) is None:
            probe = daemon_embed(["x"])
            if probe:
                self._embed_dim = len(probe[0])
            else:
                _m = engine._load_embedding_model()
                _get_dim = getattr(_m, "get_sentence_embedding_dimension", None) or _m.get_embedding_dimension
                self._embed_dim = _get_dim()
        dim = self._embed_dim

        @wrap_embedding_func_with_attrs(embedding_dim=dim, max_token_size=8192)
        async def embedding_func(texts: list[str]) -> np.ndarray:
            # nano-graphrag runs this under asyncio.gather alongside LLM
            # calls — the socket round-trip (and the in-proc encode) are
            # blocking, so push them off the event loop.
            vecs = await asyncio.to_thread(daemon_embed, list(texts))
            if vecs is not None:
                # float32 like model.encode, so daemon-served and in-proc
                # batches land in the index with the same dtype.
                return np.array(vecs, dtype=np.float32)
            model = engine._load_embedding_model()
            return np.array(
                await asyncio.to_thread(
                    lambda: model.encode(texts, normalize_embeddings=True)
                )
            )

        return embedding_func

    def _is_entity_extraction_prompt(self, prompt: str) -> bool:
        """Detect nano-graphrag's entity extraction prompt."""
        lower = prompt[:200].lower()
        return "-goal-" in lower and "text document" in lower

    async def _llm_complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        history_messages: list = [],
        **kwargs,
    ) -> str:
        """Passthrough LLM function for nano-graphrag.

        Routes different LLM call types:
        1. Entity extraction: returns pre-extracted entities (from sidecar
           queue or single pending, with placeholder fallback)
        2. Community reports: returns a minimal valid JSON report
        3. Other calls: returns empty/minimal response

        For queries with only_need_context=True, the LLM is not called
        for answer synthesis, so this fallback is rarely hit.
        """
        # Pop cache KV if present (nano-graphrag convention)
        kwargs.pop("hashing_kv", None)

        # Entity extraction calls
        if self._is_entity_extraction_prompt(prompt):
            # Batch mode: pop from queue (reindex uses this)
            if self._entity_queue:
                entities = self._entity_queue.popleft()
                return entities if entities else _PLACEHOLDER_ENTITY

            # Single mode: consume pending (add command uses this)
            if self._pending_entities is not None:
                entities = self._pending_entities
                self._pending_entities = None
                return entities

            # No entities available - return placeholder so insert completes
            return _PLACEHOLDER_ENTITY

        # Community report calls - return minimal valid JSON
        prompt_lower = prompt.lower() if prompt else ""
        if "community" in prompt_lower or "report" in prompt_lower:
            import json
            return json.dumps({
                "title": "Community Summary",
                "summary": "A group of related technical concepts and patterns.",
                "findings": [
                    {
                        "summary": "Related technical entities",
                        "explanation": "These entities are connected through technical relationships in the knowledge base."
                    }
                ],
                "rating": 5.0,
                "rating_explanation": "Moderate impact technical knowledge."
            })

        # Fallback for any other LLM calls
        return "No additional information available."

    def _init_graph(self):
        """Initialize the GraphRAG instance (lazy)."""
        if self._graph is not None:
            return

        try:
            from nano_graphrag import GraphRAG
        except ImportError:
            raise GraphEngineError(
                "nano-graphrag not installed. "
                "Run: uv pip install nano-graphrag"
            )

        graphrag_kwargs = dict(
            working_dir=str(self._cache_dir),
            embedding_func=self._get_embedding_func(),
            best_model_func=self._llm_complete,
            cheap_model_func=self._llm_complete,
            enable_naive_rag=True,
        )

        # Shared Postgres backend (opt-in): hand nano-graphrag the reflect_kb.
        # postgres storage classes so its graph / vectors / community reports
        # live in shared Postgres — same store across machines. nano-graphrag's
        # own code is unchanged; only the *_storage_cls + addon_params change.
        if self._pg_dsn and self._workspace_id:
            from reflect_kb.postgres.dsn import InsecureDSNError, assert_tls  # a one-connection probe

            try:
                assert_tls(self._pg_dsn, what="REFLECT_PG_DSN")
            except InsecureDSNError as exc:
                raise GraphEngineError(str(exc)) from exc
            try:
                from reflect_kb.postgres.nanographrag import (
                    addon_params,
                    storage_classes,
                )
            except ImportError as exc:
                raise GraphEngineError(
                    "Postgres backend requested (REFLECT_PG_DSN + "
                    "REFLECT_WORKSPACE_ID) but reflect_kb.postgres deps are not "
                    f"installed (extras: [postgres]): {exc}"
                )
            graphrag_kwargs.update(storage_classes())
            graphrag_kwargs["addon_params"] = addon_params(
                pg_dsn=self._pg_dsn,
                workspace_id=self._workspace_id,
                embedding_model=EMBEDDING_MODEL_NAME,
            )
            logger.info(
                "LearningsGraphEngine: using shared Postgres backend (workspace=%s)",
                self._workspace_id,
            )

        self._graph = GraphRAG(**graphrag_kwargs)

    @property
    def shared_backend(self) -> bool:
        """True when the derived store is the shared Postgres (Mode 2)."""
        return bool(self._pg_dsn and self._workspace_id)

    @property
    def shared_store_target(self) -> tuple[str, str] | None:
        """``(dsn, workspace_id)`` of the shared store when Mode 2 is on, else
        None. The one place the Mode 2 trigger is read (REFLECT_PG_DSN plus
        REFLECT_WORKSPACE_ID, never the generic DATABASE_URL)."""
        return (self._pg_dsn, self._workspace_id) if self.shared_backend else None

    def _floor_label(self, text: str, label: str | None = None) -> str | None:
        """The label that keeps a note out of the shared store, or None when
        the note may be indexed. The classification floor is applied once,
        before chunking: a note labelled restricted or pii (or malformed) is
        never handed to nano-graphrag, so no ng_* namespace (full_docs,
        text_chunks, vectors, graph) sees it. ``label`` is the already-parsed
        frontmatter value when the caller has it; the text is parsed only
        otherwise. Local Mode 1 stores index everything."""
        if not self.shared_backend:
            return None
        from reflect_kb.classification import (
            classification_of,
            classification_of_note,
            may_leave_machine,
        )

        if label is None:
            label = classification_of_note(text)
        else:
            label = classification_of({"classification": label})
        if may_leave_machine({"classification": label}):
            return None
        logger.warning(
            "LearningsGraphEngine: skipping a %s note; it never leaves the local store", label
        )
        return label

    def _local_only(self, text: str, label: str | None = None) -> bool:
        return self._floor_label(text, label) is not None

    def local_only(self, text: str, label: str | None = None) -> bool:
        """Public form of the floor so a caller can count what it will index."""
        return self._local_only(text, label)

    def purge_local_only(self, notes: list[str]) -> int:
        """Remove every ng_* row and graph node or edge that ``notes`` left in
        the shared store under an earlier label (Mode 2 only). Returns how
        many stored documents were purged. The floor stops new writes; this
        is the retroactive half for a note relabelled restricted or pii."""
        notes = [n for n in notes if n and n.strip()]
        if not self.shared_backend or not notes:
            return 0
        from reflect_kb.postgres.nanographrag.purge import purge_notes

        return purge_notes(self._pg_dsn, self._workspace_id, notes)

    def insert_document(
        self, text: str, entities_formatted: str | None = None, label: str | None = None
    ) -> InsertStatus:
        """Insert a single document into the graph.

        Args:
            text: The document text content.
            entities_formatted: Pre-extracted entities in nano-graphrag format.
                If provided, the passthrough LLM returns these instead of
                calling an external API.
            label: the already-parsed classification, when the caller has it.

        On the shared Postgres backend a restricted or pii note is skipped
        (see ``_floor_label``); the caller's local markdown copy is untouched
        and the returned status names the reason.
        """
        blocked = self._floor_label(text, label)
        if blocked is not None:
            return InsertStatus(False, f"classification {blocked} stays in the local store")
        self._init_graph()
        self._pending_entities = entities_formatted
        try:
            self._graph.insert(text)
        finally:
            self._pending_entities = None
            self._entity_queue.clear()
        return InsertStatus(True)

    def insert_documents_batch(
        self,
        docs_with_entities: list[tuple[Any, ...]],
    ) -> int:
        """Insert multiple documents in a single batch; return how many were
        handed to nano-graphrag after the floor.

        Batching avoids nano-graphrag state issues that occur with
        sequential insert() calls (community_reports dropped, early
        return skipping KV persistence).

        Args:
            docs_with_entities: List of (text, entities_formatted) or
                (text, entities_formatted, label) tuples. entities_formatted
                can be None for docs without sidecars; label is the
                already-parsed classification when the caller has it.
        """
        docs_with_entities = [
            (doc[0], doc[1]) for doc in docs_with_entities
            if not self._local_only(doc[0], doc[2] if len(doc) > 2 else None)
        ]
        if not docs_with_entities:
            return 0

        self._init_graph()

        # Build entity queue - one entry per document, in order.
        # nano-graphrag processes chunks in document order, so the
        # passthrough LLM pops from this queue on each extraction call.
        self._entity_queue = deque(
            entities for _, entities in docs_with_entities
        )

        texts = [text for text, _ in docs_with_entities]

        try:
            self._graph.insert(texts)
        finally:
            self._entity_queue.clear()
            self._pending_entities = None
        return len(docs_with_entities)

    def search(
        self,
        query: str,
        mode: str = "local",
        only_context: bool = True,
    ) -> str:
        """Search the graph for relevant context.

        Args:
            query: The search query.
            mode: Search mode - "naive" (vector only), "local" (entity
                  neighborhood), or "global" (community reports).
            only_context: If True, returns raw context without LLM synthesis.
                         Default True since Claude synthesizes results.

        Returns:
            Search results as a string.
        """
        self._init_graph()

        from nano_graphrag import QueryParam

        param = QueryParam(mode=mode, only_need_context=only_context)
        result = self._graph.query(query, param=param)
        return result if result else ""

    def get_typed_edges(self, link_types: list[str] | None = None) -> list[dict]:
        """Return stored graph edges, optionally filtered by typed link (S2).

        Typed causal links from sidecars survive into the GraphML as
        ``[type]``-prefixed edge descriptions (see
        ``entity_store.Relationship.typed_description``). This recovers
        them so the graph-expansion arm (R1) can filter by type, e.g.
        ``get_typed_edges(["caused_by", "enables"])``.

        Stdlib XML parse — works on slim builds without networkx.
        """
        from reflect_kb.cli.graph_links import get_typed_edges

        return get_typed_edges(self._cache_dir, link_types=link_types)

    def clear_cache(self):
        """Clear the graph cache for full rebuild."""
        if self._cache_dir.exists():
            shutil.rmtree(self._cache_dir)
            self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._graph = None

    def get_stats(self) -> dict:
        """Get graph statistics."""
        stats = {
            "cache_dir": str(self._cache_dir),
            "cache_exists": self._cache_dir.exists(),
            "entity_count": 0,
            "relationship_count": 0,
        }

        if not self._cache_dir.exists():
            return stats

        # Try to get graph-level stats from the stored graph
        graph_file = self._cache_dir / "graph_chunk_entity_relation.graphml"
        if graph_file.exists():
            try:
                import networkx as nx

                G = nx.read_graphml(str(graph_file))
                stats["entity_count"] = G.number_of_nodes()
                stats["relationship_count"] = G.number_of_edges()
            except Exception as e:
                logger.debug(f"Could not read graph stats: {e}")

        return stats
