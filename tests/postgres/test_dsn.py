"""The TLS gate judges the open connection, not the DSN string."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from reflect_kb.postgres.dsn import (
    InsecureDSNError,
    assert_tls,
    connect_secure,
    is_local_connection,
    is_local_dsn,
    requires_tls,
)


class _Conn:
    def __init__(self, host="", hostaddr="", ssl=False):
        self.info = SimpleNamespace(host=host, hostaddr=hostaddr, ssl_in_use=ssl)
        self.closed = False

    def close(self):
        self.closed = True


def _connect_to(conn):
    def connect(dsn, **kwargs):
        connect.calls.append((dsn, kwargs))
        return conn
    connect.calls = []
    return connect


@pytest.mark.parametrize("dsn", [
    "postgresql://u:p@db.example.com:5432/reflect",
    "host=prod.example.com user=x dbname=reflect",   # keyword form used to skip the check
    "service=prod",
    "postgresql://u@db.example.com/reflect?sslmode=disable",
    "not a dsn at all",
])
def test_a_remote_connection_without_tls_is_refused_whatever_the_string(dsn) -> None:
    conn = _Conn(host="prod.example.com", ssl=False)
    with pytest.raises(InsecureDSNError):
        assert_tls(dsn, env={}, connect=_connect_to(conn))
    assert conn.closed, "the insecure connection was left open"


def test_tls_local_socket_and_the_opt_out_pass() -> None:
    assert not connect_secure("x", env={}, connect=_connect_to(_Conn(host="db.example.com", ssl=True))).closed
    assert not connect_secure("x", env={}, connect=_connect_to(_Conn(host="localhost"))).closed
    assert not connect_secure("x", env={}, connect=_connect_to(_Conn(host="/tmp"))).closed
    assert not connect_secure("x", env={}, connect=_connect_to(_Conn(host="127.0.0.1", hostaddr="127.0.0.1"))).closed
    remote = _Conn(host="db.example.com")
    assert connect_secure("x", env={"REFLECT_PG_ALLOW_INSECURE": "1"}, connect=_connect_to(remote)) is remote
    # A multi-host DSN that resolved one remote member is remote.
    with pytest.raises(InsecureDSNError):
        connect_secure("x", env={}, connect=_connect_to(_Conn(host="localhost,db.example.com")))


def test_connect_kwargs_reach_the_driver_and_a_fake_without_info_passes() -> None:
    conn = _Conn(host="localhost")
    connect = _connect_to(conn)
    assert connect_secure("dsn", connect=connect, autocommit=True, row_factory="rf") is conn
    assert connect.calls == [("dsn", {"autocommit": True, "row_factory": "rf"})]
    bare = SimpleNamespace()  # a unit-test fake with no transport information
    assert connect_secure("dsn", env={}, connect=lambda d, **k: bare) is bare


def test_is_local_connection() -> None:
    assert is_local_connection(SimpleNamespace(host="::1", hostaddr=""))
    assert is_local_connection(SimpleNamespace(host="", hostaddr=""))
    assert not is_local_connection(SimpleNamespace(host="db.internal", hostaddr=""))
    assert not is_local_connection(SimpleNamespace(host="localhost", hostaddr="10.0.0.5"))


def test_string_prefilters_are_still_honest() -> None:
    assert not is_local_dsn("service=prod", env={})
    assert not is_local_dsn("dbname=reflect", env={"PGSERVICE": "prod"})
    assert is_local_dsn("dbname=reflect", env={})
    assert requires_tls("postgresql://u@db.example.com/reflect", env={"PGSSLMODE": "require"})
    assert not requires_tls("postgresql://u@db.example.com/reflect", env={"PGSSLMODE": "prefer"})
