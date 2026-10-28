"""Coordinate conversions for the TAPTrack worker (numpy).

Mirrored, with identical math, by ``nuke/taptrack/coords.py`` (stdlib only).
Keep the two files in sync; ``tests/test_coords.py`` runs the same cases on both.

Spaces
------
plate   Nuke format pixels. Continuous, origin bottom-left, y up, pixel centres at
        +0.5 (so the format spans [0, W] x [0, H]). Always the *format*, never the bbox.
image   Same continuous convention but origin top-left, y down. This is how rendered
        frames are laid out on disk and what the models see.
work    ``image`` space of the frames rendered for the worker (work_w x work_h).
        Nuke's Reformat uses "distort" so the whole format maps edge-to-edge onto the
        work frame; plate <-> work is therefore a pure per-axis scale (+ y flip).
model   Pixel space of the tensor fed to the network. Equal to ``work`` unless the model
        needs a fixed size (TAPNext: 256x256), in which case the worker resizes
        edge-to-edge and the mapping is again a pure per-axis scale.

Pixel-centre convention of the networks (see docs/TAPNET_NOTES.md):
  * TAPNext  : continuous, centres at +0.5  -> identical to ours, no shift.
  * TAPIR    : integer-centred (pixel i has its centre at i) -> shift by -0.5 going in,
               +0.5 coming out.

Pixel aspect: it never enters the plate<->work scale (a distort reformat maps format
edges to edges whatever the squeeze, and all models run on squashed square frames anyway).
It only matters when fitting rotation/scale, which must be done in square (desqueezed)
pixels: see ``plate_to_square`` / ``square_to_plate``.
"""
from __future__ import annotations

import numpy as np


def plate_to_work(xy: np.ndarray, plate_w: float, plate_h: float,
                  work_w: float, work_h: float) -> np.ndarray:
    """Nuke plate pixels (x right, y up) -> work image pixels (x right, y down)."""
    xy = np.asarray(xy, dtype=np.float64)
    out = np.empty_like(xy)
    out[..., 0] = xy[..., 0] * (work_w / plate_w)
    out[..., 1] = (plate_h - xy[..., 1]) * (work_h / plate_h)
    return out


def work_to_plate(xy: np.ndarray, plate_w: float, plate_h: float,
                  work_w: float, work_h: float) -> np.ndarray:
    """Work image pixels (x right, y down) -> Nuke plate pixels (x right, y up)."""
    xy = np.asarray(xy, dtype=np.float64)
    out = np.empty_like(xy)
    out[..., 0] = xy[..., 0] * (plate_w / work_w)
    out[..., 1] = plate_h - xy[..., 1] * (plate_h / work_h)
    return out


def scale_image(xy: np.ndarray, src_w: float, src_h: float,
                dst_w: float, dst_h: float) -> np.ndarray:
    """Edge-aligned continuous rescale between two image sizes (both y down)."""
    xy = np.asarray(xy, dtype=np.float64)
    out = np.empty_like(xy)
    out[..., 0] = xy[..., 0] * (dst_w / src_w)
    out[..., 1] = xy[..., 1] * (dst_h / src_h)
    return out


def continuous_to_integer_centre(xy: np.ndarray) -> np.ndarray:
    """Centres-at-+0.5 -> centres-at-integer (TAPIR input)."""
    return np.asarray(xy, dtype=np.float64) - 0.5


def integer_centre_to_continuous(xy: np.ndarray) -> np.ndarray:
    """Centres-at-integer -> centres-at-+0.5 (TAPIR output)."""
    return np.asarray(xy, dtype=np.float64) + 0.5


def plate_to_square(xy: np.ndarray, pixel_aspect: float) -> np.ndarray:
    """Plate pixels -> square (desqueezed) pixels: x is stretched by the pixel aspect."""
    xy = np.array(xy, dtype=np.float64, copy=True)
    xy[..., 0] *= pixel_aspect
    return xy


def square_to_plate(xy: np.ndarray, pixel_aspect: float) -> np.ndarray:
    """Inverse of ``plate_to_square``."""
    xy = np.array(xy, dtype=np.float64, copy=True)
    xy[..., 0] /= pixel_aspect
    return xy
