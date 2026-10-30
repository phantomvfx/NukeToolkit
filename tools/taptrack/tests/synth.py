"""Synthetic tracking data with exact ground truth (no Nuke needed).

A textured image is warped by a known per-frame similarity transform. Ground truth for a
point seen at the reference frame is  M_f @ inv(M_ref) @ p  in plate coordinates, using the
same continuous / bottom-left / centres-at-+0.5 convention the worker reports in.
"""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
VENV_PY = ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
WORKER = ROOT / "worker" / "taptrack_worker.py"


def textured_image(w: int, h: int, seed: int = 7) -> np.ndarray:
    """Multi-octave noise + random shapes, uint8 RGB. Plenty of trackable detail."""
    rng = np.random.default_rng(seed)
    img = np.zeros((h, w, 3), np.float32)
    for octave, amp in ((4, 0.55), (16, 0.3), (64, 0.18), (160, 0.1)):
        n = rng.random((max(2, h * octave // 256 + 2), max(2, w * octave // 256 + 2), 3)).astype(np.float32)
        img += amp * cv2.resize(n, (w, h), interpolation=cv2.INTER_CUBIC)
    img = (img - img.min()) / (img.max() - img.min())
    img = (img * 255).astype(np.uint8)
    for _ in range(80):
        c = tuple(int(v) for v in rng.integers(0, 255, 3))
        p = (int(rng.integers(0, w)), int(rng.integers(0, h)))
        if rng.random() < 0.5:
            cv2.circle(img, p, int(rng.integers(3, 18)), c, -1, cv2.LINE_AA)
        else:
            q = (p[0] + int(rng.integers(-30, 30)), p[1] + int(rng.integers(-30, 30)))
            cv2.rectangle(img, p, q, c, -1, cv2.LINE_AA)
    return img


def similarity(tx: float, ty: float, deg: float, scale: float, cx: float, cy: float) -> np.ndarray:
    """3x3 image-space (y down, continuous) similarity about (cx, cy), then translated."""
    a = math.radians(deg)
    c, s = math.cos(a) * scale, math.sin(a) * scale
    R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], np.float64)
    Tc = np.array([[1, 0, cx], [0, 1, cy], [0, 0, 1]], np.float64)
    Tn = np.array([[1, 0, -cx], [0, 1, -cy], [0, 0, 1]], np.float64)
    Tt = np.array([[1, 0, tx], [0, 1, ty], [0, 0, 1]], np.float64)
    return Tt @ Tc @ R @ Tn


def animation(n: int, w: int, h: int, amp: float = 1.0) -> list:
    out = []
    for i in range(n):
        t = i / max(1, n - 1)
        out.append(similarity(amp * 0.06 * w * math.sin(2 * math.pi * t), amp * 0.04 * h * t,
                              amp * 8.0 * math.sin(2 * math.pi * t * 0.75), 1.0 + amp * 0.12 * t, w / 2, h / 2))
    return out


def render_plate(base: np.ndarray, mats: list, w: int, h: int) -> list:
    """Warp ``base`` (>= plate size) into plate frames; mats are continuous-space 3x3."""
    half_up = np.array([[1, 0, 0.5], [0, 1, 0.5], [0, 0, 1]], np.float64)   # cv index -> continuous
    half_dn = np.array([[1, 0, -0.5], [0, 1, -0.5], [0, 0, 1]], np.float64)
    off = np.array([[1, 0, (base.shape[1] - w) / 2], [0, 1, (base.shape[0] - h) / 2], [0, 0, 1]], np.float64)
    frames = []
    for M in mats:
        # plate_cont = M @ (base_cont - off) ; build cv matrix mapping base index -> plate index
        Mc = half_dn @ M @ np.linalg.inv(off) @ half_up
        frames.append(cv2.warpAffine(base, Mc[:2], (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT))
    return frames


def to_nuke(xy_img: np.ndarray, h: int) -> np.ndarray:
    out = np.array(xy_img, dtype=np.float64)
    out[..., 1] = h - out[..., 1]
    return out


def truth_tracks(points_nuke_ref: np.ndarray, mats: list, ref: int, h: int) -> np.ndarray:
    """Ground truth ``[Q, T, 2]`` in plate (Nuke) coordinates."""
    p_img = to_nuke(points_nuke_ref, h)                      # y flip is its own inverse
    ph = np.concatenate([p_img, np.ones((len(p_img), 1))], 1)
    inv_ref = np.linalg.inv(mats[ref])
    out = np.stack([(M @ inv_ref @ ph.T).T[:, :2] for M in mats], axis=1)
    return to_nuke(out, h)


def grid_points(w: int, h: int, nx: int, ny: int, margin: float = 0.25) -> np.ndarray:
    xs = np.linspace(w * margin, w * (1 - margin), nx)
    ys = np.linspace(h * margin, h * (1 - margin), ny)
    gx, gy = np.meshgrid(xs, ys)
    return np.stack([gx.ravel(), gy.ravel()], 1)           # Nuke convention (y up)


def write_frames(frames: list, out_dir: Path, work_w: int, work_h: int, first: int = 1001) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, f in enumerate(frames):
        small = cv2.resize(f, (work_w, work_h), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(out_dir / f"f.{first + i:04d}.png"), cv2.cvtColor(small, cv2.COLOR_RGB2BGR))


def run_worker(job: dict, job_dir: Path) -> tuple[dict, list]:
    """Run the worker CLI in the venv; returns (result, list of progress/error lines)."""
    job_dir.mkdir(parents=True, exist_ok=True)
    jp = job_dir / "job.json"
    jp.write_text(json.dumps(job))
    res = job_dir / "result.json"
    if res.exists():
        res.unlink()
    p = subprocess.run([str(VENV_PY), str(WORKER), str(jp), "--result", str(res)],
                       capture_output=True, text=True)
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    if p.returncode != 0:
        raise RuntimeError(f"worker failed: {lines[-1:] or p.stderr}")
    return json.loads(res.read_text()), lines


def make_job(model: str, frames_dir: Path, n: int, ref: int, plate_w: int, plate_h: int,
             work_w: int, work_h: int, queries_xy: np.ndarray, **extra) -> dict:
    first = 1001
    return {
        "version": 1, "model": model, "frames_dir": str(frames_dir), "frame_pattern": "f.%04d.png",
        "first": first, "last": first + n - 1, "ref_frame": first + ref,
        "plate_w": plate_w, "plate_h": plate_h, "pixel_aspect": 1.0,
        "work_w": work_w, "work_h": work_h,
        "queries": [[first + ref, float(x), float(y)] for x, y in queries_xy],
        "device": "cuda", "precision": extra.pop("precision", "fp16"), "chunk": extra.pop("chunk", 16), **extra,
    }


def errors(result: dict, truth: np.ndarray) -> np.ndarray:
    """Per point/frame Euclidean error in plate px."""
    return np.linalg.norm(np.asarray(result["tracks"]) - truth, axis=-1)
