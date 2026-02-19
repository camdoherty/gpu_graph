# Modular Terminal Monitor System

Refactor `gpu_graph` from standalone scripts into a modular system. Each monitor is a plugin; a launcher composes them into tmux panes.

> [!NOTE]
> Brainstorming / planning document. No code changes proposed yet.

---

## 1. Existing Flags Reference

### [gpu_terminal_graph.py](file:///home/cad/dev/gpu_graph/gpu_terminal_graph.py)

| Type | Name | Default | Description |
|---|---|---|---|
| Const | `INTERVAL_S` | `0.5` | Update interval (seconds) |
| Const | `WINDOW_SECONDS` | `60` | Rolling window (seconds) |
| Const | `GPU_INDEX` | `0` | Which GPU to monitor |
| Env | `MOCK_MODE` | `0` | Set to `1` for random test data |

**No CLI flags** — all configuration is hardcoded constants or env vars.

---

### [net_terminal_graph.py](file:///home/cad/dev/gpu_graph/net_terminal_graph.py)

| Type | Name | Default | Description |
|---|---|---|---|
| Const | `INTERVAL_S` | `0.5` | Update interval (seconds) |
| Const | `WINDOW_SECONDS` | `60` | Rolling window (seconds) |

**No CLI flags, no env vars.** Reads all non-loopback interfaces from [/proc/net/dev](file:///proc/net/dev).

---

### [stb_netacct_terminal_graph.py](file:///home/cad/dev/gpu_graph/stb_netacct_terminal_graph.py)

| Flag | Default | Description |
|---|---|---|
| `--counters-file` | [/run/stb-netacct/counters.json](file:///run/stb-netacct/counters.json) | Path to counters JSON |
| `--rx-key` | `rx_bytes_total` | JSON key for RX bytes |
| `--tx-key` | `tx_bytes_total` | JSON key for TX bytes |
| `--interval` | `0.5` | Update interval (seconds) |
| `--window` | `60` | Rolling window (seconds) |

---

### [stb_external_net_terminal_graph.py](file:///home/cad/dev/gpu_graph/stb_external_net_terminal_graph.py)

| Flag | Default | Description |
|---|---|---|
| `--services` | 3 StB services | Systemd user service names (space/comma separated) |
| `--interval` | `0.5` | Update interval (seconds) |
| `--window` | `60` | Rolling window (seconds) |
| `--include-internal` | off | Include private/loopback/link-local peers |

---

## 2. Proposed Flags (Existing Monitors)

The base class would handle the "universal" flags automatically. Each monitor adds its own specific ones.

### Universal flags (handled by `BaseMonitor`)

| Flag | Default | Description |
|---|---|---|
| `--interval` | `0.5` | Update interval (seconds) |
| `--window` | `60` | Rolling window (seconds) |
| `--title` | auto | Override chart title text |
| `--no-legend` | off | Hide the legend labels |

### [gpu](file:///home/cad/dev/gpu_graph/gpu_terminal_graph.py#28-36) — proposed additions

| Flag | Default | Description |
|---|---|---|
| `--gpu-index` | `0` | Which GPU (replaces `GPU_INDEX` constant) |
| `--mock` | off | Random test data (replaces `MOCK_MODE` env var) |
| `--show-temp` | off | Add GPU temp as a third series (NVML provides this) |

### [net](file:///home/cad/dev/gpu_graph/mon_gpu_net) — proposed additions

| Flag | Default | Description |
|---|---|---|
| `--interface` | all non-lo | Filter to specific NIC (e.g. `enp0s31f6`) |
| `--exclude` | none | Exclude interfaces (e.g. `virbr0,tailscale0`) |

---

## 3. Full Monitor Catalog

Monitors ranked by priority. All "core" monitors use only `/proc` or `/sys` — zero new dependencies.

### Tier 1 — Core (implement first)

| Monitor | Series | Data Source | Cross-Distro |
|---|---|---|---|
| **cpu** | `usr%`, `sys%` | `/proc/stat` jiffies delta | ✅ All Linux |
| **memory** | `Used%`, `Cache%` | `/proc/meminfo` | ✅ All Linux |
| **storage** | `Read MB/s`, `Write MB/s` | `/proc/diskstats` delta | ✅ All Linux |
| **gpu** | `GPU%`, `Mem%` | NVML (`nvidia-ml-py`) | NVIDIA only |
| **net** | `↓ DL/s`, `↑ UL/s` | `/proc/net/dev` delta | ✅ All Linux |

### Tier 2 — Valuable additions

| Monitor | Series | Data Source | Cross-Distro | Notes |
|---|---|---|---|---|
| **temp** | CPU package °C, per-core °C | `/sys/class/hwmon/*/temp*_input` | ✅ All Linux | Auto-discovers sensors by name (`coretemp`, `k10temp`, etc). Your system: hwmon2 = coretemp (4 cores + package), hwmon0 = NVMe drive |
| **load** | 1m, 5m, 15m load averages | `/proc/loadavg` | ✅ All Linux | Simple but useful for spotting sustained pressure |
| **psi** | CPU pressure %, IO pressure % | `/proc/pressure/{cpu,io,memory}` | ✅ Linux 4.20+ | Pressure Stall Info — answers "how much time are tasks waiting?". Your system has PSI enabled |

### Tier 3 — Niche / future

| Monitor | Series | Data Source | Cross-Distro | Notes |
|---|---|---|---|---|
| **battery** | Charge %, Watts | `/sys/class/power_supply/*/` | Laptops only | Not available on your desktop — skip for now |
| **swap** | Used %, In/Out pages/s | `/proc/meminfo` + `/proc/vmstat` | ✅ All Linux | You have 0 swap configured, so low priority |
| **per-process** | CPU%, RSS | `/proc/[pid]/stat` + `/proc/[pid]/statm` | ✅ All Linux | Like a mini `top` for a specific process. Could be nice for tracking a specific service |
| **gpu-amd** | GPU%, VRAM% | `/sys/class/drm/card*/device/gpu_busy_percent` | AMD GPUs | No dependency needed — sysfs direct. Defer until AMD hardware available |

### Proposed flags per new monitor

#### `cpu`

| Flag | Default | Description |
|---|---|---|
| `--per-core` | off | One line per core instead of aggregate usr/sys |
| `--show-iowait` | off | Add iowait as a third series |

#### `memory`

| Flag | Default | Description |
|---|---|---|
| `--show-swap` | off | Add swap% as a third series |
| `--show-buffers` | off | Split cache into buffers + cached |

#### `storage`

| Flag | Default | Description |
|---|---|---|
| `--device` | auto (root device) | Monitor specific block device (e.g. `nvme0n1`, `sda`) |
| `--all-devices` | off | Sum I/O across all physical devices |
| `--show-iops` | off | Show IOPS instead of throughput |

#### `temp`

| Flag | Default | Description |
|---|---|---|
| `--sensor` | auto (coretemp/k10temp) | Specific hwmon name to use |
| `--per-core` | off | Show per-core temps vs just package |
| `--include-drive` | off | Include NVMe/drive temps |

#### `load`

| Flag | Default | Description |
|---|---|---|
| `--scale` | auto (nproc) | Y-axis max (default: number of CPUs) |

#### `psi`

| Flag | Default | Description |
|---|---|---|
| `--resources` | `cpu,io` | Which PSI resources to graph |
| `--metric` | `some` | `some` or `full` pressure metric |

---

## 4. Architecture (unchanged from v1)

```
monitors/
├── __init__.py
├── base.py           # BaseMonitor ABC — rendering engine + universal flags
├── cpu.py            # /proc/stat
├── memory.py         # /proc/meminfo
├── storage.py        # /proc/diskstats
├── gpu.py            # NVML (extracted from existing)
├── net.py            # /proc/net/dev (extracted from existing)
├── temp.py           # /sys/class/hwmon
├── load.py           # /proc/loadavg
├── psi.py            # /proc/pressure/*
└── ...
launcher.py           # tmux session builder: 1 pane = 1 monitor
```

`BaseMonitor` handles: deque management, draw loop, ANSI buffering, SIGWINCH, cursor hide/show, unit formatting, deadline tick loop, universal flags, plotext theme.

Subclasses implement: `setup()`, `sample()`, `add_args()` (~20-30 lines each).

---

## 5. Distro Portability

| Data Source | Deb | Ubu | Fed | Arch | Alp | RHEL |
|---|---|---|---|---|---|---|
| `/proc/stat` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/proc/meminfo` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/proc/net/dev` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/proc/diskstats` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/proc/loadavg` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| `/proc/pressure/*` | ✅⁴·²⁰ | ✅ | ✅ | ✅ | ✅ | ✅⁸ |
| `/sys/class/hwmon` | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| NVML | GPU | GPU | GPU | GPU | GPU | GPU |

**Dependencies**: `plotext` (always), `nvidia-ml-py` (GPU only, optional). Zero new packages.

---

## 6. Your System Snapshot

For reference, here's what's available on this machine (Debian Bookworm, kernel 6.1, 4-core i-series):

| Resource | Details |
|---|---|
| CPU | 4 cores, `/proc/stat` ✅ |
| Memory | 16 GB, no swap, `/proc/meminfo` ✅ |
| Storage | NVMe (`nvme0n1p5_crypt`, 327G, 81% used) + SATA (`sda`, 2 partitions) |
| Temps | coretemp: package 49°C, 4 cores 45-48°C; NVMe: 44°C |
| NICs | `enp0s31f6` (physical), `tailscale0` (VPN), `virbr0` (libvirt bridge) |
| PSI | ✅ Available (cpu some=10.3%, memory/io near 0%) |
| Battery | ❌ Desktop — no power supply |
| GPU | NVIDIA (NVML available) |

---

## 7. Open Questions

1. **Tier 1 scope**: Include memory in the first round? (My recommendation: yes, it's trivial)
2. **Tier 2 scope**: Which of temp / load / psi to include now vs later?
3. **CPU display**: Option A (usr+sys, default) with `--per-core` and `--show-iowait` as flags?
4. **Project rename?** `gpu_graph` → something like `tmon` or `termmon`?
5. **Backwards compat**: Keep old launcher scripts as wrappers, or replace?
