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
    resolve_all,
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


def test_local_git_resolver_batches_a_request_and_memoizes(git_repo, monkeypatch) -> None:
    from reflect_kb.broker.pinning import resolve_all

    root, sha = git_repo
    r = LocalGitResolver({REPO: root})
    calls: list[list[str]] = []
    original = r._git

    def counting(root_, *args, **kwargs):
        calls.append(list(args))
        return original(root_, *args, **kwargs)

    monkeypatch.setattr(r, "_git", counting)
    pins = [
        PinnedSource(REPO, sha, "src/auth.rs"),
        PinnedSource(REPO, sha, "src/missing.rs"),
        PinnedSource(REPO, "0" * 40, "src/auth.rs"),
        PinnedSource("acme/other", sha, "src/auth.rs"),
    ]
    result = resolve_all(r, pins)
    assert [result[p] for p in pins] == [True, False, False, False]
    # One batch-check for the whole repo group; the unknown repo never touches git.
    assert [c[:2] for c in calls] == [["cat-file", "--batch-check"]]
    calls.clear()
    assert r.resolve(pins[0]) is True
    assert calls == []  # positive memo


def test_local_git_resolver_handles_binary_blobs(tmp_path) -> None:
    import subprocess

    root = tmp_path / "bin-repo"
    root.mkdir()
    git = ["git", "-C", str(root), "-c", "user.email=t@t", "-c", "user.name=t"]
    subprocess.run([*git, "init", "-q", "--initial-branch=main"], check=True)
    (root / "blob.bin").write_bytes(b"\xff\xfe\x00\n" * 3 + b"\x80\x81")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "binary"], check=True)
    sha = subprocess.run([*git, "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    r = LocalGitResolver({"bin": root})
    assert r.resolve(PinnedSource("bin", sha, "blob.bin", 1, 4))
    assert not r.resolve(PinnedSource("bin", sha, "blob.bin", 1, 5))


def test_http_forge_resolver_percent_encodes_and_refuses_traversal() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="x\n")

    r = HttpForgeResolver("https://forge.test/{repo}/raw/{sha}/{path}",
                          client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert r.resolve(PinnedSource("acme/wid gets", SHA, "src/a b/c#1.rs"))
    assert seen == [f"https://forge.test/acme/wid%20gets/raw/{SHA}/src/a%20b/c%231.rs"]
    seen.clear()
    # A hand-built pin with a traversal segment never reaches the network.
    assert not r.resolve(PinnedSource(REPO, SHA, "../secrets.txt"))
    assert not r.resolve(PinnedSource(REPO, SHA, "src/./x.rs"))
    assert seen == []


def test_http_forge_resolver_heads_rangeless_pins_ranges_ranged_pins_and_memoizes() -> None:
    seen: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers.get("range")))
        if request.method == "HEAD":
            return httpx.Response(200)
        return httpx.Response(206, content=b"line1\nline2\nline3\n")

    r = HttpForgeResolver(client=httpx.Client(transport=httpx.MockTransport(handler)))
    plain = parse_source_uri("acme/widgets@" + "a" * 40 + ":src/auth.rs")
    ranged = parse_source_uri("acme/widgets@" + "a" * 40 + ":src/auth.rs#L1-L3")
    too_long = parse_source_uri("acme/widgets@" + "a" * 40 + ":src/auth.rs#L1-L9")
    out = resolve_all(r, [plain, plain, ranged, too_long])
    assert out == {plain: True, ranged: True, too_long: False}
    methods = [(m, rng) for m, _, rng in seen]
    assert methods.count(("HEAD", None)) == 1, seen  # the duplicate pin was fetched once
    assert all(m == "GET" and rng == f"bytes=0-{HttpForgeResolver.BYTE_CAP - 1}" for m, rng in methods if m == "GET")
    before = len(seen)
    assert resolve_all(r, [plain, ranged]) == {plain: True, ranged: True}
    assert len(seen) == before, "memoized pins must not be fetched again"


def test_local_git_resolver_reads_line_ranges_with_one_batch_call(git_repo, monkeypatch) -> None:
    root, sha = git_repo
    r = LocalGitResolver({"acme/widgets": root})
    calls: list[tuple[str, ...]] = []
    original = r._git

    def spy(root_, *args, **kwargs):
        calls.append(args)
        return original(root_, *args, **kwargs)

    monkeypatch.setattr(r, "_git", spy)
    a = parse_source_uri(f"acme/widgets@{sha}:src/auth.rs#L1-L2")
    b = parse_source_uri(f"acme/widgets@{sha}:src/auth.rs#L2-L3")
    out = r.resolve_many([a, b])
    assert out[a] and out[b]
    assert [c[:2] for c in calls] == [("cat-file", "--batch-check"), ("cat-file", "--batch")], calls

