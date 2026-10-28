"""Transform fitting for the export step (numpy + OpenCV, runs in the worker venv).

    python fit.py fit_job.json [--result fit_result.json]

Input: a tracking ``result.json`` plus options. Output: for every frame the motion *relative to the
reference frame*, in plate pixels, Nuke convention (origin bottom-left):

* ``matrix``      3x3 (row-major) mapping reference-frame positions -> positions on that frame
* ``params``      the same motion as Nuke ``Transform`` knobs (translate / rotate / scale / skewX / center)
* ``corners``     where four chosen anchor points go on each frame (CornerPin2D ``to``), ``from`` is the ref
* per-frame quality: ``inliers``, ``used`` (points considered), ``rms`` (px), ``valid``, ``source`` class

Nuke ``Transform`` convention (measured with a coordinate image in Nuke 16.1, tests/nuke_tests):
    linear part (in desqueezed/square pixels) = R(rotate) @ [[1, skewX], [0, 1]] @ diag(sx, sy),
    applied about ``center``; ``translate`` is added in plate pixels. For pixel aspect ``pa`` the linear
    part acts in square space: A_plate = S^-1 A_sq S with S = diag(pa, 1). Rotation is counter-clockwise (y up).

Drift robustness (see docs/DRIFT.md): per-frame RANSAC rejects points that are inconsistent with the
consensus; points that were occluded between the reference and the current frame ("class B": they were
re-acquired by the tracker, with a small offset that can grow) are only used when there are not enough
continuously visible points ("class A").
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

MODELS = ("translate", "similarity", "affine", "homography")


# ----------------------------------------------------------------------------------------- matrices
def _S(pa: float) -> np.ndarray:
    return np.diag([pa, 1.0, 1.0])


def to_square(M: np.ndarray, pa: float) -> np.ndarray:
    """Plate-space 3x3 -> square-pixel-space 3x3."""
    S = _S(pa)
    return S @ M @ np.linalg.inv(S)


def from_square(M: np.ndarray, pa: float) -> np.ndarray:
    S = _S(pa)
    return np.linalg.inv(S) @ M @ S


def apply(M: np.ndarray, pts: np.ndarray) -> np.ndarray:
    p = np.concatenate([pts, np.ones((len(pts), 1))], axis=1) @ M.T
    return p[:, :2] / p[:, 2:3]


def nuke_params(M: np.ndarray, center: Tuple[float, float], pa: float) -> Dict[str, float]:
    """Affine plate-space ``M`` -> Nuke Transform knobs (translate, rotate in degrees, sx, sy, skewX)."""
    A = to_square(M, pa)                       # square space: x' = A x
    c_sq = np.array([center[0] * pa, center[1], 1.0])
    L = A[:2, :2]
    Q, Rm = np.linalg.qr(L)                    # L = Q @ Rm, Rm upper triangular
    sgn = np.sign(np.diag(Rm))
    sgn[sgn == 0] = 1.0
    Q, Rm = Q * sgn, (Rm.T * sgn).T            # make the diagonal positive -> Q is a rotation when det(L) > 0
    theta = np.degrees(np.arctan2(Q[1, 0], Q[0, 0]))
    sx, sy = Rm[0, 0], Rm[1, 1]
    skew = Rm[0, 1] / sy
    # translate = where the centre goes minus the centre, in plate pixels
    c_plate = np.array([center[0], center[1], 1.0])
    moved = M @ c_plate
    return {"tx": float(moved[0] - center[0]), "ty": float(moved[1] - center[1]), "rotate": float(theta),
            "sx": float(sx), "sy": float(sy), "skewX": float(skew)}


def matrix_from_nuke_params(p: Dict[str, float], center: Tuple[float, float], pa: float) -> np.ndarray:
    """Inverse of :func:`nuke_params` (also the formula Nuke evaluates)."""
    th = np.radians(p["rotate"])
    R = np.array([[np.cos(th), -np.sin(th)], [np.sin(th), np.cos(th)]])
    K = np.array([[1.0, p["skewX"]], [0.0, 1.0]])
    L_sq = R @ K @ np.diag([p["sx"], p["sy"]])
    S = _S(pa)
    L_plate = np.linalg.inv(S[:2, :2]) @ L_sq @ S[:2, :2]
    c = np.array(center)
    M = np.eye(3)
    M[:2, :2] = L_plate
    M[:2, 2] = c + np.array([p["tx"], p["ty"]]) - L_plate @ c
    return M


# ----------------------------------------------------------------------------------------- per-frame fit
def _ls_similarity(src: np.ndarray, dst: np.ndarray) -> np.ndarray:
    """Plain least-squares similarity (Umeyama), 3x3."""
    mu_s, mu_d = src.mean(0), dst.mean(0)
    a, b = src - mu_s, dst - mu_d
    U, D, Vt = np.linalg.svd(b.T @ a / len(src))
    S = np.eye(2)
    if np.linalg.det(U) * np.linalg.det(Vt) < 0:
        S[1, 1] = -1
    R = U @ S @ Vt
    s = (D * np.diag(S)).sum() / (a ** 2).sum(1).mean()
    M = np.eye(3)
    M[:2, :2] = s * R
    M[:2, 2] = mu_d - s * R @ mu_s
    return M


def fit_frame(src: np.ndarray, dst: np.ndarray, model: str, thr: float, pa: float,
              method: str = "ransac") -> Tuple[Optional[np.ndarray], np.ndarray]:
    """Fit ``src -> dst`` (plate px) with ``model``; returns (3x3 plate-space matrix or None, inlier mask).

    ``method='ransac'`` is the robust default; ``'ls'`` is plain least squares (kept as a baseline for tests).
    Rotation/scale models are fitted in square pixels so anamorphic plates come out right.
    """
    n = len(src)
    need = {"translate": 1, "similarity": 2, "affine": 3, "homography": 4}[model]
    if n < need:
        return None, np.zeros(n, bool)
    s_sq, d_sq = src.copy(), dst.copy()
    s_sq[:, 0] *= pa
    d_sq[:, 0] *= pa
    if model == "translate":
        t = np.median(d_sq - s_sq, axis=0) if method == "ransac" else (d_sq - s_sq).mean(0)
        res = np.linalg.norm(d_sq - s_sq - t, axis=1)
        inl = res < thr if method == "ransac" else np.ones(n, bool)
        if inl.any() and method == "ransac":
            t = (d_sq - s_sq)[inl].mean(0)
        M = np.eye(3)
        M[:2, 2] = t
        return from_square(M, pa), inl
    if method == "ls":
        if model == "similarity":
            M = _ls_similarity(s_sq, d_sq)
        elif model == "affine":
            M = np.eye(3)
            A = np.concatenate([s_sq, np.ones((n, 1))], 1)
            M[:2, :] = np.linalg.lstsq(A, d_sq, rcond=None)[0].T
        else:
            Hm, _ = cv2.findHomography(src, dst, 0)
            return Hm, np.ones(n, bool)
        return from_square(M, pa), np.ones(n, bool)
    if model == "similarity":
        A, inl = cv2.estimateAffinePartial2D(s_sq, d_sq, method=cv2.RANSAC, ransacReprojThreshold=thr,
                                             maxIters=3000, confidence=0.999, refineIters=20)
    elif model == "affine":
        A, inl = cv2.estimateAffine2D(s_sq, d_sq, method=cv2.RANSAC, ransacReprojThreshold=thr,
                                      maxIters=3000, confidence=0.999, refineIters=20)
    else:
        Hm, inl = cv2.findHomography(src, dst, cv2.RANSAC, thr, maxIters=3000, confidence=0.999)
        if Hm is None:
            return None, np.zeros(n, bool)
        return Hm, inl.ravel().astype(bool)
    if A is None:
        return None, np.zeros(n, bool)
    M = np.eye(3)
    M[:2, :] = A
    return from_square(M, pa), inl.ravel().astype(bool)


# ----------------------------------------------------------------------------------------- reliability
def continuity(visible: np.ndarray, ref_i: int) -> np.ndarray:
    """``[Q, T]`` bool: the point was visible on every frame between the reference and this frame (inclusive)."""
    Q, T = visible.shape
    out = np.zeros((Q, T), bool)
    out[:, ref_i] = True
    for f in range(ref_i + 1, T):
        out[:, f] = out[:, f - 1] & visible[:, f]
    for f in range(ref_i - 1, -1, -1):
        out[:, f] = out[:, f + 1] & visible[:, f]
    return out


# ----------------------------------------------------------------------------------------- smoothing
def gaussian_smooth(y: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian smoothing over axis 0. Ends use odd reflection (2*y[0] - y[k]) so straight-line motion is
    preserved exactly; edge replication would flatten the ramps of a camera move at the first/last frames."""
    if sigma <= 0 or len(y) < 2:
        return y
    r = min(int(np.ceil(3 * sigma)), len(y) - 1)
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2)
    k /= k.sum()
    head = 2 * y[:1] - y[r:0:-1]
    tail = 2 * y[-1:] - y[-2:-r - 2:-1]
    pad = np.concatenate([head, y, tail], 0)
    flat = pad.reshape(len(pad), -1)
    sm = np.stack([np.convolve(flat[:, j], k, mode="valid") for j in range(flat.shape[1])], 1)
    return sm.reshape(y.shape)


def _fill_invalid(y: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Linear interpolation over frames where the fit was not valid (constant extrapolation at the ends)."""
    if valid.all() or not valid.any():
        return y
    idx = np.arange(len(y))
    out = y.copy()
    for j in range(y.shape[1]):
        out[~valid, j] = np.interp(idx[~valid], idx[valid], y[valid, j])
    return out


# ----------------------------------------------------------------------------------------- anchors
def pick_anchors(ref_pts: np.ndarray, quality: np.ndarray) -> List[int]:
    """Four well-spread, reliable points: the best-quality point nearest each corner of the points' bounding box."""
    ok = np.where(quality >= np.quantile(quality, 0.5))[0]
    if len(ok) < 4:
        ok = np.arange(len(ref_pts))
    p = ref_pts[ok]
    lo, hi = p.min(0), p.max(0)
    corners = [(lo[0], lo[1]), (hi[0], lo[1]), (hi[0], hi[1]), (lo[0], hi[1])]   # BL, BR, TR, TL (y up)
    chosen: List[int] = []
    for cx, cy in corners:
        d = np.hypot(p[:, 0] - cx, p[:, 1] - cy)
        for j in np.argsort(d):
            if int(ok[j]) not in chosen:
                chosen.append(int(ok[j]))
                break
    return chosen


# ----------------------------------------------------------------------------------------- main fit
def run_fit(res: dict, opt: dict) -> dict:
    model = opt.get("model", "similarity")
    if model not in MODELS:
        raise ValueError("Unknown transform model %r" % model)
    first, last = int(res["first"]), int(res["last"])
    ref = int(opt["ref_frame"])
    ref_i = ref - first
    pa = float(opt.get("pixel_aspect", res["meta"].get("pixel_aspect", 1.0)))
    plate_w = float(res["meta"].get("plate_w", opt.get("plate_w", 1920)))
    tracks = np.asarray(res["tracks"], np.float64)          # [Q, T, 2]
    vis = np.asarray(res["visible"]).astype(bool)
    conf = np.asarray(res.get("confidence", np.ones(vis.shape)), np.float64)
    Q, T = vis.shape
    sel = np.asarray(opt.get("indices") or list(range(Q)), int)
    tracks, vis, conf = tracks[sel], vis[sel], conf[sel]
    thr = float(opt.get("ransac_px", max(1.5, 0.0015 * plate_w)))
    min_pts = int(opt.get("min_points", {"translate": 2, "similarity": 4, "affine": 6, "homography": 8}[model]))
    method = opt.get("method", "ransac")
    prefer_continuous = bool(opt.get("prefer_continuous", True))
    cont = continuity(vis, ref_i)
    src = tracks[:, ref_i]

    mats = np.tile(np.eye(3), (T, 1, 1))
    inl_n = np.zeros(T, int)
    used_n = np.zeros(T, int)
    rms = np.zeros(T)
    valid = np.zeros(T, bool)
    source = ["-"] * T
    valid[ref_i] = True
    for f in range(T):
        if f == ref_i:
            used_n[f] = inl_n[f] = vis[:, f].sum()
            source[f] = "ref"
            continue
        v = vis[:, f]
        sets = [("A", np.where(v & cont[:, f])[0]), ("B", np.where(v)[0])] if prefer_continuous else [("B", np.where(v)[0])]
        for name, idx in sets:
            if len(idx) < min_pts:
                continue
            M, inl = fit_frame(src[idx], tracks[idx, f], model, thr, pa, method)
            if M is None or inl.sum() < min_pts:
                continue
            mats[f], valid[f], source[f] = M, True, name
            used_n[f], inl_n[f] = len(idx), int(inl.sum())
            pred = apply(M, src[idx][inl])
            rms[f] = float(np.sqrt(np.mean(np.sum((pred - tracks[idx, f][inl]) ** 2, axis=1))))
            break

    centre = tuple(opt.get("center") or src[vis[:, ref_i]].mean(0))
    out: Dict[str, object] = {"version": 1, "model": model, "first": first, "last": last, "ref_frame": ref,
                              "pixel_aspect": pa, "center": [float(centre[0]), float(centre[1])],
                              "ransac_px": thr, "min_points": min_pts, "method": method}

    sig = float(opt.get("smooth_sigma", 0.0)) if opt.get("smoothing", "none") == "gaussian" else 0.0
    if model == "homography":
        pos = {int(orig): k for k, orig in enumerate(sel)}          # original point index -> row in the selection
        user = [int(i) for i in (opt.get("corner_indices") or [])]
        if len(user) == 4:
            anchors = [pos[i] for i in user]
        else:
            q = 0.5 * vis.mean(1) + 0.5 * cont.mean(1)
            anchors = pick_anchors(src, q)
        a_src = src[anchors]
        if opt.get("robust_corners", True):
            corners = np.stack([apply(mats[f], a_src) for f in range(T)])          # through the fitted homography
            ok = valid
        else:
            corners = tracks[anchors].transpose(1, 0, 2).copy()                      # follow the raw tracks
            ok = vis[anchors].all(0)
            ok[ref_i] = True
            corners[ref_i] = a_src
        disp = _fill_invalid((corners - a_src[None]).reshape(T, -1), ok)
        if sig > 0:
            sm = gaussian_smooth(disp, sig)
            disp = sm - sm[ref_i]                                   # reference frame stays exactly the identity
        corners = disp.reshape(T, 4, 2) + a_src[None]
        out.update({"corners_from": a_src.tolist(), "corners": corners.tolist(),
                    "anchor_indices": [int(sel[i]) for i in anchors]})
        mats = np.stack([np.eye(3) if f == ref_i else
                         cv2.getPerspectiveTransform(a_src.astype(np.float32), corners[f].astype(np.float32))
                         for f in range(T)])
    else:
        params = [nuke_params(M, centre, pa) for M in mats]
        keys = ["tx", "ty", "rotate", "sx", "sy", "skewX"]
        arr = np.array([[p[k] for k in keys] for p in params], np.float64)
        arr[:, 2] = np.degrees(np.unwrap(np.radians(arr[:, 2])))
        arr[:, 3:5] = np.log(arr[:, 3:5])                       # smooth scale in log space
        arr = _fill_invalid(arr, valid)
        if model == "translate":
            arr[:, 2:] = [0, 0, 0, 0]
        elif model == "similarity":
            arr[:, 4] = arr[:, 3] = arr[:, 3:5].mean(1)
            arr[:, 5] = 0
        if sig > 0:
            sm = gaussian_smooth(arr, sig)
            arr = sm - sm[ref_i] + arr[ref_i]                   # keep the reference frame exactly identity
        arr[:, 3:5] = np.exp(arr[:, 3:5])
        mats = np.stack([matrix_from_nuke_params(dict(zip(keys, row)), centre, pa) for row in arr])
        out["params"] = {k: arr[:, i].tolist() for i, k in enumerate(keys)}
    out["matrix"] = mats.tolist()
    # quality
    med_rms = float(np.median(rms[valid & (np.arange(T) != ref_i)])) if (valid.sum() > 1) else 0.0
    weak = []
    for f in range(T):
        if f == ref_i:
            continue
        bad = (not valid[f]) or inl_n[f] < max(min_pts, 0.3 * max(1, used_n[f])) or (med_rms > 0 and rms[f] > 4 * med_rms + 1.0)
        if bad:
            weak.append(first + f)
    out.update({"inliers": inl_n.tolist(), "used": used_n.tolist(), "rms": np.round(rms, 3).tolist(),
                "valid": valid.tolist(), "source": source, "weak_frames": weak,
                "points_used": int(len(sel))})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--result", default=None)
    a = ap.parse_args(argv)
    job = json.loads(Path(a.job).read_text())
    try:
        res = json.loads(Path(job["result"]).read_text())
        out = run_fit(res, job)
        err = None
    except Exception as exc:  # noqa: BLE001
        out, err = None, str(exc)
    path = Path(a.result) if a.result else Path(a.job).with_name("fit_result.json")
    if err:
        sys.stdout.write(json.dumps({"error": {"code": "FIT_FAILED", "message": "The transform fit failed: " + err,
                                               "hint": "", "log": ""}}) + "\n")
        return 1
    path.write_text(json.dumps(out))
    sys.stdout.write(json.dumps({"progress": 1.0, "msg": "fit done"}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
