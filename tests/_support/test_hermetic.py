"""hermetic_env: the daemon is off by default (the compat gate), the eval
harness opts in explicitly, and an operator's own setting is honoured."""

from __future__ import annotations

from pathlib import Path

from _support.hermetic import hermetic_env


def _env(**kw):
    root = Path("/tmp/x")
    return hermetic_env(kb_dir=root / "kb", state_dir=root / "state", cache_home=root / "cache", **kw)


def test_default_turns_the_daemon_off() -> None:
    assert _env(base={})["REFLECT_NO_DAEMON"] == "1"


def test_the_eval_harness_opts_in_and_the_operator_setting_wins() -> None:
    assert _env(base={}, daemon=True)["REFLECT_NO_DAEMON"] == "0"
    assert _env(base={"REFLECT_NO_DAEMON": "1"}, daemon=True)["REFLECT_NO_DAEMON"] == "1"
    assert _env(base={"REFLECT_NO_DAEMON": "0"})["REFLECT_NO_DAEMON"] == "0"
    assert _env(base={}, daemon=False)["REFLECT_NO_DAEMON"] == "1"
