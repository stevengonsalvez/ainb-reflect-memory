"""OKF v0.2 storage profile: the one place reflect makes a note on disk OKF.

Two operations on a note's TEXT, both surgical: only the frontmatter lines of
the keys being set are touched; every other frontmatter line and the whole
body survive byte-for-byte.

* :func:`normalize_note` adds the OKF keys a note is missing (see
  :func:`reflect_kb.okf_profile.okf_additions`). Writers call it at write time.
* :func:`set_lifecycle_fields` is the Rule S9 carve-out: after write, ONLY
  `status`, `verified` and `stale_after` may change.

Every rewrite is re-parsed and compared against the intended result before it
is returned; anything YAML-ambiguous (duplicate keys, flow-style frontmatter)
raises ValueError rather than risk corrupting a note.

Pure derivation and conformance checks live in the stdlib-only
:mod:`reflect_kb.okf_profile` (vendored into plugin/scripts) and are
re-exported here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from reflect_kb import __version__
from reflect_kb.okf_profile import (  # noqa: F401  (re-exported API)
    LIFECYCLE_KEYS,
    OKF_VERSION,
    STATUSES,
    actor,
    is_okf_conformant,
    okf_additions,
    okf_problems,
    okf_warnings,
    render_frontmatter,
    strict_iso8601,
    to_okf,
    yaml_value,
)

_DELETE = object()
_UNSET = object()


def writer_actor(producer: str) -> str:
    """Actor for a reflect_kb writer, e.g. `reflect-cli/0.3.0`."""
    return actor(producer, __version__)


def file_mtime(path: Path) -> str | None:
    """A file's mtime as an offset ISO 8601 instant (deterministic `generated.at`
    for notes that carry no `created`); None when the file cannot be stat'ed."""
    try:
        stamp = Path(path).stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(int(stamp), UTC).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Frontmatter location and parsing
# ---------------------------------------------------------------------------


# Opening fence: `---`, optionally followed by a YAML comment (`--- # note`).
_OPENING_FENCE = re.compile(r"^---\s*(?:#.*)?$")


def _opens_frontmatter(first_line: str) -> bool:
    return bool(_OPENING_FENCE.match(first_line.rstrip()))


def _closing_delimiter(lines: list[str]) -> int | None:
    """Index of the closing `---` LINE (same rule as serve._split_frontmatter).

    Only a line that is exactly `---` delimits, so a value containing `---`
    (`title: cost --- benefit`) cannot truncate the frontmatter.
    """
    if not lines or not _opens_frontmatter(lines[0]):
        return None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return i
    return None


def _load(fm_lines: list[str]) -> dict:
    try:
        data = yaml.safe_load("\n".join(fm_lines))
    except yaml.YAMLError as exc:
        raise ValueError(f"unparseable frontmatter: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        # ValueError, not TypeError: callers treat every unsafe note alike.
        raise ValueError("frontmatter is not a mapping")  # noqa: TRY004
    return data


def _legacy_glued_split(text: str) -> tuple[dict, str] | None:
    """Read a note whose closing fence is glued to its last value.

    Before the fix, output_generator's no-PyYAML path wrote
    `  detected_at: "..."---` with no closing `---` line; such notes exist on
    disk. Fall back to the legacy text split, accepted only when the block
    parses to a mapping. Read-only: these notes are never rewritten in place.
    """
    parts = text.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    return (data, parts[2]) if isinstance(data, dict) else None


def has_frontmatter(text: str) -> bool:
    """True when `text` opens with a `---` fence and its block can be located."""
    lines = text.split("\n")
    if _closing_delimiter(lines) is not None:
        return True
    return _opens_frontmatter(lines[0]) and _legacy_glued_split(text) is not None


def parse_note(text: str) -> tuple[dict, str]:
    """(frontmatter, body), splitting only on `---` delimiter LINES.

    A note without frontmatter yields `({}, text)`. Raises ValueError when the
    block is unparseable YAML or not a mapping.
    """
    lines = text.split("\n")
    end = _closing_delimiter(lines)
    if end is None:
        legacy = _legacy_glued_split(text) if _opens_frontmatter(lines[0]) else None
        return legacy if legacy is not None else ({}, text)
    return _load(lines[1:end]), "\n".join(lines[end + 1:])


# ---------------------------------------------------------------------------
# Line surgery on top-level keys
# ---------------------------------------------------------------------------


def _continues_block(line: str) -> bool:
    # Indented lines and column-0 `- item` lines belong to the key above
    # (PyYAML's safe_dump writes block sequences at the key's own indent).
    return line[:1] in (" ", "\t", "-")


def _key_block(lines: list[str], key: str) -> tuple[int, int] | None:
    """[start, end) of a top-level key's lines, or None when absent."""
    k = re.escape(key)
    head = re.compile(rf"""^(?:{k}|"{k}"|'{k}')\s*:(\s|$)""")
    for start, line in enumerate(lines):
        if not head.match(line):
            continue
        end = start + 1
        while end < len(lines):
            if _continues_block(lines[end]):
                end += 1
                continue
            if not lines[end].strip():
                nxt = next((ln for ln in lines[end:] if ln.strip()), "")
                if _continues_block(nxt):
                    end += 1
                    continue
            break
        return start, end
    return None


def _rewrite(fm_lines: list[str], updates: Mapping[str, Any], eol: str) -> list[str]:
    """Apply {key: value | _DELETE}; verify nothing else moved.

    `eol` is "\r" for CRLF notes (lines come from splitting on "\n"), so new
    lines match the file's existing line endings.
    """
    before = _load(fm_lines)
    out = list(fm_lines)
    for key, value in updates.items():
        block = _key_block(out, key)
        new = [] if value is _DELETE else [f"{key}: {yaml_value(value)}{eol}"]
        if block:
            out[block[0]:block[1]] = new
        elif key == "type":
            out[0:0] = new  # OKF's one required key reads best first
        else:
            out.extend(new)

    expected = dict(before)
    for key, value in updates.items():
        if value is _DELETE:
            expected.pop(key, None)
        else:
            expected[key] = value
    if _load(out) != expected:
        raise ValueError("frontmatter rewrite would disturb other keys; refusing")
    return out


def _with_frontmatter(text: str, updates: Mapping[str, Any]) -> str:
    lines = text.split("\n")
    end = _closing_delimiter(lines)
    if end is None:
        raise ValueError("note has no frontmatter block")
    eol = "\r" if lines[0].endswith("\r") else ""
    new_fm = _rewrite(lines[1:end], updates, eol)
    return "\n".join([lines[0], *new_fm, *lines[end:]])


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------


def normalize_note(text: str, *, actor: str, now: Any = None) -> str:
    """Return `text` as an OKF-conformant note; unchanged if it already is.

    A note with no frontmatter gets a fresh block derived from its body.
    Raises ValueError when the existing frontmatter cannot be safely edited.
    """
    lines = text.split("\n")
    if _closing_delimiter(lines) is None:
        if _opens_frontmatter(lines[0].lstrip("\ufeff")):
            raise ValueError("frontmatter is unterminated, glued to a value, or BOM-prefixed")
        block = render_frontmatter(to_okf({}, text, actor=actor, now=now)) + "\n"
        return (block.replace("\n", "\r\n") if "\r\n" in text else block) + text
    fm, body = parse_note(text)
    additions = okf_additions(fm, body, actor=actor, now=now)
    return _with_frontmatter(text, additions) if additions else text


def set_lifecycle_fields(
    text: str, *, status: Any = _UNSET, verified: Any = _UNSET, stale_after: Any = _UNSET,
) -> str:
    """Rule S9 carve-out: rewrite only `status` / `verified` / `stale_after`.

    Pass None to remove a key. `verified` takes one `{by, at}` mapping or a
    list of them (SPEC §5.2) and is stored as a list. Timestamps must carry an
    explicit offset. Raises ValueError on invalid values or a note without
    frontmatter; returns `text` unchanged when nothing would change.
    """
    updates: dict[str, Any] = {}
    if status is not _UNSET:
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES} or None, got {status!r}")
        updates["status"] = status
    if verified is not _UNSET:
        updates["verified"] = None if verified is None else _verified_events(verified)
    if stale_after is not _UNSET:
        updates["stale_after"] = None if stale_after is None else strict_iso8601(stale_after)

    fm, _ = parse_note(text)
    changes: dict[str, Any] = {}
    for key, value in updates.items():
        if value is None and key in fm:
            changes[key] = _DELETE
        elif value is not None and fm.get(key) != value:
            changes[key] = value
    return _with_frontmatter(text, changes) if changes else text


def _verified_events(verified: Any) -> list[dict]:
    events = [verified] if isinstance(verified, Mapping) else verified
    if not isinstance(events, list) or not events:
        raise ValueError("verified must be a {by, at} mapping or a non-empty list of them")
    out = []
    for event in events:
        by = event.get("by") if isinstance(event, Mapping) else None
        if not isinstance(by, str) or not by.strip():
            raise ValueError(f"verified event needs a `by` actor: {event!r}")
        out.append({"by": by.strip(), "at": strict_iso8601(event.get("at"))})
    return out
