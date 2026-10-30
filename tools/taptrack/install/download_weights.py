"""Download TAPTrack model weights into ./checkpoints (stdlib only).

Weights are Apache-2.0 (https://huggingface.co/google/tapnet). They are never
committed to the repo. Usage:

    python install/download_weights.py [--only bootstapnext bootstapir ...] [--dest DIR]
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

HF = "https://huggingface.co/google/tapnet/resolve/main/"
GCS = "https://storage.googleapis.com/dm-tapnet/"

# key -> list of (filename, [urls in preference order], expected size in bytes)
WEIGHTS = {
    "bootstapnext": [("bootstapnext_ckpt.npz", [HF + "bootstapnext_ckpt.npz", GCS + "tapnext/bootstapnext_ckpt.npz"], 776980182)],
    "tapnext": [("tapnext_ckpt.npz", [GCS + "tapnext/tapnext_ckpt.npz"], 776980182)],
    "bootstapir": [("bootstapir_checkpoint_v2.pt", [HF + "bootstapir_checkpoint_v2.pt", GCS + "bootstap/bootstapir_checkpoint_v2.pt"], 218886140)],
    "bootstapir_online": [("causal_bootstapir_checkpoint.pt", [HF + "causal_bootstapir_checkpoint.pt", GCS + "bootstap/causal_bootstapir_checkpoint.pt"], 218887028)],
}
DEFAULT = ["bootstapnext", "bootstapir"]


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def fetch(urls, dest: Path, size: int) -> None:
    last = None
    for url in urls:
        try:
            tmp = dest.with_suffix(dest.suffix + ".part")
            with urllib.request.urlopen(url, timeout=60) as r, open(tmp, "wb") as f:
                total = int(r.headers.get("Content-Length", size))
                done = 0
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    print(f"\r  {dest.name}: {done / 1e6:7.1f} / {total / 1e6:.1f} MB", end="", flush=True)
            print()
            if tmp.stat().st_size != size:
                raise OSError(f"size mismatch: got {tmp.stat().st_size}, expected {size}")
            os.replace(tmp, dest)
            return
        except Exception as exc:  # try the next mirror
            last = exc
            print(f"\n  failed ({url}): {exc}")
    raise SystemExit(f"Could not download {dest.name}: {last}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", nargs="*", choices=sorted(WEIGHTS), default=DEFAULT)
    ap.add_argument("--all", action="store_true", help="every model (about 2 GB more)")
    ap.add_argument("--dest", default=str(Path(__file__).resolve().parent.parent / "checkpoints"))
    args = ap.parse_args()
    dest = Path(args.dest)
    if args.all:
        args.only = sorted(WEIGHTS)
    dest.mkdir(parents=True, exist_ok=True)
    for key in args.only:
        for name, urls, size in WEIGHTS[key]:
            path = dest / name
            if path.exists() and path.stat().st_size == size:
                print(f"ok  {name} (already present)")
                continue
            print(f"get {name}")
            fetch(urls, path, size)
        print(f"ok  {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
