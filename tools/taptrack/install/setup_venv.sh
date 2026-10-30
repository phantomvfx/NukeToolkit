#!/usr/bin/env bash
# Create the TAPTrack worker environment (torch lives here, never inside Nuke) and fetch the pinned tapnet source.
# Usage: install/setup_venv.sh [cu126|cpu] [venv_dir]      (normally you run install/install.sh)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CUDA="${1:-cu126}"
VENV="${2:-$ROOT/.venv}"
export PIP_CACHE_DIR="${PIP_CACHE_DIR:-$ROOT/.pip-cache}"

fail() { echo; echo "TAPTrack setup stopped: $1" >&2; exit 1; }

PY=""
for c in python3.11 python3.12 python3.10 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if (3,10) <= sys.version_info[:2] <= (3,12) else 1)'; then PY="$c"; break; fi
done
[ -n "$PY" ] || fail "Python 3.10, 3.11 or 3.12 was not found. Install Python 3.11 and run this again."
command -v git >/dev/null 2>&1 || fail "Git was not found. Install git and run this again."

if [ "$CUDA" != "cpu" ] && [ "$(uname)" = "Darwin" ]; then
  echo "macOS has no CUDA: installing the CPU version (tracking will be slow)."; CUDA=cpu
elif [ "$CUDA" != "cpu" ] && ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "No NVIDIA driver found: installing the CPU version (tracking will be slow)."; CUDA=cpu
fi

echo "Creating the tracker environment in $VENV ..."
"$PY" -m venv "$VENV" || fail "Creating the Python environment failed."
VPY="$VENV/bin/python"
"$VPY" -m pip install --upgrade pip || fail "Updating pip failed."
echo "Installing PyTorch ($CUDA) - this downloads about 2.5 GB the first time ..."
if [ "$(uname)" = "Darwin" ]; then
  "$VPY" -m pip install torch torchvision || fail "Installing PyTorch failed."
else
  "$VPY" -m pip install torch torchvision --index-url "https://download.pytorch.org/whl/$CUDA" || fail "Installing PyTorch failed."
fi
"$VPY" -m pip install -r "$ROOT/install/requirements-worker.txt" || fail "Installing the other Python packages failed."

VENDOR="$ROOT/vendor/tapnet"
COMMIT="$(tr -d '[:space:]' < "$ROOT/install/tapnet_commit.txt")"
[ -d "$VENDOR" ] || git clone https://github.com/google-deepmind/tapnet.git "$VENDOR" || fail "Downloading the tapnet source failed."
git -C "$VENDOR" fetch --quiet origin
git -C "$VENDOR" checkout --quiet "$COMMIT" || fail "Selecting the tested tapnet version failed."
echo "tapnet pinned at $COMMIT"
echo "Environment ready: $VPY"
