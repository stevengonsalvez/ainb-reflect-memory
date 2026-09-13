"""Environment configuration for the Context Broker.

Every value is an environment variable so a deployment is a config swap, not a
code change. Required: REFLECT_BROKER_ISSUER, REFLECT_BROKER_AUDIENCE,
REFLECT_BROKER_PG_DSN (the broker's own read-only role, never the writer's
REFLECT_PG_DSN). See the README broker section for an Entra ID example.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from reflect_kb.postgres.dsn import connect_secure

from .auth import OIDCConfig
from .pinning import HttpForgeResolver, LocalGitResolver, SourceResolver

__all__ = ["BrokerSettings", "assert_broker_role"]


def assert_broker_role(dsn: str, *, connect=None) -> None:
    """The broker's DSN must be a role that Row-Level Security applies to.

    A superuser or a BYPASSRLS role (Supabase ``service_role``) skips every
    policy, and the table owner is exempt unless FORCE is on; the broker
    relies on RLS as the layer under its explicit tenant scoping, so all
    three are refused at startup with a message naming the reason. ``connect``
    is psycopg.connect unless a test injects one.
    """
    # One connection: the transport is judged on it (TLS, or loopback or a
    # socket, or the explicit opt-out; dsn.py), then the role.
    conn = connect_secure(dsn, what="REFLECT_BROKER_PG_DSN", connect=connect)
    try:
        with conn.cursor() as cur:
            cur.execute("select current_user, rolsuper, rolbypassrls from pg_roles where rolname = current_user")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("REFLECT_BROKER_PG_DSN: could not read the connected role from pg_roles")
            user, superuser, bypassrls = row[0], bool(row[1]), bool(row[2])
            cur.execute(
                "select tablename from pg_tables where schemaname = 'reflect_memory' and tableowner = current_user"
            )
            owned = [r[0] for r in cur.fetchall()]
    finally:
        conn.close()
    if superuser:
        raise RuntimeError(f"REFLECT_BROKER_PG_DSN: role {user!r} is a superuser; RLS would not apply")
    if bypassrls:
        raise RuntimeError(f"REFLECT_BROKER_PG_DSN: role {user!r} has BYPASSRLS; RLS would not apply")
    if owned:
        raise RuntimeError(
            f"REFLECT_BROKER_PG_DSN: role {user!r} owns reflect_memory tables ({', '.join(sorted(owned))}); "
            "use the reflect_broker role from migration 0004, never the owner"
        )

_PREFIX = "REFLECT_BROKER_"


@dataclass(frozen=True)
class BrokerSettings:
    issuer: str
    audience: str
    pg_dsn: str
    tenant_claim: str = "workspace_id"
    jwks_url: str | None = None
    algorithms: tuple[str, ...] = ("RS256",)
    resolver_kind: str = "git"  # git | http
    repos: Mapping[str, Path] = field(default_factory=dict)
    forge_url_template: str = HttpForgeResolver.DEFAULT_TEMPLATE
    max_limit: int = 50
    host: str = "127.0.0.1"
    port: int = 8787

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> BrokerSettings:
        e = dict(os.environ if env is None else env)

        def need(name: str) -> str:
            value = e.get(name, "").strip()
            if not value:
                raise RuntimeError(f"{name} is required for the Context Broker")
            return value

        repos: dict[str, Path] = {}
        for entry in filter(None, (s.strip() for s in e.get(_PREFIX + "REPOS", "").split(","))):
            name, sep, path = entry.partition("=")
            if not sep or not name.strip() or not path.strip():
                raise RuntimeError(
                    f"{_PREFIX}REPOS entries must be <repo>=<checkout path>; got {entry!r}"
                )
            repos[name.strip()] = Path(path.strip()).expanduser()

        kind = e.get(_PREFIX + "RESOLVER", "git").strip().lower()
        if kind not in ("git", "http"):
            raise RuntimeError(f"{_PREFIX}RESOLVER must be git or http; got {kind!r}")
        if kind == "git" and not repos:
            raise RuntimeError(f"{_PREFIX}REPOS is required when the resolver is git")

        algorithms = tuple(
            a.strip() for a in e.get(_PREFIX + "ALGORITHMS", "RS256").split(",") if a.strip()
        )
        return cls(
            issuer=need(_PREFIX + "ISSUER"),
            audience=need(_PREFIX + "AUDIENCE"),
            pg_dsn=need("REFLECT_BROKER_PG_DSN"),
            tenant_claim=e.get(_PREFIX + "TENANT_CLAIM", "workspace_id").strip() or "workspace_id",
            jwks_url=e.get(_PREFIX + "JWKS_URL", "").strip() or None,
            algorithms=algorithms or ("RS256",),
            resolver_kind=kind,
            repos=repos,
            forge_url_template=e.get(_PREFIX + "FORGE_URL_TEMPLATE", "").strip()
            or HttpForgeResolver.DEFAULT_TEMPLATE,
            max_limit=int(e.get(_PREFIX + "MAX_LIMIT", "50")),
            host=e.get(_PREFIX + "HOST", "127.0.0.1").strip() or "127.0.0.1",
            port=int(e.get(_PREFIX + "PORT", "8787")),
        )

    def assert_role(self) -> None:
        """Refuse a DSN whose role would make RLS moot (see assert_broker_role)."""
        assert_broker_role(self.pg_dsn)

    def oidc(self) -> OIDCConfig:
        return OIDCConfig(
            issuer=self.issuer,
            audience=self.audience,
            tenant_claim=self.tenant_claim,
            jwks_url=self.jwks_url,
            algorithms=self.algorithms,
        )

    def resolver(self) -> SourceResolver:
        if self.resolver_kind == "http":
            return HttpForgeResolver(self.forge_url_template)
        return LocalGitResolver(self.repos)

