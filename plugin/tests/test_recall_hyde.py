"""HyDE's claude -p call carries the structural no-tools flags."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills" / "recall" / "scripts"))
import recall


def test_hyde_argv_has_no_tools_one_turn_explicit_mode(monkeypatch) -> None:
    seen: list[list[str]] = []
    envs: list[dict] = []

    def fake_run(argv, **kwargs):
        seen.append(list(argv))
        envs.append(kwargs.get("env") or {})
        return subprocess.CompletedProcess(argv, 0, stdout="a hypothetical sentence", stderr="")

    monkeypatch.setattr(recall.subprocess, "run", fake_run)
    monkeypatch.setenv("REFLECT_RECALL_HYDE", "1")
    out = recall._hyde_expand("why did the deploy fail")
    assert out.startswith("why did the deploy fail\n")
    argv = seen[0]
    assert argv[:2] == ["claude", "-p"]
    for flag, value in (("--tools", ""), ("--max-turns", "1"), ("--permission-mode", "default")):
        assert argv[argv.index(flag) + 1] == value, argv
    assert "--strict-mcp-config" in argv and "--system-prompt" in argv
    assert "--setting-sources" not in argv  # apiKeyHelper and the env block must load
    # The child keeps the operator's hooks; every reflect hook exits at once
    # under the nested marker, so recall -> HyDE -> recall cannot recurse.
    assert envs[0].get("REFLECT_NESTED") == "1"
    assert argv == recall.hyde_argv(argv[2], argv[argv.index("--model") + 1], argv[argv.index("--system-prompt") + 1])
