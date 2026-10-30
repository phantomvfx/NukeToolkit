#!/usr/bin/env bash
# Check the installation and say what to fix. Pass --selftest to also run a tiny real tracking job (about 15 s).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${TAPTRACK_PYTHON:-$ROOT/.venv/bin/python}"; [ -x "$PY" ] || PY=python3
exec "$PY" "$ROOT/install/check_install.py" "$@"
