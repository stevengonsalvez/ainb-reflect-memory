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
