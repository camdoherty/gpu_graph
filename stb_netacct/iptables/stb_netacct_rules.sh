#!/usr/bin/env bash
set -euo pipefail

ACTION="${1:-}"
if [[ "$ACTION" != "start" && "$ACTION" != "stop" ]]; then
  echo "usage: $0 {start|stop}" >&2
  exit 2
fi

STB_USER="${STB_USER:-cad}"
MARK_HEX="${MARK_HEX:-0x51}"
MARK_MASK="${MARK_MASK:-0xffffffff}"
CGROUP_PATHS_FILE="${CGROUP_PATHS_FILE:-/etc/stb-netacct/cgroup_paths.txt}"
CHAIN_OUT="${CHAIN_OUT:-STB_EXT_OUT}"
CHAIN_IN="${CHAIN_IN:-STB_EXT_IN}"

V4_EXCLUDES=(
  "0.0.0.0/8"
  "10.0.0.0/8"
  "100.64.0.0/10"
  "127.0.0.0/8"
  "169.254.0.0/16"
  "172.16.0.0/12"
  "192.168.0.0/16"
  "224.0.0.0/4"
  "240.0.0.0/4"
)

V6_EXCLUDES=(
  "::1/128"
  "fc00::/7"
  "fe80::/10"
  "ff00::/8"
)

default_cgroup_paths() {
  local uid
  uid="$(id -u "$STB_USER")"
  cat <<EOF
/user.slice/user-${uid}.slice/user@${uid}.service/app.slice/app-stb\\x2dnext\\x2dhost\\x2dagent.slice/stb-next-host-agent@split.service
/user.slice/user-${uid}.slice/user@${uid}.service/app.slice/app-stb\\x2dnext\\x2dserver.slice/stb-next-server@split.service
/user.slice/user-${uid}.slice/user@${uid}.service/app.slice/app-stb\\x2dnext\\x2dshell.slice/stb-next-shell@split.service
EOF
}

collect_cgroups() {
  if [[ -f "$CGROUP_PATHS_FILE" ]]; then
    sed -e 's/[[:space:]]*$//' "$CGROUP_PATHS_FILE" | awk 'NF && $1 !~ /^#/'
  else
    default_cgroup_paths
  fi
}

ensure_chain() {
  local bin="$1"
  local chain="$2"
  if ! "$bin" -t mangle -S "$chain" >/dev/null 2>&1; then
    "$bin" -t mangle -N "$chain"
  fi
  "$bin" -t mangle -F "$chain"
}

ensure_rule_once() {
  local bin="$1"
  shift
  if ! "$bin" -t mangle -C "$@" >/dev/null 2>&1; then
    "$bin" -t mangle -I "$@"
  fi
}

start_family() {
  local bin="$1"
  local -n excludes_ref="$2"
  local path

  ensure_chain "$bin" "$CHAIN_OUT"
  ensure_chain "$bin" "$CHAIN_IN"

  for cidr in "${excludes_ref[@]}"; do
    "$bin" -t mangle -A "$CHAIN_OUT" -d "$cidr" -j RETURN
  done
  "$bin" -t mangle -A "$CHAIN_OUT" -j CONNMARK --set-xmark "${MARK_HEX}/${MARK_MASK}"
  "$bin" -t mangle -A "$CHAIN_OUT" \
    -m connmark --mark "${MARK_HEX}/${MARK_MASK}" \
    -m comment --comment "STB_EXT_TX" \
    -j RETURN
  "$bin" -t mangle -A "$CHAIN_OUT" -j RETURN

  "$bin" -t mangle -A "$CHAIN_IN" \
    -m connmark --mark "${MARK_HEX}/${MARK_MASK}" \
    -m comment --comment "STB_EXT_RX" \
    -j RETURN
  "$bin" -t mangle -A "$CHAIN_IN" -j RETURN

  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    ensure_rule_once "$bin" OUTPUT -m cgroup --path "$path" -j "$CHAIN_OUT"
  done < <(collect_cgroups)

  ensure_rule_once "$bin" PREROUTING -j "$CHAIN_IN"
}

stop_family() {
  local bin="$1"
  local path

  while "$bin" -t mangle -C PREROUTING -j "$CHAIN_IN" >/dev/null 2>&1; do
    "$bin" -t mangle -D PREROUTING -j "$CHAIN_IN"
  done

  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    while "$bin" -t mangle -C OUTPUT -m cgroup --path "$path" -j "$CHAIN_OUT" >/dev/null 2>&1; do
      "$bin" -t mangle -D OUTPUT -m cgroup --path "$path" -j "$CHAIN_OUT"
    done
  done < <(collect_cgroups)

  "$bin" -t mangle -F "$CHAIN_OUT" >/dev/null 2>&1 || true
  "$bin" -t mangle -X "$CHAIN_OUT" >/dev/null 2>&1 || true
  "$bin" -t mangle -F "$CHAIN_IN" >/dev/null 2>&1 || true
  "$bin" -t mangle -X "$CHAIN_IN" >/dev/null 2>&1 || true
}

require_binary() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required binary: $1" >&2
    exit 1
  fi
}

require_binary iptables
require_binary ip6tables

case "$ACTION" in
  start)
    start_family iptables V4_EXCLUDES
    start_family ip6tables V6_EXCLUDES
    ;;
  stop)
    stop_family iptables
    stop_family ip6tables
    ;;
esac

