#!/usr/bin/env bash
# reflect-maintenance-watch.sh - read-only Reflect watchdog.
#
# Runs from launchd to surface drain/ingest malfunctions through the existing
# reflect errors sink, which the statusline already renders in the ERR row.
# It does not run ingest or consolidate; those remain supervised workflows.

set -uo pipefail

if [[ "${REFLECT_DISABLED:-0}" == "1" ]]; then
  exit 0
fi

STATE_DIR="${REFLECT_STATE_DIR:-$HOME/.reflect}"
LEARNINGS_DIR="${GLOBAL_LEARNINGS_PATH:-$HOME/.learnings}"
QUEUE_FILE="${STATE_DIR}/pending_reflections.jsonl"
LOCK_DIR="${STATE_DIR}/drain.lock.d"
DRAIN_LOG="${STATE_DIR}/drain.log"
WATCH_LOG="${STATE_DIR}/maintenance-watch.log"
ERRORS_JSON="${STATE_DIR}/errors.json"
INGEST_LOG="${LEARNINGS_DIR}/.memory-ingest-log.yaml"

DRAIN_STALE_SEC="${REFLECT_WATCH_DRAIN_STALE_SEC:-3600}"
DRAIN_RUNNING_SEC="${REFLECT_WATCH_DRAIN_RUNNING_SEC:-900}"
INGEST_STALE_DAYS="${REFLECT_WATCH_INGEST_STALE_DAYS:-7}"
DRAIN_LABEL="${REFLECT_WATCH_DRAIN_LABEL:-com.reflect.drain}"
MAINTENANCE_LABEL="${REFLECT_WATCH_MAINTENANCE_LABEL:-com.reflect.maintenance}"

mkdir -p "$STATE_DIR"

now_epoch() { date +%s; }

log() {
  printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$WATCH_LOG"
}

json_context() {
  python3 - "$@" <<'PY' 2>/dev/null || printf '{}'
import json, sys
pairs = sys.argv[1:]
out = {}
for i in range(0, len(pairs), 2):
    if i + 1 < len(pairs):
        out[pairs[i]] = pairs[i + 1]
print(json.dumps(out))
PY
}

append_error_json() {
  local severity="$1" source="$2" kind="$3" message="$4" context="$5"
  python3 - "$ERRORS_JSON" "$severity" "$source" "$kind" "$message" "$context" <<'PY' >/dev/null 2>&1 || true
import datetime, hashlib, json, os, sys

path, severity, source, kind, message, context_raw = sys.argv[1:7]
try:
    context = json.loads(context_raw)
except Exception:
    context = {}
now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
key = json.dumps(
    {"source": source, "kind": kind, "message": message, "context": context},
    sort_keys=True,
)
eid = "err-" + hashlib.sha1(key.encode()).hexdigest()[:6]
try:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
except Exception:
    data = {"version": 1, "errors": []}
errors = data.setdefault("errors", [])
for err in errors:
    if err.get("id") == eid and not err.get("acked", False):
        err["ts"] = now
        err["severity"] = severity
        err["count"] = int(err.get("count", 1) or 1) + 1
        break
else:
    errors.insert(0, {
        "id": eid,
        "ts": now,
        "severity": severity,
        "source": source,
        "kind": kind,
        "message": message,
        "context": context,
        "count": 1,
        "acked": False,
    })
data["updated_at"] = now
tmp = path + ".tmp"
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=2)
    fh.write("\n")
os.replace(tmp, path)
PY
}

emit_error() {
  local severity="$1" kind="$2" message="$3" context="${4:-{}}"
  log "emit ${severity} ${kind}: ${message}"
  if [[ "${REFLECT_WATCH_FORCE_JSON_ERRORS:-0}" != "1" ]] && command -v reflect >/dev/null 2>&1; then
    reflect errors append \
      --severity "$severity" \
      --source maintenance \
      --kind "$kind" \
      --message "$message" \
      --context "$context" >/dev/null 2>&1 && return 0
  fi
  append_error_json "$severity" maintenance "$kind" "$message" "$context"
}

file_mtime_epoch() {
  local f="$1"
  [[ -e "$f" ]] || { printf '0'; return; }
  stat -f %m "$f" 2>/dev/null || stat -c %Y "$f" 2>/dev/null || printf '0'
}

queue_depth() {
  [[ -f "$QUEUE_FILE" ]] || { printf '0'; return; }
  grep -cve '^[[:space:]]*$' "$QUEUE_FILE" 2>/dev/null || printf '0'
}

pid_alive() {
  local pid="$1"
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

pid_elapsed_sec() {
  local pid="$1"
  ps -p "$pid" -o etimes= 2>/dev/null | tr -d '[:space:]'
}

last_drain_event_epoch() {
  local needle="$1"
  [[ -f "$DRAIN_LOG" ]] || { printf '0'; return; }
  python3 - "$DRAIN_LOG" "$needle" <<'PY' 2>/dev/null || printf '0'
import datetime, re, sys
path, needle = sys.argv[1], sys.argv[2]
last = 0
with open(path, encoding="utf-8", errors="replace") as fh:
    for line in fh:
        if needle not in line:
            continue
        m = re.match(r"^\[([^\]]+)\]", line)
        if not m:
            continue
        ts = m.group(1).replace("Z", "+00:00")
        try:
            last = int(datetime.datetime.fromisoformat(ts).timestamp())
        except Exception:
            pass
print(last)
PY
}

check_drain() {
  local qd now pid elapsed last_start last_end age ctx
  qd="$(queue_depth)"
  now="$(now_epoch)"

  if [[ -d "$LOCK_DIR" ]]; then
    pid="$(cat "${LOCK_DIR}/pid" 2>/dev/null || true)"
    if ! pid_alive "$pid"; then
      ctx="$(json_context lock_dir "$LOCK_DIR" pid "${pid:-}")"
      emit_error error drain_stale_lock "drain lock exists but pid is not running" "$ctx"
      return 0
    fi
    elapsed="$(pid_elapsed_sec "$pid")"
    [[ "$elapsed" =~ ^[0-9]+$ ]] || elapsed=0
    if [[ "$elapsed" -gt "$DRAIN_RUNNING_SEC" ]]; then
      ctx="$(json_context pid "$pid" elapsed_sec "$elapsed" queue_depth "$qd")"
      emit_error warn drain_running_long "drain has been running for ${elapsed}s" "$ctx"
    fi
    return 0
  fi

  [[ "$qd" =~ ^[0-9]+$ ]] || qd=0
  [[ "$qd" -gt 0 ]] || return 0

  if [[ ! -f "$DRAIN_LOG" ]]; then
    ctx="$(json_context queue_depth "$qd")"
    emit_error warn drain_no_log "pending queue exists but drain log is missing" "$ctx"
    return 0
  fi

  last_start="$(last_drain_event_epoch "drain start")"
  last_end="$(last_drain_event_epoch "drain end")"
  if [[ "$last_start" == "0" ]]; then
    ctx="$(json_context queue_depth "$qd")"
    emit_error warn drain_never_started "pending queue exists but no drain start found" "$ctx"
    return 0
  fi
  age=$((now - last_start))
  if [[ "$last_end" -ge "$last_start" && "$age" -gt "$DRAIN_STALE_SEC" ]]; then
    ctx="$(json_context queue_depth "$qd" last_start_epoch "$last_start" age_sec "$age")"
    emit_error warn drain_not_running "pending queue has not had a drain start for ${age}s" "$ctx"
  fi
}

check_launchd() {
  [[ "${REFLECT_WATCH_SKIP_LAUNCHD:-0}" == "1" ]] && return 0
  [[ "$(uname -s)" == "Darwin" ]] || return 0
  command -v launchctl >/dev/null 2>&1 || return 0

  local qd ctx
  qd="$(queue_depth)"
  if [[ "$qd" =~ ^[0-9]+$ && "$qd" -gt 0 ]]; then
    if ! launchctl list "$DRAIN_LABEL" >/dev/null 2>&1; then
      ctx="$(json_context queue_depth "$qd" label "$DRAIN_LABEL")"
      emit_error warn drain_launchd_missing "pending queue exists but ${DRAIN_LABEL} is not loaded" "$ctx"
    fi
  fi
  if ! launchctl list "$MAINTENANCE_LABEL" >/dev/null 2>&1; then
    ctx="$(json_context label "$MAINTENANCE_LABEL")"
    emit_error warn maintenance_launchd_missing "${MAINTENANCE_LABEL} is not loaded" "$ctx"
  fi
}

check_ingest() {
  [[ "${REFLECT_WATCH_DISABLE_INGEST:-0}" == "1" ]] && return 0
  local now mt age_days ctx
  now="$(now_epoch)"
  if [[ ! -f "$INGEST_LOG" ]]; then
    ctx="$(json_context ingest_log "$INGEST_LOG")"
    emit_error warn ingest_never_ran "ingest log is missing; run /reflect:ingest after consolidate" "$ctx"
    return 0
  fi
  mt="$(file_mtime_epoch "$INGEST_LOG")"
  [[ "$mt" =~ ^[0-9]+$ ]] || mt=0
  age_days=$(((now - mt) / 86400))
  if [[ "$age_days" -gt "$INGEST_STALE_DAYS" ]]; then
    ctx="$(json_context ingest_log "$INGEST_LOG" age_days "$age_days")"
    emit_error warn ingest_stale "ingest log is ${age_days} days old" "$ctx"
  fi
}

main() {
  log "watch start queue_depth=$(queue_depth)"
  check_drain
  check_launchd
  check_ingest
  log "watch end"
}

main "$@" || true
exit 0
