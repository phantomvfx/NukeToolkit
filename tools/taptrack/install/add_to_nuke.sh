#!/usr/bin/env bash
# Add (or --remove) TAPTrack on Nuke's plugin path via one marked block in <nuke dir>/init.py. --nuke-dir DIR, --dry-run.
exec python3 "$(cd "$(dirname "$0")" && pwd)/add_to_nuke.py" "$@"
