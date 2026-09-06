# ABOUTME: Fixtures for the Context Broker tests: a local test OIDC issuer
# ABOUTME: (RSA key + discovery + JWKS served through an httpx MockTransport),
# ABOUTME: a token minter, a git repo to pin against, and a canned fake store.

from __future__ import annotations

import json
import subprocess
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi", reason="broker extra not installed")
jwt = pytest.importorskip("jwt", reason="broker extra not installed")
httpx = pytest.importorskip("httpx")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from reflect_kb.broker.auth import OIDCConfig, OIDCVerifier
from reflect_kb.broker.pinning import LocalGitResolver
from reflect_kb.postgres import (
    Citation,
    Edge,
    Entity,
    EvidenceHit,
    EvidencePack,
    GraphNeighborhood,
    MemoryStore,
    Tenant,
)

ISSUER = "https://issuer.test"
AUDIENCE = "reflect-broker"
WS_A = "11111111-1111-1111-1111-111111111111"
WS_B = "22222222-2222-2222-2222-222222222222"
REPO = "acme/widgets"


class TestIssuer:
    """A local OIDC issuer: one signing key, discovery + JWKS documents, a minter."""

    def __init__(self) -> None:
        self.kid = "test-key-1"
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.rogue_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key()))
        pub_jwk.update({"kid": self.kid, "use": "sig", "alg": "RS256"})
        self.jwks = {"keys": [pub_jwk]}
        self.jwks_hits = 0

    def _pem(self, key) -> bytes:
        return key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )

    def mint(
        self,
        claims: dict[str, Any] | None = None,
        *,
        workspace_id: str | None = WS_A,
        kid: str | None = None,
        rogue: bool = False,
        audience: str = AUDIENCE,
        issuer: str = ISSUER,
        expires_in: int = 300,
    ) -> str:
        now = int(time.time())
        payload: dict[str, Any] = {
            "iss": issuer,
            "aud": audience,
            "sub": "user-42",
            "iat": now,
            "exp": now + expires_in,
        }
        if workspace_id is not None:
            payload["workspace_id"] = workspace_id
        payload.update(claims or {})
        key = self.rogue_key if rogue else self.private_key
        return jwt.encode(
            payload,
            self._pem(key),
            algorithm="RS256",
            headers={"kid": kid or self.kid},
        )

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url == httpx.URL(ISSUER + "/.well-known/openid-configuration"):
                return httpx.Response(200, json={"issuer": ISSUER, "jwks_uri": ISSUER + "/jwks"})
            if request.url == httpx.URL(ISSUER + "/jwks"):
                self.jwks_hits += 1
                return httpx.Response(200, json=self.jwks)
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    def verifier(self, **overrides: Any) -> OIDCVerifier:
        cfg = OIDCConfig(issuer=ISSUER, audience=AUDIENCE, **overrides)
        return OIDCVerifier(cfg, http=httpx.Client(transport=self.transport()))


@pytest.fixture
def issuer() -> TestIssuer:
    return TestIssuer()


@pytest.fixture
def git_repo(tmp_path: Path) -> tuple[Path, str]:
    """A real git repo with one commit; returns (path, full sha)."""
    root = tmp_path / "widgets"
    root.mkdir()

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    git("init", "-q", "--initial-branch=main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (root / "src").mkdir()
    (root / "src" / "auth.rs").write_text(
        "\n".join(f"line {i}" for i in range(1, 21)) + "\n", encoding="utf-8"
    )
    git("add", ".")
    git("commit", "-q", "-m", "auth middleware")
    return root, git("rev-parse", "HEAD")


@pytest.fixture
def resolver(git_repo) -> LocalGitResolver:
    root, _ = git_repo
    return LocalGitResolver({REPO: root})


class FakeStore:
    """Returns a canned pack for whatever tenant it is asked about, recording the query."""

    def __init__(self, hits: list[EvidenceHit]) -> None:
        self.hits = hits
        self.queries: list[Any] = []
        self.bound: list[str] = []

    def get_evidence_pack(self, q) -> EvidencePack:
        self.queries.append(q)
        now = datetime.now(UTC)
        ent = Entity("e-auth", q.tenant.workspace_id, "auth", "component", ("authn",), {}, now, now)
        tok = Entity("e-token", q.tenant.workspace_id, "token", "concept", (), {}, now, now)

        def edge(eid: str, evidence: str | None) -> Edge:
            return Edge(eid, q.tenant.workspace_id, "e-auth", "e-token", "validates", evidence, 1.0, {}, now, now)

        return EvidencePack(
            query=q.query,
            tenant=q.tenant,
            lexical=list(self.hits),
            entities=[],
            graph=GraphNeighborhood(
                entities=[ent, tok],
                edges=[
                    edge("edge-kept", "pinned-ok"),  # evidence survived
                    edge("edge-no-evidence", None),  # cites no memory
                    edge("edge-dropped-evidence", "bad-sha"),  # evidence was refused
                    edge("edge-unknown-evidence", "never-a-hit"),  # evidence never returned
                ],
            ),
            citations=[Citation(h.memory_id, h.source_type, h.source_uri) for h in self.hits],
        )


def hit(memory_id: str, source_uri: str | None, **metadata: Any) -> EvidenceHit:
    return EvidenceHit(
        memory_id=memory_id,
        content=f"content of {memory_id}",
        rank=0.5,
        snippet=f"<b>{memory_id}</b>",
        source_type="codebase_note",
        source_uri=source_uri,
        metadata=metadata,
    )


@pytest.fixture
def canned_hits(git_repo) -> list[EvidenceHit]:
    _, sha = git_repo
    return [
        hit("pinned-ok", f"{REPO}@{sha}:src/auth.rs#L3-L9"),
        hit("pinned-short-sha", f"{REPO}@{sha[:12]}:src/auth.rs"),
        hit("bad-sha", f"{REPO}@{'0' * 40}:src/auth.rs"),
        hit("bad-path", f"{REPO}@{sha}:src/missing.rs"),
        hit("range-past-eof", f"{REPO}@{sha}:src/auth.rs#L1-L999"),
        hit("free-text", "src/auth.rs"),
        hit("no-source", None),
        hit("restricted", f"{REPO}@{sha}:src/auth.rs", classification="restricted"),
        hit("pii", f"{REPO}@{sha}:src/auth.rs", classification="pii"),
        hit("public", f"{REPO}@{sha}:src/auth.rs", classification="public"),
    ]


@pytest.fixture
def fake_store(canned_hits) -> FakeStore:
    return FakeStore(canned_hits)


@pytest.fixture
def client(issuer, fake_store, resolver):
    from fastapi.testclient import TestClient

    from reflect_kb.broker.app import create_app

    @contextmanager
    def factory():
        yield fake_store

    app = create_app(verifier=issuer.verifier(), store_factory=factory, resolver=resolver)
    return TestClient(app)


__all__ = ["AUDIENCE", "ISSUER", "REPO", "WS_A", "WS_B", "FakeStore", "MemoryStore", "Tenant", "hit"]
