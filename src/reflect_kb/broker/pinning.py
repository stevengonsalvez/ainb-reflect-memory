"""Source pin resolution for the Context Broker.

The pin format and parser live in :mod:`reflect_kb.pinning` (no broker
dependency for the storage layer); this module re-exports them and adds the
resolvers the broker uses to confirm that a pin names a real commit and path:
a local git checkout, or a forge over HTTP.
"""

from __future__ import annotations

import subprocess
import urllib.parse
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol

from reflect_kb.pinning import PinnedSource, SourcePinError, parse_source_uri, pinned_source_uri

__all__ = [
    "HttpForgeResolver",
    "LocalGitResolver",
    "PinnedSource",
    "SourcePinError",
    "SourceResolver",
    "parse_source_uri",
    "pinned_source_uri",
    "resolve_all",
]


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


class SourceResolver(Protocol):
    """Confirms that a pin names a real commit and path."""

    def resolve(self, pin: PinnedSource) -> bool: ...


def resolve_all(resolver: Any, pins: Iterable[PinnedSource]) -> dict[PinnedSource, bool]:
    """Resolve many pins once each (identical pins are deduplicated before
    the resolver sees them), batching when the resolver supports
    ``resolve_many``."""
    unique = list(dict.fromkeys(pins))
    many = getattr(resolver, "resolve_many", None)
    if callable(many):
        return many(unique)
    return {pin: bool(resolver.resolve(pin)) for pin in unique}


class LocalGitResolver:
    """Resolve pins against checked-out repositories with ``git cat-file``.

    ``repos`` maps the pin's repo name to a local checkout (a working tree or a
    bare repo). Unknown repo names do not resolve. One ``cat-file --batch-check``
    per repo per request answers every commit and blob existence question;
    a positive memo (bounded) skips git entirely for pins seen before. Line
    ranges read the blob as bytes, so a binary blob cannot raise on decode.
    """

    MEMO_LIMIT = 4096

    def __init__(self, repos: Mapping[str, Path], *, timeout: float = 5.0) -> None:
        self._repos = {name: Path(p) for name, p in repos.items()}
        self._timeout = timeout
        self._memo: set[PinnedSource] = set()

    def _git(self, root: Path, *args: str, stdin: bytes | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            input=stdin, capture_output=True, timeout=self._timeout, check=False,
        )

    def resolve(self, pin: PinnedSource) -> bool:
        return self.resolve_many([pin])[pin]

    def resolve_many(self, pins: Iterable[PinnedSource]) -> dict[PinnedSource, bool]:
        out: dict[PinnedSource, bool] = {}
        by_repo: dict[str, list[PinnedSource]] = {}
        for pin in pins:
            if pin in self._memo:
                out[pin] = True
            else:
                by_repo.setdefault(pin.repo, []).append(pin)
        for repo, group in by_repo.items():
            root = self._repos.get(repo)
            if root is None or not root.exists():
                out.update({pin: False for pin in group})
                continue
            try:
                out.update(self._check_repo(root, group))
            except (OSError, subprocess.SubprocessError):
                out.update({pin: False for pin in group})
        for pin, ok in out.items():
            if ok and len(self._memo) < self.MEMO_LIMIT:
                self._memo.add(pin)
        return out

    def _check_repo(self, root: Path, group: list[PinnedSource]) -> dict[PinnedSource, bool]:
        # One batch-check answers "does this commit exist" and "does this path
        # exist at that commit" for every pin in the group.
        names: list[str] = []
        for pin in group:
            names.append(f"{pin.sha}^{{commit}}")
            names.append(f"{pin.sha}:{pin.path}")
        proc = self._git(root, "cat-file", "--batch-check", stdin=("\n".join(names) + "\n").encode())
        if proc.returncode != 0:
            return {pin: False for pin in group}
        lines = proc.stdout.decode("utf-8", errors="replace").splitlines()
        if len(lines) != len(names):
            return {pin: False for pin in group}
        result: dict[PinnedSource, bool] = {}
        ranged: list[PinnedSource] = []
        for i, pin in enumerate(group):
            commit_line, blob_line = lines[2 * i], lines[2 * i + 1]
            ok = (" commit " in commit_line) and (" blob " in blob_line) and "missing" not in blob_line
            if ok and (pin.line_start is not None or pin.line_end is not None):
                ranged.append(pin)
            result[pin] = ok
        if ranged:
            result.update(self._check_line_ranges(root, ranged))
        return result

    def _check_line_ranges(self, root: Path, pins: list[PinnedSource]) -> dict[PinnedSource, bool]:
        """One ``cat-file --batch`` for every line-ranged pin: the blob contents
        arrive on the same stream (``<sha> blob <size>\\n<bytes>\\n`` per object),
        so a request with many ranged pins costs one git process, not one each."""
        names = [f"{pin.sha}:{pin.path}" for pin in pins]
        proc = self._git(root, "cat-file", "--batch", stdin=("\n".join(names) + "\n").encode())
        if proc.returncode != 0:
            return {pin: False for pin in pins}
        out: dict[PinnedSource, bool] = {}
        data = proc.stdout
        pos = 0
        for pin in pins:
            nl = data.find(b"\n", pos)
            if nl < 0:
                out[pin] = False
                continue
            header = data[pos:nl].decode("utf-8", errors="replace").split()
            pos = nl + 1
            if len(header) != 3 or header[1] != "blob":
                out[pin] = False  # "missing" or not a blob; no body follows
                continue
            size = int(header[2])
            body = data[pos:pos + size]
            pos += size + 1  # the trailing newline after the body
            out[pin] = _line_count_bytes(body) >= (pin.line_end or pin.line_start or 0)
        return out


class HttpForgeResolver:
    """Resolve pins by fetching the raw file from a forge over HTTP.

    ``url_template`` is formatted with ``repo``, ``sha`` and ``path``; the
    default is the GitHub raw endpoint. Path segments are percent-encoded and
    a path with a traversal segment is refused before any request. A 200 means
    the commit and path exist; when the pin carries a line range the body must
    have at least that many lines. ``httpx`` is used so a test can inject a
    ``MockTransport``.
    """

    DEFAULT_TEMPLATE = "https://raw.githubusercontent.com/{repo}/{sha}/{path}"
    MEMO_LIMIT = 4096
    # A line-ranged pin needs at most this many bytes of the file to count
    # lines; the request carries a Range header, the response is streamed
    # and reading stops at the cap, so a large file never sits in memory.
    BYTE_CAP = 1_048_576
    # Misses of one request are resolved concurrently on the shared client,
    # at most this many in flight: fifty serial round trips became one wave.
    MAX_IN_FLIGHT = 8

    def __init__(self, url_template: str = DEFAULT_TEMPLATE, *, client: Any = None, timeout: float = 5.0) -> None:
        import httpx

        self._template = url_template
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)
        self._memo: set[PinnedSource] = set()

    @staticmethod
    def encode_path(path: str) -> str:
        segments = path.split("/")
        if any(seg in ("", ".", "..") for seg in segments):
            raise SourcePinError(f"refusing to fetch a traversal path: {path!r}")
        return "/".join(urllib.parse.quote(seg, safe="") for seg in segments)

    def resolve_many(self, pins: Iterable[PinnedSource]) -> dict[PinnedSource, bool]:
        """Each distinct pin is fetched at most once per request, a pin that
        resolved before is answered from the memo without a request, and the
        misses are fetched concurrently (at most MAX_IN_FLIGHT at a time)."""
        from concurrent.futures import ThreadPoolExecutor

        out: dict[PinnedSource, bool] = {}
        misses: list[PinnedSource] = []
        for pin in dict.fromkeys(pins):
            if pin in self._memo:
                out[pin] = True
            else:
                misses.append(pin)
        if len(misses) == 1:
            results = [self.resolve(misses[0])]
        elif misses:
            with ThreadPoolExecutor(max_workers=min(self.MAX_IN_FLIGHT, len(misses))) as pool:
                results = list(pool.map(self.resolve, misses))
        else:
            results = []
        for pin, ok in zip(misses, results, strict=True):
            if ok and len(self._memo) < self.MEMO_LIMIT:
                self._memo.add(pin)
            out[pin] = ok
        return out

    def resolve(self, pin: PinnedSource) -> bool:
        """One request per pin: HEAD when the pin has no line range (existence
        is all that matters, no body travels back), a streamed GET with a
        Range header when lines must be counted. Reading stops at BYTE_CAP;
        a 200 (the server ignored the Range) is accepted only when its
        Content-Length is under the cap, so the whole file is never buffered."""
        try:
            url = self._template.format(
                repo="/".join(urllib.parse.quote(s, safe="") for s in pin.repo.split("/")),
                sha=pin.sha,
                path=self.encode_path(pin.path),
            )
            if pin.line_start is None and pin.line_end is None:
                resp = self._client.head(url)
                return resp.status_code == 200
            needed = pin.line_end or pin.line_start or 0
            with self._client.stream("GET", url, headers={"Range": f"bytes=0-{self.BYTE_CAP - 1}"}) as resp:
                if resp.status_code == 200:
                    length = resp.headers.get("content-length")
                    if length is None or not length.isdigit() or int(length) > self.BYTE_CAP:
                        return False
                elif resp.status_code != 206:
                    return False
                lines = 0
                seen = 0
                trailing_newline = True
                for chunk in resp.iter_bytes():
                    if not chunk:
                        continue
                    take = chunk[: self.BYTE_CAP - seen]
                    seen += len(take)
                    lines += take.count(b"\n")
                    trailing_newline = take.endswith(b"\n")
                    if seen >= self.BYTE_CAP or lines >= needed:
                        break
                if seen and not trailing_newline:
                    lines += 1
                return lines >= needed
        except Exception:  # noqa: BLE001 - a bad pin or any transport failure is "does not resolve"
            return False


def _line_count_bytes(data: bytes) -> int:
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)
