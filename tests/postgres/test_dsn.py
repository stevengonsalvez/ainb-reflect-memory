# ABOUTME: The shared Postgres DSN transport rule: network DSNs need TLS,
# ABOUTME: loopback and sockets are exempt, one explicit opt-out.

from __future__ import annotations

import pytest

from reflect_kb.postgres.dsn import InsecureDSNError, assert_tls, is_local_dsn, requires_tls


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@db.example.com/reflect",
        "postgresql://u:p@db.example.com/reflect?sslmode=prefer",
        "host=db.example.com dbname=reflect sslmode=disable",
        "postgresql://u:p@/reflect?host=db.example.com",
        "hostaddr=52.1.2.3 dbname=reflect",
    ],
)
def test_network_dsn_without_tls_is_refused(dsn: str) -> None:
    with pytest.raises(InsecureDSNError):
        assert_tls(dsn, env={})
    assert_tls(dsn, env={"REFLECT_PG_ALLOW_INSECURE": "1"})  # the single opt-out


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@db.example.com/reflect?sslmode=require",
        "host=db.example.com dbname=reflect sslmode=verify-full",
        "postgresql://reflect@127.0.0.1:5432/reflect_test",
        "postgresql://reflect@localhost/reflect_test",
        "postgresql://reflect@/reflect_test?host=/tmp",
    ],
)
def test_tls_or_local_dsn_passes(dsn: str) -> None:
    assert_tls(dsn, env={})


def test_pghost_override_counts_when_the_dsn_names_no_host() -> None:
    assert is_local_dsn("dbname=reflect", env={"PGHOST": "localhost"})
    assert not is_local_dsn("dbname=reflect", env={"PGHOST": "db.example.com"})
    assert requires_tls("postgresql://u@h/x?sslmode=verify-ca")
    assert not requires_tls("postgresql://u@h/x")


def test_service_names_are_never_local_and_pgsslmode_counts() -> None:
    """service=<name> resolves through pg_service.conf, so an empty host is
    not local; PGSSLMODE from the environment is the sslmode libpq applies."""
    from reflect_kb.postgres.dsn import InsecureDSNError, assert_tls, is_local_dsn, requires_tls

    assert not is_local_dsn("service=prod", env={})
    assert not is_local_dsn("dbname=reflect", env={"PGSERVICE": "prod"})
    assert is_local_dsn("dbname=reflect", env={})
    with pytest.raises(InsecureDSNError):
        assert_tls("service=prod sslmode=disable", env={})
    assert requires_tls("postgresql://u@db.example.com/reflect", env={"PGSSLMODE": "require"})
    assert_tls("postgresql://u@db.example.com/reflect", env={"PGSSLMODE": "verify-full"})
    with pytest.raises(InsecureDSNError):
        assert_tls("postgresql://u@db.example.com/reflect", env={"PGSSLMODE": "prefer"})

