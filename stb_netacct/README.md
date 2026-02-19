# StB Netacct Bundle (Root Side)

This folder provides the root-managed side of StB external traffic accounting:

- `iptables/stb_netacct_rules.sh`: installs/removes iptables/ip6tables mangle rules.
- `iptables/stb_netacct_export.py`: polls rule counters and writes JSON totals.
- `systemd/stb-netacct.service`: service template.
- `install_root.sh`: copies files into `/opt`, `/etc`, and `/etc/systemd/system`.

## What It Measures

- Includes: traffic from StB cgroups (`host-agent`, `server`, `shell`) and descendants.
- Excludes: localhost/private/internal ranges (including `100.64.0.0/10` for tailscale-like traffic).
- Output: `/run/stb-netacct/counters.json`.

## Install

```bash
sudo ./stb_netacct/install_root.sh
sudo systemctl enable --now stb-netacct.service
```

## Verify

```bash
watch -n 0.5 cat /run/stb-netacct/counters.json
sudo iptables -t mangle -L STB_EXT_OUT -v -n -x
sudo iptables -t mangle -L STB_EXT_IN -v -n -x
```

Then run:

```bash
./venv/bin/python3 stb_netacct_terminal_graph.py
```

