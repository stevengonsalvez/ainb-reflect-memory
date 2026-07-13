#!/usr/bin/env python3
"""``reflect fleet`` — import fleet-lambda memory into the knowledge base.

Subcommands:
    reflect fleet ingest --root PATH [--kinds patterns,discoveries,corrections]
                         [--dry-run] [--no-reindex]
    reflect fleet status     # occurrence-ledger stats

Imported docs are quarantined (kept out of claude/codex recall scope) until
Fleet promotes them. The importer writes every file, then triggers ONE full
reindex — per-doc incremental indexing fragments graph communities (see the
``reindex`` batching note in ``learnings_cli``).
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from reflect_kb.fleet import importer as importer_mod
from reflect_kb.fleet import ledger as ledger_mod

console = Console(stderr=True)

_ALL_KINDS = ("patterns", "discoveries", "corrections")


def _parse_kinds(raw: str) -> list[str]:
    kinds = [k.strip() for k in raw.split(",") if k.strip()]
    bad = [k for k in kinds if k not in _ALL_KINDS]
    if bad:
        raise click.BadParameter(
            f"unknown kind(s): {', '.join(bad)}; choose from {', '.join(_ALL_KINDS)}"
        )
    return kinds or list(_ALL_KINDS)


@click.group("fleet")
def fleet_group():
    """Import fleet-lambda patterns/discoveries/corrections as quarantined learnings."""


@fleet_group.command("ingest")
@click.option(
    "--root",
    required=True,
    type=click.Path(exists=True, file_okay=False, path_type=str),
    help="Directory holding fleet-lambda JSONL artifacts.",
)
@click.option(
    "--kinds",
    default=",".join(_ALL_KINDS),
    help="Comma-separated subset of patterns,discoveries,corrections.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Parse and classify but write nothing (no files, ledger, or reindex).",
)
@click.option(
    "--no-reindex",
    is_flag=True,
    default=False,
    help="Skip the post-import graph reindex (useful for tests / batching).",
)
def ingest(root: str, kinds: str, dry_run: bool, no_reindex: bool):
    """Import fleet-lambda artifacts under --root into the knowledge base."""
    kind_list = _parse_kinds(kinds)

    result = importer_mod.ingest(root, kind_list, dry_run=dry_run)

    table = Table(title="fleet ingest" + (" (dry-run)" if dry_run else ""))
    table.add_column("metric")
    table.add_column("count", justify="right")
    table.add_row("imported", str(result.imported))
    table.add_row("deduped", str(result.deduped))
    table.add_row("skipped", str(result.skipped))
    table.add_row("errors", str(result.errors))
    console.print(table)

    for detail in result.skipped_details:
        console.print(f"[yellow]skipped:[/yellow] {detail}")
    for detail in result.error_details:
        console.print(f"[red]error:[/red] {detail}")

    if not dry_run and not no_reindex and (result.imported or result.deduped):
        console.print("[bold]Reindexing knowledge base…[/bold]")
        from reflect_kb.cli.learnings_cli import reindex

        reindex.callback(force=False)

    if result.errors:
        raise SystemExit(1)


@fleet_group.command("status")
def status():
    """Print occurrence-ledger stats (documents, occurrences, promotion candidates)."""
    stats = ledger_mod.stats()
    table = Table(title="fleet ledger")
    table.add_column("metric")
    table.add_column("count", justify="right")
    table.add_row("documents", str(stats["documents"]))
    table.add_row("occurrences", str(stats["occurrences"]))
    table.add_row("promotion candidates", str(stats["promotion_candidates"]))
    console.print(table)
    console.print(f"[dim]ledger: {ledger_mod.ledger_path()}[/dim]")
