import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

import sam3matte_roto_core as rc


def disc(w, h, cx, cy, r, inner=None):
    """Grey mask, 255 inside the disc (pixel centres), 0 outside. inner: hole radius for a ring."""
    out = bytearray(w * h)
    r2 = r * r
    i2 = None if inner is None else inner * inner
    for y in range(h):
        dy = y + 0.5 - cy
        for x in range(w):
            d2 = (x + 0.5 - cx) ** 2 + dy * dy
            if d2 <= r2 and (i2 is None or d2 > i2):
                out[y * w + x] = 255
    return bytes(out)


def blob(w, h, x0, y0, bw, bh):
    out = bytearray(w * h)
    for y in range(y0, y0 + bh):
        for x in range(x0, x0 + bw):
            out[y * w + x] = 255
    return bytes(out)


def signed_area(pts):
    s = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        s += x0 * y1 - x1 * y0
    return s / 2.0


def test_outline_of_disc():
    pts = rc.largest_outline(disc(300, 240, 150, 120, 100), 300, 240)
    assert pts is not None
    for x, y in pts:
        assert abs(math.hypot(x - 150, y - 120) - 100) < 1.5
    assert abs(abs(signed_area(pts)) - math.pi * 100 ** 2) / (math.pi * 100 ** 2) < 0.03


def test_resample_count_and_spacing():
    pts = rc.largest_outline(disc(300, 240, 150, 120, 100), 300, 240)
    rs = rc.resample_closed(pts, 32)
    assert len(rs) == 32
    d = [math.dist(rs[i], rs[(i + 1) % 32]) for i in range(32)]
    assert max(d) / min(d) < 1.5


def test_largest_region_wins():
    a = disc(300, 200, 100, 100, 60)
    b = disc(300, 200, 250, 100, 20)
    data = bytes(max(p, q) for p, q in zip(a, b))
    pts = rc.largest_outline(data, 300, 200)
    cx = sum(p[0] for p in pts) / len(pts)
    cy = sum(p[1] for p in pts) / len(pts)
    assert math.hypot(cx - 100, cy - 100) < 5


def test_hole_ignored():
    pts = rc.largest_outline(disc(300, 240, 150, 120, 80, inner=40), 300, 240)
    assert pts is not None
    for x, y in pts:
        assert abs(math.hypot(x - 150, y - 120) - 80) < 1.5


def test_empty_and_tiny_are_none():
    assert rc.largest_outline(bytes(64 * 48), 64, 48) is None
    assert rc.largest_outline(blob(64, 48, 10, 10, 3, 3), 64, 48) is None
    assert rc.largest_outline(blob(64, 48, 10, 10, 5, 5), 64, 48) is not None


def test_below_threshold_is_none():
    assert rc.largest_outline(bytes([100]) * (64 * 48), 64, 48) is None
    assert rc.largest_outline(bytes([100]) * (64 * 48), 64, 48, threshold=100) is not None


def test_region_touching_border():
    pts = rc.largest_outline(bytes([255]) * (64 * 48), 64, 48)
    assert pts is not None
    for cx, cy in [(0, 0), (64, 0), (64, 48), (0, 48)]:
        assert min(math.hypot(x - cx, y - cy) for x, y in pts) <= 0.5
    for x, y in pts:
        assert -0.01 <= x <= 64.01 and -0.01 <= y <= 48.01


def test_diagonal_pixels_are_separate_regions():
    # a 5x5 block and a single pixel touching it only at a corner: 4-connected, so the pixel is not part of it
    data = bytearray(blob(40, 30, 10, 10, 5, 5))
    data[15 * 40 + 15] = 255
    pts = rc.largest_outline(bytes(data), 40, 30)
    assert max(p[0] for p in pts) < 15.6 and max(p[1] for p in pts) < 15.6


def test_clockwise():
    pts = rc.shape_for_frame(disc(300, 240, 150, 120, 100), 300, 240, 32)
    assert len(pts) == 32
    assert signed_area(pts) > 0


def test_counter_clockwise_input_is_flipped():
    pts = rc.largest_outline(disc(300, 240, 150, 120, 100), 300, 240)
    rs = rc.resample_closed(pts[::-1], 32)
    assert signed_area(rs) < 0
    out = rc.shape_for_frame(disc(300, 240, 150, 120, 100), 300, 240, 32)
    assert signed_area(out) > 0


def test_align_start_follows_previous():
    prev = None
    p0s = []
    for t in range(5):
        data = disc(300, 240, 100 + 3 * t, 120, 60)
        pts = rc.shape_for_frame(data, 300, 240, 32, prev=prev)
        if prev is None:
            assert pts[0][1] == min(p[1] for p in pts)
        p0s.append(pts[0])
        prev = pts
    for a, b in zip(p0s, p0s[1:]):
        assert math.dist(a, b) < 12


def test_align_start_unit():
    pts = [(5.0, 5.0), (1.0, 0.0), (0.0, 0.0), (9.0, 9.0)]
    assert rc.align_start(pts, None)[0] == (0.0, 0.0)          # topmost, then leftmost
    assert rc.align_start(pts, [(8.0, 8.0)])[0] == (9.0, 9.0)   # nearest to prev[0]
    assert rc.align_start(pts, [(8.0, 8.0)]) == [(9.0, 9.0), (5.0, 5.0), (1.0, 0.0), (0.0, 0.0)]


def test_bezier_handles_circle():
    n = 32
    pts = [(60 * math.cos(2 * math.pi * i / n), 60 * math.sin(2 * math.pi * i / n)) for i in range(n)]
    hs = rc.bezier_handles(pts)
    assert len(hs) == n
    spacing = math.dist(pts[0], pts[1])
    for (px, py), (hx, hy) in zip(pts, hs):
        mag = math.hypot(hx, hy)
        radial = (hx * px + hy * py) / math.hypot(px, py)
        assert abs(radial) < 0.05 * mag
        assert abs(mag - spacing / 3) < 0.1 * (spacing / 3)


def test_to_plate():
    assert rc.to_plate([(0, 0), (512, 270)], 512, 270, 2048, 1080) == [(0.0, 1080.0), (2048.0, 0.0)]


def test_shape_for_frame_speed():
    data = disc(512, 270, 256, 135, 120)
    t0 = time.perf_counter()
    for _ in range(20):
        pts = rc.shape_for_frame(data, 512, 270, 32)
    mean = (time.perf_counter() - t0) / 20
    print("shape_for_frame mean %.4f s" % mean)
    assert pts is not None and len(pts) == 32
    assert mean < 0.15
