"""Resolve product environment aliases without mutating process state.

Explicit PLUGLY_REFLECT_MEMORY_* values win, including empty strings. Legacy
REFLECT_* settings remain readable, and storage paths retain existing defaults.
Keep this stdlib-only file identical to the standalone plugin copy.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import TypeVar

LEGACY_PREFIX = "REFLECT_"
CANONICAL_PREFIX = "PLUGLY_REFLECT_MEMORY_"
_Default = TypeVar("_Default")


def canonical_name(name: str) -> str:
    """Return canonical alias, leaving unrelated environment names unchanged."""
    if name.startswith(LEGACY_PREFIX):
        return CANONICAL_PREFIX + name[len(LEGACY_PREFIX):]
    return name


def getenv(name: str, default: _Default = None, *, environ: Mapping[str, str] | None = None) -> str | _Default:
    """Read current environment on every call; canonical presence wins."""
    source = os.environ if environ is None else environ
    canonical = canonical_name(name)
    if canonical in source:
        return source[canonical]
    return source.get(name, default)


def subprocess_env(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """Copy environment and expose both spellings to older child processes."""
    source = os.environ if environ is None else environ
    resolved = dict(source)
    for name, value in source.items():
        if name.startswith(LEGACY_PREFIX):
            resolved.setdefault(canonical_name(name), value)
    for name, value in tuple(resolved.items()):
        if name.startswith(CANONICAL_PREFIX):
            resolved[LEGACY_PREFIX + name[len(CANONICAL_PREFIX):]] = value
    return resolved


if __name__ == "__main__":
    import argparse
    import re
    import shlex

    parser = argparse.ArgumentParser(description="Export compatible product environment names")
    parser.add_argument("--shell", action="store_true", required=True)
    parser.parse_args()
    for key, value in sorted(subprocess_env().items()):
        if key.startswith((LEGACY_PREFIX, CANONICAL_PREFIX)) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            print(f"export {key}={shlex.quote(value)}")
