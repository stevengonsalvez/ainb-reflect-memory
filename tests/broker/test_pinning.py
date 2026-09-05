"""Source pin parsing, ingest-time pin building, and the two resolvers."""

from __future__ import annotations

import httpx
import pytest

from reflect_kb.broker.pinning import (
    HttpForgeResolver,
    LocalGitResolver,
    PinnedSource,
    SourcePinError,
    parse_source_uri,
    pinned_source_uri,
)

from .conftest import REPO

SHA = "3f2a9c1d4e5b6a7f8091a2b3c4d5e6f708192a3b"


@pytest.mark.parametrize(
    "uri,expected",
    [
        (f"{REPO}@{SHA}:src/auth.rs", PinnedSource(REPO, SHA, "src/auth.rs")),
        (f"{REPO}@{SHA[:7]}:src/auth.rs#L12", PinnedSource(REPO, SHA[:7], "src/auth.rs", 12)),
        (f"{REPO}@{SHA}:src/auth.rs#L12-L20", PinnedSource(REPO, SHA, "src/auth.rs", 12, 20)),
        (f"{REPO}@{SHA}:src/auth.rs#L12-20", PinnedSource(REPO, SHA, "src/auth.rs", 12, 20)),
        (f"widgets@{SHA}:README.md", PinnedSource("widgets", SHA, "README.md")),
    ],
)
def test_parse_accepts_the_documented_shapes(uri: str, expected: PinnedSource) -> None:
    pin = parse_source_uri(uri)
    assert pin == expected
    # Canonical form always spells the range as #Lstart-Lend and round-trips.
    assert parse_source_uri(str(pin)) == pin


@pytest.mark.parametrize(
    "uri",
    [
        None,
        "",
        "src/auth.rs",
        "https://github.com/acme/widgets/blob/main/src/auth.rs",
        f"{REPO}@main:src/auth.rs",  # a branch is not a pin
        f"{REPO}@{SHA[:6]}:src/auth.rs",  # too short to be a sha
        f"{REPO}@{SHA.upper()}:src/auth.rs",  # uppercase hex
        f"{REPO}@{SHA}:/etc/passwd",  # absolute
        f"{REPO}@{SHA}:../secrets.txt",  # traversal
        f"{REPO}@{SHA}:src//auth.rs",  # empty segment
        f"{REPO}@{SHA}:src/auth.rs#L20-L12",  # inverted range
        f"{REPO}@{SHA}:src/auth.rs#L0",  # lines start at 1
        f"acme widgets@{SHA}:src/auth.rs",  # whitespace in repo
    ],
)
def test_parse_refuses_anything_that_is_not_a_pin(uri) -> None:
    with pytest.raises(SourcePinError):
        parse_source_uri(uri)


def test_pinned_source_uri_from_frontmatter() -> None:
    fm = {"repo": REPO, "commit": SHA.upper(), "source_path": "src/auth.rs", "lines": "3-9"}
    assert pinned_source_uri(fm) == f"{REPO}@{SHA}:src/auth.rs#L3-L9"
    fm = {"repository": REPO, "sha": SHA, "file": "src/auth.rs", "line_start": 4}
    assert pinned_source_uri(fm) == f"{REPO}@{SHA}:src/auth.rs#L4"
    # No repo or no sha means unpinned, never a guess.
    assert pinned_source_uri({"repo": REPO, "source_path": "src/auth.rs"}) is None
    assert pinned_source_uri({"commit": SHA, "source_path": "src/auth.rs"}) is None
    # A branch name in the sha slot does not become a pin.
    assert pinned_source_uri({"repo": REPO, "commit": "main", "source_path": "x"}) is None


def test_local_git_resolver(git_repo) -> None:
    root, sha = git_repo
    r = LocalGitResolver({REPO: root})
    assert r.resolve(PinnedSource(REPO, sha, "src/auth.rs"))
    assert r.resolve(PinnedSource(REPO, sha[:10], "src/auth.rs", 1, 20))
    assert not r.resolve(PinnedSource(REPO, sha, "src/auth.rs", 1, 21))  # past EOF
    assert not r.resolve(PinnedSource(REPO, sha, "src/nope.rs"))
    assert not r.resolve(PinnedSource(REPO, "0" * 40, "src/auth.rs"))
    assert not r.resolve(PinnedSource("acme/other", sha, "src/auth.rs"))  # unknown repo


def test_http_forge_resolver_uses_the_template_and_status() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/src/auth.rs"):
            return httpx.Response(200, text="a\nb\nc\n")
        return httpx.Response(404)

    r = HttpForgeResolver(
        "https://forge.test/{repo}/raw/{sha}/{path}",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert r.resolve(PinnedSource(REPO, SHA, "src/auth.rs"))
    assert seen[-1] == f"https://forge.test/{REPO}/raw/{SHA}/src/auth.rs"
    assert r.resolve(PinnedSource(REPO, SHA, "src/auth.rs", 1, 3))
    assert not r.resolve(PinnedSource(REPO, SHA, "src/auth.rs", 1, 4))
    assert not r.resolve(PinnedSource(REPO, SHA, "src/missing.rs"))


def test_http_forge_resolver_treats_transport_errors_as_unresolved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("forge down")

    r = HttpForgeResolver(client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert not r.resolve(PinnedSource(REPO, SHA, "src/auth.rs"))
