#!/usr/bin/env bash
# Download the model weights into checkpoints/ (about 1 GB; pass --all for every model, about 3 GB).
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${TAPTRACK_PYTHON:-$ROOT/.venv/bin/python}"; [ -x "$PY" ] || PY=python3
exec "$PY" "$ROOT/install/download_weights.py" "$@"
