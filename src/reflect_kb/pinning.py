"""Source pinning: the ``repo@sha:path#Lstart-Lend`` format and its parser.

A pinned source names one immutable location: a repository, a commit and a
path inside that commit, optionally a line range. Ingest builds pins from note
metadata here; the Context Broker (``reflect_kb.broker.pinning``) resolves
them against a checkout or a forge. The parser lives outside the broker so the
storage layer never depends on the broker package.

Format
------
``<repo>@<sha>:<path>[#L<start>[-L<end>]]``

* ``repo``: one or more ``/``-separated segments of ``[A-Za-z0-9._-]``.
* ``sha``: 7 to 64 lowercase hex characters (a git commit id).
* ``path``: a clean relative path inside the commit; no ``..`` segments, not
  absolute, no whitespace or ``#``.
* line range: ``#L12`` or ``#L12-L20`` (``#L12-20`` is accepted and
  canonicalised to ``#L12-L20``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = ["PinnedSource", "SourcePinError", "parse_source_uri", "pinned_source_uri"]

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
    """The first non-empty string under ``keys``, at the top level or, as a
    fallback, under a ``provenance`` mapping (the skill's note template nests
    it there)."""
    nested = mapping.get("provenance")
    scopes = [mapping] + ([nested] if isinstance(nested, Mapping) else [])
    for scope in scopes:
        for key in keys:
            value = scope.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _first_top_level(mapping: Mapping[str, Any], keys: tuple[str, ...]) -> str | None:
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
