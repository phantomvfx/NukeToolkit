"""VRAM budget: 500 points x 200 frames must stay under 11 GB on a 12 GB card.

Run with:  pytest -m slow tests/test_vram.py -s
Peak is measured two ways: torch's allocator stats (from the worker) and nvidia-smi sampled
every 0.2 s during the run (includes the CUDA context, i.e. what the driver really holds).
"""
import subprocess
import threading
import time

import pytest

import synth
from conftest import OUT, Scene

pytestmark = [pytest.mark.gpu, pytest.mark.slow]

LIMIT_MB = 11 * 1024


def _smi_used_mb() -> float:
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True).stdout.split()
    return float(out[0])


class SmiPeak:
    def __enter__(self):
        self.base, self.peak, self._stop = _smi_used_mb(), 0.0, False
        self._t = threading.Thread(target=self._poll, daemon=True)
        self._t.start()
        return self

    def _poll(self):
        while not self._stop:
            self.peak = max(self.peak, _smi_used_mb())
            time.sleep(0.2)

    def __exit__(self, *a):
        self._stop = True
        self._t.join()


@pytest.fixture(scope="module")
def long_scene():
    return Scene("long", 640, 360, 200, 100, nx=25, ny=20)


@pytest.mark.parametrize("model", ["bootstapnext", "bootstapir", "bootstapir_online"])
def test_500_points_200_frames(long_scene, model):
    assert len(long_scene.pts) == 500
    with SmiPeak() as smi:
        res, _ = long_scene.run(model)
    m = res["meta"]
    fps = m["frame_passes"] / m["track_s"]
    print(f"\n{model}: torch peak alloc={m['peak_vram_allocated_mb']}MB reserved={m['peak_vram_reserved_mb']}MB "
          f"| nvidia-smi peak={smi.peak:.0f}MB (idle baseline {smi.base:.0f}MB) "
          f"| load={m['load_s']}s track={m['track_s']}s ({fps:.1f} frame-passes/s)")
    assert smi.peak < LIMIT_MB
    assert m["peak_vram_reserved_mb"] < LIMIT_MB
