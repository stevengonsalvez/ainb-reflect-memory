"""Shared test config.

Unit tests mock the in-process model paths (fake sentence_transformers
modules, spy encoders) — a live model daemon would silently answer instead
and break every such assertion. Disable the daemon client by default; the
daemon's own tests (tests/test_model_daemon.py) explicitly re-enable it.
"""

import pytest


@pytest.fixture(autouse=True)
def _no_model_daemon(monkeypatch):
    monkeypatch.setenv("REFLECT_NO_DAEMON", "1")
