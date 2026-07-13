"""Import fleet-lambda JSONL artifacts as quarantined markdown learnings.

fleet-lambda records three kinds of memory on disk:

* ``patterns.jsonl``      — reusable patterns/heuristics an agent has distilled.
* ``discoveries.jsonl``   — problem/solution pairs (plus a ``discoveries-archive.jsonl``
  of retired ones). ``retracted: true`` entries are skipped.
* corrections           — a ``corrections.md`` markdown ledger and/or a
  ``pending-corrections.jsonl`` of unprocessed correction signals.

Each entry becomes one markdown document written the same way ``reflect add``
writes docs: content-addressed filename via
:func:`~reflect_kb.cli.learnings_cli.generate_document_id` (slug + short hash of
title+body) so a re-import maps to the same file — idempotent by construction.
Every imported doc carries ``quarantine: true`` and ``authority: advisory`` so it
stays out of the claude/codex recall scope until Fleet explicitly promotes it.

Dedupe is by ``content_hash`` (full sha256 of title+body) through
:mod:`reflect_kb.fleet.ledger`: re-importing the same content increments the
occurrence count instead of erroring on the filename collision — the prior
non-TTY ``click.confirm`` footgun (silent abort) is avoided entirely; we never
prompt.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from reflect_kb.cli.learnings_cli import (
    DOCUMENTS_DIR,
    generate_document_id,
    get_repo_path,
    parse_frontmatter,
)
from reflect_kb.fleet import ledger as ledger_mod

_CATEGORY = {
    "patterns": "fleet-pattern",
    "discoveries": "fleet-discovery",
    "corrections": "fleet-correction",
}

# tag/agent substrings that steer a doc's domain away from the coding default.
_DOMAIN_KEYWORDS = {
    "personal": "personal",
    "research": "research",
    "devops": "ops",
    "infra": "ops",
    "ops": "ops",
    "security": "security",
    "writing": "writing",
}


@dataclass
class ImportResult:
    imported: int = 0
    deduped: int = 0
    skipped: int = 0
    errors: int = 0
    new_doc_ids: list[str] = field(default_factory=list)
    skipped_details: list[str] = field(default_factory=list)
    error_details: list[str] = field(default_factory=list)

    def merge(self, other: "ImportResult") -> None:
        self.imported += other.imported
        self.deduped += other.deduped
        self.skipped += other.skipped
        self.errors += other.errors
        self.new_doc_ids.extend(other.new_doc_ids)
        self.skipped_details.extend(other.skipped_details)
        self.error_details.extend(other.error_details)


def _content_hash(title: str, body: str) -> str:
    return hashlib.sha256(
        (title + "\n" + body).encode("utf-8", errors="replace")
    ).hexdigest()


def _infer_domain(tags: Iterable[Any] | None, agent: Any = None) -> str:
    hay = " ".join(str(t) for t in (tags or [])).lower()
    hay += " " + str(agent or "").lower()
    for keyword, domain in _DOMAIN_KEYWORDS.items():
        if keyword in hay:
            return domain
    return "coding"


def _clean_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _first_line(text: str, limit: int = 120) -> str:
    line = text.strip().splitlines()[0].strip() if text.strip() else ""
    return line[:limit]


def _normalize_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(t).strip() for t in raw if str(t).strip()]
    return []


@dataclass
class _Doc:
    """A parsed fleet entry ready to become a markdown learning."""

    title: str
    body: str
    key_insight: str
    tags: list[str]
    category: str
    source_kind: str
    source_path: str
    domain: str
    workflow_state: str = "open"
    supersedes: Optional[str] = None

    def content_hash(self) -> str:
        return _content_hash(self.title, self.body)

    def render(self, occurrences: int) -> str:
        fm: dict[str, Any] = {
            "title": self.title,
            "category": self.category,
            "key_insight": self.key_insight,
            "tags": self.tags,
            "source_system": "fleet",
            "source_kind": self.source_kind,
            "source_path": self.source_path,
            "content_hash": self.content_hash(),
            "authority": "advisory",
            "domain": self.domain,
            "quarantine": True,
            "workflow_state": self.workflow_state,
            "occurrences": occurrences,
        }
        if self.supersedes:
            fm["supersedes"] = self.supersedes
        front = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
        return f"---\n{front}\n---\n\n{self.body.strip()}\n"


# ── per-kind parsers ─────────────────────────────────────────────────────────


def _pattern_to_doc(entry: dict, source_path: str) -> _Doc:
    title = _clean_str(
        entry.get("title") or entry.get("name") or entry.get("pattern")
    )
    description = _clean_str(
        entry.get("description") or entry.get("pattern") or entry.get("body")
    )
    if not title:
        title = _first_line(description) or "Untitled pattern"
    key_insight = _clean_str(
        entry.get("key_insight") or entry.get("insight") or entry.get("summary")
    ) or _first_line(description) or title
    tags = _normalize_tags(entry.get("tags"))

    parts = []
    if description:
        parts.append(description)
    for label in ("rationale", "example", "context"):
        val = _clean_str(entry.get(label))
        if val:
            parts.append(f"## {label.title()}\n\n{val}")
    body = "\n\n".join(parts) or title

    return _Doc(
        title=title,
        body=body,
        key_insight=key_insight,
        tags=tags,
        category=_CATEGORY["patterns"],
        source_kind="patterns",
        source_path=source_path,
        domain=_infer_domain(tags, entry.get("agent")),
        supersedes=_clean_str(entry.get("supersedes")) or None,
    )


def _discovery_to_doc(entry: dict, source_path: str, workflow_state: str) -> _Doc:
    problem = _clean_str(entry.get("problem"))
    solution = _clean_str(entry.get("solution"))
    context = _clean_str(entry.get("context"))
    title = _clean_str(entry.get("title")) or _first_line(problem) or "Untitled discovery"
    key_insight = _clean_str(entry.get("key_insight")) or _first_line(solution) or title
    tags = _normalize_tags(entry.get("tags"))

    parts = []
    if problem:
        parts.append(f"## Problem\n\n{problem}")
    if context:
        parts.append(f"## Context\n\n{context}")
    if solution:
        parts.append(f"## Solution\n\n{solution}")
    body = "\n\n".join(parts) or title

    return _Doc(
        title=title,
        body=body,
        key_insight=key_insight,
        tags=tags,
        category=_CATEGORY["discoveries"],
        source_kind="discoveries",
        source_path=source_path,
        domain=_infer_domain(tags, entry.get("agent")),
        workflow_state=workflow_state,
        supersedes=_clean_str(entry.get("supersedes")) or None,
    )


def _correction_to_doc(entry: dict, source_path: str) -> _Doc:
    title = _clean_str(
        entry.get("title") or entry.get("correction") or entry.get("problem")
    )
    body = _clean_str(
        entry.get("body") or entry.get("correction") or entry.get("solution")
    )
    if not title:
        title = _first_line(body) or "Untitled correction"
    if not body:
        body = title
    key_insight = _clean_str(entry.get("key_insight")) or _first_line(body) or title
    tags = _normalize_tags(entry.get("tags"))

    return _Doc(
        title=title,
        body=body,
        key_insight=key_insight,
        tags=tags,
        category=_CATEGORY["corrections"],
        source_kind="corrections",
        source_path=source_path,
        domain=_infer_domain(tags, entry.get("agent")),
        supersedes=_clean_str(entry.get("supersedes")) or None,
    )


def _split_corrections_markdown(text: str) -> list[dict]:
    """Best-effort split of a corrections.md ledger into per-entry dicts.

    Entries are delimited by markdown headings (``# ...``). Anything before the
    first heading is ignored; a file with no headings becomes a single entry.
    """
    lines = text.splitlines()
    entries: list[dict] = []
    current_title: Optional[str] = None
    current_body: list[str] = []

    def flush() -> None:
        if current_title is None and not any(l.strip() for l in current_body):
            return
        title = current_title or _first_line("\n".join(current_body)) or "Untitled correction"
        body = "\n".join(current_body).strip()
        entries.append({"title": title, "body": body})

    for line in lines:
        if line.lstrip().startswith("#"):
            flush()
            current_title = line.lstrip("# ").strip()
            current_body = []
        else:
            current_body.append(line)
    flush()
    return entries


# ── source readers ───────────────────────────────────────────────────────────


def _read_jsonl(path: Path, result: ImportResult) -> Iterable[tuple[int, dict]]:
    """Yield ``(line_no, obj)`` for each valid JSON object; count malformed lines."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        result.errors += 1
        result.error_details.append(f"{path}: {exc}")
        return
    for line_no, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            result.skipped += 1
            result.skipped_details.append(f"{path}:{line_no}: malformed JSON ({exc.msg})")
            continue
        if not isinstance(obj, dict):
            result.skipped += 1
            result.skipped_details.append(f"{path}:{line_no}: not a JSON object")
            continue
        yield line_no, obj


def _iter_docs(root: Path, kinds: list[str], result: ImportResult) -> Iterable[_Doc]:
    if "patterns" in kinds:
        path = root / "patterns.jsonl"
        if path.exists():
            for line_no, obj in _read_jsonl(path, result):
                try:
                    yield _pattern_to_doc(obj, str(path))
                except Exception as exc:  # noqa: BLE001 — one bad entry must not abort the run
                    result.skipped += 1
                    result.skipped_details.append(f"{path}:{line_no}: {exc}")

    if "discoveries" in kinds:
        for filename, state in (
            ("discoveries.jsonl", "open"),
            ("discoveries-archive.jsonl", "archived"),
        ):
            path = root / filename
            if not path.exists():
                continue
            for line_no, obj in _read_jsonl(path, result):
                if obj.get("retracted") is True:
                    result.skipped += 1
                    result.skipped_details.append(f"{path}:{line_no}: retracted")
                    continue
                try:
                    yield _discovery_to_doc(obj, str(path), state)
                except Exception as exc:  # noqa: BLE001
                    result.skipped += 1
                    result.skipped_details.append(f"{path}:{line_no}: {exc}")

    if "corrections" in kinds:
        md_path = root / "corrections.md"
        if md_path.exists():
            try:
                for obj in _split_corrections_markdown(md_path.read_text(encoding="utf-8")):
                    yield _correction_to_doc(obj, str(md_path))
            except OSError as exc:
                result.errors += 1
                result.error_details.append(f"{md_path}: {exc}")
        jsonl_path = root / "pending-corrections.jsonl"
        if jsonl_path.exists():
            for line_no, obj in _read_jsonl(jsonl_path, result):
                try:
                    yield _correction_to_doc(obj, str(jsonl_path))
                except Exception as exc:  # noqa: BLE001
                    result.skipped += 1
                    result.skipped_details.append(f"{jsonl_path}:{line_no}: {exc}")


# ── write path ───────────────────────────────────────────────────────────────


def _bump_occurrences(dest: Path, occurrences: int) -> None:
    """Rewrite the ``occurrences`` frontmatter field on an existing doc.

    The dedupe key (``content_hash``) is over title+body only, so touching a
    frontmatter field never changes the hash or the filename.
    """
    try:
        content = dest.read_text(encoding="utf-8")
        frontmatter, body = parse_frontmatter(content)
    except OSError:
        return
    if not frontmatter:
        return
    frontmatter["occurrences"] = occurrences
    front = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=True).strip()
    dest.write_text(f"---\n{front}\n---\n\n{body.strip()}\n", encoding="utf-8")


def _write_sidecar(dest: Path, content: str, frontmatter: dict) -> None:
    """Best-effort entity sidecar, mirroring the ``reflect add`` auto path."""
    try:
        from reflect_kb.cli.entity_store import auto_extract_entities, write_sidecar

        doc_entities = auto_extract_entities(content, frontmatter)
        if doc_entities.entity_count > 0:
            write_sidecar(dest, doc_entities)
    except Exception:  # noqa: BLE001 — sidecar generation is never fatal
        pass


def ingest(
    root: str | Path,
    kinds: list[str],
    *,
    dry_run: bool = False,
    ledger_file: Optional[Path] = None,
) -> ImportResult:
    """Import every ``kinds`` artifact under ``root`` into the KB.

    ``dry_run`` parses and classifies but writes nothing (no files, no ledger,
    no metrics). Returns an :class:`ImportResult` the CLI renders as a summary.
    """
    root = Path(root)
    result = ImportResult()
    docs_dir = get_repo_path() / DOCUMENTS_DIR
    if not dry_run:
        docs_dir.mkdir(parents=True, exist_ok=True)

    for doc in _iter_docs(root, kinds, result):
        try:
            doc_id = generate_document_id(doc.title, doc.body)
            dest = docs_dir / f"{doc_id}.md"
            hash_ = doc.content_hash()

            if dry_run:
                # Existence check only — a filename collision means the same
                # content is already imported (dedupe), not an error.
                if dest.exists():
                    result.deduped += 1
                else:
                    result.imported += 1
                    result.new_doc_ids.append(doc_id)
                continue

            entry = ledger_mod.record_occurrence(hash_, doc_id, path=ledger_file)
            occurrences = int(entry.get("count", 1))

            if dest.exists():
                _bump_occurrences(dest, occurrences)
                result.deduped += 1
            else:
                content = doc.render(occurrences)
                dest.write_text(content, encoding="utf-8")
                frontmatter, _ = parse_frontmatter(content)
                _write_sidecar(dest, content, frontmatter or {})
                result.imported += 1
                result.new_doc_ids.append(doc_id)
        except Exception as exc:  # noqa: BLE001 — never let one doc abort the batch
            result.errors += 1
            result.error_details.append(f"{doc.source_path}: {exc}")

    return result
