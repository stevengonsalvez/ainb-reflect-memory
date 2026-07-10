"""Persistent model daemon: load torch + the embed/rerank models once,
serve them over a unix socket.

Every ``reflect search/embed/rerank`` used to cold-boot torch and load
all-mpnet-base-v2 (+ the cross-encoder) per process — ~3.5 GB RSS and
10-30 s each, multiplied by session-start recall fan-out across parallel
claude sessions → OOM. This module fixes that:

- **Server** (:func:`serve`): binds a unix socket, lazily loads the models
  on first use, answers ``embed``/``rerank``/``ping``/``shutdown`` as
  newline-delimited JSON, exits after an idle timeout.
- **Client** (:func:`daemon_embed` / :func:`daemon_rerank`): sends the op
  directly, auto-spawning the daemon when the socket is dead. Any failure
  returns ``None`` so callers fall back to the in-process path — the daemon
  is a pure optimization, never a blocker.

Liveness is judged by connect() outcome, never by response latency: the
server is serial, so a request issued during a cold model load waits its
turn (a busy daemon must NOT be mistaken for a dead one and replaced).
Spawning is serialized by a spawn flock so parallel first-use clients
produce exactly one daemon; the daemon only unlinks its socket file on
exit when it still owns it (inode check).

The socket is keyed on (uid, embed model, CE model, TMPDIR): the models
are KB-independent, so one daemon serves every KB on the box. Requests
are handled serially — warm ops are ms-scale and torch prefers one thread.
# ponytail: serial daemon; add a thread pool only if warm latency ever matters.

This module is also the single source of truth for the model names —
graph_engine and cross_encoder import them from here so the daemon key,
the client guard, and both in-process loaders can never disagree.

Env knobs:
- ``REFLECT_EMBED_MODEL``   — embedding model (default all-mpnet-base-v2).
- ``REFLECT_CE_MODEL``      — cross-encoder model (default ms-marco-MiniLM-L-6-v2).
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

# Model names are read from the env ONCE, here. The embedding model is
# shared by indexing (nano-graphrag) and `reflect embed` (recall's MMR
# diversity step) — similarity must live in ONE space, so a swap (e.g.
# BAAI/bge-large-en-v1.5) requires a fresh reindex. The CE default
# (~90MB) is auto-downloaded on first use.
EMBEDDING_MODEL_NAME = os.environ.get("REFLECT_EMBED_MODEL", "all-mpnet-base-v2")
DEFAULT_CE_MODEL = os.environ.get(
    "REFLECT_CE_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2"
)

_SPAWN_WAIT_S = 15.0  # daemon binds before loading models, so ready is fast
_CONNECT_TIMEOUT_S = 2.0  # connect() to a bound socket is instant even when busy

# True inside the daemon process (the server reuses in-proc scoring code
# that is daemon-aware — without this it would dial its own busy socket
# and deadlock), and set after a failed spawn so a box where the daemon
# can't run pays the spawn wait once per process, not once per call.
_DAEMON_DISABLED = False


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _key() -> str:
    """Runtime-file key: same scope for the socket, spawn lock, and
    single-flight lock so they always travel together."""
    tmpdir = os.environ.get("TMPDIR", "/tmp")
    return hashlib.sha1(
        f"{os.getuid()}|{EMBEDDING_MODEL_NAME}|{DEFAULT_CE_MODEL}|{tmpdir}".encode()
    ).hexdigest()[:16]


def _runtime_path(suffix: str) -> Path:
    """A runtime file in TMPDIR, falling back to /tmp when TMPDIR would push
    a socket path past AF_UNIX's ~104-char sun_path limit (macOS). TMPDIR is
    part of the key, so the fallback stays collision-free per TMPDIR."""
    candidate = Path(os.environ.get("TMPDIR", "/tmp")) / f"reflect-md-{_key()}{suffix}"
    if len(str(candidate)) > 100:
        candidate = Path("/tmp") / f"reflect-md-{_key()}{suffix}"
    return candidate


def socket_path() -> Path:
    return _runtime_path(".sock")


# ---------------------------------------------------------------------------
# Single-flight lock — caps concurrent IN-PROCESS model loads. Loading torch
# + the models costs ~3.5 GB RSS; parallel cold boots OOM the box. CLI
# fallback processes hold the lock for their lifetime (so at most one
# fallback's models are resident at a time); the daemon acquires with a
# bounded wait and releases right after loading (it IS the shared instance —
# starving it behind a long-lived fallback job would hang every client).
# ---------------------------------------------------------------------------

_SINGLEFLIGHT_FD = None


def acquire_singleflight(timeout: Optional[float] = None) -> bool:
    """Take the model-load single-flight lock.

    Blocks indefinitely by default; with ``timeout`` polls non-blocking and
    gives up after the deadline (returns False → caller proceeds uncapped).
    Idempotent; best-effort — if flock is unavailable it degrades to running
    uncapped rather than failing the command."""
    global _SINGLEFLIGHT_FD
    if _SINGLEFLIGHT_FD is not None:
        return True
    try:
        import fcntl

        fd = open(_runtime_path(".lock"), "w")
        if timeout is None:
            fcntl.flock(fd, fcntl.LOCK_EX)  # blocks; released on process exit
        else:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() > deadline:
                        fd.close()
                        return False
                    time.sleep(0.2)
        _SINGLEFLIGHT_FD = fd
        return True
    except Exception:
        return True  # no lock available → run uncapped, don't break the command


def release_singleflight() -> None:
    """Release the lock early (on load failure, or after the daemon's
    one-time load). CLI fallback processes otherwise hold until exit."""
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


def _client_enabled() -> bool:
    return (
        os.name == "posix"
        and not _DAEMON_DISABLED
        and os.environ.get("REFLECT_NO_DAEMON") != "1"
    )


def _request(payload: dict, timeout: float) -> Optional[dict]:
    """One JSON line out, one JSON line back. None on any failure."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_CONNECT_TIMEOUT_S)
            sock.connect(str(socket_path()))
            sock.settimeout(timeout)  # op may wait behind a cold model load
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


def _connectable() -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_CONNECT_TIMEOUT_S)
            sock.connect(str(socket_path()))
        return True
    except Exception:
        return False


def _ensure_daemon() -> bool:
    """A connectable daemon, spawning one if the socket is dead.

    Dead means connect() fails — ENOENT (no socket) or ECONNREFUSED (stale
    file, no listener). A connected-but-slow daemon is BUSY, never dead;
    it must not be unlinked or replaced. The whole check-unlink-spawn
    sequence runs under a spawn flock so N parallel first-use clients
    produce one daemon: the losers block, then find the winner's socket."""
    global _DAEMON_DISABLED
    if not _client_enabled():
        return False
    if _connectable():
        return True
    try:
        import fcntl

        with open(_runtime_path(".spawnlock"), "w") as lock_fd:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if _connectable():
                return True  # another client won the spawn while we waited
            sp = socket_path()
            try:
                sp.unlink()  # stale socket from a dead daemon
            except OSError:
                pass
            subprocess.Popen(
                [
                    sys.executable,
                    # Import the canonical module: `-m` would create a second
                    # module object whose lock global shadows this one's.
                    "-c",
                    "from reflect_kb.model_daemon import serve; serve()",
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,  # detach: survives this CLI's exit
            )
            deadline = time.monotonic() + _SPAWN_WAIT_S
            while time.monotonic() < deadline:
                if _connectable():
                    return True
                time.sleep(0.15)
    except Exception:
        pass
    # Spawn failed (sandbox, unwritable TMPDIR, broken interpreter …) —
    # don't re-pay the spawn wait on every call this process makes.
    _DAEMON_DISABLED = True
    return False


def _daemon_call(payload: dict) -> Optional[dict]:
    if not _client_enabled():
        return None
    timeout = _env_float("REFLECT_DAEMON_TIMEOUT", 120.0)
    resp = _request(payload, timeout)
    if resp is None and _ensure_daemon():
        resp = _request(payload, timeout)
    return resp


def daemon_embed(texts: Sequence[str]) -> Optional[list]:
    """Unit-normalized vectors via the daemon, or None → caller loads in-proc.
    Texts are sent verbatim — truncation policy belongs to the caller."""
    resp = _daemon_call({"op": "embed", "texts": list(texts)})
    return resp["vectors"] if resp else None


def daemon_rerank(query: str, texts: Sequence[str]) -> Optional[list]:
    """Cross-encoder logits via the daemon, or None → caller loads in-proc."""
    resp = _daemon_call({"op": "rerank", "query": query, "texts": list(texts)})
    return resp["scores"] if resp else None


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------


class _Server:
    """Serial unix-socket server. Models load lazily on first use under a
    bounded single-flight wait, released right after — a daemon boot can't
    stack on top of an in-proc fallback load, and a long-lived fallback
    holder can only delay (never hang) the daemon."""

    # ponytail: 60s bounded wait, then load uncapped — a transient 2x RAM
    # peak beats every client on the box hanging behind one long job.
    _LOCK_WAIT_S = 60.0

    def __init__(self) -> None:
        self._embedder = None
        self._reranker = None

    def _load_embedder(self):
        if self._embedder is None:
            acquire_singleflight(timeout=self._LOCK_WAIT_S)
            try:
                from sentence_transformers import SentenceTransformer

                self._embedder = SentenceTransformer(EMBEDDING_MODEL_NAME)
            finally:
                release_singleflight()
        return self._embedder

    def _load_reranker(self):
        if self._reranker is None:
            acquire_singleflight(timeout=self._LOCK_WAIT_S)
            try:
                from reflect_kb.recall.cross_encoder import CrossEncoderReranker

                reranker = CrossEncoderReranker()
                reranker._load()  # nested acquire is an idempotent no-op
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
            texts = [str(t) for t in (req.get("texts") or [])]
            model = self._load_embedder()
            vectors = model.encode(texts, normalize_embeddings=True)
            return {"ok": True, "vectors": vectors.tolist()}
        if op == "rerank":
            texts = [str(t) for t in (req.get("texts") or [])]
            scores = self._load_reranker().score(str(req.get("query", "")), texts)
            return {"ok": True, "scores": scores}
        return {"ok": False, "error": f"unknown op: {op}"}


def serve() -> None:
    global _DAEMON_DISABLED
    # The daemon reuses in-proc code paths (CrossEncoderReranker.score) that
    # themselves try the daemon first — disable the client in this process
    # or it would deadlock requesting itself while serving serially.
    _DAEMON_DISABLED = True
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
    own_ino = os.stat(sp).st_ino  # for the exit-time ownership check
    server_sock.listen(16)

    idle_limit = _env_float("REFLECT_IDLE_TIMEOUT", 1800.0)
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
                    return  # idle out: free the RAM
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
        # Unlink only a socket file we still own — if the path was ever
        # re-bound by a replacement daemon, deleting it would orphan them.
        try:
            if os.stat(sp).st_ino == own_ino:
                sp.unlink()
        except OSError:
            pass


if __name__ == "__main__":
    from reflect_kb.model_daemon import serve as _serve  # canonical module

    _serve()
