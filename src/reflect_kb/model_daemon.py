"""Persistent model daemon: load torch + the embed/rerank models once,
serve them over a unix socket.

Every ``reflect search/embed/rerank`` used to cold-boot torch and load
all-mpnet-base-v2 (+ the cross-encoder) per process — ~3.5 GB RSS and
10-30 s each, multiplied by session-start recall fan-out across parallel
claude sessions → OOM. This module fixes that:

- **Server** (``python -m reflect_kb.model_daemon``): binds a unix socket,
  lazily loads the models on first use, answers ``embed``/``rerank``/``ping``
  as newline-delimited JSON, exits after an idle timeout.
- **Client** (:func:`daemon_embed` / :func:`daemon_rerank`): connects to the
  socket, auto-spawning the daemon when absent. Any failure returns ``None``
  so callers fall back to the in-process path — the daemon is a pure
  optimization, never a blocker.

The socket is keyed on (uid, embed model, CE model): the models are
KB-independent, so one daemon serves every KB on the box. Requests are
handled serially — warm ops are ms-scale and torch prefers one thread.
# ponytail: serial daemon; add a thread pool only if warm latency ever matters.

Env knobs:
- ``REFLECT_NO_DAEMON=1``   — skip the daemon entirely (always in-proc).
- ``REFLECT_IDLE_TIMEOUT``  — daemon idle seconds before exit (default 1800, 0 = never).
- ``REFLECT_DAEMON_TIMEOUT``— client per-request seconds (default 120; covers a
  cold model load happening inside the daemon).
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

_SPAWN_WAIT_S = 15.0  # daemon binds before loading models, so ready is fast


def _model_names() -> tuple[str, str]:
    embed = os.environ.get("REFLECT_EMBED_MODEL", "all-mpnet-base-v2")
    ce = os.environ.get("REFLECT_CE_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
    return embed, ce


def socket_path() -> Path:
    """Per-(user, model pair, TMPDIR) socket path. Models are KB-independent,
    so one daemon serves every KB; a REFLECT_EMBED_MODEL/REFLECT_CE_MODEL
    override hashes to its own socket and gets its own daemon. TMPDIR is part
    of the key (isolation for tests), but the file lands in /tmp when TMPDIR
    would push the path past AF_UNIX's ~104-char sun_path limit (macOS)."""
    embed, ce = _model_names()
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    key = hashlib.sha1(
        f"{os.getuid()}|{embed}|{ce}|{tmpdir}".encode()
    ).hexdigest()[:16]
    candidate = Path(tmpdir) / f"reflect-md-{key}.sock"
    if len(str(candidate)) > 100:
        candidate = Path("/tmp") / f"reflect-md-{key}.sock"
    return candidate


# ---------------------------------------------------------------------------
# Single-flight lock (Phase 1) — caps in-process model loads at one at a time.
# Loading torch + the models costs ~3.5 GB RSS; parallel cold boots OOM the
# box. Held for the caller's process lifetime unless released explicitly
# (the daemon releases after loading so fallback processes aren't starved).
# ---------------------------------------------------------------------------

_SINGLEFLIGHT_FD = None


def acquire_singleflight() -> None:
    """Block until this process holds the model-load single-flight lock.

    Idempotent; best-effort — if flock is unavailable it degrades to running
    uncapped rather than failing the command."""
    global _SINGLEFLIGHT_FD
    if _SINGLEFLIGHT_FD is not None:
        return
    try:
        import fcntl

        embed, ce = _model_names()
        key = hashlib.sha1(f"{os.getuid()}|{embed}|{ce}".encode()).hexdigest()[:16]
        lock_path = Path(os.environ.get("TMPDIR", "/tmp")) / f"reflect-sf-{key}.lock"
        fd = open(lock_path, "w")
        fcntl.flock(fd, fcntl.LOCK_EX)  # blocks; released on process exit
        _SINGLEFLIGHT_FD = fd
    except Exception:
        pass  # no lock available → run uncapped, don't break the command


def release_singleflight() -> None:
    """Release the lock early (daemon: after its one-time model load)."""
    global _SINGLEFLIGHT_FD
    if _SINGLEFLIGHT_FD is None:
        return
    try:
        import fcntl

        fcntl.flock(_SINGLEFLIGHT_FD, fcntl.LOCK_UN)
        _SINGLEFLIGHT_FD.close()
    except Exception:
        pass
    _SINGLEFLIGHT_FD = None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def _request(payload: dict, timeout: float) -> Optional[dict]:
    """One JSON line out, one JSON line back. None on any failure."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            sock.connect(str(socket_path()))
            f = sock.makefile("rwb")
            f.write(json.dumps(payload).encode() + b"\n")
            f.flush()
            line = f.readline()
        if not line:
            return None
        resp = json.loads(line)
        return resp if isinstance(resp, dict) and resp.get("ok") else None
    except Exception:
        return None


def _ensure_daemon() -> bool:
    """Connectable daemon, spawning one if needed. False → use fallback."""
    if os.name != "posix" or os.environ.get("REFLECT_NO_DAEMON") == "1":
        return False
    if _request({"op": "ping"}, timeout=2.0):
        return True
    sp = socket_path()
    try:
        sp.unlink()  # stale socket from a dead daemon
    except OSError:
        pass
    try:
        subprocess.Popen(
            [sys.executable, "-m", "reflect_kb.model_daemon"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,  # detach: survives this CLI's exit
        )
    except Exception:
        return False
    deadline = time.monotonic() + _SPAWN_WAIT_S
    while time.monotonic() < deadline:
        if _request({"op": "ping"}, timeout=1.0):
            return True
        time.sleep(0.15)
    return False


def _op_timeout() -> float:
    try:
        return float(os.environ.get("REFLECT_DAEMON_TIMEOUT", "120"))
    except ValueError:
        return 120.0


def daemon_embed(texts: Sequence[str], truncate: bool = True) -> Optional[list]:
    """Unit-normalized vectors via the daemon, or None → caller loads in-proc."""
    if not _ensure_daemon():
        return None
    resp = _request(
        {"op": "embed", "texts": list(texts), "truncate": truncate},
        timeout=_op_timeout(),
    )
    return resp["vectors"] if resp else None


def daemon_rerank(query: str, texts: Sequence[str]) -> Optional[list]:
    """Cross-encoder logits via the daemon, or None → caller loads in-proc."""
    if not _ensure_daemon():
        return None
    resp = _request(
        {"op": "rerank", "query": query, "texts": list(texts)},
        timeout=_op_timeout(),
    )
    return resp["scores"] if resp else None


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class _Server:
    """Serial unix-socket server. Models load lazily on first use, under the
    single-flight lock (released right after) so a daemon boot can't stack on
    top of an in-proc fallback load."""

    def __init__(self) -> None:
        self._embedder = None
        self._reranker = None

    def _load_embedder(self):
        if self._embedder is None:
            acquire_singleflight()
            try:
                embed, _ = _model_names()
                from sentence_transformers import SentenceTransformer

                self._embedder = SentenceTransformer(embed)
            finally:
                release_singleflight()
        return self._embedder

    def _load_reranker(self):
        if self._reranker is None:
            # CrossEncoderReranker._load acquires the single-flight lock
            # itself — don't also acquire here (a nested acquire through a
            # second module instance self-deadlocks; see __main__ note).
            try:
                from reflect_kb.recall.cross_encoder import CrossEncoderReranker

                reranker = CrossEncoderReranker()
                reranker._load()
                self._reranker = reranker
            finally:
                release_singleflight()
        return self._reranker

    def handle(self, req: dict) -> dict:
        op = req.get("op")
        if op == "ping":
            return {"ok": True}
        if op == "shutdown":
            return {"ok": True, "bye": True}
        if op == "embed":
            # Mirrors the in-proc paths exactly for parity: embed_texts
            # truncates to _MAX_EMBED_CHARS, nano-graphrag's embedding_func
            # does not — the client says which behavior it wants.
            texts = [str(t) for t in (req.get("texts") or [])]
            if req.get("truncate", True):
                from reflect_kb.cli.graph_engine import _MAX_EMBED_CHARS

                texts = [t[:_MAX_EMBED_CHARS] for t in texts]
            model = self._load_embedder()
            vectors = model.encode(texts, normalize_embeddings=True)
            return {"ok": True, "vectors": [[float(x) for x in v] for v in vectors]}
        if op == "rerank":
            texts = [str(t) for t in (req.get("texts") or [])]
            scores = self._load_reranker().score(str(req.get("query", "")), texts)
            return {"ok": True, "scores": scores}
        return {"ok": False, "error": f"unknown op: {op}"}


def serve() -> None:
    # The daemon reuses in-proc code paths (CrossEncoderReranker.score) that
    # themselves try the daemon first — disable the client inside the daemon
    # or it would deadlock requesting itself while serving serially.
    os.environ["REFLECT_NO_DAEMON"] = "1"
    sp = socket_path()
    server_sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        old_umask = os.umask(0o177)  # socket file 0600 — per-user only
        try:
            server_sock.bind(str(sp))
        finally:
            os.umask(old_umask)
    except OSError:
        return  # lost the spawn race to another session's daemon — theirs serves
    server_sock.listen(8)

    try:
        idle_limit = float(os.environ.get("REFLECT_IDLE_TIMEOUT", "1800"))
    except ValueError:
        idle_limit = 1800.0
    # Idle is only checked when accept() times out, so the accept timeout
    # bounds how late an idle exit can fire (also keeps tests fast).
    server_sock.settimeout(min(30.0, idle_limit) if idle_limit > 0 else 30.0)

    state = _Server()
    last_used = time.monotonic()
    try:
        while True:
            try:
                conn, _ = server_sock.accept()
            except socket.timeout:
                if idle_limit > 0 and time.monotonic() - last_used > idle_limit:
                    return  # idle out: free the ~3.5 GB
                continue
            last_used = time.monotonic()
            bye = False
            try:
                with conn:
                    conn.settimeout(60.0)
                    f = conn.makefile("rwb")
                    line = f.readline()
                    if not line:
                        continue
                    try:
                        resp = state.handle(json.loads(line))
                    except Exception as e:  # bad request must never kill the daemon
                        resp = {"ok": False, "error": str(e)}
                    bye = bool(resp.get("bye"))
                    f.write(json.dumps(resp).encode() + b"\n")
                    f.flush()
            except Exception:
                continue  # client vanished mid-request; keep serving
            if bye:
                return
    finally:
        try:
            sp.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    # Run serve() from the canonical module import, not this __main__ copy.
    # `python -m` gives the file two module objects (__main__ and
    # reflect_kb.model_daemon); the single-flight fd global must live in ONE
    # of them or a nested acquire flocks the file twice in the same process
    # and deadlocks against itself.
    from reflect_kb.model_daemon import serve as _serve

    _serve()
