#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "run as root: sudo ./stb_netacct/install_root.sh" >&2
  exit 1
fi

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

install -d -m 0755 /opt/stb-netacct/iptables
install -m 0755 "$SCRIPT_DIR/iptables/stb_netacct_rules.sh" /opt/stb-netacct/iptables/stb_netacct_rules.sh
install -m 0755 "$SCRIPT_DIR/iptables/stb_netacct_export.py" /opt/stb-netacct/iptables/stb_netacct_export.py

install -d -m 0755 /etc/stb-netacct
if [[ ! -f /etc/stb-netacct/stb-netacct.env ]]; then
  install -m 0644 "$SCRIPT_DIR/systemd/stb-netacct.env.example" /etc/stb-netacct/stb-netacct.env
fi
if [[ ! -f /etc/stb-netacct/cgroup_paths.txt ]]; then
  install -m 0644 "$SCRIPT_DIR/systemd/cgroup_paths.example.txt" /etc/stb-netacct/cgroup_paths.txt
fi

install -m 0644 "$SCRIPT_DIR/systemd/stb-netacct.service" /etc/systemd/system/stb-netacct.service

systemctl daemon-reload

cat <<'EOF'
Installed.
Next steps:
  1) Review /etc/stb-netacct/stb-netacct.env
  2) Review /etc/stb-netacct/cgroup_paths.txt (or leave defaults)
  3) systemctl enable --now stb-netacct.service
  4) watch -n 0.5 cat /run/stb-netacct/counters.json
EOF

