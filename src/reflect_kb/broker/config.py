"""Environment configuration for the Context Broker.

Every value is an environment variable so a deployment is a config swap, not a
code change. Required: REFLECT_BROKER_ISSUER, REFLECT_BROKER_AUDIENCE,
REFLECT_PG_DSN. See the README broker section for an Entra ID example.
"""

from __future__ import annotations

import os
import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .auth import OIDCConfig
from .pinning import HttpForgeResolver, LocalGitResolver, SourceResolver

__all__ = ["BrokerSettings"]

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
    allow_insecure_pg: bool = False

    def __post_init__(self) -> None:
        # Notes, vectors and graph cross the network on this DSN. Require TLS
        # unless the operator says otherwise for a loopback or socket setup.
        if not self.allow_insecure_pg and not _dsn_requires_tls(self.pg_dsn):
            raise RuntimeError(
                "REFLECT_PG_DSN must carry sslmode=require, verify-ca or verify-full; "
                "set REFLECT_BROKER_ALLOW_INSECURE_PG=1 only for loopback or Unix-socket "
                "databases"
            )

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
            pg_dsn=need("REFLECT_PG_DSN"),
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
            allow_insecure_pg=e.get(_PREFIX + "ALLOW_INSECURE_PG", "").strip() == "1",
        )

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


_TLS_MODES = ("require", "verify-ca", "verify-full")


def _dsn_requires_tls(dsn: str) -> bool:
    """True when the libpq DSN pins an encrypting sslmode (URI or key=value form)."""
    if "://" in dsn:
        query = urllib.parse.urlparse(dsn).query
        modes = urllib.parse.parse_qs(query).get("sslmode", [])
        return any(m in _TLS_MODES for m in modes)
    m = re.search(r"(?:^|\s)sslmode=(\S+)", dsn)
    return bool(m and m.group(1).strip("'\"") in _TLS_MODES)
