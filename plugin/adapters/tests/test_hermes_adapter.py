"""Tests for the Hermes adapter (plugins/reflect/adapters/hermes).

Two groups:

  * Structural install tests mirroring test_codex_adapter — dry-run touches
    nothing, install deploys pointers + recall scripts + shim scripts + the
    reflect.toml, and (unlike Codex) writes NO hooks.json.
  * Shim behavior tests via subprocess (test_hooks_silent_fail pattern): the
    pre_llm_recall shim's bank/shadow/reflect modes, its silent-fail contract,
    and the post_llm_capture enqueue + correction-priority heuristic.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
ADAPTER_DIR = HERE.parent / "hermes"
ADAPTER = ADAPTER_DIR / "hermes_adapter.py"
SHIM_DIR = ADAPTER_DIR / "shim"
PRE_LLM = SHIM_DIR / "pre_llm_recall.py"
POST_LLM = SHIM_DIR / "post_llm_capture.py"

sys.path.insert(0, str(ADAPTER_DIR))

import hermes_adapter  # noqa: E402


@pytest.fixture(autouse=True)
def _sanity():
    assert ADAPTER.exists(), f"missing adapter script at {ADAPTER}"
    assert PRE_LLM.exists(), f"missing shim at {PRE_LLM}"
    assert POST_LLM.exists(), f"missing shim at {POST_LLM}"


# --- structural install tests ------------------------------------------------


def test_find_plugin_root_resolves_to_reflect_dir():
    root = hermes_adapter.find_plugin_root()
    assert (root / "skills").is_dir()
    assert (root / "adapters").is_dir()


def test_dry_run_reports_actions_without_touching_home(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ADAPTER), "install", "--dry-run", "--home", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "dry-run" in result.stdout
    assert "pointer:" in result.stdout
    assert "recall" in result.stdout
    # Shim + reflect.toml appear in the plan.
    assert "shim" in result.stdout
    assert "reflect.toml" in result.stdout
    # Nothing was written.
    assert not (tmp_path / ".hermes").exists()


def test_install_writes_pointer_files_under_dot_hermes(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ADAPTER), "install", "--home", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    skills_root = tmp_path / ".hermes" / "skills"
    assert skills_root.is_dir()

    recall = skills_root / "recall" / "SKILL.md"
    reflect = skills_root / "reflect" / "SKILL.md"
    assert recall.exists()
    assert reflect.exists()

    body = recall.read_text(encoding="utf-8")
    assert hermes_adapter.POINTER_MANAGED_BY in body

    # Hermes owns no hook wiring — no hooks.json, no settings.json.
    assert not (tmp_path / ".hermes" / "hooks.json").exists()
    assert not (tmp_path / ".hermes" / "settings.json").exists()
    # And nothing leaked into a Claude/Codex dir.
    assert not (tmp_path / ".claude").exists()
    assert not (tmp_path / ".codex").exists()


def test_install_deploys_recall_script_on_disk(tmp_path):
    """The shim resolves recall.py by relative path, so the physical file
    must land under ~/.hermes/skills/recall/scripts/."""
    subprocess.run(
        [sys.executable, str(ADAPTER), "install", "--home", str(tmp_path)],
        check=True, capture_output=True,
    )
    recall_py = (
        tmp_path / ".hermes" / "skills" / "recall" / "scripts" / "recall.py"
    )
    assert recall_py.exists()
    assert recall_py.read_text().startswith("#!/usr/bin/env -S uv run")


def test_install_deploys_shim_scripts_under_reflect(tmp_path):
    subprocess.run(
        [sys.executable, str(ADAPTER), "install", "--home", str(tmp_path)],
        check=True, capture_output=True,
    )
    shim_root = tmp_path / ".hermes" / "skills" / "reflect" / "shim"
    assert (shim_root / "pre_llm_recall.py").exists()
    assert (shim_root / "post_llm_capture.py").exists()


def test_install_copies_reflect_toml(tmp_path):
    subprocess.run(
        [sys.executable, str(ADAPTER), "install", "--home", str(tmp_path)],
        check=True, capture_output=True,
    )
    toml = tmp_path / ".hermes" / "skills" / "reflect" / "reflect.toml"
    assert toml.exists()
    assert "[providers.hermes]" in toml.read_text(encoding="utf-8")


def test_install_is_idempotent(tmp_path):
    for _ in range(2):
        subprocess.run(
            [sys.executable, str(ADAPTER), "install", "--home", str(tmp_path)],
            check=True, capture_output=True,
        )
    pointers = list((tmp_path / ".hermes" / "skills").rglob("SKILL.md"))
    assert len(pointers) >= 2  # recall + reflect at minimum


def test_uninstall_removes_pointers_and_shim(tmp_path):
    subprocess.run(
        [sys.executable, str(ADAPTER), "install", "--home", str(tmp_path)],
        check=True, capture_output=True,
    )
    user_file = tmp_path / ".hermes" / "skills" / "recall" / "user-note.md"
    user_file.write_text("hand-written", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ADAPTER), "uninstall", "--home", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr

    # Managed pointer gone, user sibling untouched.
    assert not (tmp_path / ".hermes" / "skills" / "recall" / "SKILL.md").exists()
    assert user_file.exists()
    # Shim dir + reflect.toml (adapter-owned) removed.
    assert not (tmp_path / ".hermes" / "skills" / "reflect" / "shim").exists()
    assert not (tmp_path / ".hermes" / "skills" / "reflect" / "reflect.toml").exists()


def test_install_refuses_to_overwrite_non_pointer_skill_marker(tmp_path):
    hermes_dir = tmp_path / ".hermes"
    (hermes_dir / "skills" / "recall").mkdir(parents=True)
    handwritten = "---\nname: user-handwritten\n---\nbody\n"
    target = hermes_dir / "skills" / "recall" / "SKILL.md"
    target.write_text(handwritten, encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ADAPTER), "install", "--home", str(tmp_path)],
        capture_output=True, text=True,
    )
    assert result.returncode != 0, result.stdout
    assert "refused to overwrite non-pointer file" in result.stdout
    assert target.read_text(encoding="utf-8") == handwritten


def test_install_force_replaces_non_pointer_skill_marker(tmp_path):
    hermes_dir = tmp_path / ".hermes"
    (hermes_dir / "skills" / "recall").mkdir(parents=True)
    target = hermes_dir / "skills" / "recall" / "SKILL.md"
    target.write_text("---\nname: user-handwritten\n---\nbody\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(ADAPTER), "install",
         "--home", str(tmp_path), "--force"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "replaced non-pointer file" in result.stdout
    assert hermes_adapter.POINTER_MANAGED_BY in target.read_text(encoding="utf-8")


# --- shim behavior tests -----------------------------------------------------

# A stub that impersonates recall.py: ignores every arg and prints a canned
# fleet-context block with two rendered items (two ``source:`` lines).
_STUB_RECALL_BLOCK = (
    "## Reflect Recall (fleet memory, advisory) <!-- fleet-context/v1 -->\n"
    "_Query: x_\n"
    "\n"
    "### Advisory memory\n"
    "- **Alpha** — insight a\n"
    "  source: /p/a.md · score: 0.900\n"
    "- **Beta** — insight b\n"
    "  source: /p/b.md · score: 0.800\n"
)


def _write_stub_recall(tmp_path: Path) -> Path:
    stub = tmp_path / "stub_recall.py"
    stub.write_text(
        "import sys\n"
        f"sys.stdout.write({_STUB_RECALL_BLOCK!r})\n",
        encoding="utf-8",
    )
    return stub


def _run_shim(shim: Path, stdin: str, env_extra: dict, state_dir: Path):
    return subprocess.run(
        [sys.executable, str(shim)],
        input=stdin,
        capture_output=True,
        text=True,
        env={**os.environ, "REFLECT_STATE_DIR": str(state_dir), **env_extra},
        timeout=30,
    )


def _read_metric_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def test_pre_llm_shadow_emits_nothing_and_writes_metric(tmp_path):
    stub = _write_stub_recall(tmp_path)
    metrics = tmp_path / "metrics.jsonl"
    result = _run_shim(
        PRE_LLM,
        json.dumps({"prompt": "how do I auth", "agent_id": "worker-1",
                    "domain_hint": "coding"}),
        {
            "FLEET_MEMORY_BACKEND": "shadow",
            "REFLECT_RECALL_SCRIPT": str(stub),
            "REFLECT_RECALL_RUNNER": sys.executable,
            "REFLECT_METRICS_PATH": str(metrics),
        },
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    # Shadow mode injects NOTHING.
    assert result.stdout == ""

    lines = _read_metric_lines(metrics)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["op"] == "fleet_shadow_recall"
    assert rec["harness"] == "hermes"
    assert rec["hits"] == 2
    assert rec["mode"] == "shadow"
    assert rec["agent"] == "worker-1"
    assert isinstance(rec["latency_ms"], (int, float))
    assert rec["tokens_est"] > 0


def test_pre_llm_bank_mode_exits_instantly_no_metric(tmp_path):
    stub = _write_stub_recall(tmp_path)
    metrics = tmp_path / "metrics.jsonl"
    result = _run_shim(
        PRE_LLM,
        json.dumps({"prompt": "how do I auth"}),
        {
            "FLEET_MEMORY_BACKEND": "bank",
            "REFLECT_RECALL_SCRIPT": str(stub),
            "REFLECT_RECALL_RUNNER": sys.executable,
            "REFLECT_METRICS_PATH": str(metrics),
        },
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    # bank mode never touches recall or telemetry.
    assert not metrics.exists()


def test_pre_llm_reflect_mode_prints_block(tmp_path):
    stub = _write_stub_recall(tmp_path)
    metrics = tmp_path / "metrics.jsonl"
    result = _run_shim(
        PRE_LLM,
        json.dumps({"prompt": "how do I auth"}),
        {
            "FLEET_MEMORY_BACKEND": "reflect",
            "REFLECT_RECALL_SCRIPT": str(stub),
            "REFLECT_RECALL_RUNNER": sys.executable,
            "REFLECT_METRICS_PATH": str(metrics),
        },
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "fleet-context/v1" in result.stdout
    # reflect mode still records telemetry.
    assert len(_read_metric_lines(metrics)) == 1


def test_pre_llm_survives_corrupt_stdin(tmp_path):
    result = _run_shim(
        PRE_LLM,
        "this is not json at all {{{",
        {"FLEET_MEMORY_BACKEND": "shadow"},
        tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_pre_llm_missing_recall_script_is_silent_with_breadcrumb(tmp_path):
    metrics = tmp_path / "metrics.jsonl"
    result = _run_shim(
        PRE_LLM,
        json.dumps({"prompt": "how do I auth"}),
        {
            "FLEET_MEMORY_BACKEND": "shadow",
            "REFLECT_RECALL_SCRIPT": str(tmp_path / "does_not_exist.py"),
            "REFLECT_METRICS_PATH": str(metrics),
        },
        tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    # No metric on a failed recall, but a breadcrumb for the status line.
    assert not metrics.exists()
    breadcrumb = tmp_path / "last-event.json"
    assert breadcrumb.exists()
    event = json.loads(breadcrumb.read_text())
    assert event["event"] == "error"
    assert event["hook"] == "pre_llm_recall"


def test_pre_llm_empty_prompt_is_noop(tmp_path):
    metrics = tmp_path / "metrics.jsonl"
    result = _run_shim(
        PRE_LLM,
        json.dumps({"prompt": "   "}),
        {"FLEET_MEMORY_BACKEND": "shadow", "REFLECT_METRICS_PATH": str(metrics)},
        tmp_path,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert not metrics.exists()


# --- post_llm_capture --------------------------------------------------------


def _queue_entries(state_dir: Path) -> list[dict]:
    qf = state_dir / "pending_reflections.jsonl"
    if not qf.exists():
        return []
    return [json.loads(l) for l in qf.read_text().splitlines() if l.strip()]


def test_post_llm_capture_enqueues_entry(tmp_path):
    result = _run_shim(
        POST_LLM,
        json.dumps({
            "last_user_msg": "add a retry to the client",
            "last_assistant_msg": "done, added exponential backoff",
            "session_id": "sess-1",
            "agent_id": "worker-2",
        }),
        {},
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""

    entries = _queue_entries(tmp_path)
    assert len(entries) == 1
    e = entries[0]
    # stop_reflect.py's five-field shape — transcript_path is load-bearing.
    assert e["source"] == "hermes"
    assert e["trigger"] == "stop"
    assert e["session_id"] == "sess-1"
    assert e["agent_id"] == "worker-2"
    assert "ts" in e
    assert "cwd" in e
    # transcript_path present AND the file exists AND holds the messages.
    tpath = Path(e["transcript_path"])
    assert tpath.exists(), f"transcript missing: {tpath}"
    body = tpath.read_text()
    assert "add a retry to the client" in body
    assert "done, added exponential backoff" in body
    # Each transcript line is a {"message": {"role", "content"}} record the
    # drain's extract_dialogue understands.
    recs = [json.loads(l) for l in body.splitlines() if l.strip()]
    assert {r["message"]["role"] for r in recs} == {"user", "assistant"}
    # Non-correction message → no priority flag.
    assert "priority" not in e


def test_post_llm_capture_dedupes_and_appends_transcript(tmp_path):
    """A second capture on the same pending session must NOT enqueue a
    duplicate; it appends the new turn to the same transcript file."""
    payload_1 = json.dumps({
        "last_user_msg": "first message",
        "last_assistant_msg": "first reply",
        "session_id": "sess-dup",
    })
    payload_2 = json.dumps({
        "last_user_msg": "second message",
        "last_assistant_msg": "second reply",
        "session_id": "sess-dup",
    })
    r1 = _run_shim(POST_LLM, payload_1, {}, tmp_path)
    r2 = _run_shim(POST_LLM, payload_2, {}, tmp_path)
    assert r1.returncode == 0 and r2.returncode == 0

    entries = _queue_entries(tmp_path)
    # Exactly one queue entry despite two captures.
    assert len(entries) == 1
    tpath = Path(entries[0]["transcript_path"])
    body = tpath.read_text()
    # Both turns landed in the single session transcript.
    assert "first message" in body
    assert "second message" in body


def test_post_llm_capture_flags_correction_high_priority(tmp_path):
    result = _run_shim(
        POST_LLM,
        json.dumps({
            "last_user_msg": "no, that's wrong — it should be a POST",
            "last_assistant_msg": "sorry, switching to POST",
            "session_id": "sess-2",
        }),
        {},
        tmp_path,
    )
    assert result.returncode == 0, result.stderr
    entries = _queue_entries(tmp_path)
    assert len(entries) == 1
    assert entries[0]["priority"] == "high"


def test_post_llm_capture_survives_corrupt_stdin(tmp_path):
    result = _run_shim(POST_LLM, "not json {{{", {}, tmp_path)
    assert result.returncode == 0
    assert _queue_entries(tmp_path) == []


def test_post_llm_capture_empty_turn_is_noop(tmp_path):
    result = _run_shim(POST_LLM, json.dumps({"session_id": "s"}), {}, tmp_path)
    assert result.returncode == 0
    assert _queue_entries(tmp_path) == []


def test_post_llm_capture_transcript_failure_is_silent_no_dangling_entry(tmp_path):
    """If the transcript can't be written, the shim must exit 0 with a
    breadcrumb and enqueue NOTHING (never a pointer to a missing file)."""
    # Block transcript dir creation: plant a FILE where the dir must go.
    (tmp_path / "hermes-transcripts").write_text("not a dir", encoding="utf-8")

    result = _run_shim(
        POST_LLM,
        json.dumps({"last_user_msg": "hello", "session_id": "sess-x"}),
        {},
        tmp_path,
    )
    assert result.returncode == 0
    # No queue entry pointing at a transcript that was never written.
    assert _queue_entries(tmp_path) == []
    breadcrumb = tmp_path / "last-event.json"
    assert breadcrumb.exists()
    event = json.loads(breadcrumb.read_text())
    assert event["event"] == "error"
    assert event["hook"] == "post_llm_capture"
