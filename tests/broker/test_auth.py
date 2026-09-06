"""OIDC verification: who gets in, who gets 401, who gets 403."""

from __future__ import annotations

import pytest

from reflect_kb.broker.auth import AuthError

from .conftest import ISSUER, WS_A


def test_valid_token_yields_tenant_from_claim(issuer) -> None:
    who = issuer.verifier().verify(f"Bearer {issuer.mint()}")
    assert who.workspace_id == WS_A
    assert who.subject == "user-42"


@pytest.mark.parametrize("header", [None, "", "Basic abc", "Bearer", "Bearer   "])
def test_missing_or_malformed_authorization_is_401(issuer, header) -> None:
    with pytest.raises(AuthError) as exc:
        issuer.verifier().verify(header)
    assert exc.value.status == 401


@pytest.mark.parametrize(
    "kwargs",
    [
        {"rogue": True},  # wrong key, right kid
        {"kid": "unknown-kid"},  # key not in JWKS
        {"audience": "someone-else"},
        {"issuer": "https://evil.test"},
        {"expires_in": -60},
    ],
)
def test_bad_tokens_are_401(issuer, kwargs) -> None:
    with pytest.raises(AuthError) as exc:
        issuer.verifier().verify(f"Bearer {issuer.mint(**kwargs)}")
    assert exc.value.status == 401


def test_token_without_tenant_claim_is_403(issuer) -> None:
    with pytest.raises(AuthError) as exc:
        issuer.verifier().verify(f"Bearer {issuer.mint(workspace_id=None)}")
    assert exc.value.status == 403
    with pytest.raises(AuthError) as exc:
        issuer.verifier().verify(f"Bearer {issuer.mint(workspace_id='   ')}")
    assert exc.value.status == 403


def test_tenant_claim_name_is_configuration(issuer) -> None:
    """Entra style: the tenant rides in a differently named claim."""
    v = issuer.verifier(tenant_claim="tid")
    with pytest.raises(AuthError) as exc:
        v.verify(f"Bearer {issuer.mint()}")  # has workspace_id, not tid
    assert exc.value.status == 403
    who = v.verify(f"Bearer {issuer.mint({'tid': WS_A}, workspace_id=None)}")
    assert who.workspace_id == WS_A


def test_alg_none_and_hs256_are_refused(issuer) -> None:
    import jwt as pyjwt

    payload = {"iss": ISSUER, "aud": "reflect-broker", "exp": 4102444800, "workspace_id": WS_A}
    none_token = pyjwt.encode(payload, None, algorithm="none", headers={"kid": issuer.kid})
    hs_token = pyjwt.encode(payload, "secret", algorithm="HS256", headers={"kid": issuer.kid})
    for token in (none_token, hs_token):
        with pytest.raises(AuthError) as exc:
            issuer.verifier().verify(f"Bearer {token}")
        assert exc.value.status == 401


def test_jwks_is_cached_and_refreshed_once_on_unknown_kid(issuer) -> None:
    v = issuer.verifier(jwks_refresh_floor=0.0)
    v.verify(f"Bearer {issuer.mint()}")
    v.verify(f"Bearer {issuer.mint()}")
    assert issuer.jwks_hits == 1
    with pytest.raises(AuthError):
        v.verify(f"Bearer {issuer.mint(kid='rotated')}")
    assert issuer.jwks_hits == 2


def test_unknown_kids_cannot_amplify_fetches(issuer) -> None:
    """Within the refresh floor an unknown kid triggers no fetch, and a kid the
    fresh fetch still lacked is answered from the negative cache."""
    v = issuer.verifier()  # default floor: 30s
    v.verify(f"Bearer {issuer.mint()}")
    assert issuer.jwks_hits == 1
    for _ in range(25):
        with pytest.raises(AuthError) as exc:
            v.verify(f"Bearer {issuer.mint(kid='rotated-' + str(_ % 5))}")
        assert exc.value.status == 401
    assert issuer.jwks_hits == 1


def test_warm_resolves_discovery_once_and_fetches_keys(issuer) -> None:
    v = issuer.verifier()
    v.warm()
    assert issuer.jwks_hits == 1
    v.verify(f"Bearer {issuer.mint()}")
    assert issuer.jwks_hits == 1
    assert v._jwks_uri == ISSUER + "/jwks"


def test_tenant_claim_must_be_a_uuid(issuer) -> None:
    with pytest.raises(AuthError) as exc:
        issuer.verifier().verify(f"Bearer {issuer.mint(workspace_id='acme-workspace')}")
    assert exc.value.status == 403
    assert "UUID" in exc.value.detail
    who = issuer.verifier().verify(f"Bearer {issuer.mint(workspace_id=WS_A.upper())}")
    assert who.workspace_id == WS_A  # canonical form


def test_hmac_and_none_cannot_be_configured(issuer) -> None:
    for algs in (("HS256",), ("RS256", "HS512"), ("none",), ()):
        with pytest.raises(ValueError):
            issuer.verifier(algorithms=algs)


def test_malformed_issuer_documents_are_503_not_500(issuer) -> None:
    import httpx

    from reflect_kb.broker.auth import OIDCConfig, OIDCVerifier

    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("openid-configuration"):
            return httpx.Response(200, text="<html>not json</html>")
        return httpx.Response(200, json=["not", "an", "object"])

    v = OIDCVerifier(
        OIDCConfig(issuer=ISSUER, audience="reflect-broker"),
        http=httpx.Client(transport=httpx.MockTransport(broken)),
    )
    with pytest.raises(AuthError) as exc:
        v.verify(f"Bearer {issuer.mint()}")
    assert exc.value.status == 503

    v = OIDCVerifier(
        OIDCConfig(issuer=ISSUER, audience="reflect-broker", jwks_url=ISSUER + "/jwks"),
        http=httpx.Client(transport=httpx.MockTransport(broken)),
    )
    with pytest.raises(AuthError) as exc:
        v.verify(f"Bearer {issuer.mint()}")
    assert exc.value.status == 503
