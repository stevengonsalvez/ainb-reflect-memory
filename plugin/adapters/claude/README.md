# Claude Code adapter for reflect-kb (uninstall only)

`install` is **refused**. Claude Code installs reflect through its native
plugin runtime, which deploys the hook scripts and wires every hook from
`plugin/.claude-plugin/plugin.json`:

```bash
claude plugin marketplace add stevengonsalvez/ainb-reflect-memory
claude plugin install reflect@ainb-reflect-memory
```

`python claude_adapter.py install` (with any flags, including `--dry-run`)
prints those commands to stderr, exits 2 and writes nothing.

## Why

Earlier versions copied each `SKILL.md` into `~/.claude/skills/<name>/` and
merged a `SessionStart` entry into `~/.claude/settings.json` pointing at
`~/.claude/skills/recall/hooks/session_start_recall.py`, a file the adapter
never deployed. The entry was a dead hook that failed on every session start.

## Uninstall (cleanup of old installs)

```bash
python claude_adapter.py uninstall             # skills + hook entry
python claude_adapter.py uninstall --no-hooks  # leave settings.json alone
```

Removes only what the old adapter wrote: `SKILL.md` files carrying
`managed_by: reflect-kb/adapters/claude`, and `SessionStart` hook entries whose
command exactly matches the one it generated (including the legacy
`{{HOME_TOOL_DIR}}` literal). Hand-written skills, other files in the skill
dirs, foreign hooks and unrelated settings are left untouched. Invalid
`settings.json` is skipped, never overwritten. Safe to re-run.

Use `--home /tmp/...` to exercise a throwaway HOME.
