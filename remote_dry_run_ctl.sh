#!/usr/bin/env bash
# Remote browser-terminal controller. Read-only by construction.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG="${CONFIG:-$ROOT/remote_runtime_config.json}"
VENV="${VENV:-$ROOT/.venv}"
PIDFILE="${PIDFILE:-$ROOT/policy.pid}"
LOGFILE="${LOGFILE:-$ROOT/logs/live.log}"
META="${META:-$ROOT/logs/runtime_meta.json}"
ACTION="${1:-status}"
PINNED_COMMIT="${PINNED_COMMIT:-$(test -f "$ROOT/.pinned_commit" && cat "$ROOT/.pinned_commit" || true)}"
UNIT=solarbank-dry-run.service

die() { echo "ERROR: $*" >&2; exit 1; }
need_files() { [[ -f "$CONFIG" ]] || die "missing $CONFIG"; [[ -x "$VENV/bin/python" ]] || die "missing $VENV/bin/python"; mkdir -p "$(dirname "$LOGFILE")"; }
validate_config() {
  "$VENV/bin/python" - "$CONFIG" <<'PY'
import json, sys
from pathlib import Path
p = Path(sys.argv[1]); c = json.loads(p.read_text(encoding="utf-8"))
if str(c.get("mode", "dry-run")).lower().replace("_", "-") != "dry-run":
    raise SystemExit("remote config mode must be dry-run")
for key in ("live_enabled", "allow_ha_write", "allow_formal_contest"):
    if c.get(key) is not False:
        raise SystemExit(f"remote config {key} must be false")
for site, item in (c.get("stations") or {}).items():
    if not item.get("suffix") or str(item["suffix"]).startswith("<"):
        raise SystemExit(f"station {site} suffix is incomplete")
print("dry-run safety gates: verified; HA writes disabled")
PY
}
alive() {
  [[ -s "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null || return 1
  local command
  command="$(tr '\0' ' ' < "/proc/$(cat "$PIDFILE")/cmdline")"
  [[ "$command" == *"run_live.py"* && "$command" == *"--mode dry-run"* && "$command" == *"--strategy milp"* ]]
}
managed() { command -v systemctl >/dev/null && systemctl --user is-enabled "$UNIT" >/dev/null 2>&1; }
status() {
  if managed; then systemctl --user --no-pager status "$UNIT" || true; fi
  if alive; then echo "RUNNING pid=$(cat "$PIDFILE")"; else echo "STOPPED"; return 1; fi
  [[ -f "$META" ]] && cat "$META"
}
start() {
  if managed; then systemctl --user start "$UNIT"; status; return; fi
  need_files; validate_config
  if alive; then echo "ALREADY_RUNNING pid=$(cat "$PIDFILE")"; return 0; fi
  rm -f "$PIDFILE"
  local commit="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  [[ -z "$PINNED_COMMIT" || "$commit" == "$PINNED_COMMIT" ]] || die "checked-out commit $commit does not match PINNED_COMMIT $PINNED_COMMIT"
  local cfg_hash="$(sha256sum "$CONFIG" | awk '{print $1}')"
  local started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  "$VENV/bin/python" - "$META" "$commit" "$cfg_hash" "$started" <<'PY'
import json, sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps({"commit":sys.argv[2],"config_sha256":sys.argv[3],"started_at":sys.argv[4],"mode":"dry-run","strategy":"milp","write_enabled":False}, indent=2)+"\n", encoding="utf-8")
PY
  nohup "$VENV/bin/python" -u run_live.py --config "$CONFIG" --mode dry-run --strategy milp --audit-csv logs/run_live_audit.csv >> "$LOGFILE" 2>&1 </dev/null &
  echo $! > "$PIDFILE"; sleep 2
  alive || die "process exited; inspect $LOGFILE"
  echo "STARTED pid=$(cat "$PIDFILE") commit=$commit config_sha256=$cfg_hash"
}
foreground() {
  need_files; validate_config
  local commit="$(git rev-parse HEAD 2>/dev/null || echo unknown)"
  [[ -z "$PINNED_COMMIT" || "$commit" == "$PINNED_COMMIT" ]] || die "checked-out commit $commit does not match PINNED_COMMIT $PINNED_COMMIT"
  echo $$ > "$PIDFILE"
  trap 'rm -f "$PIDFILE"' EXIT INT TERM
  exec "$VENV/bin/python" -u run_live.py --config "$CONFIG" --mode dry-run --strategy milp --audit-csv logs/run_live_audit.csv
}
stop() {
  if managed && systemctl --user is-active --quiet "$UNIT"; then
    systemctl --user stop "$UNIT"; echo "STOPPED systemd unit=$UNIT"; return
  fi
  if ! [[ -s "$PIDFILE" ]]; then echo STOPPED; return 0; fi
  local pid="$(cat "$PIDFILE")"
  if kill -0 "$pid" 2>/dev/null; then
    alive || die "pid file points to a different process; refusing to kill $pid"
    kill -TERM "$pid"
    for _ in {1..20}; do kill -0 "$pid" 2>/dev/null || break; sleep .25; done
    if kill -0 "$pid" 2>/dev/null && alive; then kill -KILL "$pid"; fi
  fi
  rm -f "$PIDFILE"; echo "STOPPED pid=$pid"
}
logs() { need_files; tail -n "${LINES:-80}" "$LOGFILE"; }
case "$ACTION" in start|restart) [[ "$ACTION" != restart ]] || stop; start;; foreground) foreground;; status) status || true;; logs) logs;; stop) stop;; *) die "usage: $0 {start|restart|foreground|status|logs|stop}";; esac
