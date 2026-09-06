"""Postgres test-database helpers shared by tests/postgres and tests/compat.

The integration tier truncates tables and, in the compat gate, drops and
recreates the whole reflect_memory schema. That must never happen to a real
database, so a DSN is used only when it is disposable by construction: it came
from REFLECT_TEST_DATABASE_URL, or it came from DATABASE_URL and points at
localhost. Anything else skips with a message that names the rule.
"""

from __future__ import annotations

import os
import urllib.parse

import pytest

WS_A = "11111111-1111-1111-1111-111111111111"
WS_B = "22222222-2222-2222-2222-222222222222"

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", ""}


class NotDisposableDSN(RuntimeError):
    """The DSN may name a real database; refusing destructive test setup."""


def dsn_host(dsn: str) -> str:
    """Host of a libpq DSN in URI or key=value form ('' for a Unix socket)."""
    if "://" in dsn:
        return (urllib.parse.urlsplit(dsn).hostname or "").lower()
    for part in dsn.split():
        key, _, value = part.partition("=")
        if key == "host":
            return value.strip("'\"").lower()
    return ""


def assert_disposable_dsn(dsn: str, *, source_var: str) -> None:
    """Raise unless the DSN is safe to wipe: test-named variable, or localhost."""
    if "TEST" in source_var.upper():
        return
    host = dsn_host(dsn)
    if host in _LOCAL_HOSTS or host.startswith("/"):
        return
    raise NotDisposableDSN(
        f"{source_var} points at {host!r}, which is not localhost; the integration "
        "tests truncate and drop reflect_memory, so they only run against "
        "REFLECT_TEST_DATABASE_URL or a localhost DATABASE_URL"
    )


def test_dsn() -> tuple[str, str] | None:
    """``(dsn, source_var)`` for a disposable database, else None."""
    for var in ("REFLECT_TEST_DATABASE_URL", "DATABASE_URL"):
        dsn = os.environ.get(var)
        if dsn:
            assert_disposable_dsn(dsn, source_var=var)
            return dsn, var
    return None


def connect_or_skip(*, autocommit: bool = True, row_factory=None):
    """A psycopg connection to the disposable test database, or a clean skip."""
    found = test_dsn()
    if found is None:
        pytest.skip("no REFLECT_TEST_DATABASE_URL or localhost DATABASE_URL")
    dsn, _ = found
    psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")
    kwargs = {"autocommit": autocommit}
    if row_factory is not None:
        kwargs["row_factory"] = row_factory
    try:
        return psycopg.connect(dsn, **kwargs)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable ({exc})")
