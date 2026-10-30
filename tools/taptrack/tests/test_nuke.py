"""Runs tests/nuke_tests/ inside headless Nuke (terminal mode) and reports the result.

Set TAPTRACK_NUKE to a Nuke executable; otherwise Nuke 16/17 under L:\\Apps or /usr/local/Nuke* is tried.
"""
import glob
import json
import os
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

import synth
from conftest import OUT, ROOT, Scene

pytestmark = pytest.mark.nuke


def _nuke_exe():
    env = os.environ.get("TAPTRACK_NUKE")
    if env:
        return env
    for pat in (r"L:\Apps\Nuke16*\Nuke16*.exe", r"L:\Apps\Nuke17*\Nuke17*.exe", "/usr/local/Nuke16*/Nuke16*",
                "/usr/local/Nuke17*/Nuke17*"):
        hits = [h for h in sorted(glob.glob(pat)) if h.lower().endswith((".exe", "")) and "python" not in h.lower()]
        if hits:
            return hits[-1]
    return None


def _plate_data():
    sc = Scene("nukeplate", 640, 360, 10, 5, nx=4, ny=3)
    d = OUT / "nuke_plate"
    d.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(sc.plate):
        cv2.imwrite(str(d / f"plate.{1001 + i:04d}.png"), cv2.cvtColor(f, cv2.COLOR_RGB2BGR))
    truth = sc.truth()
    data = {"pattern": str(d / "plate.####.png"), "first": 1001, "last": 1010, "ref": 1006,
            "pts": sc.pts.tolist(), "truth": truth.tolist(), "cache": str(OUT / "nuke_cache")}
    p = OUT / "nuke_test_data.json"
    p.write_text(json.dumps(data))
    return p


def test_nuke_suite():
    exe = _nuke_exe()
    if not exe:
        pytest.skip("No Nuke executable found (set TAPTRACK_NUKE)")
    env = dict(os.environ, TAPTRACK_TEST_DATA=str(_plate_data()), TAPTRACK_LOG_DIR=str(OUT / "logs"))
    p = subprocess.run([exe, "-t", str(ROOT / "tests" / "nuke_tests" / "run.py")], capture_output=True, text=True, env=env)
    print(p.stdout[-6000:])
    assert "ALL PASSED" in p.stdout, p.stdout[-4000:] + p.stderr[-2000:]
