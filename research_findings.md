**Goal:** measure and graph **StB external-only** network throughput (RX/TX), including subprocesses, with near-real-time updates (target **0.5s**, acceptable ≥1s). 
Host is Debian 12, cgroup v2; StB is split across **three systemd --user services** with known **cgroup paths**. 
**Do not use UID-based attribution** (another Electron app runs under the same user). 
You may assume we can do privileged setup, but **non-root cannot manage/list netfilter rules** on this host, so any rule installation and counter reads must run as root (interactive sudo expected). 

---

## 1) Best final design (most reliable): cgroup-attached eBPF byte accounting + unprivileged JSON export

### Why this is “best”

* Exact attribution to StB **by cgroup**, independent of UID noise. (We have cgroup v2 and explicit StB service cgroup paths.) 
* Works for **RX and TX** without conntrack/marking edge cases.
* Clean external-vs-internal filtering in-kernel (LPM trie of excluded CIDRs).
* Low overhead; high sampling frequency is easy.

### Implementation plan (deliverables)

1. **Root-required:** Install prerequisites for building/loading BPF:

   * Packages: `bpftool`, `clang`, `llvm`, `libbpf-dev`, `linux-headers-$(uname -r)`, `make`, `jq` (or equivalent).
2. Create `/opt/stb-netacct/` containing:

   * `stb_netacct.bpf.c` (eBPF program):

     * Attach type: `cgroup_skb/egress` and `cgroup_skb/ingress` (two attach points).
     * Maintain a BPF map with counters:

       * Keys: `{service_id, direction}` or simply `{direction}` if you attach separately per service and sum in userspace.
       * Values: `u64 bytes`.
     * External-only filter:

       * For **egress**: check destination IP; if in excluded ranges -> ignore; else add skb->len to TX.
       * For **ingress**: check source IP; if in excluded ranges -> ignore; else add skb->len to RX.
       * Excluded ranges must include at least: loopback, RFC1918, link-local, CGNAT (100.64/10, i.e., tailscale-like), multicast, and ULA/link-local/multicast for IPv6. The brief explicitly calls out excluding loopback/private/internal/tailscale. 
   * `stb_netacct_loader.sh` (or small C/Python loader) to:

     * Compile and load BPF program.
     * Attach to **each of the three StB service cgroups** (paths from brief):

       * `/user.slice/user-1000.slice/user@1000.service/app.slice/app-stb\x2dnext\x2dhost\x2dagent.slice/stb-next-host-agent@split.service`
       * `/user.slice/user-1000.slice/user@1000.service/app.slice/app-stb\x2dnext\x2dserver.slice/stb-next-server@split.service`
       * `/user.slice/user-1000.slice/user@1000.service/app.slice/app-stb\x2dnext\x2dshell.slice/stb-next-shell@split.service` 
3. **Root-required:** Add a root systemd service `stb-netacct.service`:

   * `ExecStartPre` loads/attaches BPF.
   * `ExecStart` runs an exporter daemon that every **0.25–0.5s**:

     * Reads BPF map(s).
     * Writes `/run/stb-netacct/counters.json` atomically (write temp + rename), mode `0644`, owner `cad`.
     * JSON schema example:

       ```json
       {"rx_bytes_total": 123456789, "tx_bytes_total": 987654321, "ts_monotonic": 12345.67}
       ```
4. Modify the terminal graph to read those counters instead of `/proc/net/dev`:

   * Current script sums **all non-loopback interfaces** from `/proc/net/dev`, which includes non-StB traffic and will include tailscale0 unless excluded elsewhere. 
   * Keep its 0.5s cadence (already `INTERVAL_S = 0.5`). 
   * Replace `read_net_bytes()` with `read_stb_bytes()` that reads `/run/stb-netacct/counters.json` and returns `(rx_total, tx_total)`.
   * Add resilience: if JSON missing/unreadable, return last good values (don’t crash).

---

## 2) Best interim compromise (least effort): iptables-nft cgroup match + CONNMARK + byte counters + JSON export

This is the “ship today” option: no BPF code, just netfilter rules. It leverages that `xt_cgroup` exists and `iptables -m cgroup --path` works with cgroup v2 paths. 

### Core idea

* In **OUTPUT (mangle)**: if packet originates from one of the StB cgroups AND destination is “external”, set a **connmark** (e.g., `0x51`).
* Count TX bytes on packets with that connmark (still in OUTPUT).
* Count RX bytes on packets with that connmark in **PREROUTING (mangle)** (reply traffic for those marked connections).
* External-only is enforced by **only marking** when dst is not in excluded ranges (loopback/RFC1918/link-local/CGNAT/etc). Since the connmark is only set for “external” flows, RX counting becomes “external-only” automatically.

### Implementation steps (host-specific)

#### A) Root-required: install two systemd units and scripts

Create `/opt/stb-netacct/iptables/` with:

1. `stb_netacct_rules.sh` (start/stop):

   * `start`:

     * Create chains in `mangle`: `STB_EXT_OUT`, `STB_EXT_IN`.
     * Insert jump rules near top of `OUTPUT` and `PREROUTING`.
     * In `STB_EXT_OUT`:

       1. Match StB cgroups (3 rules; jump to same chain) using the exact cgroup paths from the brief. 
       2. Exclusion “returns” for internal/private/tailscale-like destinations (multiple `-d CIDR -j RETURN` rules).
       3. `CONNMARK --set-mark 0x51`
       4. **Counter rule** for TX (same connmark) with a stable comment, e.g. `--comment STB_EXT_TX` and `-j RETURN`.
     * In `STB_EXT_IN` (hooked from `PREROUTING`):

       * **Counter rule** matching `-m connmark --mark 0x51` with comment `STB_EXT_RX` and `-j RETURN`.
     * Repeat similarly for IPv6 using `ip6tables`, excluding IPv6 internal ranges (ULA, link-local, loopback, multicast).
   * `stop`:

     * Remove inserted jumps; flush and delete the custom chains (both v4 and v6).

2. `stb_netacct_export.py` (root daemon):

   * Every 0.25–0.5s:

     * Runs `iptables -t mangle -nvx -L STB_EXT_OUT` and extracts bytes for the rule with comment `STB_EXT_TX`.
     * Runs `iptables -t mangle -nvx -L STB_EXT_IN` and extracts bytes for `STB_EXT_RX`.
     * Also reads v6 chains (`ip6tables ...`) and adds them to totals.
     * Writes `/run/stb-netacct/counters.json` atomically with `0644` perms and `cad` ownership.

3. systemd unit: `/etc/systemd/system/stb-netacct.service`

   * `ExecStartPre=/opt/stb-netacct/iptables/stb_netacct_rules.sh start`
   * `ExecStart=/usr/bin/python3 /opt/stb-netacct/iptables/stb_netacct_export.py`
   * `ExecStopPost=/opt/stb-netacct/iptables/stb_netacct_rules.sh stop`
   * `Restart=on-failure`

#### B) Unprivileged: patch the graph script to read `/run/stb-netacct/counters.json`

The existing graph:

* Samples every 0.5s (`INTERVAL_S = 0.5`). 
* Currently sums all non-loopback interfaces via `/proc/net/dev`. 

Update it as follows:

* Replace `read_net_bytes()` with JSON-based reader.
* Keep the rest of the graph logic unchanged (units, rolling window, plotext rendering).

---

## 3) Exact implementation checklist (label root-required)

### Root-required

1. Create `/opt/stb-netacct/...` directory structure.
2. Install required packages:

   * For interim: just `jq` (optional) + ensure `iptables/ip6tables` present (they are). 
   * For final: add `bpftool`, `clang`, `llvm`, `libbpf-dev`, kernel headers.
3. Implement and install `stb-netacct.service` under `/etc/systemd/system/`.
4. `systemctl daemon-reload && systemctl enable --now stb-netacct.service`
5. Confirm `/run/stb-netacct/counters.json` is world-readable and updated at target cadence.

### Non-root (cad)

6. Patch `net_terminal_graph.py` to read `/run/stb-netacct/counters.json` instead of `/proc/net/dev`.
7. Run the graph normally: `python3 net_terminal_graph.py`

---

## 4) Verification plan (prove internal excluded; StB external included)

### Prepare (baseline)

1. Start `stb-netacct.service`.
2. Confirm counters exist and are stable at idle:

   * `cat /run/stb-netacct/counters.json` (rx/tx totals should either hold steady or change minimally).

### A) Prove StB internal traffic is excluded

The brief shows StB services listen on `127.0.0.1:8856/8857` and observed traffic was local-only at snapshot time. 
Steps:

1. Trigger a known StB internal action that causes backend↔shell traffic over `127.0.0.1` (e.g., refresh UI, call an internal API endpoint).
2. Observe:

   * Your graph should **not** show a spike attributable to those internal calls.
   * `/run/stb-netacct/counters.json` should not meaningfully increase.
3. Control test: generate lots of localhost traffic outside StB (`curl 127.0.0.1:8856/...` in a normal shell). External counters must remain unchanged.

### B) Prove StB external traffic is included

1. Trigger a known StB feature that fetches from the public internet (or temporarily add a “fetch example.com” action inside StB).
2. Observe:

   * The graph shows corresponding RX/TX spikes.
   * `rx_bytes_total/tx_bytes_total` increases during the operation.
3. Confirm that other user processes do **not** contaminate:

   * Generate external traffic from a non-StB process (e.g., run `curl https://example.com` in a normal terminal).
   * Counters should **not** increase (this validates you are not using UID-based attribution, which is known noisy here). 

### C) Prove tailscale/internal ranges excluded

1. Generate traffic to a tailscale/CGNAT address or over `tailscale0` (if feasible).
2. Ensure counters do not move; if they do, add/adjust excluded ranges (notably `100.64.0.0/10`) until they don’t.

### Acceptance criteria

* At steady state with only localhost activity, graph stays ~0 external throughput.
* When StB performs a public fetch, graph spikes, and totals increase.
* External traffic from unrelated apps (including the other Electron app) does not change counters.

---

## Decision guidance

* Implement **interim iptables/CONNMARK** first to get a working StB-external-only graph quickly using cgroup path matching (supported here). 
* If you need maximum correctness and future-proofing (and you’re okay installing toolchain), upgrade to the **final eBPF design**; keep the same JSON contract so the terminal graph doesn’t change.
