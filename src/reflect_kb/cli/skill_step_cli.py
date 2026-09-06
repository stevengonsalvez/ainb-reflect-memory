"""``reflect skill-step``: the one command surface for the /reflect skill's
scripted steps.

The skill's steps used to be spelled ``python3 <path>/<script>.py``. The
path differed per layout (plugin runtime, adapter install, a symlinked
home), so the headless drain's allow rules had to name every spelling and a
step under any other spelling was denied. Every step is now one command,
``reflect skill-step <step> ...``, with no path in it: the rules carry one
prefix and the skill text carries no directory.

Every step except ``index`` delegates to the plugin's stdlib script, found
through REFLECT_SKILL_SCRIPTS_DIR (the drain exports it), CLAUDE_PLUGIN_ROOT,
an adapter install under the home, or the plugin cache. ``index`` runs in
this process: it validates the sidecar strictly, runs ``reflect add
--force``, and appends one receipt line when REFLECT_DRAIN_RECEIPT names a
file, so the drain counts what was indexed from the receipt instead of file
mtimes.
"""

from __future__ import annotations

import glob
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import click

# step name -> (script, argv prefix the script needs before the user's args)
STEPS: dict[str, tuple[str, tuple[str, ...]]] = {
    "validate-sidecar": ("validate_sidecar.py", ()),
    "metrics": ("metrics_updater.py", ()),
    "state": ("state_manager.py", ()),
    "revise": ("reflect_cascade.py", ("revise",)),
    "observe": ("reflect_cascade.py", ("observe",)),
}
_PROBE = "validate_sidecar.py"


def _candidates() -> Iterator[Path]:
    explicit = os.environ.get("REFLECT_SKILL_SCRIPTS_DIR")
    if explicit:
        yield Path(explicit).expanduser()
    root = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if root:
        yield Path(root) / "scripts"
    home = Path.home()
    for harness in (".claude", ".codex", ".copilot", ".hermes"):
        yield home / harness / "skills" / "reflect" / "scripts"
    cache = home / ".claude" / "plugins" / "cache"
    for pattern in ("*/reflect/*/plugin/scripts", "*/reflect/*/scripts"):
        for found in sorted(glob.glob(str(cache / pattern)), reverse=True):
            yield Path(found)
    yield Path(__file__).resolve().parents[3] / "plugin" / "scripts"  # a checkout


def scripts_dir() -> Path | None:
    """The directory holding the skill's scripts, or None when no layout has them."""
    for candidate in _candidates():
        if (candidate / _PROBE).is_file():
            return candidate
    return None


def _require_scripts_dir() -> Path:
    found = scripts_dir()
    if found is None:
        raise click.ClickException(
            "the reflect skill's scripts were not found; set REFLECT_SKILL_SCRIPTS_DIR "
            "(or CLAUDE_PLUGIN_ROOT) to the directory holding validate_sidecar.py"
        )
    return found


def write_receipt(note: Path, sidecar: Path, doc_id: str) -> None:
    """One JSON line per indexed note, when the drain asked for a receipt."""
    receipt = os.environ.get("REFLECT_DRAIN_RECEIPT")
    if not receipt:
        return
    line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "note": str(note.resolve()),
            "sidecar": str(sidecar.resolve()), "doc_id": doc_id}
    with open(receipt, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(line) + "\n")


@click.group("skill-step", context_settings={"help_option_names": ["-h", "--help"]})
def skill_step() -> None:
    """The /reflect skill's scripted steps, one command each (no paths).

    validate-sidecar, metrics, state, revise and observe pass their
    arguments to the skill's script; index validates then runs reflect add.
    """


def _delegate(step: str, args: tuple[str, ...]) -> None:
    script, prefix = STEPS[step]
    directory = _require_scripts_dir()
    proc = subprocess.run([sys.executable, str(directory / script), *prefix, *args], check=False)
    sys.exit(proc.returncode)


def _make_step(step: str) -> None:
    @skill_step.command(step, context_settings={"ignore_unknown_options": True, "allow_extra_args": True,
                                                 "help_option_names": []})
    @click.argument("args", nargs=-1, type=click.UNPROCESSED)
    def _cmd(args: tuple[str, ...]) -> None:
        _delegate(step, args)

    _cmd.__doc__ = f"Run the skill's {STEPS[step][0]} {' '.join(STEPS[step][1])} with the given arguments."


for _step in STEPS:
    _make_step(_step)


@skill_step.command("index")
@click.argument("note", type=click.Path(exists=True, dir_okay=False))
@click.argument("sidecar", type=click.Path(exists=True, dir_okay=False))
@click.pass_context
def index(ctx: click.Context, note: str, sidecar: str) -> None:
    """Validate SIDECAR strictly, then index NOTE with reflect add --force.

    A malformed sidecar stops here with a non-zero exit and nothing is
    indexed. The add runs in this process (never a second reflect looked up
    on PATH), so "written but not indexed" cannot happen silently: a failed
    add is a non-zero exit. With REFLECT_DRAIN_RECEIPT set, one JSON line
    per indexed note is appended for the drain's ledger.
    """
    from reflect_kb.cli.learnings_cli import add as add_command
    from reflect_kb.cli.learnings_cli import generate_document_id, parse_frontmatter

    directory = _require_scripts_dir()
    rc = subprocess.run([sys.executable, str(directory / _PROBE), "--strict", sidecar], check=False).returncode
    if rc != 0:
        raise click.ClickException(f"sidecar validation failed for {sidecar}; not indexed")
    ctx.invoke(add_command, file_path=note, entities=sidecar, force=True)
    # add rewrote the note in place if it held a secret; the id is the one it stored.
    frontmatter, body = parse_frontmatter(Path(note).read_text(encoding="utf-8"))
    doc_id = generate_document_id(str(frontmatter.get("title", "")), body)
    write_receipt(Path(note), Path(sidecar), doc_id)
