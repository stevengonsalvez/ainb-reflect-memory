"""OKF v0.2 profile for reflect learning notes: the stdlib-only core.

Every learning note reflect writes is an Open Knowledge Format v0.2 concept
(OKF SPEC §4.1, §5, §11): `type` is always present, OKF's recommended keys
(`title`, `description`) and its trust/provenance/lifecycle families
(`generated`, `sources`, `stale_after`) are derived from reflect's own fields,
and every reflect key rides along untouched as an OKF extension key.

VENDORED: plugin/scripts/okf_profile.py is a byte-identical copy, because the
plugin hooks run under `uv run --script` with no dependencies and cannot import
reflect_kb (or even PyYAML). Edit this file, then copy it over;
tests/test_okf_profile.py fails while the two differ.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

OKF_VERSION = "0.2"
DEFAULT_TYPE = "learning"
STATUSES = ("draft", "stable", "deprecated")
# Rule S9 carve-out: the ONLY frontmatter keys that may be rewritten after write.
LIFECYCLE_KEYS = ("status", "verified", "stale_after")
DESCRIPTION_MAX = 200

# reflect's own type names. Any casing of these collapses to lowercase
# (`LEARNING` -> `learning`); every other value is a producer-defined OKF type
# and passes through untouched.
_REFLECT_TYPES = frozenset({"learning", "observation"})

# Python 3.9 compatible on purpose: the drain runs drain_extract.py (which
# imports this vendored copy) with the system `python3`, 3.9 on macOS.
# No `datetime.UTC` (3.11+), and `fromisoformat` there rejects a trailing `Z`.
_UTC = timezone.utc  # noqa: UP017


# ---------------------------------------------------------------------------
# Small value helpers
# ---------------------------------------------------------------------------


def actor(producer: str, version: str | None) -> str:
    """OKF actor for a tool (SPEC §7): `<producer>/<version>`."""
    return f"{producer}/{version or 'unknown'}"


def manifest_version(plugin_root: Path) -> str | None:
    """The reflect plugin's version from its manifest, None when unreadable."""
    for manifest in (plugin_root / ".claude-plugin" / "plugin.json", plugin_root / "plugin.json"):
        try:
            version = json.loads(manifest.read_text(encoding="utf-8")).get("version")
        except (OSError, ValueError, AttributeError):
            continue
        if isinstance(version, str) and version.strip():
            return version.strip()
    return None


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _parse_instant(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=_UTC)
    if isinstance(value, str) and value.strip():
        try:
            text = value.strip()
            if text[-1:] in ("Z", "z"):
                text = text[:-1] + "+00:00"
            return datetime.fromisoformat(text)
        except ValueError:
            return None
    return None


def _format_instant(dt: datetime) -> str:
    out = dt.isoformat()
    return out[:-6] + "Z" if out.endswith("+00:00") else out


def to_iso8601(value: Any) -> str | None:
    """ISO 8601 instant with an explicit offset, or None when unparseable.

    Legacy reflect timestamps are often naive (`2026-04-24`, a bare datetime);
    those are taken as UTC so the result always carries an offset (SPEC §5).
    """
    dt = _parse_instant(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return _format_instant(dt)


def strict_iso8601(value: Any) -> str:
    """Like :func:`to_iso8601` but refuses naive input instead of assuming UTC."""
    dt = _parse_instant(value)
    if dt is None or isinstance(value, date) and not isinstance(value, datetime):
        raise ValueError(f"not an ISO 8601 datetime: {value!r}")
    if dt.tzinfo is None:
        raise ValueError(f"timestamp needs an explicit UTC offset: {value!r}")
    return _format_instant(dt)


def has_offset(value: Any) -> bool:
    try:
        strict_iso8601(value)
    except ValueError:
        return False
    return True


def one_line(text: Any, cap: int = DESCRIPTION_MAX) -> str:
    """Single line, capped. Runs of 3+ hyphens collapse to one so a summary can
    never look like a `---` frontmatter delimiter to a text-splitting parser."""
    flat = re.sub(r"-{3,}", "-", " ".join(str(text or "").split()))
    return flat if len(flat) <= cap else flat[: cap - 3].rstrip() + "..."


def _first_sentence(body: str) -> str:
    for line in (body or "").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "```", "---", "|")):
            continue
        idx = line.find(". ")
        return line[: idx + 1] if idx != -1 else line
    return ""


def _first_heading(body: str) -> str:
    m = re.search(r"^#{1,6}\s+(.+?)\s*$", body or "", re.MULTILINE)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# Deriving the OKF keys from a reflect learning
# ---------------------------------------------------------------------------


def derive_sources(fm: Mapping[str, Any]) -> list[dict]:
    """OKF `sources[]` (SPEC §5.1) from reflect's provenance fields."""
    prov = fm.get("provenance")
    prov = prov if isinstance(prov, Mapping) else {}
    path = fm.get("source_path") or prov.get("source_path")
    if not _blank(path):
        return [{"id": "source", "resource": str(path).strip()}]
    session = fm.get("session_id") or prov.get("session_id")
    if not _blank(session):
        return [{"id": "session", "resource": f"session:{str(session).strip()}"}]
    return []


def okf_additions(
    fm: Mapping[str, Any], body: str = "", *, actor: str, now: Any = None,
) -> dict:
    """The OKF keys a note is missing (or must normalize), and nothing else.

    Never touches a reflect key: the only existing key it may return is
    `type`, when its casing needs normalizing. `now` (e.g. the source file's
    mtime) stamps `generated.at` only when the note has no `created` or
    `captured_at`; with neither, `at` is omitted.
    """
    add: dict[str, Any] = {}
    kind = fm.get("type")
    if not isinstance(kind, str) or not kind.strip():
        add["type"] = DEFAULT_TYPE
    elif kind.strip().lower() in _REFLECT_TYPES and kind != kind.strip().lower():
        add["type"] = kind.strip().lower()

    title = fm.get("title")
    if _blank(title):
        title = (fm.get("name") or _first_heading(body) or fm.get("id") or "untitled")
        add["title"] = one_line(title)

    if _blank(fm.get("description")):
        desc = one_line(fm.get("key_insight") or _first_sentence(body) or title)
        if desc:
            add["description"] = desc

    generated = fm.get("generated")
    if not (isinstance(generated, Mapping) and not _blank(generated.get("by"))):
        at = (to_iso8601(fm.get("created")) or to_iso8601(fm.get("captured_at"))
              or to_iso8601(now))
        # `at` is optional (SPEC §5.2). Omitted rather than stamped with the
        # wall clock so re-normalizing the same note yields identical bytes.
        add["generated"] = {"by": actor, "at": at} if at else {"by": actor}

    if "sources" not in fm:
        sources = derive_sources(fm)
        if sources:
            add["sources"] = sources

    if _blank(fm.get("stale_after")) and not _blank(fm.get("forget_after")):
        stale = to_iso8601(fm.get("forget_after"))
        if stale:
            add["stale_after"] = stale
    return add


def to_okf(fm: Mapping[str, Any], body: str = "", *, actor: str, now: Any = None) -> dict:
    """A new frontmatter dict: `type` first, every reflect key kept, OKF keys added."""
    merged = {**fm, **okf_additions(fm, body, actor=actor, now=now)}
    return {"type": merged.pop("type"), **merged}


# ---------------------------------------------------------------------------
# Conformance
# ---------------------------------------------------------------------------


def _as_list(value: Any) -> list:
    # SPEC §5.2: a bare `verified` mapping is a one-element list.
    return [value] if isinstance(value, Mapping) else value if isinstance(value, list) else [None]


def okf_problems(fm: Any) -> list[str]:
    """Why a parsed frontmatter block is not an OKF v0.2 concept ([] = conformant).

    Exactly SPEC §11: a mapping with a non-empty `type`. Everything about the
    optional families is soft guidance there (consumers MUST NOT reject on
    it), so it is reported by :func:`okf_warnings` instead.
    """
    if not isinstance(fm, Mapping):
        return ["frontmatter is not a mapping"]
    kind = fm.get("type")
    if not isinstance(kind, str) or not kind.strip():
        return ["missing non-empty `type`"]
    return []


def okf_warnings(fm: Any) -> list[str]:
    """Deviations from the §5 family rules a producer SHOULD follow.

    Includes an unknown `status` value (reflect observations use
    `active|retired`); none of these make a note non-conformant.
    """
    if not isinstance(fm, Mapping):
        return []
    problems = []
    if "sources" in fm:
        sources = fm["sources"]
        if not isinstance(sources, list) or not all(
            isinstance(s, Mapping) and not _blank(s.get("resource")) for s in sources
        ):
            problems.append("every `sources` entry needs a `resource`")
    if "generated" in fm:
        gen = fm["generated"]
        if not isinstance(gen, Mapping) or _blank(gen.get("by")):
            problems.append("`generated.by` is required")
        elif "at" in gen and not has_offset(gen["at"]):
            problems.append("`generated.at` needs an ISO 8601 offset")
    if "verified" in fm:
        for event in _as_list(fm["verified"]):
            if not (isinstance(event, Mapping) and not _blank(event.get("by"))
                    and has_offset(event.get("at"))):
                problems.append("each `verified` event needs `by` and an offset `at`")
                break
    if "status" in fm and fm["status"] not in STATUSES:
        problems.append(f"`status` must be one of {STATUSES}")
    if not _blank(fm.get("stale_after")) and not has_offset(fm["stale_after"]):
        problems.append("`stale_after` needs an ISO 8601 offset")
    return problems


def is_okf_conformant(fm: Any) -> bool:
    return not okf_problems(fm)


# ---------------------------------------------------------------------------
# Dependency-free YAML emission (one line per key, flow style for collections)
# ---------------------------------------------------------------------------

# Strings safe to emit unquoted: identifier-like, so YAML cannot read them as a
# number, timestamp, bool, null or structure. Anything else is double-quoted.
_PLAIN = re.compile(r"^[A-Za-z_][A-Za-z0-9_./-]*$")
_YAML_WORDS = frozenset({"y", "n", "yes", "no", "on", "off", "true", "false", "null"})
# Characters JSON leaves raw that YAML would fold as line breaks or reject as
# non-printable inside a double-quoted scalar; escape them explicitly.
_YAML_UNSAFE = re.compile("[\x7f-\x9f  \ud800-\udfff﻿￾￿]")


def _quoted(text: str) -> str:
    raw = json.dumps(text, ensure_ascii=False)
    return _YAML_UNSAFE.sub(lambda m: f"\\u{ord(m.group()):04x}", raw)


def yaml_value(value: Any) -> str:
    """Render a value as a single-line YAML scalar or flow collection."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return _quoted(str(value))
        text = repr(value)
        # YAML 1.1 (PyYAML) reads `1e-09` as a string; it needs `1.0e-09`.
        return text.replace("e", ".0e") if "e" in text and "." not in text else text
    if isinstance(value, (datetime, date)):
        return _quoted(value.isoformat())
    if isinstance(value, Mapping):
        return "{" + ", ".join(
            f"{yaml_value(str(k))}: {yaml_value(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(yaml_value(v) for v in value) + "]"
    text = str(value)
    if _PLAIN.match(text) and text.lower() not in _YAML_WORDS:
        return text
    return _quoted(text)


def render_frontmatter(fm: Mapping[str, Any]) -> str:
    """`---` block (trailing newline included) for a whole frontmatter dict."""
    lines = [f"{yaml_value(str(k))}: {yaml_value(v)}" for k, v in fm.items()]
    return "---\n" + "".join(line + "\n" for line in lines) + "---\n"
