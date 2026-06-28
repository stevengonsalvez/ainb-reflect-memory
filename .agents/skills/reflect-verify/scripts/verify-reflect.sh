#!/usr/bin/env bash
# Reflect verification harness. Runs static, isolated, live read-only, and
# optional real-Claude tmux smoke checks without mutating live Reflect state
# unless an explicit live option is requested.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
find_repo_root() {
  local dir="$SCRIPT_DIR"
  while [[ "$dir" != "/" ]]; do
    if [[ -f "$dir/plugin/hooks/reflect-drain-bg.sh" ]]; then
      printf '%s\n' "$dir"
      return 0
    fi
    dir="$(dirname "$dir")"
  done
  return 1
}
REPO_ROOT="$(find_repo_root)" || {
  printf '[reflect-verify] FAIL: could not find repo root from %s\n' "$SCRIPT_DIR" >&2
  exit 1
}
PLUGIN_ROOT="${REPO_ROOT}/plugin"
DRAIN="${PLUGIN_ROOT}/hooks/reflect-drain-bg.sh"
WATCH="${PLUGIN_ROOT}/hooks/reflect-maintenance-watch.sh"
IDLE="${PLUGIN_ROOT}/hooks/idle_reflect.sh"

usage() {
  cat <<'EOF'
Usage: verify-reflect.sh [--static] [--isolated] [--live-readonly] [--real-claude] [--workdir DIR] [--all]

  --static        Syntax + focused Python compile checks.
  --isolated      Temp-state drain smoke with stub Claude.
  --live-readonly Read live queue/log/error/launchd state only.
  --real-claude   Run a tiny claude -p probe inside an exact tmux session.
  --workdir DIR   Workdir for --real-claude (default: ~/d/git/ai-coder-rules).
  --all           Run --static --isolated --live-readonly.
EOF
}

say() { printf '[reflect-verify] %s\n' "$*"; }
fail() { printf '[reflect-verify] FAIL: %s\n' "$*" >&2; exit 1; }

_to() {
  if command -v timeout >/dev/null 2>&1; then timeout "$@"
  elif command -v gtimeout >/dev/null 2>&1; then gtimeout "$@"
  else shift; "$@"; fi
}

run_static() {
  say "static: shell syntax"
  bash -n "$DRAIN" || return 1
  bash -n "$WATCH" || return 1
  bash -n "$IDLE" || return 1
  bash -n "$0" || return 1

  say "static: python compile"
  python3 -m py_compile \
    "${PLUGIN_ROOT}/scripts/reflect_gate.py" \
    "${PLUGIN_ROOT}/scripts/output_classifier.py" \
    "${PLUGIN_ROOT}/scripts/quota_store.py" \
    "${PLUGIN_ROOT}/scripts/memory_discovery.py" || return 1
}

write_signal_transcript() {
  local path="$1"
  mkdir -p "$(dirname "$path")"
  python3 - "$path" <<'PY'
import json, sys
path = sys.argv[1]
rows = [
    {"message": {"role": "user", "content": "No, never use var here. The root cause was a missing index."}},
    {"message": {"role": "assistant", "content": "Understood. I will use const and add the index."}},
]
with open(path, "w", encoding="utf-8") as fh:
    for row in rows:
        fh.write(json.dumps(row) + "\n")
PY
}

run_isolated() {
  local tmp state bin transcript
  tmp="$(mktemp -d "${TMPDIR:-/tmp}/reflect-verify.XXXXXX")" || return 1
  state="${tmp}/state"
  bin="${tmp}/bin"
  transcript="${tmp}/session.jsonl"
  mkdir -p "$state" "$bin"

  cat > "${bin}/claude-stub" <<'SH'
#!/usr/bin/env bash
exit 124
SH
  chmod +x "${bin}/claude-stub"
  write_signal_transcript "$transcript"
  printf '{"ts":"2026-01-01T00:00:00Z","session_id":"s1","transcript_path":"%s","trigger":"stop","cwd":"/tmp"}\n' "$transcript" \
    > "${state}/pending_reflections.jsonl"

  say "isolated: timeout entry quarantines and leaves no queue head block"
  REFLECT_STATE_DIR="$state" \
  REFLECT_DRAIN_NO_DELEGATE=1 \
  REFLECT_DRAIN_CLAUDE_BIN="${bin}/claude-stub" \
  REFLECT_DRAIN_CASCADE=0 \
  REFLECT_DRAIN_MAX=1 \
  REFLECT_DRAIN_TIMEOUT=1 \
  REFLECT_DRAIN_TIMEOUT_RETRIES=1 \
  REFLECT_DRAIN_DEBOUNCE_SEC=0 \
  REFLECT_DRAIN_SKIP_REINDEX=1 \
  REFLECT_QUOTA_GATE=0 \
  "$DRAIN" >/dev/null 2>&1 || return 1

  if [[ -s "${state}/pending_reflections.jsonl" ]]; then
    cat "${state}/drain.log" >&2
    return 1
  fi
  rg -q '"outcome": "poison_timeout_exit_124"' "${state}/drain-cost.jsonl" || {
    cat "${state}/drain-cost.jsonl" >&2
    return 1
  }

  say "isolated: maintenance watchdog writes statusline-visible errors"
  local watch_state learnings
  watch_state="${tmp}/watch-state"
  learnings="${tmp}/learnings"
  mkdir -p "$watch_state" "$learnings"
  REFLECT_STATE_DIR="$watch_state" \
  GLOBAL_LEARNINGS_PATH="$learnings" \
  REFLECT_WATCH_FORCE_JSON_ERRORS=1 \
  REFLECT_WATCH_SKIP_LAUNCHD=1 \
  "$WATCH" >/dev/null 2>&1 || return 1
  rg -q '"kind": "ingest_never_ran"' "${watch_state}/errors.json" || {
    cat "${watch_state}/errors.json" >&2
    return 1
  }
}

run_live_readonly() {
  local state queue drain_log errors ingest_log qdepth
  state="${REFLECT_STATE_DIR:-$HOME/.reflect}"
  queue="${state}/pending_reflections.jsonl"
  drain_log="${state}/drain.log"
  errors="${state}/errors.json"
  ingest_log="${GLOBAL_LEARNINGS_PATH:-$HOME/.learnings}/.memory-ingest-log.yaml"

  if [[ -f "$queue" ]]; then
    qdepth=$(grep -cve '^[[:space:]]*$' "$queue" 2>/dev/null || echo 0)
  else
    qdepth=0
  fi
  say "live: queue_depth=${qdepth}"
  [[ -d "${state}/drain.lock.d" ]] && say "live: drain_lock=present pid=$(cat "${state}/drain.lock.d/pid" 2>/dev/null || echo '?')" || say "live: drain_lock=absent"
  [[ -f "$drain_log" ]] && say "live: latest_drain=$(tail -1 "$drain_log" 2>/dev/null)" || say "live: drain_log=missing"
  if command -v reflect >/dev/null 2>&1; then
    say "live: unacked_errors=$(reflect errors count 2>/dev/null || echo '?')"
  elif [[ -f "$errors" ]]; then
    say "live: errors_json=${errors}"
  fi
  [[ -f "$ingest_log" ]] && say "live: ingest_log_mtime=$(stat -f '%Sm' "$ingest_log" 2>/dev/null || stat -c '%y' "$ingest_log" 2>/dev/null)" || say "live: ingest_log=missing"
  if [[ "$(uname -s)" == "Darwin" ]] && command -v launchctl >/dev/null 2>&1; then
    for label in com.reflect.drain com.reflect.idle com.reflect.maintenance com.stevengonsalvez.reflect-healthcheck; do
      if launchctl list "$label" >/dev/null 2>&1; then
        say "live: launchd ${label}=loaded"
      else
        say "live: launchd ${label}=missing"
      fi
    done
  fi
}

json_result_ok() {
  local log="$1"
  python3 - "$log" <<'PY'
import json, sys
path = sys.argv[1]
text = open(path, encoding="utf-8", errors="replace").read()
if "REFLECT_VERIFY_OK" in text:
    raise SystemExit(0)
for line in reversed(text.splitlines()):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        obj = json.loads(line)
    except Exception:
        continue
    if obj.get("type") == "result" or "result" in obj:
        raise SystemExit(0)
raise SystemExit(1)
PY
}

run_real_claude() {
  local workdir="$1"
  command -v tmux >/dev/null 2>&1 || fail "tmux missing"
  command -v claude >/dev/null 2>&1 || fail "claude missing"
  [[ -d "$workdir" ]] || fail "workdir missing: $workdir"

  local session log signal prompt
  session="reflect-verify-$(date +%s)"
  log="${TMPDIR:-/tmp}/${session}.log"
  signal="${session}-done"
  prompt="Reply exactly REFLECT_VERIFY_OK and nothing else."

  say "real-claude: starting tmux session ${session}"
  tmux new-session -d -s "$session" -n verify || return 1
  tmux send-keys -t "${session}:verify" \
    "cd $(printf '%q' "$workdir") && claude -p $(printf '%q' "$prompt") --output-format json --max-turns 1 2>&1 | tee $(printf '%q' "$log"); tmux wait-for -S $(printf '%q' "$signal"); exit" C-m

  if ! _to 150 tmux wait-for "$signal"; then
    say "real-claude: timed out; killing exact session ${session}"
    tmux kill-session -t "$session" 2>/dev/null || true
    [[ -f "$log" ]] && cat "$log" >&2
    return 1
  fi
  tmux has-session -t "$session" 2>/dev/null && tmux kill-session -t "$session" 2>/dev/null || true

  json_result_ok "$log" || {
    cat "$log" >&2
    return 1
  }
  say "real-claude: log=${log}"
}

do_static=0
do_isolated=0
do_live=0
do_real=0
workdir="$HOME/d/git/ai-coder-rules"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --static) do_static=1 ;;
    --isolated) do_isolated=1 ;;
    --live-readonly) do_live=1 ;;
    --real-claude) do_real=1 ;;
    --workdir) shift; workdir="${1:-}" ;;
    --all) do_static=1; do_isolated=1; do_live=1 ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
  esac
  shift
done

if [[ "$do_static$do_isolated$do_live$do_real" == "0000" ]]; then
  usage >&2
  exit 2
fi

if [[ "$do_static" == "1" ]]; then
  run_static || exit $?
fi
if [[ "$do_isolated" == "1" ]]; then
  run_isolated || exit $?
fi
if [[ "$do_live" == "1" ]]; then
  run_live_readonly || exit $?
fi
if [[ "$do_real" == "1" ]]; then
  run_real_claude "$workdir" || exit $?
fi

say "complete"
