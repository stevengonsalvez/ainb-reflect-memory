"""`reflect serve` — launch the local memory browser."""

from pathlib import Path
from typing import Optional

import click


@click.command("serve")
@click.option("--host", default="127.0.0.1", show_default=True,
              help="Bind address. Keep loopback; expose via `tailscale serve` if needed.")
@click.option("--port", "-p", default=8377, show_default=True, type=int)
@click.option("--repo", type=click.Path(exists=True, file_okay=False),
              help="KB path override (default: $GLOBAL_LEARNINGS_PATH or ~/.claude/global-learnings)")
def serve_command(host: str, port: int, repo: Optional[str]):
    """Browse, search, and graph the knowledge base in a local web UI.

    Read-only: archive/compress/confidence edits stay with the CLI and
    /reflect skills until the full serve milestone lands.
    """
    from reflect_kb.serve import run

    run(host=host, port=port, repo=Path(repo) if repo else None)
