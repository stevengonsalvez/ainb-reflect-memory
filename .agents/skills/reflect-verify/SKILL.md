---
name: reflect-verify
description: Verify Reflect end-to-end health, especially drain, ingest/consolidate maintenance, launchd timers, statusline error surfacing, and real Claude/Codex harness smoke checks. Use when Reflect seems stuck, drain is not clearing pending reflections, ingest has gone quiet, statusline ERR needs proof, or a change needs tmux-backed validation.
---

# Reflect Verify

Run Reflect validation in layers: static checks, isolated queue/drain smoke,
live read-only health, then optional real Claude tmux smoke. Do not run full
ingest or consolidate as part of verification unless the user explicitly asks;
the watchdog only surfaces due/malfunction states.

## Workflow

1. Check source and install drift.
   - Compare repo hook files against installed harness copies and Claude plugin cache.
   - Confirm launchd labels for `com.reflect.drain`, `com.reflect.idle`, and `com.reflect.maintenance` when running on macOS.
   - Never kill tmux server or broad process names; only use exact session names created by this verification.

2. Run static gates.
   ```bash
   .agents/skills/reflect-verify/scripts/verify-reflect.sh --static
   ```

3. Run isolated drain smoke.
   ```bash
   .agents/skills/reflect-verify/scripts/verify-reflect.sh --isolated
   ```
   This uses a temp `REFLECT_STATE_DIR` and a stub Claude binary. It must prove
   timeout/no-output entries are quarantined and removed from the queue without
   touching live Reflect state.

4. Run live read-only health.
   ```bash
   .agents/skills/reflect-verify/scripts/verify-reflect.sh --live-readonly
   ```
   Report queue depth, drain lock status, latest drain activity, unacked errors,
   ingest log age, and loaded launchd labels. Do not mutate queue, errors, or KB.

5. Run maintenance watchdog smoke when statusline surfacing is in scope.
   ```bash
   REFLECT_STATE_DIR="$(mktemp -d)" \
     GLOBAL_LEARNINGS_PATH="$(mktemp -d)" \
     REFLECT_WATCH_FORCE_JSON_ERRORS=1 \
     REFLECT_WATCH_SKIP_LAUNCHD=1 \
     plugin/hooks/reflect-maintenance-watch.sh
   ```
   Verify `errors.json` receives `ingest_never_ran` or drain malfunction entries.

6. Run real Claude tmux smoke only when needed.
   ```bash
   .agents/skills/reflect-verify/scripts/verify-reflect.sh \
     --real-claude --workdir ~/d/git/ai-coder-rules
   ```
   The script creates a tmux session named `reflect-verify-<timestamp>`, runs a
   tiny `claude -p` probe, captures a log, waits with a timeout, and kills only
   that exact session if cleanup is needed.

## Done Criteria

- Static checks pass.
- Isolated drain smoke proves bad entries cannot head-of-line block the queue.
- Live read-only check explains current Reflect state with exact queue depth,
  launchd labels, and latest drain/error timestamps.
- If real Claude smoke is run, a tmux log proves `claude -p` produced a result
  or captures the exact failure mode.
- Final report lists commands and pass/fail evidence, not just conclusions.

## Safety Rules

- Do not run full `/reflect:ingest` or `/reflect:consolidate` inside automated
  smoke checks.
- Do not delete queue files. Use isolated temp state for destructive proofs.
- Do not use `tmux kill-server`, `pkill tmux`, wildcard tmux kills, or process
  name kills.
- Prefer read-only live checks unless the user explicitly asks to repair live
  state.
