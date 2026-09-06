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

import threading
import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
import jwt

__all__ = ["ASYMMETRIC_ALGORITHMS", "AuthError", "OIDCConfig", "OIDCVerifier", "Principal"]

ASYMMETRIC_ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)


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
    # Floor between two JWKS fetches, so a flood of unknown kids cannot turn the
    # broker into a request amplifier against the issuer.
    jwks_refresh_floor: float = 30.0
    # Claims PyJWT must see on every token. ``exp`` keeps a stolen token short
    # lived; ``iss``/``aud`` are verified against the values above.
    required_claims: Sequence[str] = ("exp", "iss", "aud")

    def __post_init__(self) -> None:
        # JWKS keys are public keys; only asymmetric algorithms make sense. An
        # HMAC entry would let anyone holding the public JWK mint tokens, and
        # ``none`` is unsigned, so both are refused at configuration time.
        bad = [a for a in self.algorithms if a not in ASYMMETRIC_ALGORITHMS]
        if bad or not self.algorithms:
            raise ValueError(
                f"algorithms must be asymmetric JWS algorithms {sorted(ASYMMETRIC_ALGORITHMS)}; "
                f"refused {bad or 'empty list'}"
            )

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
        self._jwks_uri: str | None = None
        self._lock = threading.Lock()
        # kid -> monotonic time it was last confirmed absent after a fresh fetch
        self._unknown: dict[str, float] = {}

    # -- JWKS -----------------------------------------------------------------

    def warm(self) -> None:
        """Resolve the JWKS URI and fetch the keys once, at startup, so the
        first request does not pay for discovery and a broken issuer fails
        loudly before the broker serves anything."""
        with self._lock:
            self._refresh_keys()

    def _jwks_url(self) -> str:
        if self._cfg.jwks_url:
            return self._cfg.jwks_url
        if self._jwks_uri:
            return self._jwks_uri
        resp = self._http.get(self._cfg.discovery_url)
        resp.raise_for_status()
        doc = _json_object(resp, "issuer discovery document")
        jwks_uri = doc.get("jwks_uri")
        if not isinstance(jwks_uri, str) or not jwks_uri:
            raise AuthError(503, "issuer discovery document has no jwks_uri")
        issuer = doc.get("issuer")
        if isinstance(issuer, str) and issuer.rstrip("/") != self._cfg.issuer.rstrip("/"):
            raise AuthError(503, "issuer discovery document names a different issuer")
        self._jwks_uri = jwks_uri  # resolved once; discovery is not re-read per refresh
        return jwks_uri

    def _refresh_keys(self) -> None:
        resp = self._http.get(self._jwks_url())
        resp.raise_for_status()
        keys: dict[str, Any] = {}
        raw_keys = _json_object(resp, "JWKS document").get("keys")
        if not isinstance(raw_keys, list):
            raise AuthError(503, "JWKS document has no keys list")
        for jwk_dict in raw_keys:
            if not isinstance(jwk_dict, dict):
                continue
            kid = jwk_dict.get("kid")
            if not kid:
                continue
            try:
                keys[kid] = jwt.PyJWK(jwk_dict).key
            except jwt.PyJWTError:
                continue
        self._keys = keys
        self._fetched_at = time.monotonic()
        self._unknown.clear()

    def _key_for(self, kid: str) -> Any:
        with self._lock:
            now = time.monotonic()
            stale = (now - self._fetched_at) > self._cfg.jwks_cache_ttl
            if stale or not self._keys:
                self._refresh_keys()
                now = time.monotonic()
            key = self._keys.get(kid)
            if key is not None:
                return key
            # Unknown kid. Refresh once for key rotation, but never more often
            # than the floor, and remember a kid the fresh fetch still lacked
            # (negative cache) so repeats are answered without a fetch.
            seen_absent = self._unknown.get(kid)
            if seen_absent is not None and (now - seen_absent) < self._cfg.jwks_refresh_floor:
                raise AuthError(401, "token signed by an unknown key")
            if (now - self._fetched_at) >= self._cfg.jwks_refresh_floor:
                self._refresh_keys()
                key = self._keys.get(kid)
                if key is not None:
                    return key
            self._unknown[kid] = time.monotonic()
            raise AuthError(401, "token signed by an unknown key")

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
                options={"require": list(self._cfg.required_claims), "verify_iss": False},
            )
        except jwt.PyJWTError as exc:
            raise AuthError(401, f"invalid token: {exc}") from exc
        # Discovery tolerates a trailing slash on the configured issuer; the
        # token's iss is compared the same way, so one spelling on each side
        # cannot pass discovery and then fail byte-exact here.
        iss = claims.get("iss")
        if not isinstance(iss, str) or _norm_issuer(iss) != _norm_issuer(self._cfg.issuer):
            raise AuthError(401, f"invalid token: issuer {iss!r} is not {self._cfg.issuer!r}")

        tenant = claims.get(self._cfg.tenant_claim)
        if not isinstance(tenant, str) or not tenant.strip():
            raise AuthError(403, f"token has no {self._cfg.tenant_claim} claim")
        try:
            tenant = str(uuid.UUID(tenant.strip()))
        except ValueError as exc:
            # workspace_id is a uuid column; a claim that is not one can never
            # scope a query and must not reach SQL.
            raise AuthError(403, f"{self._cfg.tenant_claim} claim is not a UUID") from exc
        subject = claims.get("sub")
        return Principal(
            subject=str(subject) if subject is not None else None,
            workspace_id=tenant,
            claims=claims,
        )


def _json_object(resp: httpx.Response, what: str) -> Mapping[str, Any]:
    """Parse a response as a JSON object, or fail as 503 (issuer unusable)."""
    try:
        doc = resp.json()
    except ValueError as exc:
        raise AuthError(503, f"{what} is not valid JSON") from exc
    if not isinstance(doc, Mapping):
        raise AuthError(503, f"{what} is not a JSON object")
    return doc


def _norm_issuer(value: str) -> str:
    return value.strip().rstrip("/")


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise AuthError(401, "missing bearer token")
    scheme, _, token = authorization.strip().partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError(401, "authorization must be 'Bearer <token>'")
    return token.strip()
