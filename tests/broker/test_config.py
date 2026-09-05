"""BrokerSettings.from_env: required values, resolver wiring, TLS on the DSN."""

from __future__ import annotations

import pytest

from reflect_kb.broker.config import BrokerSettings
from reflect_kb.broker.pinning import HttpForgeResolver, LocalGitResolver

BASE = {
    "REFLECT_BROKER_ISSUER": "https://issuer.test",
    "REFLECT_BROKER_AUDIENCE": "reflect-broker",
    "REFLECT_PG_DSN": "postgresql://u:p@db.example.com:5432/reflect?sslmode=require",
    "REFLECT_BROKER_REPOS": "acme/widgets=/srv/widgets, acme/gadgets=/srv/gadgets",
}


def test_happy_path_builds_oidc_and_git_resolver() -> None:
    s = BrokerSettings.from_env(BASE)
    assert s.oidc().issuer == "https://issuer.test"
    assert s.oidc().tenant_claim == "workspace_id"
    assert isinstance(s.resolver(), LocalGitResolver)
    assert set(s.repos) == {"acme/widgets", "acme/gadgets"}


@pytest.mark.parametrize(
    "missing", ["REFLECT_BROKER_ISSUER", "REFLECT_BROKER_AUDIENCE", "REFLECT_PG_DSN"]
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


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@db.example.com/reflect",
        "postgresql://u:p@db.example.com/reflect?sslmode=prefer",
        "host=db.example.com dbname=reflect sslmode=disable",
    ],
)
def test_plaintext_postgres_dsn_is_refused_by_default(dsn: str) -> None:
    env = {**BASE, "REFLECT_PG_DSN": dsn}
    with pytest.raises(RuntimeError, match="sslmode"):
        BrokerSettings.from_env(env)
    env["REFLECT_BROKER_ALLOW_INSECURE_PG"] = "1"
    assert BrokerSettings.from_env(env).allow_insecure_pg


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@db.example.com/reflect?sslmode=verify-full",
        "host=db.example.com dbname=reflect sslmode=require",
    ],
)
def test_tls_postgres_dsn_is_accepted(dsn: str) -> None:
    assert BrokerSettings.from_env({**BASE, "REFLECT_PG_DSN": dsn}).pg_dsn == dsn


def test_hmac_algorithms_are_refused_at_config_time() -> None:
    with pytest.raises(ValueError, match="asymmetric"):
        BrokerSettings.from_env({**BASE, "REFLECT_BROKER_ALGORITHMS": "RS256,HS256"}).oidc()
    with pytest.raises(ValueError, match="asymmetric"):
        BrokerSettings.from_env({**BASE, "REFLECT_BROKER_ALGORITHMS": "none"}).oidc()
    assert BrokerSettings.from_env(
        {**BASE, "REFLECT_BROKER_ALGORITHMS": "RS256,ES256"}
    ).oidc().algorithms == ("RS256", "ES256")
