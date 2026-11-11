"""Shared entry point for canonical and legacy console scripts.

Both names expose the same Click command group and operational state.
"""

from reflect_kb.cli.learnings_cli import cli as main

from reflect_kb.migration import migration_group

main.add_command(migration_group)

__all__ = ["main"]


if __name__ == "__main__":
    main()
