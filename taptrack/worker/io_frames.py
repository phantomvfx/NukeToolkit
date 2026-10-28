"""Frame loading for the worker. Frames are display-referred 8-bit sRGB written by Nuke."""
from __future__ import annotations

import os
import re
from typing import Callable, Optional

import cv2
import numpy as np


class FrameError(Exception):
    """Raised for missing/unreadable frames (message is artist-readable)."""


def frame_path(frames_dir: str, pattern: str, frame: int) -> str:
    """Resolve a frame file. Accepts printf (%04d) and Nuke hash (####) patterns."""
    if "%" in pattern:
        name = pattern % frame
    else:
        def pad(m: "re.Match[str]") -> str:
            return str(frame).zfill(len(m.group(0)))
        name = re.sub(r"#+", pad, pattern)
    return os.path.join(frames_dir, name)


def load_frames(frames_dir: str, pattern: str, first: int, last: int,
                size: Optional[tuple[int, int]] = None,
                progress: Optional[Callable[[int, int], None]] = None) -> np.ndarray:
    """Load frames ``first..last`` inclusive as uint8 RGB ``[T, H, W, 3]``.

    ``size`` is ``(w, h)``; when given and different from the file size, frames are
    resized edge-to-edge (INTER_AREA down, INTER_LINEAR up), which is a pure per-axis
    scale in continuous coordinates, see ``coords.scale_image``.
    """
    frames = []
    n = last - first + 1
    for i, f in enumerate(range(first, last + 1)):
        p = frame_path(frames_dir, pattern, f)
        if not os.path.isfile(p):
            raise FrameError(f"Frame {f} was not rendered: {p}")
        img = cv2.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            raise FrameError(f"Frame {f} could not be read: {p}")
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if size is not None and (img.shape[1], img.shape[0]) != tuple(size):
            interp = cv2.INTER_AREA if size[0] * size[1] < img.shape[0] * img.shape[1] else cv2.INTER_LINEAR
            img = cv2.resize(img, tuple(size), interpolation=interp)
        frames.append(img)
        if progress:
            progress(i + 1, n)
    return np.stack(frames)


def to_model_range(frames_u8) -> "object":
    """uint8 [0,255] -> float [-1,1] (both TAPNext and TAPIR expect this). Works on torch tensors."""
    return frames_u8.float() / 255.0 * 2.0 - 1.0
