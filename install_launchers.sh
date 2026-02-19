#!/usr/bin/env bash
set -euo pipefail

ROOT="/home/cad/dev/gpu_graph"
BIN_DIR="${HOME}/.local/bin"

mkdir -p "$BIN_DIR"

ln -sfn "$ROOT/stb_mon_gpu_net" "$BIN_DIR/stb_mon_gpu_net"
ln -sfn "$ROOT/mon_gpu_net_all.sh" "$BIN_DIR/mon_gpu_net_all"

# Remove legacy links so only the two canonical launchers remain.
rm -f "$BIN_DIR/mon_gpu_net" "$BIN_DIR/gpu_net_mon"

echo "Installed launchers:"
echo "  $BIN_DIR/stb_mon_gpu_net"
echo "  $BIN_DIR/mon_gpu_net_all"
