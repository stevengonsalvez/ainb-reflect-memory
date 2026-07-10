"""Gate tests for the persistent model daemon (Phase 2 of the RAM fix).

Hard gates from the goal spec:
1. Warm-path latency: daemon embed round-trip <500ms once warm.
2. RAM cap: 4 parallel model calls ≈ one daemon's RSS, not 4× cold boots.
3. Fallback parity: socket path and in-proc path return identical vectors
   and scores; killing the daemon never breaks a call.

The model-backed tests need the [graph] extra (sentence-transformers) and
are skipped on the slim build. Lightweight protocol tests always run.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from reflect_kb import model_daemon

HAS_ST = importlib.util.find_spec("sentence_transformers") is not None
needs_models = pytest.mark.skipif(
    not HAS_ST, reason="sentence-transformers not installed (slim build)"
)


@pytest.fixture()
def isolated_daemon_env(tmp_path, monkeypatch):
    """Point sockets/locks at a private TMPDIR so tests never touch a real
    daemon; shut down any daemon spawned in it afterwards."""
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.delenv("REFLECT_NO_DAEMON", raising=False)
    monkeypatch.setenv("REFLECT_IDLE_TIMEOUT", "120")
    yield tmp_path
    model_daemon._request({"op": "shutdown"}, timeout=5.0)


def _rss_kb(pid: int) -> int:
    out = subprocess.run(
        ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True
    )
    try:
        return int(out.stdout.strip())
    except ValueError:
        return 0


def _daemon_pid(tmp_path: Path) -> int | None:
    """Pid of the daemon bound to this test's socket (via lsof)."""
    sp = model_daemon.socket_path()
    out = subprocess.run(
        ["lsof", "-t", "-U", "-a", str(sp)], capture_output=True, text=True
    )
    pids = [int(p) for p in out.stdout.split() if p.strip().isdigit()]
    return pids[0] if pids else None


# ---------------------------------------------------------------------------
# Protocol-level tests (no models, always run)
# ---------------------------------------------------------------------------


def test_no_daemon_env_disables_client(monkeypatch):
    monkeypatch.setenv("REFLECT_NO_DAEMON", "1")
    assert model_daemon.daemon_embed(["x"]) is None
    assert model_daemon.daemon_rerank("q", ["x"]) is None


def test_client_returns_none_when_socket_dead(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    # Stale socket file with no daemon behind it, and spawning disabled by
    # pointing python at a broken interpreter path is overkill — instead
    # assert the low-level request cleanly fails.
    sp = model_daemon.socket_path()
    sp.write_text("")  # not a socket at all
    assert model_daemon._request({"op": "ping"}, timeout=1.0) is None


def test_singleflight_serializes(tmp_path):
    """Two processes taking the lock run one-after-another, not overlapped."""
    helper = (
        "import os, sys, time\n"
        f"os.environ['TMPDIR'] = {str(tmp_path)!r}\n"
        "from reflect_kb.model_daemon import acquire_singleflight\n"
        "acquire_singleflight()\n"
        "print(f'ACQ {time.time():.3f}', flush=True)\n"
        "time.sleep(float(sys.argv[1]))\n"
    )
    p1 = subprocess.Popen(
        [sys.executable, "-c", helper, "1.2"], stdout=subprocess.PIPE, text=True
    )
    time.sleep(0.4)
    t0 = time.time()
    p2 = subprocess.Popen(
        [sys.executable, "-c", helper, "0.0"], stdout=subprocess.PIPE, text=True
    )
    p1.wait(timeout=30)
    out2 = p2.communicate(timeout=30)[0]
    acq2 = float(out2.split()[1])
    assert acq2 - t0 > 0.6, "second process acquired the lock while first held it"


# ---------------------------------------------------------------------------
# Model-backed gate tests ([graph] extra required)
# ---------------------------------------------------------------------------


@needs_models
def test_gate_warm_latency_and_parity(isolated_daemon_env):
    texts = ["the cat sat on the mat", "torch cold boots are expensive"]

    # First call may spawn the daemon and cold-load the model.
    warm = model_daemon.daemon_embed(texts)
    assert warm is not None, "daemon did not come up"
    assert len(warm) == 2 and len(warm[0]) > 0

    # GATE 1: warm round-trip <500ms.
    start = time.monotonic()
    again = model_daemon.daemon_embed(texts)
    elapsed = time.monotonic() - start
    assert again is not None
    assert elapsed < 0.5, f"warm daemon embed took {elapsed:.3f}s (gate: <0.5s)"

    # GATE 3a (parity): daemon vectors == in-proc vectors.
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_daemon._model_names()[0])
    local = model.encode(
        [t[:2000] for t in texts], normalize_embeddings=True
    )
    for dv, lv in zip(again, local):
        assert len(dv) == len(lv)
        for a, b in zip(dv, lv):
            assert abs(a - b) < 1e-5

    # GATE 3b: kill the daemon → client cleanly reports None (callers fall
    # back in-proc); a fresh call auto-respawns.
    assert model_daemon._request({"op": "shutdown"}, timeout=5.0)
    time.sleep(0.3)
    assert model_daemon._request({"op": "ping"}, timeout=1.0) is None
    respawned = model_daemon.daemon_embed(["respawn probe"])
    assert respawned is not None, "auto-respawn after daemon death failed"


@needs_models
def test_gate_rerank_parity(isolated_daemon_env):
    query = "how to cap RAM usage"
    texts = ["use a persistent daemon", "cats are fluffy", "flock the loaders"]

    scores = model_daemon.daemon_rerank(query, texts)
    assert scores is not None and len(scores) == 3

    # In-proc scores with the daemon disabled must match.
    os.environ["REFLECT_NO_DAEMON"] = "1"
    try:
        from reflect_kb.recall.cross_encoder import CrossEncoderReranker

        local = CrossEncoderReranker().score(query, texts)
    finally:
        os.environ.pop("REFLECT_NO_DAEMON", None)
    for a, b in zip(scores, local):
        assert abs(a - b) < 1e-4


@needs_models
def test_gate_ram_cap_parallel(isolated_daemon_env):
    """GATE 2: 4 parallel embed clients share ONE model daemon — total RSS of
    (4 clients + daemon) stays far under 4 cold boots (~14 GB)."""
    model_daemon.daemon_embed(["warmup"])  # daemon up + model loaded

    client_src = (
        "from reflect_kb.model_daemon import daemon_embed\n"
        "import os, sys\n"
        "v = daemon_embed(['parallel client %s payload' % sys.argv[1]])\n"
        "assert v is not None and len(v[0]) > 0\n"
        "print(os.getpid(), flush=True)\n"
        "import time; time.sleep(3)\n"  # stay alive for the RSS sample
    )
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", client_src, str(i)],
            stdout=subprocess.PIPE,
            text=True,
        )
        for i in range(4)
    ]
    pids = [int(p.stdout.readline().strip()) for p in procs]

    daemon_pid = _daemon_pid(isolated_daemon_env)
    sampled = pids + ([daemon_pid] if daemon_pid else [])
    total_kb = sum(_rss_kb(pid) for pid in sampled)
    for p in procs:
        p.wait(timeout=30)

    total_gb = total_kb / 1024 / 1024
    assert daemon_pid is not None, "daemon pid not found via lsof"
    assert total_gb < 4.5, (
        f"total RSS {total_gb:.2f} GB across 4 clients + daemon "
        f"(gate: <4.5 GB; 4 cold boots would be ~14 GB)"
    )
    # And the clients themselves must be lightweight (no torch loaded).
    for pid in pids:
        client_gb = _rss_kb(pid) / 1024 / 1024
        assert client_gb < 0.5, f"client {pid} RSS {client_gb:.2f} GB — loaded torch?"


@needs_models
def test_idle_timeout_exits(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("REFLECT_IDLE_TIMEOUT", "2")
    monkeypatch.delenv("REFLECT_NO_DAEMON", raising=False)
    assert model_daemon._ensure_daemon()
    sp = model_daemon.socket_path()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and sp.exists():
        time.sleep(0.5)
    assert not sp.exists(), "daemon did not idle out and unlink its socket"
    assert model_daemon._request({"op": "ping"}, timeout=1.0) is None
