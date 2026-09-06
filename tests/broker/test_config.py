"""BrokerSettings.from_env: required values, resolver wiring, TLS on the DSN."""

from __future__ import annotations

import pytest

from reflect_kb.broker.config import BrokerSettings
from reflect_kb.broker.pinning import HttpForgeResolver, LocalGitResolver

BASE = {
    "REFLECT_BROKER_ISSUER": "https://issuer.test",
    "REFLECT_BROKER_AUDIENCE": "reflect-broker",
    "REFLECT_BROKER_PG_DSN": "postgresql://u:p@db.example.com:5432/reflect?sslmode=require",
    "REFLECT_BROKER_REPOS": "acme/widgets=/srv/widgets, acme/gadgets=/srv/gadgets",
}


def test_happy_path_builds_oidc_and_git_resolver() -> None:
    s = BrokerSettings.from_env(BASE)
    assert s.oidc().issuer == "https://issuer.test"
    assert s.oidc().tenant_claim == "workspace_id"
    assert isinstance(s.resolver(), LocalGitResolver)
    assert set(s.repos) == {"acme/widgets", "acme/gadgets"}


@pytest.mark.parametrize(
    "missing", ["REFLECT_BROKER_ISSUER", "REFLECT_BROKER_AUDIENCE", "REFLECT_BROKER_PG_DSN"]
)
def test_required_values(missing: str) -> None:
    env = dict(BASE)
    env.pop(missing)
    with pytest.raises(RuntimeError, match=missing):
        BrokerSettings.from_env(env)


def test_git_resolver_needs_repos_and_http_does_not() -> None:
    env = dict(BASE)
    env.pop("REFLECT_BROKER_REPOS")
    with pytest.raises(RuntimeError, match="REPOS"):
        BrokerSettings.from_env(env)
    env["REFLECT_BROKER_RESOLVER"] = "http"
    env["REFLECT_BROKER_FORGE_URL_TEMPLATE"] = "https://forge.test/{repo}/raw/{sha}/{path}"
    assert isinstance(BrokerSettings.from_env(env).resolver(), HttpForgeResolver)


def test_settings_do_not_judge_the_dsn_string() -> None:
    """The transport is judged on the open connection at startup
    (assert_broker_role), never on the string: a keyword-form DSN used to
    slip past a string check."""
    for dsn in ("postgresql://u:p@db.example.com/reflect", "host=prod.example.com user=x dbname=reflect", "service=prod"):
        assert BrokerSettings.from_env({**BASE, "REFLECT_BROKER_PG_DSN": dsn}).pg_dsn == dsn


@pytest.mark.parametrize("host,ssl", [("db.example.com", False), ("prod.internal", False)])
def test_plaintext_network_connection_is_refused_unless_opted_out(host, ssl, monkeypatch) -> None:
    from reflect_kb.broker.config import assert_broker_role
    from reflect_kb.postgres.dsn import InsecureDSNError

    monkeypatch.delenv("REFLECT_PG_ALLOW_INSECURE", raising=False)
    connect = _connect(("reflect_broker", False, False), host=host, ssl=ssl)
    with pytest.raises(InsecureDSNError, match="without TLS"):
        assert_broker_role("host=prod.example.com user=x", connect=connect)
    assert connect.conns and connect.conns[0].closed
    monkeypatch.setenv("REFLECT_PG_ALLOW_INSECURE", "1")  # the one opt-out, shared with the writer path
    assert_broker_role("host=prod.example.com user=x", connect=_connect(("reflect_broker", False, False), host=host))


@pytest.mark.parametrize("host,ssl", [("db.example.com", True), ("127.0.0.1", False), ("/tmp", False), ("localhost", False)])
def test_tls_or_local_connection_is_accepted(host, ssl, monkeypatch) -> None:
    from reflect_kb.broker.config import assert_broker_role

    monkeypatch.delenv("REFLECT_PG_ALLOW_INSECURE", raising=False)
    assert_broker_role("postgresql://x", connect=_connect(("reflect_broker", False, False), host=host, ssl=ssl))


def test_hmac_algorithms_are_refused_at_config_time() -> None:
    with pytest.raises(ValueError, match="asymmetric"):
        BrokerSettings.from_env({**BASE, "REFLECT_BROKER_ALGORITHMS": "RS256,HS256"}).oidc()
    with pytest.raises(ValueError, match="asymmetric"):
        BrokerSettings.from_env({**BASE, "REFLECT_BROKER_ALGORITHMS": "none"}).oidc()
    assert BrokerSettings.from_env(
        {**BASE, "REFLECT_BROKER_ALGORITHMS": "RS256,ES256"}
    ).oidc().algorithms == ("RS256", "ES256")


# --------------------------------------------------------------------------- #
# assert_broker_role: the DSN's role must be one RLS applies to
# --------------------------------------------------------------------------- #

class _Cursor:
    def __init__(self, role_row, owned):
        self._role_row, self._owned, self._last = role_row, owned, None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql_text, params=None):
        self._last = "roles" if "pg_roles" in sql_text else "tables"

    def fetchone(self):
        return self._role_row

    def fetchall(self):
        return [(t,) for t in self._owned]


class _Conn:
    def __init__(self, role_row, owned, host="localhost", ssl=False):
        from types import SimpleNamespace

        self._c = _Cursor(role_row, owned)
        self.closed = False
        self.info = SimpleNamespace(host=host, hostaddr="", ssl_in_use=ssl)

    def cursor(self):
        return self._c

    def close(self):
        self.closed = True


def _connect(role_row, owned=(), host="localhost", ssl=False):
    conns = []

    def connect(dsn, **kwargs):
        c = _Conn(role_row, owned, host=host, ssl=ssl)
        conns.append(c)
        return c

    connect.conns = conns
    return connect


@pytest.mark.parametrize(
    "row,owned,reason",
    [
        (("postgres", True, False), (), "superuser"),
        (("service_role", False, True), (), "BYPASSRLS"),
        (("reflect_owner", False, False), ("memory_items", "entities"), "own"),
    ],
)
def test_assert_broker_role_refuses_roles_that_bypass_rls(row, owned, reason) -> None:
    from reflect_kb.broker.config import assert_broker_role

    connect = _connect(row, owned)
    with pytest.raises(RuntimeError, match=reason):
        assert_broker_role("postgresql://x", connect=connect)
    assert connect.conns and connect.conns[0].closed


def test_assert_broker_role_accepts_a_plain_reader() -> None:
    from reflect_kb.broker.config import assert_broker_role

    connect = _connect(("reflect_broker", False, False))
    assert_broker_role("postgresql://x", connect=connect)
    assert connect.conns[0].closed


def test_settings_assert_role_uses_the_dsn(monkeypatch) -> None:
    import reflect_kb.broker.config as cfg

    seen = {}
    monkeypatch.setattr(cfg, "assert_broker_role", lambda dsn: seen.setdefault("dsn", dsn))
    BrokerSettings.from_env(BASE).assert_role()
    assert seen["dsn"] == BASE["REFLECT_BROKER_PG_DSN"]
