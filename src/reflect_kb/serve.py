"""reflect serve — local web browser for the knowledge base.

Stdlib-only HTTP server (no fastapi/uvicorn in the base dependency set) that
exposes the KB as a small JSON API plus a bundled single-file SPA. Read-only:
mutations stay with the CLI/skills until the full serve milestone lands.

Endpoints:
    GET /                      SPA (cli/serve_static/index.html)
    GET /api/memories          all memories (frontmatter + derived fields)
    GET /api/memories/<id>     one memory: body, entities, related memories
    GET /api/search?q=...      lexical BM25-lite ranking over title/tags/body
    GET /api/graph             two-layer graph: memory + entity nodes, weighted edges
    GET /api/stats             KB counts + metrics.jsonl op aggregates
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


class KnowledgeBase:
    """Read-only view over the learnings repo, cached by directory mtime."""

    def __init__(self, repo: Optional[Path] = None):
        self._repo = repo or get_repo_path()
        self._lock = threading.Lock()
        self._loaded_at: float = -1.0
        self._docs: List[Dict[str, Any]] = []
        self._bodies: Dict[str, str] = {}
        self._entities: Dict[str, Dict[str, Any]] = {}

    @property
    def repo(self) -> Path:
        return self._repo

    def _dir_mtime(self) -> float:
        docs = self._repo / DOCUMENTS_DIR
        if not docs.exists():
            return 0.0
        stamps = [docs.stat().st_mtime]
        stamps += [p.stat().st_mtime for p in docs.glob("*.md")]
        return max(stamps)

    def _ensure_loaded(self) -> None:
        with self._lock:
            stamp = self._dir_mtime()
            if stamp == self._loaded_at:
                return
            self._load()
            self._loaded_at = stamp

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

    # ---------- public API ----------

    def memories(self) -> List[Dict[str, Any]]:
        self._ensure_loaded()
        out = []
        for d in self._docs:
            item = dict(d)
            item["recall_score"] = round(self._recall_score(d), 3)
            out.append(item)
        return out

    def memory(self, doc_id: str) -> Optional[Dict[str, Any]]:
        self._ensure_loaded()
        for d in self._docs:
            if d["id"] == doc_id:
                item = dict(d)
                item["body"] = self._bodies.get(doc_id, "")
                item["entities"] = self._entities.get(doc_id, {}).get("entities", [])
                item["relationships"] = self._entities.get(doc_id, {}).get("relationships", [])
                item["related"] = self._related(d)
                item["recall_score"] = round(self._recall_score(d), 3)
                return item
        return None

    def search(self, query: str, limit: int = 25) -> List[Dict[str, Any]]:
        """BM25-lite lexical ranking (semantic engine is optional-extra only)."""
        self._ensure_loaded()
        terms = _tokenize(query)
        if not terms:
            return []
        n_docs = max(len(self._docs), 1)
        df: Counter = Counter()
        doc_tokens: Dict[str, Counter] = {}
        for d in self._docs:
            toks = Counter(_tokenize(
                d["title"] * 1 + " " + " ".join(d["tags"]) + " " + self._bodies.get(d["id"], "")
            ))
            # weight title/tag hits by counting them again
            for t in _tokenize(d["title"] + " " + " ".join(d["tags"])):
                toks[t] += 2
            doc_tokens[d["id"]] = toks
            for term in set(toks):
                df[term] += 1

        avg_len = sum(sum(t.values()) for t in doc_tokens.values()) / n_docs
        k1, b = 1.4, 0.6
        scored = []
        for d in self._docs:
            toks = doc_tokens[d["id"]]
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
                item["recall_score"] = round(self._recall_score(d, terms), 3)
                scored.append(item)
        scored.sort(key=lambda x: -x["match_score"])
        return scored[:limit]

    def graph(self) -> Dict[str, Any]:
        """Two-layer graph: memory nodes + entity nodes, weighted edges."""
        self._ensure_loaded()
        nodes: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []

        for d in self._docs:
            nid = "m:" + d["id"]
            nodes[nid] = {
                "id": nid, "label": d["title"], "kind": "memory",
                "confidence": d["confidence"], "type": d["type"],
                "doc": d["id"], "score": self._recall_score(d),
            }
        entity_types: Dict[str, str] = {}
        for d in self._docs:
            side = self._entities.get(d["id"], {})
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
        self._ensure_loaded()
        conf = Counter(d["confidence"] for d in self._docs)
        types = Counter(d["type"] for d in self._docs)
        scopes = Counter(d["scope"] for d in self._docs)
        tags = Counter(t for d in self._docs for t in d["tags"])
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
            "documents": len(self._docs),
            "repo": str(self._repo),
            "confidence": dict(conf),
            "types": dict(types.most_common()),
            "scopes": dict(scopes.most_common()),
            "top_tags": dict(tags.most_common(20)),
            "metrics_ops": dict(ops),
            "metrics_errors": errors,
            "with_sidecars": sum(1 for d in self._docs if d["entity_count"]),
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
        self._ensure_loaded()
        for d in self._docs:
            if d["id"] == doc_id:
                md = self._repo / DOCUMENTS_DIR / d["file"]
                sidecar = md.parent / (md.stem + ".entities.yaml")
                return md, (sidecar if sidecar.exists() else None)
        return None

    def archive(self, doc_id: str) -> Dict[str, Any]:
        """Soft-archive: move note + sidecar out of documents/ into archived/."""
        paths = self._doc_paths(doc_id)
        if not paths:
            raise MutationError(f"unknown memory: {doc_id}")
        md, sidecar = paths
        dest_dir = self._archive_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        md.replace(dest_dir / md.name)
        if sidecar:
            sidecar.replace(dest_dir / sidecar.name)
        self._dequeue_from_compress(doc_id)
        self._invalidate()
        return {"ok": True, "id": doc_id, "archived": True, "graph_index_stale": True}

    def restore(self, doc_id: str) -> Dict[str, Any]:
        """Reverse an archive: move note + sidecar back into documents/."""
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
        src.replace(docs_dir / src.name)
        sidecar = adir / (src.stem + ".entities.yaml")
        if sidecar.exists():
            sidecar.replace(docs_dir / sidecar.name)
        self._invalidate()
        return {"ok": True, "id": doc_id, "archived": False, "graph_index_stale": True}

    def set_confidence(self, doc_id: str, value: str) -> Dict[str, Any]:
        """Rewrite the frontmatter `confidence` field (minimal line-level edit)."""
        value = str(value).strip().lower()
        if value not in _VALID_CONFIDENCE:
            raise MutationError(f"confidence must be one of {_VALID_CONFIDENCE}")
        paths = self._doc_paths(doc_id)
        if not paths:
            raise MutationError(f"unknown memory: {doc_id}")
        md = paths[0]
        text = md.read_text()
        if not text.startswith("---"):
            raise MutationError("note has no frontmatter to edit")
        head, fm_block, body = text.split("---", 2)
        lines = fm_block.split("\n")
        replaced = False
        for i, line in enumerate(lines):
            if re.match(r"\s*confidence\s*:", line):
                lines[i] = f"confidence: {value}"
                replaced = True
                break
        if not replaced:
            insert_at = 1 if lines and lines[0] == "" else 0
            lines.insert(insert_at, f"confidence: {value}")
        md.write_text(head + "---" + "\n".join(lines) + "---" + body)
        self._invalidate()
        return {"ok": True, "id": doc_id, "confidence": value, "graph_index_stale": True}

    def queue_compress(self, ids: List[str]) -> Dict[str, Any]:
        """Mark a group of memories for compression by the /reflect consolidate skill."""
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
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text()) or {}
                data.setdefault("version", _COMPRESS_QUEUE_VERSION)
                data.setdefault("groups", [])
                return data
            except Exception:
                pass
        return {"version": _COMPRESS_QUEUE_VERSION, "groups": []}

    def _write_compress_queue(self, queue: Dict[str, Any]) -> None:
        path = self._repo / _COMPRESS_QUEUE_FILE
        path.write_text(yaml.safe_dump(queue, sort_keys=False))

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

    def _recall_score(self, d: Dict[str, Any], terms: Optional[List[str]] = None) -> float:
        """confidence × recency × tag-overlap — mirrors the recall reranker shape."""
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

    def _related(self, d: Dict[str, Any], limit: int = 6) -> List[Dict[str, Any]]:
        mine_tags = set(d["tags"])
        mine_ents = set(d["entity_names"])
        scored = []
        for other in self._docs:
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

        def do_GET(self):  # noqa: N802 (stdlib naming)
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
