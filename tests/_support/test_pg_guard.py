"""The disposable-database guard: destructive setup never reaches a real server."""

from __future__ import annotations

import pytest

pytest.importorskip("psycopg")

from _support.pg import NotDisposableDSN, assert_local_server, server_dsn

REMOTE = "db.abcdefgh.supabase.co"


@pytest.mark.parametrize(
    "dsn",
    [
        f"postgresql://postgres:pw@{REMOTE}:5432/postgres",
        f"postgres://u:p@{REMOTE}/reflect",
        f"host={REMOTE} dbname=reflect user=u",
        f"  host = {REMOTE}   dbname=reflect  ",  # whitespace conninfo
        "postgresql://u:p@localhost/reflect?hostaddr=52.1.2.3",  # hostaddr off-host
        "hostaddr=52.1.2.3 dbname=reflect",
        f"postgresql://u:p@/reflect?host={REMOTE}",  # ?host= override
        f"postgresql://u:p@localhost,{REMOTE}/reflect",  # multi-host with a remote
        # reproduced bypass strings from review
        "postgresql://user:pw@/prod?host=db.prod.internal",
        "postgresql:///prod?host=prod-pg.internal&port=5432",
        "host = prod-pg.internal dbname=reflect",
        "hostaddr=10.0.0.5 dbname=reflect",
    ],
)
def test_remote_servers_are_refused_whatever_the_variable(dsn: str) -> None:
    for var in ("DATABASE_URL", "REFLECT_TEST_DATABASE_URL"):
        with pytest.raises(NotDisposableDSN):
            assert_local_server(dsn, source_var=var, env={})


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://reflect@127.0.0.1:54321/reflect_test",
        "postgresql://reflect@localhost/reflect",
        "host=/tmp dbname=reflect_test",
        "postgresql://reflect@/reflect_test?host=/tmp",
        "postgresql://reflect@[::1]:5432/x",
    ],
)
def test_localhost_servers_are_accepted(dsn: str) -> None:
    assert_local_server(dsn, source_var="DATABASE_URL", env={})


def test_pghost_override_off_host_is_refused() -> None:
    with pytest.raises(NotDisposableDSN):
        assert_local_server("dbname=reflect", source_var="DATABASE_URL", env={"PGHOST": REMOTE})
    with pytest.raises(NotDisposableDSN):
        assert_local_server("dbname=reflect", source_var="DATABASE_URL", env={"PGHOSTADDR": "52.1.2.3"})
    assert_local_server("dbname=reflect", source_var="DATABASE_URL", env={"PGHOST": "localhost"})


def test_server_dsn_refuses_before_any_connection() -> None:
    env = {"DATABASE_URL": f"postgresql://postgres:pw@{REMOTE}:5432/postgres"}
    with pytest.raises(NotDisposableDSN):
        server_dsn(env)
    env = {"REFLECT_TEST_DATABASE_URL": f"postgresql://postgres:pw@{REMOTE}:5432/postgres"}
    with pytest.raises(NotDisposableDSN):
        server_dsn(env)
    assert server_dsn({}) is None


def test_the_developers_own_database_is_never_the_target() -> None:
    """A localhost DSN naming a real database is accepted as a SERVER only;
    the fixtures create reflect_test_<random> next to it and drop that."""
    from _support.pg import _with_dbname

    fresh = _with_dbname("postgresql://reflect@localhost/reflect", "reflect_test_deadbeef")
    assert "dbname=reflect_test_deadbeef" in fresh
    assert "dbname=reflect " not in fresh + " " or fresh.count("dbname=") == 1


class _Info:
    def __init__(self, host, hostaddr="", dbname="reflect_test_x"):
        self.host, self.hostaddr, self.dbname = host, hostaddr, dbname


class _Conn:
    def __init__(self, info):
        self.info, self.closed = info, False

    def close(self):
        self.closed = True


def test_resolved_host_probe_refuses_a_remote_the_string_check_cannot_see(monkeypatch) -> None:
    """service=<name> resolves through pg_service.conf; only libpq knows the
    host, so the probe checks conn.info and refuses before any DDL."""
    import psycopg

    from _support import pg

    monkeypatch.setattr(psycopg, "connect", lambda dsn, **kw: _Conn(_Info("db.prod.internal")))
    with pytest.raises(pg.NotDisposableDSN, match="db.prod.internal"):
        pg.assert_resolved_local("service=prod", source_var="DATABASE_URL")
    monkeypatch.setattr(psycopg, "connect", lambda dsn, **kw: _Conn(_Info("", hostaddr="10.0.0.5")))
    with pytest.raises(pg.NotDisposableDSN, match="hostaddr='10.0.0.5'"):
        pg.assert_resolved_local("service=prod", source_var="DATABASE_URL")
    monkeypatch.setattr(psycopg, "connect", lambda dsn, **kw: _Conn(_Info("localhost")))
    conn = pg.assert_resolved_local("service=local", source_var="DATABASE_URL")
    assert not conn.closed


def test_first_usable_dsn_wins_and_ci_fails_when_none_does(monkeypatch) -> None:
    import psycopg

    from _support import pg

    def connect(dsn, **kw):
        if "prod" in dsn:
            return _Conn(_Info("db.prod.internal"))
        return _Conn(_Info("127.0.0.1"))

    monkeypatch.setattr(psycopg, "connect", connect)
    env = {"REFLECT_TEST_DATABASE_URL": "postgresql://u@localhost/prod_stale",
           "DATABASE_URL": "postgresql://u@localhost/reflect_test"}
    assert pg.server_dsn(env) == ("postgresql://u@localhost/reflect_test", "DATABASE_URL")
    with pytest.raises(pytest.skip.Exception):
        pg.server_dsn({"DATABASE_URL": "postgresql://u@db.prod.internal/reflect"})
    with pytest.raises(pytest.fail.Exception, match="no usable disposable Postgres"):
        pg.server_dsn({"DATABASE_URL": "postgresql://u@db.prod.internal/reflect", "CI": "true"})

