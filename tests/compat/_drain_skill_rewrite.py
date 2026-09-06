"""The exact SKILL.md rewrite the drain fix applies (baseline text on the
left, this branch on the right), so the install gate can prove the
installed skill equals render(rewrite(baseline)) and nothing else."""

from __future__ import annotations

PAIRS: tuple[tuple[str, str], ...] = (
    ('4. Index globally (validates sidecar first to catch schema errors early):\n',
     '4. Index globally (validates sidecar first to catch schema errors early):\n   One command: it validates the sidecar strictly first, so a malformed\n   sidecar fails loudly here and is never indexed; then it runs `reflect add`\n   with `--force` (no y/N prompt; the content-hash doc id makes re-runs\n   idempotent) and exits non-zero if the add fails. Spell it exactly like\n   this, with the real paths in place (no shell variables, no `uv run`, no\n   `python3`: every scripted step is a `reflect skill-step` command and the\n   headless drain allows exactly that):\n'),
    ('   SIDECAR="docs/solutions/{category}/{filename}.entities.yaml"\n   DOC="docs/solutions/{category}/{filename}.md"\n   VALIDATE="{{HOME_TOOL_DIR}}/skills/reflect/scripts/validate_sidecar.py"\n\n   if command -v reflect >/dev/null 2>&1; then\n       # Validate before ingest — malformed sidecars fail loudly here, not\n       # silently at GraphRAG time\n       uv run "$VALIDATE" --strict "$SIDECAR" || {\n           echo "ERROR: sidecar validation failed for $SIDECAR" >&2\n           exit 1\n       }\n       # --force skips the interactive y/N prompt; content-hash doc_id makes\n       # the call idempotent so re-runs no-op cleanly.\n       reflect add "$DOC" --entities "$SIDECAR" --force\n   fi\n',
     '   reflect skill-step index docs/solutions/{category}/{filename}.md docs/solutions/{category}/{filename}.entities.yaml\n'),
    ('   python {{HOME_TOOL_DIR}}/skills/reflect/scripts/metrics_updater.py \\\n',
     '   reflect skill-step metrics \\\n'),
    ('   python {{HOME_TOOL_DIR}}/skills/reflect/scripts/state_manager.py status\n',
     '   reflect skill-step state status\n'),
    ('python3 {{HOME_TOOL_DIR}}/skills/reflect/scripts/reflect_cascade.py revise \\\n',
     'reflect skill-step revise \\\n'),
    ('python3 {{HOME_TOOL_DIR}}/skills/reflect/scripts/reflect_cascade.py observe \\\n',
     'reflect skill-step observe \\\n'),
    ('reflect on   -> python {{HOME_TOOL_DIR}}/skills/reflect/scripts/state_manager.py on\nreflect off  -> python {{HOME_TOOL_DIR}}/skills/reflect/scripts/state_manager.py off\n',
     'reflect on   -> reflect skill-step state on\nreflect off  -> reflect skill-step state off\n'),
)


def drain_skill_rewrite(text: str) -> str:
    for old, new in PAIRS:
        assert text.count(old) == 1, old[:60]
        text = text.replace(old, new)
    return text
