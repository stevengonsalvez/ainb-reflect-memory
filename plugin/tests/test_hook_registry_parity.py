"""Registry and manifest parity tests for Reflect lifecycle hooks."""

from __future__ import annotations

import json
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
PLUGIN_ROOT = HERE.parent
REPO_ROOT = PLUGIN_ROOT.parent

sys.path.insert(0, str(PLUGIN_ROOT / "hooks"))
import registry  # noqa: E402


def _claude_events(path: Path) -> set[str]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return set(cfg["hooks"])


def _codex_events(path: Path) -> set[str]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return set(cfg["hooks"])


def _copilot_events(path: Path) -> set[str]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return set(cfg["hooks"])


def test_source_manifests_match_registry_events():
    assert _claude_events(REPO_ROOT / ".claude-plugin" / "plugin.json") == registry.expected_events("claude")
    assert _claude_events(PLUGIN_ROOT / ".claude-plugin" / "plugin.json") == registry.expected_events("claude")
    assert _codex_events(PLUGIN_ROOT / "codex-hooks.json") == registry.expected_events("codex")
    assert _copilot_events(PLUGIN_ROOT / "copilot-hooks.json") == registry.expected_events("copilot")


def test_reflect_drain_is_the_only_registered_drain():
    drains = [spec for spec in registry.HOOKS if spec.drains]
    assert [spec.canonical for spec in drains] == ["SessionStart.drain"]
    assert drains[0].script == "hooks/reflect-drain-bg.sh"


def test_queue_producers_do_not_point_at_drain_script():
    for spec in registry.HOOKS:
        if spec.queues:
            assert spec.script != "hooks/reflect-drain-bg.sh", spec
