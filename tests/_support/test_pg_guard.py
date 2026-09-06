"""The disposable-DSN guard: destructive test setup never runs on a real DB."""

from __future__ import annotations

import pytest

from _support.pg import NotDisposableDSN, assert_disposable_dsn, dsn_host, test_dsn


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://postgres:pw@db.abcdefgh.supabase.co:5432/postgres",
        "postgres://u:p@prod-pg.internal/reflect",
        "host=prod-pg.internal dbname=reflect user=u",
    ],
)
def test_real_looking_database_url_is_refused(dsn: str) -> None:
    with pytest.raises(NotDisposableDSN):
        assert_disposable_dsn(dsn, source_var="DATABASE_URL")


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://reflect@127.0.0.1:54321/reflect_test",
        "postgresql://reflect@localhost/reflect_test",
        "host=/tmp dbname=reflect_test",
        "postgresql://reflect@/reflect_test?host=/tmp",
    ],
)
def test_localhost_database_url_is_accepted(dsn: str) -> None:
    assert_disposable_dsn(dsn, source_var="DATABASE_URL")


def test_test_named_variable_is_trusted_even_when_remote() -> None:
    assert_disposable_dsn("postgresql://u:p@ci-pg.example.test/x", source_var="REFLECT_TEST_DATABASE_URL")


def test_test_dsn_refuses_before_any_connection(monkeypatch) -> None:
    monkeypatch.delenv("REFLECT_TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:pw@db.abcdefgh.supabase.co:5432/postgres")
    with pytest.raises(NotDisposableDSN):
        test_dsn()
    assert dsn_host("postgresql://u@[::1]:5/x") in ("::1", "[::1]")
