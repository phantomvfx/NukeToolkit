#!/usr/bin/env bash
# TAPTrack installer (Linux/macOS): tracker environment + pinned tapnet + model weights + a check.
#   install/install.sh [--cuda cu126|cpu] [--venv DIR] [--all-models] [--skip-weights] [--add-to-nuke]
# Safe to run again: it repairs what is missing and skips what is already there.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CUDA=cu126; VENV="$ROOT/.venv"; ALL=""; SKIP=0; ADD=0
while [ $# -gt 0 ]; do
  case "$1" in
    --cuda) CUDA="$2"; shift 2;;
    --venv) VENV="$2"; shift 2;;
    --all-models) ALL="--all"; shift;;
    --skip-weights) SKIP=1; shift;;
    --add-to-nuke) ADD=1; shift;;
    *) echo "unknown option: $1"; exit 2;;
  esac
done
echo "== 1/4  Tracker environment"
"$ROOT/install/setup_venv.sh" "$CUDA" "$VENV"
VPY="$VENV/bin/python"
export TAPTRACK_PYTHON="$VPY"
if [ "$SKIP" = 0 ]; then
  echo "== 2/4  Model weights (about 1 GB, 3 GB with --all-models)"
  "$VPY" "$ROOT/install/download_weights.py" $ALL || { echo "The weights download failed. Check the internet connection and run install/download_weights.sh again." >&2; exit 1; }
else echo "== 2/4  Model weights skipped"; fi
echo "== 3/4  Nuke"
if [ "$ADD" = 1 ]; then "$VPY" "$ROOT/install/add_to_nuke.py"; else
  echo "To make TAPTrack appear in Nuke, run:  install/add_to_nuke.sh"
  echo "or add this line to your Nuke init.py:   nuke.pluginAddPath('$ROOT/nuke')"
fi
echo "== 4/4  Check"
"$VPY" "$ROOT/install/check_install.py" --selftest
