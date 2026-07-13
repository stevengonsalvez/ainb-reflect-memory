"""`reflect serve` — launch the local memory browser."""

from pathlib import Path
from typing import Optional

import click


@click.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Bind address. Loopback only — there is NO auth and curation "
                   "mutates your local KB, so do not bind a public interface.")
@click.option("--port", "-p", default=8377, show_default=True, type=int)
@click.option("--repo", type=click.Path(exists=True, file_okay=False),
              help="KB path override (default: $GLOBAL_LEARNINGS_PATH or ~/.learnings)")
def serve_command(host: str, port: int, repo: Optional[str]):
    """Browse, search, graph, and curate the knowledge base in a local web UI.

    Curation is LIVE and edits the local markdown KB: soft archive/restore,
    confidence edits, and queueing groups for /reflect compression. It edits
    metadata only (note bodies stay agent-authored) and does not rebuild the
    nano-graphrag cache — run `reflect reindex` afterward to refresh semantic
    search and the entity graph. No authentication; loopback only.
    """
    from reflect_kb.serve import run

    run(host=host, port=port, repo=Path(repo) if repo else None)
