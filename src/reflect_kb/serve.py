"""reflect serve — local web browser for the knowledge base.

Stdlib-only HTTP server (no fastapi/uvicorn in the base dependency set) that
exposes the KB as a small JSON API plus a bundled single-file SPA.

Binds loopback and has NO authentication. Curation mutations (archive,
confidence edit, compress-queue) are live and act on the LOCAL markdown KB,
so every request is gated by a loopback Host-header check and POSTs require an
`X-Reflect` header (both defend against a webpage in the user's browser driving
the localhost server — see _guard). Do not expose this to a network until it
grows real authz. Postgres backends are read-only.

Endpoints:
    GET  /                              SPA (cli/serve_static/index.html)
    GET  /api/memories                  all memories (frontmatter + derived fields)
    GET  /api/memories/<id>             one memory: body, entities, related memories
    GET  /api/search?q=...              lexical BM25-lite ranking over title/tags/body
    GET  /api/graph                     two-layer graph: memory + entity nodes, weighted edges
    GET  /api/stats                     KB counts + metrics.jsonl op aggregates
    GET  /api/archived                  soft-archived notes (restore candidates)
    GET  /api/compress-queue            groups queued for /reflect consolidation
    POST /api/memories/<id>/archive     soft-archive a note
    POST /api/memories/<id>/restore     restore an archived note
    POST /api/memories/<id>/confidence  edit frontmatter confidence  {value}
    POST /api/compress-queue            queue a group for compression  {ids}
"""

from __future__ import annotations

import json
import math
import re
import threading
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse

import yaml

from reflect_kb.cli.learnings_cli import (
    CACHE_DIR,
    DOCUMENTS_DIR,
    documents_mtime,
    get_repo_path,
    parse_frontmatter,
)

_GRAPHML = "graph_chunk_entity_relation.graphml"
_NS = {"g": "http://graphml.graphdrawing.org/xmlns"}

_CONF_WEIGHT = {"high": 1.0, "medium": 0.7, "low": 0.4}
_RECENCY_HALF_LIFE_DAYS = 180.0

_ARCHIVE_DIRNAME = "archived"
_COMPRESS_QUEUE_FILE = "compress-queue.yaml"
_COMPRESS_QUEUE_VERSION = 1
_VALID_CONFIDENCE = ("high", "medium", "low")


class MutationError(Exception):
    """Raised when a curation mutation cannot be applied (bad id/value)."""


def _norm_confidence(raw: Any) -> str:
    """Collapse the KB's mixed confidence encodings (strings and floats)."""
    if raw is None:
        return "unknown"
    s = str(raw).strip().lower()
    if s in _CONF_WEIGHT:
        return s
    try:
        v = float(s)
    except ValueError:
        return "unknown"
    if v >= 0.8:
        return "high"
    if v >= 0.5:
        return "medium"
    return "low"


def _norm_type(fm: Dict[str, Any]) -> str:
    for key in ("learning_type", "category", "type"):
        v = fm.get(key)
        if v:
            return str(v)
    return "uncategorized"


def _doc_date(fm: Dict[str, Any], path: Path) -> str:
    for key in ("created", "captured_at", "updated"):
        v = fm.get(key)
        if v:
            s = str(v)
            # Normalise bare dates and full timestamps alike to ISO strings.
            return s[:19]
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()[:19]


def _title(fm: Dict[str, Any], body: str, path: Path) -> str:
    for key in ("title", "name"):
        if fm.get(key):
            return str(fm[key])
    m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    if m:
        return m.group(1).strip()
    return path.stem


def _tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_./-]{2,}", text.lower())


def _split_frontmatter(text: str) -> Optional[tuple[str, str]]:
    """Split a note into (frontmatter_text, body) on `---` DELIMITER LINES.

    Unlike ``str.split("---", 2)``, this only treats a line that is exactly
    ``---`` as a delimiter, so a frontmatter *value* containing ``---`` (e.g.
    ``title: cost --- benefit``) can't truncate the frontmatter and corrupt the
    note on write-back. Returns None when there is no frontmatter block.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "\n".join(lines[1:i]), "\n".join(lines[i + 1:])
    return None


class KnowledgeBase:
    """Read + curation view over the learnings repo, cached by directory mtime.

    Reads are cheap and reload on documents/ mtime. Curation mutations (archive,
    confidence, compress-queue) are file-first and serialized under a mutation
    lock; they do not rebuild the nano-graphrag cache (callers get
    graph_index_stale). Local backend only.
    """

    def __init__(self, repo: Optional[Path] = None):
        self._repo = repo or get_repo_path()
        self._lock = threading.Lock()       # guards the cached read model
        self._mut_lock = threading.Lock()   # serializes all disk mutations
        self._loaded_at: float = -1.0
        self._docs: List[Dict[str, Any]] = []
        self._bodies: Dict[str, str] = {}
        self._entities: Dict[str, Dict[str, Any]] = {}
        # Search index, built once per load (not per query).
        self._tokens: Dict[str, Counter] = {}
        self._df: Counter = Counter()
        self._avg_len: float = 1.0

    @property
    def repo(self) -> Path:
        return self._repo

    def _dir_mtime(self) -> float:
        return documents_mtime(self._repo)

    def _snapshot(self) -> Dict[str, Any]:
        """Load if documents/ changed, then capture a consistent generation of
        the read model UNDER the lock. _load reassigns these attributes (never
        mutates them in place), so the captured references stay coherent even if
        another thread reloads afterward — closing the read-tear where a reader
        could otherwise mix _docs from one generation with _tokens from the next.
        """
        with self._lock:
            stamp = self._dir_mtime()
            if stamp != self._loaded_at:
                self._load()
                self._loaded_at = stamp
            return {
                "docs": self._docs, "bodies": self._bodies, "entities": self._entities,
                "tokens": self._tokens, "df": self._df, "avg_len": self._avg_len,
            }

    def _ensure_loaded(self) -> None:
        self._snapshot()

    def _load(self) -> None:
        docs_dir = self._repo / DOCUMENTS_DIR
        docs: List[Dict[str, Any]] = []
        bodies: Dict[str, str] = {}
        sidecars: Dict[str, Dict[str, Any]] = {}

        for path in sorted(docs_dir.glob("*.md")):
            try:
                fm, body = parse_frontmatter(path.read_text())
            except Exception:
                continue
            doc_id = str(fm.get("id") or path.stem)
            tags = fm.get("tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            sidecar = path.parent / (path.stem + ".entities.yaml")
            entities: List[Dict[str, Any]] = []
            relationships: List[Dict[str, Any]] = []
            if sidecar.exists():
                try:
                    side = yaml.safe_load(sidecar.read_text()) or {}
                    entities = side.get("entities") or []
                    relationships = side.get("relationships") or []
                except Exception:
                    pass
            docs.append({
                "id": doc_id,
                "file": path.name,
                "title": _title(fm, body, path),
                "confidence": _norm_confidence(fm.get("confidence")),
                "type": _norm_type(fm),
                "scope": str(fm.get("scope") or "unscoped"),
                "tags": [str(t) for t in tags],
                "date": _doc_date(fm, path),
                "superseded_by": fm.get("superseded_by"),
                "provenance": fm.get("provenance"),
                "key_insight": fm.get("key_insight"),
                "agent": fm.get("agent"),
                "entity_names": [str(e.get("name", "")).lower() for e in entities if e.get("name")],
                "entity_count": len(entities),
                "word_count": len(body.split()),
            })
            bodies[doc_id] = body
            sidecars[doc_id] = {"entities": entities, "relationships": relationships}

        self._docs = docs
        self._bodies = bodies
        self._entities = sidecars
        self._build_search_index()

    def _build_search_index(self) -> None:
        """Tokenize the corpus once per load so search() is O(query), not O(corpus)."""
        tokens: Dict[str, Counter] = {}
        df: Counter = Counter()
        for d in self._docs:
            toks = Counter(_tokenize(
                d["title"] + " " + " ".join(d["tags"]) + " " + self._bodies.get(d["id"], "")
            ))
            # weight title/tag hits by counting them a second time
            for t in _tokenize(d["title"] + " " + " ".join(d["tags"])):
                toks[t] += 2
            tokens[d["id"]] = toks
            for term in toks:
                df[term] += 1
        self._tokens = tokens
        self._df = df
        self._avg_len = (sum(sum(t.values()) for t in tokens.values()) / len(tokens)
                         if tokens else 1.0)

    # ---------- public API ----------

    def memories(self) -> List[Dict[str, Any]]:
        out = []
        for d in self._snapshot()["docs"]:
            item = dict(d)
            item["browse_score"] = round(self._browse_score(d), 3)
            out.append(item)
        return out

    def memory(self, doc_id: str) -> Optional[Dict[str, Any]]:
        snap = self._snapshot()
        for d in snap["docs"]:
            if d["id"] == doc_id:
                item = dict(d)
                item["body"] = snap["bodies"].get(doc_id, "")
                item["entities"] = snap["entities"].get(doc_id, {}).get("entities", [])
                item["relationships"] = snap["entities"].get(doc_id, {}).get("relationships", [])
                item["related"] = self._related(d, snap["docs"])
                item["browse_score"] = round(self._browse_score(d), 3)
                return item
        return None

    def search(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        """BM25-lite lexical ranking over the index built in _load (semantic
        engine is optional-extra only)."""
        terms = _tokenize(query)
        if not terms:
            return []
        snap = self._snapshot()
        docs, tokens, df, avg_len = snap["docs"], snap["tokens"], snap["df"], snap["avg_len"]
        n_docs = max(len(docs), 1)
        k1, b = 1.4, 0.6
        scored = []
        for d in docs:
            toks = tokens.get(d["id"], Counter())
            dl = sum(toks.values()) or 1
            score = 0.0
            for term in terms:
                tf = toks.get(term, 0)
                if not tf:
                    continue
                idf = math.log(1 + (n_docs - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * dl / avg_len))
            if score > 0:
                item = dict(d)
                item["match_score"] = round(score, 3)
                item["browse_score"] = round(self._browse_score(d, terms), 3)
                scored.append(item)
        scored.sort(key=lambda x: -x["match_score"])
        return scored[:limit]

    def graph(self) -> Dict[str, Any]:
        """Two-layer graph: memory nodes + entity nodes, weighted edges."""
        snap = self._snapshot()
        docs, entities = snap["docs"], snap["entities"]
        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []

        for d in docs:
            nid = "m:" + d["id"]
            nodes[nid] = {
                "id": nid, "label": d["title"], "kind": "memory",
                "confidence": d["confidence"], "type": d["type"],
                "doc": d["id"], "score": self._browse_score(d),
            }
        entity_types: Dict[str, str] = {}
        for d in docs:
            side = entities.get(d["id"], {})
            for e in side.get("entities", []):
                name = str(e.get("name", "")).strip()
                if not name:
                    continue
                key = "e:" + name.lower()
                if key not in nodes:
                    nodes[key] = {
                        "id": key, "label": name, "kind": "entity",
                        "type": str(e.get("type", "concept")),
                    }
                entity_types[name.lower()] = str(e.get("type", "concept"))
                edges.append({"s": "m:" + d["id"], "t": key, "w": 1.0, "kind": "mention"})

        # entity<->entity relations from the indexed graphml, weights preserved
        for src, dst, weight in self._graphml_edges():
            ks, kd = "e:" + src, "e:" + dst
            if ks in nodes and kd in nodes and ks != kd:
                edges.append({"s": ks, "t": kd, "w": weight, "kind": "relation"})

        degree: Counter = Counter()
        for e in edges:
            degree[e["s"]] += 1
            degree[e["t"]] += 1
        for nid, node in nodes.items():
            node["degree"] = degree.get(nid, 0)

        return {"nodes": list(nodes.values()), "edges": edges}

    def stats(self) -> Dict[str, Any]:
        docs = self._snapshot()["docs"]
        conf = Counter(d["confidence"] for d in docs)
        types = Counter(d["type"] for d in docs)
        scopes = Counter(d["scope"] for d in docs)
        tags = Counter(t for d in docs for t in d["tags"])
        ops: Counter = Counter()
        errors = 0
        metrics_path = self._repo / "metrics.jsonl"
        if metrics_path.exists():
            for line in metrics_path.read_text().splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ops[rec.get("op", "unknown")] += 1
                if rec.get("error"):
                    errors += 1
        return {
            "documents": len(docs),
            "repo": str(self._repo),
            "confidence": dict(conf),
            "types": dict(types.most_common()),
            "scopes": dict(scopes.most_common()),
            "top_tags": dict(tags.most_common(20)),
            "metrics_ops": dict(ops),
            "metrics_errors": errors,
            "with_sidecars": sum(1 for d in docs if d["entity_count"]),
        }

    # ---------- curation (mutations, local backend only) ----------
    #
    # Mutations are file-first: the markdown note is the source of truth, and
    # the browser's in-memory view reloads on directory mtime — so an archive
    # or confidence edit is reflected immediately. The nano-graphrag cache used
    # by `reflect search` is NOT rebuilt synchronously: the engine only supports
    # a full-batch reindex (no incremental single-doc path — see the spec's open
    # questions), and blocking a confidence toggle on a multi-minute rebuild is
    # unacceptable. Callers get `graph_index_stale: true` so the UI can hint at
    # running `reflect reindex`.

    def _archive_dir(self) -> Path:
        return self._repo / _ARCHIVE_DIRNAME

    def _doc_paths(self, doc_id: str) -> Optional[tuple[Path, Optional[Path]]]:
        """Return (md_path, sidecar_path|None) for a live doc, or None."""
        for d in self._snapshot()["docs"]:
            if d["id"] == doc_id:
                md = self._repo / DOCUMENTS_DIR / d["file"]
                sidecar = md.parent / (md.stem + ".entities.yaml")
                return md, (sidecar if sidecar.exists() else None)
        return None

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """Write via a temp file + os.replace so a reader never sees a half file."""
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(text)
        tmp.replace(path)

    @staticmethod
    def _move_pair(md_src: Path, md_dst: Path,
                   side_src: Optional[Path], side_dst: Path) -> None:
        """Move a note and, if present, its sidecar together.

        If the sidecar move fails, roll the note move back so the pair is never
        left half-moved (a note in archived/ with its sidecar still in documents/
        or vice-versa).
        """
        moving_sidecar = side_src is not None and side_src.exists()
        # Guard the sidecar destination too (the note dest is checked by callers),
        # so we never silently overwrite orphan sidecar debris.
        if moving_sidecar and side_dst.exists():
            raise MutationError(f"destination sidecar already exists: {side_dst.name}")
        md_src.replace(md_dst)
        if moving_sidecar:
            try:
                side_src.replace(side_dst)
            except OSError:
                md_dst.replace(md_src)
                raise

    def archive(self, doc_id: str) -> Dict[str, Any]:
        """Soft-archive: move note + sidecar out of documents/ into archived/.

        The note leaves documents/, so the file-based recall corpus drops it at
        once; the nano-graphrag cache used by `reflect search` graph/naive modes
        still returns it until `reflect reindex` (graph_index_stale). This
        archived/ dir is the browser's own soft-delete and is deliberately
        separate from the forget sweep's `.forgotten/` (which the sweep tracks
        with its own DB accounting) — see docs/reflect-serve.md.
        """
        with self._mut_lock:
            paths = self._doc_paths(doc_id)
            if not paths:
                raise MutationError(f"unknown memory: {doc_id}")
            md, sidecar = paths
            dest_dir = self._archive_dir()
            dest_dir.mkdir(parents=True, exist_ok=True)
            if (dest_dir / md.name).exists():
                raise MutationError(f"archive already contains {md.name}")
            self._move_pair(md, dest_dir / md.name,
                            sidecar, dest_dir / (md.stem + ".entities.yaml"))
            self._dequeue_from_compress(doc_id)
            self._invalidate()
        return {"ok": True, "id": doc_id, "archived": True, "graph_index_stale": True}

    def restore(self, doc_id: str) -> Dict[str, Any]:
        """Reverse an archive: move note + sidecar back into documents/."""
        with self._mut_lock:
            adir = self._archive_dir()
            src = None
            for p in adir.glob("*.md"):
                try:
                    fm, _ = parse_frontmatter(p.read_text())
                except Exception:
                    continue
                if str(fm.get("id") or p.stem) == doc_id:
                    src = p
                    break
            if src is None:
                raise MutationError(f"not in archive: {doc_id}")
            docs_dir = self._repo / DOCUMENTS_DIR
            docs_dir.mkdir(parents=True, exist_ok=True)
            if (docs_dir / src.name).exists():
                raise MutationError(f"a live note already occupies {src.name}")
            sidecar = adir / (src.stem + ".entities.yaml")
            self._move_pair(src, docs_dir / src.name,
                            sidecar, docs_dir / (src.stem + ".entities.yaml"))
            self._invalidate()
        return {"ok": True, "id": doc_id, "archived": False, "graph_index_stale": True}

    def set_confidence(self, doc_id: str, value: str) -> Dict[str, Any]:
        """Rewrite the frontmatter `confidence` field.

        Splits on real `---` delimiter LINES (not any `---` substring) so a
        value containing `---` can't corrupt the note, and only matches a
        column-0 `confidence:` so a nested/quoted value is left alone.
        """
        value = str(value).strip().lower()
        if value not in _VALID_CONFIDENCE:
            raise MutationError(f"confidence must be one of {_VALID_CONFIDENCE}")
        with self._mut_lock:
            paths = self._doc_paths(doc_id)
            if not paths:
                raise MutationError(f"unknown memory: {doc_id}")
            md = paths[0]
            split = _split_frontmatter(md.read_text())
            if split is None:
                raise MutationError("note has no frontmatter to edit")
            fm_text, body = split
            lines = fm_text.split("\n")
            replaced = False
            for i, line in enumerate(lines):
                if re.match(r"^confidence\s*:", line):
                    lines[i] = f"confidence: {value}"
                    replaced = True
                    break
            if not replaced:
                lines.append(f"confidence: {value}")
            self._atomic_write(md, "---\n" + "\n".join(lines) + "\n---\n" + body)
            self._invalidate()
        return {"ok": True, "id": doc_id, "confidence": value, "graph_index_stale": True}

    def queue_compress(self, ids: List[str]) -> Dict[str, Any]:
        """Mark a group of memories for compression by the /reflect consolidate skill."""
        with self._mut_lock:
            self._ensure_loaded()
            live = {d["id"] for d in self._docs}
            ids = [i for i in dict.fromkeys(ids) if i in live]
            if len(ids) < 2:
                raise MutationError("compress needs at least two live memories")
            queue = self._read_compress_queue()
            queue["groups"].append({
                "ids": ids,
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "status": "pending",
            })
            self._write_compress_queue(queue)
        return {"ok": True, "queued": ids, "groups": len(queue["groups"])}

    def archived(self) -> List[Dict[str, Any]]:
        adir = self._archive_dir()
        if not adir.exists():
            return []
        out = []
        for p in sorted(adir.glob("*.md")):
            try:
                fm, body = parse_frontmatter(p.read_text())
            except Exception:
                continue
            out.append({
                "id": str(fm.get("id") or p.stem),
                "title": _title(fm, body, p),
                "confidence": _norm_confidence(fm.get("confidence")),
                "type": _norm_type(fm),
                "scope": str(fm.get("scope") or "unscoped"),
            })
        return out

    def compress_queue(self) -> Dict[str, Any]:
        return self._read_compress_queue()

    def _read_compress_queue(self) -> Dict[str, Any]:
        path = self._repo / _COMPRESS_QUEUE_FILE
        if not path.exists():
            return {"version": _COMPRESS_QUEUE_VERSION, "groups": []}
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as e:
            # Fail loud rather than silently overwrite an existing-but-malformed
            # queue on the next write.
            raise MutationError(f"{_COMPRESS_QUEUE_FILE} is malformed: {e}")
        if not isinstance(data, dict):
            raise MutationError(f"{_COMPRESS_QUEUE_FILE} is not a mapping")
        data.setdefault("version", _COMPRESS_QUEUE_VERSION)
        data.setdefault("groups", [])
        return data

    def _write_compress_queue(self, queue: Dict[str, Any]) -> None:
        self._atomic_write(self._repo / _COMPRESS_QUEUE_FILE,
                           yaml.safe_dump(queue, sort_keys=False))

    def _dequeue_from_compress(self, doc_id: str) -> None:
        """Drop an archived id from any pending compress group (spec edge case)."""
        queue = self._read_compress_queue()
        changed = False
        for group in queue["groups"]:
            if doc_id in group.get("ids", []):
                group["ids"] = [i for i in group["ids"] if i != doc_id]
                changed = True
        queue["groups"] = [g for g in queue["groups"] if len(g.get("ids", [])) >= 2]
        if changed:
            self._write_compress_queue(queue)

    def _invalidate(self) -> None:
        with self._lock:
            self._loaded_at = -1.0

    # ---------- internals ----------

    def _browse_score(self, d: Dict[str, Any], terms: Optional[List[str]] = None) -> float:
        """A browse-ordering heuristic: confidence × recency × tag-overlap.

        This is NOT the recall reranker's score. The real reranker (recall.py R8)
        deliberately dropped exp-decay recency because it crushed year-old notes
        to ~2%, replacing it with bounded ±10% boosts over a cross-encoder base.
        This score keeps the simple exp-decay purely to order the browse list —
        do not read it as "what recall would rank first".
        """
        conf = _CONF_WEIGHT.get(d["confidence"], 0.55)
        try:
            age_days = (datetime.now(timezone.utc)
                        - datetime.fromisoformat(d["date"]).replace(tzinfo=timezone.utc)).days
        except ValueError:
            age_days = 365
        recency = math.exp(-max(age_days, 0) / _RECENCY_HALF_LIFE_DAYS)
        overlap = 1.0
        if terms:
            tagset = {t.lower() for t in d["tags"]}
            hits = sum(1 for t in terms if t in tagset)
            overlap = 1.0 + 0.5 * hits
        return conf * recency * overlap

    def _related(self, d: Dict[str, Any], docs: List[Dict[str, Any]],
                 limit: int = 6) -> List[Dict[str, Any]]:
        mine_tags = set(d["tags"])
        mine_ents = set(d["entity_names"])
        scored = []
        for other in docs:
            if other["id"] == d["id"]:
                continue
            s = 3 * len(mine_tags & set(other["tags"])) + len(mine_ents & set(other["entity_names"]))
            if other["superseded_by"] == d["id"] or d["superseded_by"] == other["id"]:
                s += 10
            if s > 0:
                scored.append((s, other))
        scored.sort(key=lambda x: (-x[0], x[1]["title"]))
        return [{"id": o["id"], "title": o["title"], "confidence": o["confidence"],
                 "shared": s} for s, o in scored[:limit]]

    def _graphml_edges(self):
        for candidate in (self._repo / CACHE_DIR / _GRAPHML, self._repo / ".graph" / _GRAPHML):
            if candidate.exists():
                try:
                    tree = ET.parse(candidate)
                except ET.ParseError:
                    continue
                weight_key = None
                for key in tree.findall(".//g:key", _NS):
                    if key.get("attr.name") == "weight" and key.get("for") == "edge":
                        weight_key = key.get("id")
                for edge in tree.findall(".//g:edge", _NS):
                    w = 1.0
                    if weight_key is not None:
                        el = edge.find(f"g:data[@key='{weight_key}']", _NS)
                        if el is not None and el.text:
                            try:
                                w = float(el.text)
                            except ValueError:
                                pass
                    src = (edge.get("source") or "").strip('"').lower()
                    dst = (edge.get("target") or "").strip('"').lower()
                    if src and dst:
                        yield src, dst, w
                return


_STATIC_DIR = Path(__file__).parent / "cli" / "serve_static"


def make_handler(kb: KnowledgeBase):
    class Handler(BaseHTTPRequestHandler):
        server_version = "reflect-serve"

        def log_message(self, fmt, *args):  # quiet by default; tmux log has access lines
            print("%s - %s" % (self.address_string(), fmt % args))

        def _send(self, code: int, payload: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)

        def _json(self, obj: Any, code: int = 200) -> None:
            self._send(code, json.dumps(obj, default=str).encode(), "application/json")

        def _read_json_body(self) -> Any:
            length = int(self.headers.get("Content-Length") or 0)
            if not length:
                return {}
            raw = self.rfile.read(length)
            try:
                return json.loads(raw or b"{}")
            except json.JSONDecodeError:
                return {}

        def _guard(self, require_csrf: bool) -> bool:
            """Loopback-only + anti-CSRF gate.

            Rejects a mismatched Host (defeats DNS-rebinding: an attacker page on
            evil.com resolving to 127.0.0.1 still sends `Host: evil.com`), and
            requires an `X-Reflect` header on mutations. A cross-origin page can
            only send that header via a fetch that triggers a CORS preflight,
            which this server never approves — so a drive-by POST can't mutate.
            """
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
            if host not in ("127.0.0.1", "localhost", "[::1]", "::1"):
                self._json({"error": "forbidden: non-loopback Host"}, 403)
                return False
            if require_csrf and not self.headers.get("X-Reflect"):
                self._json({"error": "forbidden: missing X-Reflect header"}, 403)
                return False
            return True

        def do_GET(self):  # noqa: N802 (stdlib naming)
            if not self._guard(require_csrf=False):
                return
            url = urlparse(self.path)
            parts = [unquote(p) for p in url.path.split("/") if p]
            try:
                if not parts or parts == ["index.html"]:
                    html = (_STATIC_DIR / "index.html").read_bytes()
                    self._send(200, html, "text/html; charset=utf-8")
                elif parts[:2] == ["api", "memories"] and len(parts) == 2:
                    self._json(self.server_kb.memories())
                elif parts[:2] == ["api", "memories"] and len(parts) == 3:
                    mem = self.server_kb.memory(parts[2])
                    self._json(mem or {"error": "not found"}, 200 if mem else 404)
                elif parts == ["api", "search"]:
                    q = (parse_qs(url.query).get("q") or [""])[0]
                    self._json(self.server_kb.search(q))
                elif parts == ["api", "graph"]:
                    self._json(self.server_kb.graph())
                elif parts == ["api", "stats"]:
                    self._json(self.server_kb.stats())
                elif parts == ["api", "archived"]:
                    self._json(self.server_kb.archived())
                elif parts == ["api", "compress-queue"]:
                    self._json(self.server_kb.compress_queue())
                else:
                    self._json({"error": "not found"}, 404)
            except BrokenPipeError:
                pass
            except Exception as e:  # surface server faults as JSON, not silence
                self._json({"error": str(e)}, 500)

        def do_POST(self):  # noqa: N802 (stdlib naming)
            if not self._guard(require_csrf=True):
                return
            url = urlparse(self.path)
            parts = [unquote(p) for p in url.path.split("/") if p]
            kb = self.server_kb
            try:
                body = self._read_json_body()
                if parts[:2] == ["api", "memories"] and len(parts) == 4:
                    doc_id, action = parts[2], parts[3]
                    if action == "archive":
                        self._json(kb.archive(doc_id))
                    elif action == "restore":
                        self._json(kb.restore(doc_id))
                    elif action == "confidence":
                        self._json(kb.set_confidence(doc_id, body.get("value", "")))
                    else:
                        self._json({"error": "unknown action"}, 404)
                elif parts == ["api", "compress-queue"]:
                    self._json(kb.queue_compress(body.get("ids", [])))
                else:
                    self._json({"error": "not found"}, 404)
            except MutationError as e:
                self._json({"error": str(e)}, 400)
            except BrokenPipeError:
                pass
            except Exception as e:
                self._json({"error": str(e)}, 500)

        server_kb = kb

    return Handler


def run(host: str = "127.0.0.1", port: int = 8377, repo: Optional[Path] = None) -> None:
    kb = KnowledgeBase(repo)
    httpd = ThreadingHTTPServer((host, port), make_handler(kb))
    print(f"reflect serve — browsing {kb.repo}")
    print(f"listening on http://{host}:{port}")
    httpd.serve_forever()
