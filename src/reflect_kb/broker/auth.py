"""OIDC bearer-token verification for the Context Broker.

Generic OIDC: the issuer, audience, tenant claim and (optionally) an explicit
JWKS URL are configuration. Microsoft Entra ID, Okta, Auth0, Keycloak and a
test issuer are all config swaps; nothing here knows any provider.

Verification order for every request:

1. ``Authorization: Bearer <token>`` must be present, else 401.
2. The token header's ``kid`` must match a key in the issuer's JWKS (fetched
   through OIDC discovery unless ``jwks_url`` is set; cached; refreshed once
   on an unknown ``kid`` to survive key rotation), else 401.
3. Signature, ``exp``, ``iss`` and ``aud`` are verified by PyJWT, else 401.
4. The configured tenant claim must be a non-empty string, else 403. The
   tenant comes from this claim only, never from the request.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

__all__ = ["AuthError", "OIDCConfig", "OIDCVerifier", "Principal"]


class AuthError(Exception):
    """Authentication or tenant failure carrying the HTTP status to return."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class OIDCConfig:
    issuer: str
    audience: str
    tenant_claim: str = "workspace_id"
    jwks_url: str | None = None
    algorithms: Sequence[str] = ("RS256",)
    jwks_cache_ttl: float = 300.0
    # Claims PyJWT must see on every token. ``exp`` keeps a stolen token short
    # lived; ``iss``/``aud`` are verified against the values above.
    required_claims: Sequence[str] = ("exp", "iss", "aud")

    @property
    def discovery_url(self) -> str:
        return self.issuer.rstrip("/") + "/.well-known/openid-configuration"


@dataclass(frozen=True)
class Principal:
    subject: str | None
    workspace_id: str
    claims: Mapping[str, Any] = field(default_factory=dict)


class OIDCVerifier:
    def __init__(self, config: OIDCConfig, *, http: httpx.Client | None = None) -> None:
        self._cfg = config
        self._http = http or httpx.Client(timeout=5.0)
        self._keys: dict[str, Any] = {}
        self._fetched_at = 0.0

    # -- JWKS -----------------------------------------------------------------

    def _jwks_url(self) -> str:
        if self._cfg.jwks_url:
            return self._cfg.jwks_url
        resp = self._http.get(self._cfg.discovery_url)
        resp.raise_for_status()
        doc = resp.json()
        jwks_uri = doc.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri:
            raise AuthError(503, "issuer discovery document has no jwks_uri")
        issuer = doc.get("issuer")
        if isinstance(issuer, str) and issuer.rstrip("/") != self._cfg.issuer.rstrip("/"):
            raise AuthError(503, "issuer discovery document names a different issuer")
        return jwks_uri

    def _refresh_keys(self) -> None:
        resp = self._http.get(self._jwks_url())
        resp.raise_for_status()
        keys: dict[str, Any] = {}
        for jwk_dict in resp.json().get("keys", []):
            kid = jwk_dict.get("kid")
            if not kid:
                continue
            try:
                keys[kid] = jwt.PyJWK(jwk_dict).key
            except jwt.PyJWTError:
                continue
        self._keys = keys
        self._fetched_at = time.monotonic()

    def _key_for(self, kid: str) -> Any:
        stale = (time.monotonic() - self._fetched_at) > self._cfg.jwks_cache_ttl
        if stale or not self._keys:
            self._refresh_keys()
        if kid not in self._keys and not stale:
            # Unknown kid on a fresh cache: refresh once for key rotation.
            self._refresh_keys()
        key = self._keys.get(kid)
        if key is None:
            raise AuthError(401, "token signed by an unknown key")
        return key

    # -- verification -----------------------------------------------------------

    def verify(self, authorization: str | None) -> Principal:
        token = _bearer_token(authorization)
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise AuthError(401, f"malformed token: {exc}") from exc
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise AuthError(401, "token header has no kid")
        try:
            key = self._key_for(kid)
        except httpx.HTTPError as exc:
            raise AuthError(503, f"issuer keys unavailable: {exc}") from exc
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=list(self._cfg.algorithms),
                audience=self._cfg.audience,
                issuer=self._cfg.issuer,
                options={"require": list(self._cfg.required_claims)},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(401, f"invalid token: {exc}") from exc

        tenant = claims.get(self._cfg.tenant_claim)
        if not isinstance(tenant, str) or not tenant.strip():
            raise AuthError(403, f"token has no {self._cfg.tenant_claim} claim")
        subject = claims.get("sub")
        return Principal(
            subject=str(subject) if subject is not None else None,
            workspace_id=tenant.strip(),
            claims=claims,
        )


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise AuthError(401, "missing bearer token")
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError(401, "authorization must be 'Bearer <token>'")
    return token.strip()
