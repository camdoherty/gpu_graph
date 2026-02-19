Do it in 3 steps:

1. Put all StB units in one cgroup slice  
Add this to each user unit (`host-agent`, `server`, `shell`):
```ini
[Service]
Slice=stb-next.slice
```
Then:
```bash
systemctl --user daemon-reload
systemctl --user restart stb-next-host-agent@split.service stb-next-server@split.service stb-next-shell@split.service
systemctl --user show -p ControlGroup --value stb-next-server@split.service
```

2. Add kernel counters for that cgroup (external traffic only)  
Use `iptables` cgroup match + connmark (root required):
```bash
UID_NUM=$(id -u)
CG="/user.slice/user-${UID_NUM}.slice/user@${UID_NUM}.service/stb-next.slice"
MARK_HEX=0x42

sudo iptables -t mangle -N STB_EXT_OUT 2>/dev/null || true
sudo iptables -t mangle -N STB_EXT_IN  2>/dev/null || true
sudo iptables -t mangle -F STB_EXT_OUT
sudo iptables -t mangle -F STB_EXT_IN
sudo iptables -t mangle -A STB_EXT_OUT -j RETURN
sudo iptables -t mangle -A STB_EXT_IN -j RETURN

# outbound: StB cgroup + non-local/non-private destinations
sudo iptables -t mangle -A OUTPUT -m cgroup --path "$CG" \
  ! -d 10.0.0.0/8 ! -d 172.16.0.0/12 ! -d 192.168.0.0/16 ! -d 100.64.0.0/10 ! -d 127.0.0.0/8 \
  -j CONNMARK --set-mark ${MARK_HEX}/0xffffffff

sudo iptables -t mangle -A OUTPUT -m cgroup --path "$CG" \
  -m connmark --mark ${MARK_HEX}/0xffffffff \
  -j STB_EXT_OUT

# inbound replies for those marked flows
sudo iptables -t mangle -A INPUT -m connmark --mark ${MARK_HEX}/0xffffffff -j STB_EXT_IN
```

3. Read bytes every 0.5s in your graph script  
Use deltas from:
```bash
sudo iptables -t mangle -L STB_EXT_OUT -v -n -x
sudo iptables -t mangle -L STB_EXT_IN  -v -n -x
```
`STB_EXT_OUT` = upload, `STB_EXT_IN` = download.

This is the simplest scalable cgroup-based method that is much more reliable than PID tree parsing.  
If you want, I can wire this into a new graph script next.