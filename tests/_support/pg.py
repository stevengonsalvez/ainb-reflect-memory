"""Postgres test-database helpers shared by tests/postgres and tests/compat.

Destructive setup (migrations, truncates, schema drops) runs ONLY inside a
database the fixture created itself: ``reflect_test_<random>`` on a localhost
server named by REFLECT_TEST_DATABASE_URL or DATABASE_URL. The developer's own
databases on that server are never touched, and a server that is not on this
host is refused outright, whatever the variable is called. A DSN that fails
the rule makes the integration tier SKIP with a message naming the rule.

The DSN is parsed with psycopg's conninfo parser (URI or key=value form,
whitespace, ``hostaddr``, ``?host=``), and PGHOST / PGHOSTADDR from the
environment count when the DSN names no host, because libpq would use them.
"""

from __future__ import annotations

import os
import secrets
from contextlib import contextmanager

import pytest

WS_A = "11111111-1111-1111-1111-111111111111"
WS_B = "22222222-2222-2222-2222-222222222222"

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", ""}
DSN_VARS = ("REFLECT_TEST_DATABASE_URL", "DATABASE_URL")


class NotDisposableDSN(RuntimeError):
    """The DSN may reach a real server; refusing destructive test setup."""


def _is_local(host: str) -> bool:
    host = host.strip().strip("[]").lower()
    return host in _LOCAL_HOSTS or host.startswith("/")


def parse_conninfo(dsn: str) -> dict[str, str]:
    from psycopg.conninfo import conninfo_to_dict

    return {k: str(v) for k, v in conninfo_to_dict(dsn).items() if v is not None}


def assert_local_server(dsn: str, *, source_var: str, env: dict[str, str] | None = None) -> dict[str, str]:
    """Return the parsed conninfo if every host it can reach is this host."""
    env = os.environ if env is None else env
    try:
        info = parse_conninfo(dsn)
    except Exception as exc:
        raise NotDisposableDSN(f"{source_var} is not a parseable libpq DSN: {exc}") from exc
    hosts = [h for h in info.get("host", "").split(",") if h] or [env.get("PGHOST", "")]
    addrs = [a for a in info.get("hostaddr", "").split(",") if a] or [env.get("PGHOSTADDR", "")]
    for name, values in (("host", hosts), ("hostaddr", addrs)):
        for value in values:
            if not _is_local(value):
                raise NotDisposableDSN(
                    f"{source_var} reaches {name}={value!r}, not this host; the integration "
                    "tests create and drop a reflect_test_<random> database, so they only run "
                    "against a localhost server"
                )
    return info


def _in_ci(env: dict[str, str]) -> bool:
    return env.get("CI", "").lower() == "true" or bool(env.get("GITHUB_ACTIONS"))


def assert_resolved_local(dsn: str, *, source_var: str):
    """Connect with a read-only probe and check the host libpq actually
    resolved (``conn.info.host`` / ``hostaddr``), which sees service=,
    PGSERVICE, pg_service.conf and any libpq key the string pre-filter cannot.
    Returns the open autocommit connection; raises NotDisposableDSN before
    any DDL when the resolved server is not this host."""
    import psycopg

    from reflect_kb.postgres.dsn import is_local_connection

    conn = psycopg.connect(dsn, autocommit=True, application_name="reflect-tests-probe")
    # The same judgement the product makes on an open connection (dsn.py),
    # inverted: the tests refuse anything that is not this host.
    if not is_local_connection(conn.info):
        info = conn.info
        conn.close()
        raise NotDisposableDSN(
            f"{source_var} resolved to host={info.host!r} hostaddr={info.hostaddr!r}, not this host; "
            "the integration tests create and drop a reflect_test_<random> database, so they only "
            "run against a localhost server"
        )
    return conn


def server_dsn(env: dict[str, str] | None = None) -> tuple[str, str] | None:
    """``(dsn, source_var)`` of the first configured DSN that passes the
    disposable check (string pre-filter, then the resolved-host probe). None
    when nothing is configured. When something is configured but nothing
    passes: fail in CI (the service DSN is present and must work), skip
    locally with every reason."""
    env = os.environ if env is None else env
    reasons: list[str] = []
    for var in DSN_VARS:
        dsn = env.get(var)
        if not dsn:
            continue
        try:
            assert_local_server(dsn, source_var=var, env=env)
            assert_resolved_local(dsn, source_var=var).close()
        except NotDisposableDSN as exc:
            reasons.append(str(exc))
            continue
        except Exception as exc:  # noqa: BLE001, unreachable server
            reasons.append(f"{var}: not reachable ({exc})")
            continue
        return dsn, var
    if reasons:
        message = "no usable disposable Postgres: " + "; ".join(reasons)
        if _in_ci(env):
            pytest.fail(message)
        pytest.skip(message)
    return None


def _with_dbname(dsn: str, dbname: str) -> str:
    from psycopg.conninfo import make_conninfo

    return make_conninfo(dsn, dbname=dbname)


@contextmanager
def disposable_database():
    """Yield the DSN of a freshly created ``reflect_test_<random>`` database on
    the localhost server; drop it on exit. Without CREATEDB the fixture falls
    back to the configured database when its own name says it is a test
    database (``reflect_test*``), disposing of the ``reflect_memory`` schema
    inside it instead; in CI the service role has CREATEDB, so a missing
    privilege there fails loudly. Skips (never errors) locally when no local
    server is configured or reachable."""
    psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")
    found = server_dsn()
    if found is None:
        pytest.skip("no localhost REFLECT_TEST_DATABASE_URL or DATABASE_URL")
    dsn, source_var = found
    admin = assert_resolved_local(dsn, source_var=source_var)
    try:
        with admin.cursor() as cur:
            cur.execute("select rolcreatedb or rolsuper from pg_roles where rolname = current_user")
            can_create = bool(cur.fetchone()[0])
        if can_create:
            dbname = f"reflect_test_{secrets.token_hex(4)}"
            with admin.cursor() as cur:
                cur.execute(psycopg.sql.SQL("create database {}").format(psycopg.sql.Identifier(dbname)))
            try:
                yield _with_dbname(dsn, dbname)
            finally:
                with admin.cursor() as cur:
                    cur.execute(
                        psycopg.sql.SQL("drop database if exists {} with (force)").format(psycopg.sql.Identifier(dbname))
                    )
            return
        current = admin.info.dbname or ""
        if _in_ci(os.environ):
            pytest.fail(f"{source_var}: the CI role lacks CREATEDB; the disposable database cannot be created")
        if not current.startswith("reflect_test"):
            pytest.skip(
                f"{source_var}: role lacks CREATEDB and database {current!r} is not a reflect_test* database; "
                "the schema-level fallback only disposes of a database named as a test database"
            )
        with admin.cursor() as cur:
            cur.execute("drop schema if exists reflect_memory cascade")
        try:
            yield dsn
        finally:
            with admin.cursor() as cur:
                cur.execute("drop schema if exists reflect_memory cascade")
    finally:
        admin.close()


def connect_or_skip(dsn: str, *, autocommit: bool = True, row_factory=None):
    """Connect to a disposable database DSN, or skip cleanly."""
    psycopg = pytest.importorskip("psycopg", reason="psycopg not installed")
    kwargs = {"autocommit": autocommit}
    if row_factory is not None:
        kwargs["row_factory"] = row_factory
    try:
        return psycopg.connect(dsn, **kwargs)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable ({exc})")
