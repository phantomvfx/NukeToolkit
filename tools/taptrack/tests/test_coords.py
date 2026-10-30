"""Coordinate round-trips: 16:9, 2.39 anamorphic (pixel aspect 2.0), odd sizes."""
import numpy as np
import pytest

import coords

CASES = [
    ("16:9", 1920, 1080, 1.0, 512, 512),
    ("anamorphic 2.39", 2048, 858, 2.0, 512, 512),
    ("anamorphic squeeze plate", 1440, 1080, 2.0, 256, 256),
    ("odd", 1921, 1081, 1.0, 255, 253),
    ("portrait", 1080, 1920, 1.0, 256, 256),
]


@pytest.mark.parametrize("name,pw,ph,pa,ww,wh", CASES)
def test_plate_work_roundtrip(name, pw, ph, pa, ww, wh):
    rng = np.random.default_rng(1)
    xy = rng.random((200, 2)) * [pw, ph]
    back = coords.work_to_plate(coords.plate_to_work(xy, pw, ph, ww, wh), pw, ph, ww, wh)
    np.testing.assert_allclose(back, xy, atol=1e-9)


@pytest.mark.parametrize("name,pw,ph,pa,ww,wh", CASES)
def test_format_edges_map_to_work_edges(name, pw, ph, pa, ww, wh):
    # Nuke bottom-left (0,0) is the work image bottom-left corner = (0, wh) with y down.
    got = coords.plate_to_work(np.array([[0, 0], [pw, ph], [pw / 2, ph / 2]]), pw, ph, ww, wh)
    np.testing.assert_allclose(got, [[0, wh], [ww, 0], [ww / 2, wh / 2]], atol=1e-9)


def test_pixel_centres_at_half():
    # The centre of Nuke pixel (0,0) is (0.5,0.5); in a same-size image that is the centre of
    # the bottom-left pixel = (0.5, h-0.5) with y down.
    got = coords.plate_to_work(np.array([[0.5, 0.5]]), 100, 50, 100, 50)
    np.testing.assert_allclose(got, [[0.5, 49.5]])


def test_scale_independent_axes():
    got = coords.plate_to_work(np.array([[1000.0, 270.0]]), 2000, 1080, 500, 108)
    np.testing.assert_allclose(got, [[250.0, 81.0]])           # x*0.25, (1080-270)*0.1


def test_integer_centre_shift():
    xy = np.array([[10.25, 3.75]])
    np.testing.assert_allclose(coords.integer_centre_to_continuous(coords.continuous_to_integer_centre(xy)), xy)
    np.testing.assert_allclose(coords.continuous_to_integer_centre(xy), [[9.75, 3.25]])


@pytest.mark.parametrize("pa", [1.0, 2.0, 1.5, 0.5])
def test_square_roundtrip(pa):
    xy = np.random.default_rng(3).random((50, 2)) * 1000
    np.testing.assert_allclose(coords.square_to_plate(coords.plate_to_square(xy, pa), pa), xy)


def test_square_stretches_x_by_pixel_aspect():
    np.testing.assert_allclose(coords.plate_to_square(np.array([[100.0, 50.0]]), 2.0), [[200.0, 50.0]])


def test_scale_image_edge_aligned():
    got = coords.scale_image(np.array([[0.0, 0.0], [640.0, 360.0]]), 640, 360, 256, 256)
    np.testing.assert_allclose(got, [[0, 0], [256, 256]])
