"""Check a TAPTrack installation and say, in plain language, what to fix.

    python install/check_install.py [--selftest] [--json]

Run it with the worker venv's Python (install/check_install.ps1 / .sh do that for you). It never changes anything.
``--selftest`` additionally runs a tiny real tracking job (about 15 seconds) to prove the whole chain works.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEIGHTS = {  # file -> (size in bytes, which model needs it, required for the default setup)
    "bootstapnext_ckpt.npz": (776980182, "BootsTAPNext (default)", True),
    "bootstapir_checkpoint_v2.pt": (218886140, "BootsTAPIR (offline)", True),
    "tapnext_ckpt.npz": (776980182, "TAPNext", False),
    "causal_bootstapir_checkpoint.pt": (218887028, "BootsTAPIR (online)", False),
}


def venv_python() -> Path:
    env = os.environ.get("TAPTRACK_PYTHON")
    if env:
        return Path(env)
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


class Report:
    def __init__(self):
        self.items = []

    def add(self, level: str, what: str, fix: str = ""):
        self.items.append({"level": level, "what": what, "fix": fix})

    @property
    def ok(self) -> bool:
        return not any(i["level"] == "PROBLEM" for i in self.items)


def check_environment(rep: Report) -> None:
    py = venv_python()
    if not py.exists():
        rep.add("PROBLEM", "The tracker environment (a private Python with PyTorch) is not installed.",
                "Run install/install.ps1 (Windows) or install/install.sh (Linux/macOS).")
        return
    code = ("import json,sys\nout={'python':sys.version.split()[0]}\n"
            "try:\n import torch\n out['torch']=torch.__version__\n out['cuda']=torch.cuda.is_available()\n"
            " if out['cuda']:\n  p=torch.cuda.get_device_properties(0)\n  out['gpu']=p.name\n  out['vram_gb']=round(p.total_memory/2**30,1)\n"
            "except Exception as e:\n out['torch_error']=str(e)[:300]\n"
            "for m in ('numpy','cv2','einops','einshape','tree'):\n"
            " try:\n  __import__(m)\n except Exception as e:\n  out.setdefault('missing',[]).append(m)\n"
            "print(json.dumps(out))")
    p = subprocess.run([str(py), "-c", code], capture_output=True, text=True)
    try:
        info = json.loads(p.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        rep.add("PROBLEM", "The tracker's Python could not be started.", "Run install/install again to repair it. " + p.stderr[-200:])
        return
    rep.add("OK", "Python %s in %s" % (info["python"], py.parent.parent.name))
    if "torch_error" in info:
        rep.add("PROBLEM", "PyTorch will not start (%s)." % info["torch_error"], "Run install/install again to repair it.")
    else:
        rep.add("OK", "PyTorch %s" % info["torch"])
        if info.get("cuda"):
            msg = "Graphics card: %s, %s GB" % (info["gpu"], info["vram_gb"])
            rep.add("OK" if info["vram_gb"] >= 8 else "WARN", msg + ("" if info["vram_gb"] >= 8 else " (8 GB or more is recommended)"))
        else:
            rep.add("WARN", "No NVIDIA graphics card is available to PyTorch: tracking will run on the CPU, which is much slower.",
                    "Update the NVIDIA driver, or reinstall with install/install.ps1 -Cuda cu126.")
    if info.get("missing"):
        rep.add("PROBLEM", "Missing Python packages: " + ", ".join(info["missing"]), "Run install/install again to repair it.")


def check_tapnet(rep: Report) -> None:
    src = Path(os.environ.get("TAPTRACK_TAPNET", ROOT / "vendor" / "tapnet"))
    want = (ROOT / "install" / "tapnet_commit.txt").read_text().strip()
    if not (src / "tapnet").is_dir():
        rep.add("PROBLEM", "The tapnet source code is not installed.", "Run install/install again; it fetches the exact version.")
        return
    try:
        head = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        head = ""
    if head and head != want:
        rep.add("WARN", "tapnet is at %s, this version of TAPTrack was tested with %s." % (head[:8], want[:8]),
                "Run install/install again to move it to the tested version.")
    else:
        rep.add("OK", "tapnet source (%s)" % (want[:8]))


def check_weights(rep: Report) -> None:
    ck = Path(os.environ.get("TAPTRACK_CHECKPOINTS", ROOT / "checkpoints"))
    for name, (size, who, required) in WEIGHTS.items():
        f = ck / name
        if f.exists() and f.stat().st_size == size:
            rep.add("OK", "Model weights: %s (%s)" % (name, who))
        elif f.exists():
            rep.add("PROBLEM" if required else "WARN", "Model weights %s are incomplete (the download was interrupted)." % name,
                    "Delete the file and run install/download_weights.")
        elif required:
            rep.add("PROBLEM", "Model weights missing: %s (needed for %s)." % (name, who), "Run install/download_weights.")
        else:
            rep.add("INFO", "Optional weights not installed: %s (%s)." % (name, who), "install/download_weights -All fetches every model.")


def selftest(rep: Report) -> None:
    """Track four points over three frames of a generated image through the real worker."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        # started with a Python that has no OpenCV: run the same check with the tracker's own Python
        out = subprocess.run([str(venv_python()), str(Path(__file__).resolve()), "--selftest"], capture_output=True, text=True)
        line = next((l for l in out.stdout.splitlines() if "Self-test" in l), "")
        rep.add("OK" if out.returncode == 0 else "PROBLEM", line.split("] ", 1)[-1] or "Self-test failed: " + out.stderr[-300:],
                "" if out.returncode == 0 else "Run install/install again to repair it.")
        return
    tmp = Path(tempfile.mkdtemp(prefix="taptrack_selftest_"))
    rng = np.random.default_rng(1)
    base = cv2.resize(rng.random((24, 24, 3)).astype("float32"), (320, 320), interpolation=cv2.INTER_CUBIC)
    for i in range(3):
        img = np.roll(base, i * 2, axis=1)
        cv2.imwrite(str(tmp / ("f.%04d.png" % (1001 + i))), cv2.resize((img * 255).astype("uint8"), (256, 256)))
    job = {"version": 1, "model": "bootstapnext", "frames_dir": str(tmp), "frame_pattern": "f.%04d.png", "first": 1001, "last": 1003,
           "ref_frame": 1001, "plate_w": 320, "plate_h": 320, "pixel_aspect": 1.0, "work_w": 256, "work_h": 256,
           "queries": [[1001, 100.0, 100.0], [1001, 200.0, 220.0], [1001, 150.0, 80.0], [1001, 60.0, 250.0]],
           "device": "auto", "precision": "fp16", "chunk": 4}
    (tmp / "job.json").write_text(json.dumps(job))
    p = subprocess.run([str(venv_python()), str(ROOT / "worker" / "taptrack_worker.py"), str(tmp / "job.json")],
                       capture_output=True, text=True, env=dict(os.environ, TAPTRACK_LOG_DIR=str(tmp)))
    last = [json.loads(l) for l in p.stdout.splitlines() if l.strip().startswith("{")][-1:] or [{}]
    if p.returncode == 0 and (tmp / "result.json").exists():
        meta = json.loads((tmp / "result.json").read_text())["meta"]
        rep.add("OK", "Self-test: tracked 4 points on %s in %.1f s" % (meta.get("gpu", meta["device"]), meta["elapsed_s"]))
    else:
        err = last[0].get("error", {})
        rep.add("PROBLEM", "Self-test failed: " + err.get("message", "see the log"), err.get("hint", "") + " Log: " + err.get("log", ""))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    rep = Report()
    check_environment(rep)
    check_tapnet(rep)
    check_weights(rep)
    if a.selftest and rep.ok:
        selftest(rep)
    if a.json:
        print(json.dumps({"ok": rep.ok, "items": rep.items}, indent=1))
    else:
        for i in rep.items:
            print("[%-7s] %s" % (i["level"], i["what"]))
            if i["fix"]:
                print("          -> " + i["fix"])
        print()
        print("TAPTrack is ready to use." if rep.ok else "TAPTrack needs attention: fix the PROBLEM lines above, then run this check again.")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    sys.exit(main())
