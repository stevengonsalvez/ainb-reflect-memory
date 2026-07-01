# GitHub Copilot adapter for reflect-kb

Installer that plugs the reflect plugin into Copilot's `~/.copilot/` layout.
Sister to the Claude and Codex adapters. Since the GitHub Copilot CLI grew a
lifecycle hook system (GA Feb 2026), this adapter has full hook parity: it
deploys the plugin's full skill content into `~/.copilot/skills/` and writes a
copilot-native drop-in hooks file `~/.copilot/hooks/reflect.json`.

Copilot's hook format differs from Claude/Codex, so the drop-in is **native**,
not Claude-shaped:

- `{"version": 1, "hooks": { "<event>": [ {"type":"command","command":"…","timeoutSec":N} ] }}`
- FLAT per-event arrays (no `{matcher, hooks:[…]}` nesting), camelCase event
  names, `timeoutSec` (not `timeout`), top-level `version: 1`.
- Events wired: `sessionStart` (recall + bg-drain),
  `userPromptSubmitted`, `preToolUse`, `permissionRequest`, `postToolUse`,
  `postToolUseFailure`, `notification`, `preCompact`, `subagentStart`,
  `subagentStop`, `agentStop`, `sessionEnd`, and `errorOccurred`.

Copilot per-prompt recall is automatic on current CLI builds. Verified on
GitHub Copilot CLI 1.0.66: `userPromptSubmitted` hook `additionalContext` is
included in model-visible `transformedContent`. If an older build drops that
output, `/recall` remains the manual fallback.
`postCompact` is intentionally not wired until Copilot exposes that event; on
Claude/Codex it is bookkeeping only and never drains or recalls.
The `sessionStart` and `userPromptSubmitted` `additionalContext` envelopes are
confirmed against the live binary; the scripts gate their output shape on
`REFLECT_HARNESS=copilot`.

## Usage

```bash
python copilot_adapter.py install --dry-run    # preview the plan
python copilot_adapter.py install              # deploy skills + write ~/.copilot/hooks/reflect.json
python copilot_adapter.py install --no-hooks   # skills only, skip the hooks drop-in
python copilot_adapter.py install --no-bg-drain # wire recall but skip the SessionStart bg-drain
python copilot_adapter.py uninstall            # remove reflect.json + adapter-managed skills only
```

A `managed_by: reflect-kb/adapters/copilot` sentinel keeps `uninstall` from
touching hand-written sibling skills; the `reflect.json` drop-in is owned
wholesale by the adapter and removed on uninstall, leaving any other
`~/.copilot/hooks/*.json` untouched.
