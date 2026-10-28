"""nuke/taptrack/coords.py (stdlib) must agree with worker/coords.py (numpy) everywhere."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

import coords as wcoords

_spec = importlib.util.spec_from_file_location("nuke_coords", Path(__file__).parent.parent / "nuke" / "taptrack" / "coords.py")
ncoords = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ncoords)

SIZES = [(1920, 1080, 512, 512), (2048, 858, 256, 256), (1921, 1081, 255, 253), (1080, 1920, 256, 256)]


@pytest.mark.parametrize("pw,ph,ww,wh", SIZES)
def test_plate_work_agree(pw, ph, ww, wh):
    pts = np.random.default_rng(5).random((100, 2)) * [pw, ph]
    w = wcoords.plate_to_work(pts, pw, ph, ww, wh)
    for p, wp in zip(pts, w):
        assert ncoords.plate_to_work(tuple(p), pw, ph, ww, wh) == pytest.approx(tuple(wp), abs=1e-9)
        assert ncoords.work_to_plate(tuple(wp), pw, ph, ww, wh) == pytest.approx(tuple(p), abs=1e-9)


@pytest.mark.parametrize("pa", [1.0, 2.0, 0.5])
def test_square_agree(pa):
    p = (123.5, 45.25)
    assert ncoords.plate_to_square(p, pa) == pytest.approx(tuple(wcoords.plate_to_square(np.array(p), pa)))
    assert ncoords.square_to_plate(ncoords.plate_to_square(p, pa), pa) == pytest.approx(p)


def test_scale_image_agree():
    p = (100.0, 50.0)
    assert ncoords.scale_image(p, 640, 360, 256, 256) == pytest.approx(tuple(wcoords.scale_image(np.array(p), 640, 360, 256, 256)))


def test_grid_and_cap():
    g = ncoords.grid_points(1920, 1080, 120)
    assert len(g) == 16 * 9 and g[0] == (60.0, 60.0) and g[-1] == (1860.0, 1020.0)
    assert len(ncoords.cap_points(g, 10)) == 10 and ncoords.cap_points(g, 0) == g
    inside = ncoords.grid_points(1920, 1080, 120, keep=lambda x, y: x < 500)
    assert inside and all(p[0] < 500 for p in inside)
