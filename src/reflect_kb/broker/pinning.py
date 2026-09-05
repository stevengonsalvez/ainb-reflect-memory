"""Source pinning: ``repo@sha:path#Lstart-Lend`` parsing and resolution.

A pinned source names one immutable location: a repository, a commit and a
path inside that commit, optionally a line range. The broker refuses to serve
any hit whose source does not parse into this shape or does not resolve
through a :class:`SourceResolver`, so a context snippet can always be traced
to a commit a reviewer can open.

Format
------
``<repo>@<sha>:<path>[#L<start>[-L<end>]]``

* ``repo``: one or more ``/``-separated segments of ``[A-Za-z0-9._-]`` (for
  example ``acme/widgets``).
* ``sha``: 7 to 64 lowercase hex characters (a git commit id, abbreviated or
  full).
* ``path``: a relative path inside the commit; no ``..`` segments, not
  absolute, no whitespace or ``#``.
* line range: ``#L12`` or ``#L12-L20`` (``#L12-20`` is accepted and
  canonicalised to ``#L12-L20``).

This module has no third-party imports so ingest code can build pins without
the ``broker`` extra installed.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

__all__ = [
    "HttpForgeResolver",
    "LocalGitResolver",
    "PinnedSource",
    "SourcePinError",
    "SourceResolver",
    "parse_source_uri",
    "pinned_source_uri",
]

_SOURCE_URI_RE = re.compile(
    r"^(?P<repo>[A-Za-z0-9._\-]+(?:/[A-Za-z0-9._\-]+)*)"
    r"@(?P<sha>[0-9a-f]{7,64})"
    r":(?P<path>[^\s#]+)"
    r"(?:#L(?P<start>[1-9]\d*)(?:-L?(?P<end>[1-9]\d*))?)?$"
)


class SourcePinError(ValueError):
    """A source_uri is not a valid ``repo@sha:path`` pin."""


@dataclass(frozen=True)
class PinnedSource:
    repo: str
    sha: str
    path: str
    line_start: int | None = None
    line_end: int | None = None

    def __str__(self) -> str:
        out = f"{self.repo}@{self.sha}:{self.path}"
        if self.line_start is not None:
            out += f"#L{self.line_start}"
            if self.line_end is not None:
                out += f"-L{self.line_end}"
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "repo": self.repo,
            "sha": self.sha,
            "path": self.path,
            "line_start": self.line_start,
            "line_end": self.line_end,
        }


def parse_source_uri(uri: str | None) -> PinnedSource:
    """Parse ``repo@sha:path#Lstart-Lend``; raise :class:`SourcePinError` otherwise."""
    if not isinstance(uri, str) or not uri:
        raise SourcePinError("source_uri is missing")
    m = _SOURCE_URI_RE.match(uri)
    if not m:
        raise SourcePinError(f"source_uri is not a repo@sha:path pin: {uri!r}")
    path = m.group("path")
    if path.startswith("/") or any(seg in ("", ".", "..") for seg in path.split("/")):
        raise SourcePinError(f"source_uri path must be a clean relative path: {path!r}")
    start = int(m.group("start")) if m.group("start") else None
    end = int(m.group("end")) if m.group("end") else None
    if end is not None and start is not None and end < start:
        raise SourcePinError(f"source_uri line range is inverted: {uri!r}")
    return PinnedSource(m.group("repo"), m.group("sha"), path, start, end)


# ---------------------------------------------------------------------------
# Building pins at ingest from note frontmatter
# ---------------------------------------------------------------------------

_REPO_KEYS = ("repo", "repository", "source_repo")
_SHA_KEYS = ("sha", "commit", "commit_sha", "source_sha")
_PATH_KEYS = ("path", "source_path", "file", "source_file")


def _first(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = mapping.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def pinned_source_uri(frontmatter: Mapping[str, Any]) -> str | None:
    """Build a canonical pin from note metadata, or ``None`` when the note does
    not carry both a repo and a sha (such notes are stored unpinned and the
    broker will never serve them).

    Recognised keys: repo/repository/source_repo, sha/commit/commit_sha/
    source_sha, path/source_path/file/source_file, line_start/line_end (or
    ``lines: "12-20"``). A pin is returned only if it parses.
    """
    repo = _first(frontmatter, _REPO_KEYS)
    sha = _first(frontmatter, _SHA_KEYS)
    path = _first(frontmatter, _PATH_KEYS)
    if not (repo and sha and path):
        return None
    sha = sha.lower()
    start = frontmatter.get("line_start")
    end = frontmatter.get("line_end")
    lines = frontmatter.get("lines")
    if isinstance(lines, str) and re.fullmatch(r"\d+(-\d+)?", lines.strip()):
        parts = lines.strip().split("-")
        start = int(parts[0])
        end = int(parts[1]) if len(parts) == 2 else None
    uri = f"{repo}@{sha}:{path}"
    if isinstance(start, int) and start > 0:
        uri += f"#L{start}"
        if isinstance(end, int) and end >= start:
            uri += f"-L{end}"
    try:
        return str(parse_source_uri(uri))
    except SourcePinError:
        return None


# ---------------------------------------------------------------------------
# Resolvers
# ---------------------------------------------------------------------------


class SourceResolver(Protocol):
    """Confirms that a pin names a real commit and path."""

    def resolve(self, pin: PinnedSource) -> bool: ...


class LocalGitResolver:
    """Resolve pins against checked-out repositories with ``git cat-file``.

    ``repos`` maps the pin's repo name to a local checkout (a working tree or a
    bare repo). Unknown repo names do not resolve. Used by the tests and by a
    deployment that mirrors its repositories next to the broker.
    """

    def __init__(self, repos: Mapping[str, Path], *, timeout: float = 5.0) -> None:
        self._repos = {name: Path(p) for name, p in repos.items()}
        self._timeout = timeout

    def _git(self, root: Path, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=self._timeout,
            check=False,
        )

    def resolve(self, pin: PinnedSource) -> bool:
        root = self._repos.get(pin.repo)
        if root is None or not root.exists():
            return False
        try:
            commit = self._git(root, "rev-parse", "--verify", "--quiet", f"{pin.sha}^{{commit}}")
            if commit.returncode != 0:
                return False
            blob = self._git(root, "cat-file", "-e", f"{pin.sha}:{pin.path}")
            if blob.returncode != 0:
                return False
            if pin.line_end is None and pin.line_start is None:
                return True
            shown = self._git(root, "cat-file", "-p", f"{pin.sha}:{pin.path}")
            if shown.returncode != 0:
                return False
            return _line_count(shown.stdout) >= (pin.line_end or pin.line_start or 0)
        except (OSError, subprocess.SubprocessError):
            return False


class HttpForgeResolver:
    """Resolve pins by fetching the raw file from a forge over HTTP.

    ``url_template`` is formatted with ``repo``, ``sha`` and ``path``; the
    default is the GitHub raw endpoint. A 200 means the commit and path exist;
    when the pin carries a line range the body must have at least that many
    lines. ``httpx`` is used so a test can inject a ``MockTransport``.
    """

    DEFAULT_TEMPLATE = "https://raw.githubusercontent.com/{repo}/{sha}/{path}"

    def __init__(
        self,
        url_template: str = DEFAULT_TEMPLATE,
        *,
        client: Any = None,
        timeout: float = 5.0,
    ) -> None:
        import httpx

        self._template = url_template
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=True)

    def resolve(self, pin: PinnedSource) -> bool:
        url = self._template.format(repo=pin.repo, sha=pin.sha, path=pin.path)
        try:
            resp = self._client.get(url)
        except Exception:  # noqa: BLE001 - any transport failure is "does not resolve"
            return False
        if resp.status_code != 200:
            return False
        if pin.line_start is None and pin.line_end is None:
            return True
        return _line_count(resp.text) >= (pin.line_end or pin.line_start or 0)


def _line_count(text: str) -> int:
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)
