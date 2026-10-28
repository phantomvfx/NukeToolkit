"""Where the points to track come from (all in plate pixels, Nuke convention).

* ``tracker4_points``  existing tracks of a Tracker4 at the reference frame.
* ``mask_grid``        grid inside the alpha of a mask at the reference frame.
* ``full_grid``        grid over the whole format.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from . import coords, export

Point = Tuple[float, float]


class QueryError(Exception):
    """A problem the artist can fix; the message is shown as-is."""


def tracker4_points(node, frame: int) -> Tuple[List[Point], List[str]]:
    """(x, y) of every track enabled at ``frame``, plus their names."""
    if node is None or node.Class() != "Tracker4":
        raise QueryError("Pick a Tracker4 node to take the points from.")
    knob = node["tracks"]
    pts: List[Point] = []
    names: List[str] = []
    for r in range(export.table_rows(knob)):
        if export.evaluate(node, r, "enable", frame) < 0.5:
            continue
        pts.append((export.evaluate(node, r, "track_x", frame), export.evaluate(node, r, "track_y", frame)))
        names.append("track %d" % (r + 1))
    if not pts:
        raise QueryError("The Tracker4 '%s' has no enabled tracks on frame %d." % (node.name(), frame))
    return pts, names


def mask_grid(mask_node, frame: int, plate_w: float, plate_h: float, spacing: float,
              max_points: int, channel: str = "rgba.alpha") -> List[Point]:
    """Grid points whose mask alpha is above 0.5 on ``frame``."""
    def inside(x: float, y: float) -> bool:
        return mask_node.sample(channel, x, y, 0.5, 0.5, frame=frame) > 0.5

    pts = coords.grid_points(plate_w, plate_h, spacing, keep=inside)
    if not pts:
        raise QueryError("The mask is empty on frame %d (no grid point falls inside it). "
                         "Connect a Roto to the mask input, or lower the grid spacing." % frame)
    return coords.cap_points(pts, max_points)


def full_grid(plate_w: float, plate_h: float, spacing: float, max_points: int) -> List[Point]:
    """Grid over the whole format, thinned to ``max_points``."""
    return coords.cap_points(coords.grid_points(plate_w, plate_h, spacing), max_points)
