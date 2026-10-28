import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "worker"))
sys.path.insert(0, str(Path(__file__).parent))

import synth  # noqa: E402

OUT = Path(__file__).parent / "_out"


def _gpu_ready() -> str:
    if not synth.VENV_PY.exists():
        return "worker venv missing (run install/setup_venv)"
    if not (ROOT / "checkpoints" / "bootstapnext_ckpt.npz").exists():
        return "weights missing (run install/download_weights)"
    return ""


def pytest_collection_modifyitems(config, items):
    reason = _gpu_ready()
    if reason:
        for it in items:
            if "gpu" in it.keywords:
                it.add_marker(pytest.mark.skip(reason=reason))


class Scene:
    """A synthetic shot: plate frames + ground truth, with frames written per work size."""

    def __init__(self, name, plate_w, plate_h, n, ref, seed=7, amp=1.0, nx=8, ny=6):
        self.name, self.pw, self.ph, self.n, self.ref = name, plate_w, plate_h, n, ref
        base = synth.textured_image(int(plate_w * 1.6), int(plate_h * 1.6), seed)
        self.mats = synth.animation(n, plate_w, plate_h, amp)
        self.plate = synth.render_plate(base, self.mats, plate_w, plate_h)
        self.pts = synth.grid_points(plate_w, plate_h, nx, ny)
        self.dirs = {}

    def frames_dir(self, side):
        if side not in self.dirs:
            d = OUT / f"{self.name}_{side}"
            if not (d / f"f.{1001 + self.n - 1:04d}.png").exists():
                synth.write_frames(self.plate, d, side, side)
            self.dirs[side] = d
        return self.dirs[side]

    def truth(self, ref=None, pts=None):
        return synth.truth_tracks(self.pts if pts is None else pts, self.mats,
                                  self.ref if ref is None else ref, self.ph)

    def job(self, model, side, **kw):
        return synth.make_job(model, self.frames_dir(side), self.n, self.ref, self.pw, self.ph,
                              side, side, self.pts, **kw)

    def run(self, model, **kw):
        side = 256 if "next" in model else 512
        return synth.run_worker(self.job(model, side, **kw), OUT / f"run_{self.name}_{model}")


@pytest.fixture(scope="session")
def scene_hd():
    return Scene("hd", 640, 360, 24, 12)
