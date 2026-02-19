# Prompt For Network Engineer Agent
Design the best way to measure **StB external network traffic only** (RX/TX), including subprocesses, with near-real-time updates (target 0.5s, acceptable >=1s). Use this host snapshot and the provided network script copy. Return:
1. Best final design (most reliable).
2. Best interim compromise (least effort).
3. Exact host-specific implementation steps (label root-required steps).
4. Verification plan proving internal traffic is excluded and StB external traffic is included.

## Objective
- Graph StB-only external throughput (download/upload).
- Exclude localhost/private/internal traffic.
- Cover full app stack (backend + desktop shell + subprocesses).

## Snapshot Time
- `2026-02-17T13:28:40-05:00`

## Host / OS
- Host: `devbox`
- User: `cad` (`uid=1000`, groups include `sudo`, `docker`, `kvm`, `libvirt`)
- OS: Debian 12 (bookworm)
- Kernel: `6.1.0-43-amd64`

## Network Topology
- Interfaces:
  - `lo` -> `127.0.0.1/8`, `::1`
  - `enp0s31f6` -> `192.168.2.159/24` (default route)
  - `tailscale0` -> `100.74.95.80/32` + IPv6
  - `virbr0` -> `192.168.122.1/24` (down)
- Routes:
  - `default via 192.168.2.1 dev enp0s31f6`
  - `192.168.2.0/24 dev enp0s31f6`
  - `127.0.0.0/8 dev lo`

## StB Runtime State (systemd --user)
- `stb-next-host-agent@split.service` -> `active`, MainPID `10018`
- `stb-next-server@split.service` -> `active`, MainPID `10020`
- `stb-next-shell@split.service` -> `active`, MainPID `10200`

### StB cgroups
- host-agent:
  - `/user.slice/user-1000.slice/user@1000.service/app.slice/app-stb\x2dnext\x2dhost\x2dagent.slice/stb-next-host-agent@split.service`
- server:
  - `/user.slice/user-1000.slice/user@1000.service/app.slice/app-stb\x2dnext\x2dserver.slice/stb-next-server@split.service`
- shell:
  - `/user.slice/user-1000.slice/user@1000.service/app.slice/app-stb\x2dnext\x2dshell.slice/stb-next-shell@split.service`

### Service launch details
- host-agent: uvicorn on `127.0.0.1:8857`
- server: uvicorn on `127.0.0.1:8856`
- shell: `npm run desktop:preview` in `/home/cad/dev/jan-testing-hub-109e38bc`

### Observed StB process tree (abbrev)
- shell root `10200` -> `node ...electron` (`10219`) -> electron browser process `10226`
- includes utility network process `10277`, renderers `10286/10287`, gpu/broker processes
- computed descendants from StB roots:
  - roots: `[10018, 10020, 10200]`
  - descendant pid count: `15`
  - descendants: `[10018,10020,10200,10218,10219,10226,10229,10230,10232,10277,10286,10287,10603,15196,15198]`

## Current Socket Observations
### Listening sockets
- `127.0.0.1:8856` users: python pid `10020`
- `127.0.0.1:8857` users: python pid `10018`
- `127.0.0.1:43057` users: electron pid `10226`
- `127.0.0.1:39605` users: electron pid `10226`

### StB-attributed TCP socket sample (ss + pid descendants)
- internal TCP sockets: `15`
- external TCP sockets: `0` at snapshot instant
- all observed were local `127.0.0.1 <-> 127.0.0.1` flows at sample time

## Important Noise / Attribution Note
- Another unrelated Electron app is running under the same Linux user (`/home/cad/dev/electron-gpt`, network utility pid `2775`).
- Implication: **uid-based attribution is noisy** on this host unless that app is stopped or StB uses a dedicated user.

## Cgroup / Kernel Capability Facts
- cgroup mode: `v2` (`cgroup2fs`)
- controllers: `cpuset cpu io memory hugetlb pids rdma misc`
- `xt_cgroup` module available (`ipt_cgroup`/`ip6t_cgroup` aliases)
- `iptables -m cgroup` supports `--path` (cgroup v2 path match)

## Netfilter / Privilege Facts
- binaries installed at `/usr/sbin`: `iptables`, `ip6tables`, `nft`, `tc`
- `iptables` backend: nft (`iptables-nft` alternative active)
- current shell has no effective caps (`CapEff=0`)
- passwordless sudo unavailable (`sudo -n true` -> exit `1`)
- non-root cannot list/apply nftables/iptables rules

## Monitoring Tools Present / Missing
- present: `ss` (`/usr/bin/ss`)
- missing: `nethogs`, `bpftrace`, `bpftool`, `iftop`, `vnstat`, `ifstat`, `nload`, `iptraf-ng`, `conntrack`

## Existing Metrics Stack
- `prometheus-node-exporter.service` active

## Key Constraints
1. Need strict external-vs-internal filtering policy (loopback/private/link-local/tailscale).
2. Root-required mechanisms (nft/iptables/eBPF) are feasible but require privileged execution.
3. StB currently spans three user services and many subprocesses.
4. UID-only counting is currently contaminated by another Electron app under the same user.
