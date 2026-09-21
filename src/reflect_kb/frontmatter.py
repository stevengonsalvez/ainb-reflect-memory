"""One frontmatter parser for every reader of a learning note.

The block opens with a line that is exactly ``---`` and closes with the next
line that is exactly ``---``; a ``---`` inside a value or inside the body is
never a delimiter. ``str.split("---", 2)`` truncated the block at the first
``---`` in a value, which dropped every key after it (a ``classification``
dropped that way read as ``internal`` and a restricted note reached the
shared store). A key given twice is malformed too: YAML lets the later value
win, so ``classification: restricted`` followed by ``classification: internal``
read as internal.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import yaml

__all__ = ["Frontmatter", "split_frontmatter", "split_frontmatter_text"]

_DELIMITER = re.compile(r"^---[ \t]*\r?$", re.MULTILINE)


class _UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader that refuses a mapping naming the same key twice."""


def _construct_unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> Any:
    seen: set[Any] = set()
    for key_node, _ in node.value:
        if key_node.tag == "tag:yaml.org,2002:merge":
            continue  # ``<<`` merges are overridden by explicit keys by design
        key = loader.construct_object(key_node, deep=True)
        try:
            duplicate = key in seen
        except TypeError:
            continue  # unhashable key: the base constructor raises its own error
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping", node.start_mark, f"found duplicate key {key!r}", key_node.start_mark
            )
        seen.add(key)
    return loader.construct_mapping(node, deep=deep)


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping)


@dataclass(frozen=True)
class Frontmatter:
    """``mapping`` is the parsed block (``{}`` for an empty block), or None
    when the note has no block or the block is not a YAML mapping;
    ``malformed`` says which of those it was. ``body`` is the text after the
    closing delimiter, or the whole text when there is no block."""

    mapping: Mapping[str, Any] | None
    body: str
    malformed: bool = False

    @property
    def present(self) -> bool:
        return self.mapping is not None


def split_frontmatter_text(text: str) -> tuple[str, str] | None:
    """(raw frontmatter text, body) split on delimiter lines, or None when the
    text does not open with a ``---`` line closed by another."""
    opening = _DELIMITER.match(text)
    if opening is None:
        return None
    closing = _DELIMITER.search(text, opening.end())
    if closing is None:
        return None
    raw = text[opening.end():closing.start()]
    body = text[closing.end():]
    if body.startswith("\r\n"):
        body = body[2:]
    elif body.startswith("\n"):
        body = body[1:]
    if raw.startswith("\r\n"):
        raw = raw[2:]
    elif raw.startswith("\n"):
        raw = raw[1:]
    return raw, body


def split_frontmatter(text: str) -> Frontmatter:
    """Parse the frontmatter block of ``text``."""
    parts = split_frontmatter_text(text)
    if parts is None:
        return Frontmatter(None, text)
    raw, body = parts
    try:
        loaded = yaml.load(raw, Loader=_UniqueKeyLoader)  # a SafeLoader subclass: no arbitrary objects
    except yaml.YAMLError:
        return Frontmatter(None, text, malformed=True)
    if loaded is None:
        return Frontmatter({}, body)
    if not isinstance(loaded, Mapping):
        return Frontmatter(None, text, malformed=True)
    return Frontmatter(loaded, body)
