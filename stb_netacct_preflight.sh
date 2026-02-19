#!/usr/bin/env bash
set -euo pipefail

NETACCT_UNIT="${NETACCT_UNIT:-stb-netacct.service}"
COUNTERS_FILE="${COUNTERS_FILE:-/run/stb-netacct/counters.json}"
MAX_AGE_SEC="${MAX_AGE_SEC:-5}"

USER_UNITS=(
  "stb-next-host-agent@split.service"
  "stb-next-server@split.service"
  "stb-next-shell@split.service"
)

FIX=0
QUIET=0

usage() {
  cat <<'EOF'
Usage: stb_netacct_preflight.sh [--fix] [--quiet]

Checks:
  1) root unit stb-netacct.service is active
  2) /run/stb-netacct/counters.json is present and fresh
  3) stb-netacct start time is not older than split user services

Options:
  --fix    Attempt "sudo systemctl restart stb-netacct.service" if stale/unhealthy
  --quiet  Reduce output; rely on exit code
EOF
}

log() {
  if [[ "$QUIET" -eq 0 ]]; then
    echo "$*"
  fi
}

die() {
  log "preflight: $*"
  exit 1
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --fix)
        FIX=1
        ;;
      --quiet)
        QUIET=1
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "unknown option: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
    shift
  done
}

unit_is_active() {
  systemctl is-active --quiet "$NETACCT_UNIT"
}

unit_started_mono() {
  systemctl show -p ActiveEnterTimestampMonotonic --value "$1" 2>/dev/null || true
}

file_fresh() {
  [[ -f "$COUNTERS_FILE" ]] || return 1
  local now epoch age
  now="$(date +%s)"
  epoch="$(stat -c %Y "$COUNTERS_FILE" 2>/dev/null || echo 0)"
  age=$(( now - epoch ))
  [[ "$age" -le "$MAX_AGE_SEC" ]]
}

split_services_newer_than_netacct() {
  local net_mono unit unit_mono
  net_mono="$(unit_started_mono "$NETACCT_UNIT")"
  [[ "$net_mono" =~ ^[0-9]+$ ]] || return 0

  for unit in "${USER_UNITS[@]}"; do
    unit_mono="$(systemctl --user show -p ActiveEnterTimestampMonotonic --value "$unit" 2>/dev/null || true)"
    [[ "$unit_mono" =~ ^[0-9]+$ ]] || continue
    if (( unit_mono > net_mono )); then
      log "preflight: stale ordering detected ($unit newer than $NETACCT_UNIT)"
      return 0
    fi
  done
  return 1
}

attempt_fix() {
  log "preflight: attempting restart of $NETACCT_UNIT"
  sudo systemctl restart "$NETACCT_UNIT"
}

healthy_now() {
  local ok=1
  if ! unit_is_active; then
    log "preflight: $NETACCT_UNIT is not active"
    ok=0
  fi
  if ! file_fresh; then
    log "preflight: counters file missing/stale: $COUNTERS_FILE"
    ok=0
  fi
  if split_services_newer_than_netacct; then
    ok=0
  fi
  [[ "$ok" -eq 1 ]]
}

main() {
  parse_args "$@"

  if healthy_now; then
    log "preflight: ok"
    exit 0
  fi

  if [[ "$FIX" -eq 1 ]]; then
    attempt_fix
    sleep 0.2
    if healthy_now; then
      log "preflight: fixed"
      exit 0
    fi
  fi

  die "not healthy; run: sudo systemctl restart $NETACCT_UNIT"
}

main "$@"

