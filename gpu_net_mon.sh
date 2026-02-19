#!/usr/bin/env bash
set -euo pipefail

# Backward-compatible wrapper for the legacy host-wide monitor launcher.
exec /home/cad/dev/gpu_graph/mon_gpu_net_all.sh "$@"

