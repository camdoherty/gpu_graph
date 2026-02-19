#!/usr/bin/env bash
set -euo pipefail

# Launch GPU + host-wide network monitor in a tmux split
PYTHON="/home/cad/dev/gpu_graph/venv/bin/python3"
GPU="/home/cad/dev/gpu_graph/gpu_terminal_graph.py"
NET="/home/cad/dev/gpu_graph/net_terminal_graph.py"
SESSION="gpu_net_mon"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  tmux attach-session -t "$SESSION"
else
  tmux new-session -d -s "$SESSION" "$PYTHON $GPU" \; \
    set-option -t "$SESSION" status off \; \
    split-window -v "$PYTHON $NET" \; \
    select-pane -t 0 \; \
    attach-session -t "$SESSION"
fi
