# ABOUTME: Proves the LOCOMO harness's arms_off config really disables every recall arm.
# ABOUTME: Toy KB + fake `reflect` CLI, no model, no API, no LLM spend.
"""Regression for the LOCOMO ablation defect.

recall.py reads every arm knob as ``os.environ.get(NAME, "1") != "0"`` (ON unless
exported "0"). The harness used to build ``arms_off`` by *deleting* the
variables, so arms_on and arms_off ran identical retrieval. These tests drive the
real recall.py with the env the harness actually builds for each config, over a
toy KB and a fake engine CLI, and assert the two configs retrieve differently and
that each knob changes behaviour on its own.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
BENCH = REPO / "tests" / "eval" / "locomo" / "locomo_bench.py"
RECALL = REPO / "plugin" / "skills" / "recall" / "scripts" / "recall.py"
HOOK = REPO / "plugin" / "skills" / "recall" / "hooks" / "session_start_recall.py"

_spec = importlib.util.spec_from_file_location("locomo_bench", BENCH)
bench = importlib.util.module_from_spec(_spec)
sys.modules["locomo_bench"] = bench
_spec.loader.exec_module(bench)

QUERY = "redis pool exhaustion in March 2024"

# Fake engine CLI: vector (naive) hits n1..n3, graph (local) hit g1, a cross-
# encoder that prefers g1/t1, embeddings where n1/n2 are near-duplicates.
FAKE_REFLECT = """#!/usr/bin/env python3
import json, os, sys
cmd = sys.argv[1] if len(sys.argv) > 1 else "?"
with open(os.environ["FAKE_CALLS_LOG"], "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")

def chunk(name, body):
    return "---\\nname: %s\\nconfidence: high\\n---\\n%s" % (name, body)

if cmd == "search":
    mode = sys.argv[sys.argv.index("--mode") + 1]
    if mode == "local":
        chunks = [chunk("g1", "redis pool exhaustion owned by the payments service")]
    else:
        chunks = [chunk("n%d" % i, "redis pool exhaustion fix %d" % i) for i in (1, 2, 3)]
    print(json.dumps({"context": "--New Chunk--".join(chunks)}))
elif cmd == "rerank":
    ids = [c["id"] for c in json.load(sys.stdin)["candidates"]]
    pref = {"g1": 9.0, "t1": 8.0, "n3": 5.0, "n1": 2.0, "n2": 1.0}
    print(json.dumps({"available": True, "model": "fake",
                      "scores": {i: pref.get(i, 0.5) for i in ids}}))
elif cmd == "embed":
    ids = [c["id"] for c in json.load(sys.stdin)["candidates"]]
    vec = {"n1": [1.0, 0.0, 0.0], "n2": [1.0, 0.0, 0.0], "n3": [0.0, 1.0, 0.0],
           "g1": [0.0, 0.0, 1.0], "t1": [0.5, 0.5, 0.0]}
    print(json.dumps({"available": True, "model": "fake", "query_embedding": [1.0, 0.0, 0.0],
                      "embeddings": {i: vec.get(i, [0.1, 0.1, 0.1]) for i in ids}}))
"""
FAKE_QMD = "#!/bin/sh\nexit 1\n"  # no lexical arm: keeps the pool deterministic


@pytest.fixture()
def sandbox(tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in (("reflect", FAKE_REFLECT), ("qmd", FAKE_QMD)):
        p = bindir / name
        p.write_text(body)
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    kb = tmp_path / "kb"
    (kb / "documents").mkdir(parents=True)
    # Dated inside the query's window, absent from every vector/graph result:
    # only the temporal arm can surface it.
    (kb / "documents" / "t1.md").write_text(
        "---\nname: t1\nconfidence: high\ncreated: 2024-03-10\n---\n"
        "redis pool exhaustion postmortem written in March\n"
    )
    (kb / "documents" / "old.md").write_text(
        "---\nname: old\nconfidence: high\ncreated: 2023-01-02\n---\nredis pool notes\n"
    )
    calls = tmp_path / "calls.log"
    state = tmp_path / "state"
    state.mkdir()
    # Fake CLIs first on PATH; the harness must not pick up a real venv's reflect.
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("FAKE_CALLS_LOG", str(calls))
    monkeypatch.setenv("REFLECT_METRICS_PATH", str(tmp_path / "metrics.jsonl"))
    monkeypatch.setenv("REFLECT_NO_DAEMON", "1")
    monkeypatch.setattr(bench, "REFLECT_BIN_DIR", tmp_path / "no-venv" / "bin")
    for k in bench.ARM_KNOBS:
        monkeypatch.delenv(k, raising=False)
    return {"kb": kb, "state": state, "calls": calls, "bin": bindir}


def _recall(sb, env_over, query=QUERY, extra=(), cache=False):
    """Run the real recall.py with the harness's base env plus ``env_over``."""
    env = bench.base_env(sb["kb"], sb["state"])
    env.update(env_over)
    cmd = [sys.executable, str(RECALL), query, "--limit", "5", "--format", "json",
           "--confidence", "ANY", *extra]
    if not cache:
        cmd.append("--no-cache")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60, env=env)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def _calls(sb):
    p = sb["calls"]
    return p.read_text().splitlines() if p.exists() else []


def _reset(sb):
    sb["calls"].unlink(missing_ok=True)


def _ids(out):
    return [r["id"] for r in out["results"]]


# ---------------------------------------------------------------- harness env

def test_arms_off_exports_zero_for_every_knob_not_unset():
    on, off = bench.arm_env("arms_on"), bench.arm_env("arms_off")
    assert set(on) == set(off) == set(bench.ARM_KNOBS)
    assert all(v == "1" for v in on.values())
    assert all(v == "0" for v in off.values())  # "unset" would leave arms ON
    assert bench.arm_env("no_memory") == bench.arm_env("full_context") == {}


def test_every_knob_is_read_by_the_engine():
    assert bench.knobs_missing_from_source() == []


def test_print_config_lists_effective_env_per_config():
    r = subprocess.run([sys.executable, str(BENCH), "--print-config"],
                       capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    on_block = r.stdout.split("[arms_on]")[1].split("[arms_off]")[0]
    off_block = r.stdout.split("[arms_off]")[1].split("[no_memory]")[0]
    for k in bench.ARM_KNOBS:
        assert f"{k}=1" in on_block
        assert f"{k}=0" in off_block
    assert "9/9" in r.stdout


def test_inherited_arm_knobs_do_not_leak_into_harness_env(sandbox, monkeypatch):
    monkeypatch.setenv("RECALL_MMR", "1")
    env = bench.base_env(sandbox["kb"], sandbox["state"])
    assert not any(k in env for k in bench.ARM_KNOBS)


def test_verdict_cache_is_keyed_on_effective_arm_env(tmp_path, monkeypatch):
    """A verdict cached by the old harness (arms_off == arms_on) must not be reused."""
    monkeypatch.setattr(bench, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(bench, "_RUN_TAG", "t")
    old = tmp_path / "cache" / "qa" / "t" / "s" / "arms_off"
    old.mkdir(parents=True)
    (old / "0000.json").write_text(json.dumps(
        {"correct": True, "recall_s": 0, "answer_s": 0, "tokens": 0, "cost": 0}))
    seen = {}

    async def fake_claude(prompt, system, sem, model="sonnet", retries=2):
        seen["called"] = True
        return bench.LLMOut("x", 0, 0, 0, 0.0, False)

    async def fake_recall(*a, **k):
        return "ctx", 0.0

    monkeypatch.setattr(bench, "claude", fake_claude)
    monkeypatch.setattr(bench, "recall_context", fake_recall)
    qa = {"question": "q", "answer": "a", "category": 4}
    asyncio.run(bench.run_qa("s", "arms_off", 0, qa, tmp_path, tmp_path,
                             "", asyncio.Semaphore(1)))
    assert seen.get("called"), "stale pre-fix verdict was reused for arms_off"


# ---------------------------------------------------- end-to-end retrieval diff

def test_arms_off_retrieval_differs_from_arms_on(sandbox):
    on = _recall(sandbox, bench.arm_env("arms_on"))
    on_calls = _calls(sandbox)
    _reset(sandbox)
    off = _recall(sandbox, bench.arm_env("arms_off"))
    off_calls = _calls(sandbox)

    assert _ids(on) != _ids(off)
    # arms ON: graph arm (g1) and temporal arm (t1) contribute, CE + MMR models run.
    assert {"g1", "t1"} <= set(_ids(on))
    assert any("--mode local" in c for c in on_calls)
    assert any(c.startswith("rerank") for c in on_calls)
    assert any(c.startswith("embed") for c in on_calls)
    assert on["temporal"] is not None
    # arms OFF: vector-only candidates, no rerank / embed, no date parsing.
    assert set(_ids(off)) == {"n1", "n2", "n3"}
    assert all(c.startswith("search") and "--mode local" not in c for c in off_calls)
    assert off["temporal"] is None


def test_unset_knobs_are_the_old_bug_arms_stay_on(sandbox):
    """Documents the defect: no knobs exported behaves exactly like arms ON."""
    unset = _recall(sandbox, {})
    on = _recall(sandbox, bench.arm_env("arms_on"))
    assert _ids(unset) == _ids(on)


def test_recall_context_uses_the_config_env(sandbox):
    """The harness's own recall_context path (markdown, as sent to the answerer)."""
    async def run(arms_on):
        bench._RECALL_SEM = asyncio.Semaphore(1)
        ctx, _ = await bench.recall_context(QUERY, sandbox["kb"], sandbox["state"], arms_on)
        return ctx

    on, off = asyncio.run(run(True)), asyncio.run(run(False))
    assert on and off and on != off
    assert "[g1]" in on and "[g1]" not in off  # graph-arm note only with arms on


def test_failing_recall_subprocess_is_reported_not_swallowed(sandbox, monkeypatch):
    """A recall that exits non-zero must raise with its stderr, not record an empty arm."""
    monkeypatch.setattr(bench, "RECALL_PY", sandbox["bin"] / "boom.py")
    (sandbox["bin"] / "boom.py").write_text(
        "import sys\nprint('engine exploded: no module named yaml', file=sys.stderr)\nsys.exit(3)\n")

    async def run():
        bench._RECALL_SEM = asyncio.Semaphore(1)
        return await bench.recall_context(QUERY, sandbox["kb"], sandbox["state"], False)

    with pytest.raises(RuntimeError, match=r"(?s)exited 3.*engine exploded"):
        asyncio.run(run())


def test_empty_recall_context_is_warned_loudly(sandbox, monkeypatch, capsys):
    monkeypatch.setattr(bench, "RECALL_PY", sandbox["bin"] / "quiet.py")
    (sandbox["bin"] / "quiet.py").write_text("pass\n")

    async def run():
        bench._RECALL_SEM = asyncio.Semaphore(1)
        return await bench.recall_context(QUERY, sandbox["kb"], sandbox["state"], True)

    ctx, _ = asyncio.run(run())
    assert ctx == ""
    assert "empty context" in capsys.readouterr().err


# ------------------------------------------ each knob changes behaviour alone

ALL_ON = bench.arm_env("arms_on")


def _only_off(knob):
    return {**ALL_ON, knob: "0"}


def test_graph_arm_knob(sandbox):
    out = _recall(sandbox, _only_off("RECALL_GRAPH_ARM"))
    assert not any("--mode local" in c for c in _calls(sandbox))
    assert "g1" not in _ids(out)


def test_cross_encoder_knob(sandbox):
    base = _recall(sandbox, ALL_ON)
    _reset(sandbox)
    out = _recall(sandbox, _only_off("RECALL_CROSS_ENCODER"))
    assert not any(c.startswith("rerank") for c in _calls(sandbox))
    assert _ids(out) != _ids(base)  # CE no longer reorders


def test_mmr_knob(sandbox):
    _recall(sandbox, _only_off("RECALL_MMR"))
    assert not any(c.startswith("embed") for c in _calls(sandbox))


def test_temporal_parse_knob(sandbox):
    out = _recall(sandbox, _only_off("RECALL_TEMPORAL"))
    assert out["temporal"] is None
    assert "t1" not in _ids(out)  # no window parsed, so no temporal arm either


def test_temporal_arm_knob(sandbox):
    out = _recall(sandbox, _only_off("RECALL_TEMPORAL_ARM"))
    assert out["temporal"] is not None  # parsing stays on
    assert "t1" not in _ids(out)  # but the date-window scan does not run


def test_bitemporal_edges_knob():
    code = (
        "import sys; sys.path.insert(0, %r)\n"
        "from datetime import datetime\n"
        "import recall\n"
        "t = recall.extract_temporal_constraint('what held in April 2026')\n"
        "dead = {'tvalid': '2026-01-01', 'tvalid_end': '2026-03-01'}\n"
        "print(len(recall.filter_edges_by_tvalid([dead], t)))\n"
    ) % str(RECALL.parent)

    def kept(val):
        env = {**os.environ, "RECALL_BITEMPORAL_EDGES": val}
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr
        return int(r.stdout.strip())

    assert kept("1") == 0  # edge superseded before the window: filtered
    assert kept("0") == 1  # knob off: filter bypassed, edge kept


def test_fuzzy_cache_knob(sandbox):
    q1, q2 = "redis pool exhaustion fix", "the redis pool exhaustion fix"  # same tokens, new hash

    def second_query_served_from_cache(val):
        _reset(sandbox)
        env = {**ALL_ON, "RECALL_FUZZY_CACHE": val, "RECALL_GRAPH_ARM": "0",
               "RECALL_CROSS_ENCODER": "0", "RECALL_MMR": "0"}
        for f in (sandbox["state"] / "recall_cache").glob("*"):
            f.unlink()
        _recall(sandbox, env, query=q1, cache=True)
        n = len(_calls(sandbox))
        _recall(sandbox, env, query=q2, cache=True)
        return len(_calls(sandbox)) == n

    assert second_query_served_from_cache("1") is True
    assert second_query_served_from_cache("0") is False


def test_followup_knob(sandbox):
    path = sandbox["state"] / "recent-searches.json"
    _recall(sandbox, _only_off("RECALL_FOLLOWUP"), extra=("--session-id", "s1"))
    assert not path.exists()
    _recall(sandbox, ALL_ON, extra=("--session-id", "s1"))
    assert path.exists()


def test_tiered_inject_knob_reads_env():
    spec = importlib.util.spec_from_file_location("session_start_recall", HOOK)
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    saved = os.environ.get("REFLECT_TIERED_INJECT")
    try:
        os.environ["REFLECT_TIERED_INJECT"] = "1"
        assert hook.tiered_inject_enabled() is True
        os.environ["REFLECT_TIERED_INJECT"] = "0"
        assert hook.tiered_inject_enabled() is False
    finally:
        if saved is None:
            os.environ.pop("REFLECT_TIERED_INJECT", None)
        else:
            os.environ["REFLECT_TIERED_INJECT"] = saved
