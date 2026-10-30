"""Coordinate conversions on the Nuke side (stdlib only).

Same math, same conventions as ``worker/coords.py`` (see its docstring for the space
definitions); ``tests/test_coords_parity.py`` checks the two agree. Points are ``(x, y)``
tuples. All values are continuous pixels with centres at +0.5, and plate space is Nuke's:
origin bottom-left, y up, measured in the *format* (never the bbox).
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

Point = Tuple[float, float]


def plate_to_work(p: Point, plate_w: float, plate_h: float, work_w: float, work_h: float) -> Point:
    """Plate pixels (y up) -> work image pixels (y down)."""
    return (p[0] * (work_w / plate_w), (plate_h - p[1]) * (work_h / plate_h))


def work_to_plate(p: Point, plate_w: float, plate_h: float, work_w: float, work_h: float) -> Point:
    """Work image pixels (y down) -> plate pixels (y up)."""
    return (p[0] * (plate_w / work_w), plate_h - p[1] * (plate_h / work_h))


def scale_image(p: Point, src_w: float, src_h: float, dst_w: float, dst_h: float) -> Point:
    """Edge-aligned rescale between two image sizes (both y down)."""
    return (p[0] * (dst_w / src_w), p[1] * (dst_h / src_h))


def plate_to_square(p: Point, pixel_aspect: float) -> Point:
    """Plate pixels -> square (desqueezed) pixels."""
    return (p[0] * pixel_aspect, p[1])


def square_to_plate(p: Point, pixel_aspect: float) -> Point:
    """Inverse of :func:`plate_to_square`."""
    return (p[0] / pixel_aspect, p[1])


def grid_points(plate_w: float, plate_h: float, spacing: float,
                keep: Optional["callable"] = None) -> List[Point]:
    """Regular grid of pixel centres over the format, ``spacing`` px apart.

    The grid is centred in the format (equal margin on both sides). ``keep(x, y)`` can reject
    points (used for the mask test).
    """
    spacing = max(1.0, float(spacing))
    nx = max(1, int(plate_w // spacing))
    ny = max(1, int(plate_h // spacing))
    x0 = (plate_w - (nx - 1) * spacing) / 2.0
    y0 = (plate_h - (ny - 1) * spacing) / 2.0
    out: List[Point] = []
    for j in range(ny):
        for i in range(nx):
            p = (x0 + i * spacing, y0 + j * spacing)
            if keep is None or keep(p[0], p[1]):
                out.append(p)
    return out


def cap_points(points: Sequence[Point], max_points: int) -> List[Point]:
    """Deterministic, evenly spread subsample down to ``max_points``."""
    n = len(points)
    if max_points <= 0 or n <= max_points:
        return list(points)
    return [points[int(i * n / max_points)] for i in range(max_points)]
