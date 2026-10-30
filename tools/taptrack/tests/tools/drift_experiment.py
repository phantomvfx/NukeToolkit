"""Compare fitting policies on real tracker output (evidence for docs/DRIFT.md).

    .venv/Scripts/python.exe tests/tools/drift_experiment.py            # synthetic occluder + Kling clip

Synthetic: exact ground truth. Kling clip: photometric alignment error (frames warped back onto the reference
by the fitted motion; mean absolute difference over the valid overlap, occluder excluded).
Needs tests/_out/review/ from review_footage.py for the real-footage part.
"""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(ROOT / "worker"))
import fit  # noqa: E402
import synth  # noqa: E402

OUT = ROOT / "tests" / "_out" / "drift"
POLICIES = {
    "LS (all visible)": {"method": "ls", "prefer_continuous": False},
    "RANSAC (all visible)": {"prefer_continuous": False},
    "RANSAC + continuity": {"prefer_continuous": True},
}


def synthetic():
    PW, PH, N, REF = 640, 360, 70, 20
    base = synth.textured_image(int(PW * 1.8), int(PH * 1.8), 3)
    mats = synth.animation(N, PW, PH, amp=1.3)
    plate = synth.render_plate(base, mats, PW, PH)
    rng = np.random.default_rng(5)
    for f in range(N):                                   # dark occluder (textured, like a person) sweeping left -> right
        if 28 <= f <= 44:
            x0 = int(-0.35 * PW + (f - 28) / 16 * 1.35 * PW)
            band = (np.clip(rng.normal(40, 15, (PH, int(0.32 * PW), 3)), 0, 255)).astype(np.uint8)
            xs = slice(max(0, x0), min(PW, x0 + band.shape[1]))
            if xs.stop > xs.start:
                plate[f][:, xs] = band[:, xs.start - x0: xs.stop - x0]
    pts = synth.grid_points(PW, PH, 10, 6, margin=0.12)
    truth = synth.truth_tracks(pts, mats, REF, PH)
    res = {}
    for model, side in (("bootstapnext", 256), ("bootstapir", 512)):
        d = OUT / f"syn_{side}"
        synth.write_frames(plate, d, side, side)
        job = synth.make_job(model, d, N, REF, PW, PH, side, side, pts)
        res[model], _ = synth.run_worker(job, OUT / f"syn_{model}")
    return res, truth, pts, mats, REF, PH, PW


def eval_synthetic():
    res, truth, pts, mats, REF, PH, PW = synthetic()
    for model, r in res.items():
        tr = np.asarray(r["tracks"])
        vis = np.asarray(r["visible"]).astype(bool)
        e = np.linalg.norm(tr - truth, axis=-1)
        after = slice(46, None)
        ever = ~vis[:, 28:46].all(0).any() if False else None
        occ_any = (~vis[:, 28:46]).any(1)                  # points that were flagged occluded at some point
        print(f"\n[{model}] raw track error after occlusion: re-acquired points mean {e[occ_any][:, after].mean():.2f}px "
              f"(max {e[occ_any][:, after].max():.1f}), never-lost points mean {e[~occ_any][:, after].mean() if (~occ_any).any() else float('nan'):.2f}px; "
              f"{occ_any.sum()}/{len(pts)} points were flagged occluded")
        for name, opt in POLICIES.items():
            for fm in ("similarity", "homography"):
                out = fit.run_fit(r, {"model": fm, "ref_frame": 1001 + REF, **opt})
                M = np.asarray(out["matrix"])
                pr = np.stack([fit.apply(M[f], pts) for f in range(len(M))], 1)
                err = np.linalg.norm(pr - truth, axis=-1).mean(0)
                print(f"   {fm:10s} {name:22s} matrix error px: before occ {err[:28].mean():5.2f} | during {err[28:46].mean():5.2f} | after {err[46:].mean():5.2f} (worst {err[46:].max():.2f})")


def photometric():
    rev = ROOT / "tests" / "_out" / "review"
    if not (rev / "bootstapnext" / "result.json").exists():
        print("no review results; run review_footage.py first")
        return
    cap = cv2.VideoCapture(str(ROOT / "tests" / "footage" / "kling25_wall_handheld.mp4"))
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY).astype(np.float32))
    H, W = frames[0].shape
    REF = 1075 - 1001
    ref_img = cv2.GaussianBlur(frames[REF], (0, 0), 1.2)
    # frames with the person in them are excluded from the comparison
    person = {f for f in range(1083, 1107)}
    for model in ("bootstapnext", "bootstapir"):
        res = json.loads((rev / model / "result.json").read_text())
        print(f"\n[{model}] photometric alignment on the Kling clip (mean abs diff vs ref frame, 0-255, lower is better)")
        for fm in ("similarity", "affine", "homography"):
            for name, opt in POLICIES.items():
                if "LS" in name and fm == "homography":
                    continue
                out = fit.run_fit(res, {"model": fm, "ref_frame": 1075, "pixel_aspect": 1.0, **opt})
                M = np.asarray(out["matrix"])
                rows = []
                for rng_name, rg in (("pre 1010-1070", range(9, 70, 6)), ("post 1110-1160", range(109, 160, 6)), ("post 1170-1240", range(169, 240, 6))):
                    errs = []
                    for f in rg:
                        if (1001 + f) in person:
                            continue
                        # M maps ref -> frame f in Nuke coords (y up). Image coords (y down): conjugate with the flip.
                        Fl = np.array([[1, 0, 0], [0, -1, H], [0, 0, 1]], float)
                        Mi = Fl @ M[f] @ Fl                    # ref image -> frame f image
                        warped = cv2.warpPerspective(frames[f], np.linalg.inv(Mi), (W, H), flags=cv2.INTER_LINEAR)
                        valid = cv2.warpPerspective(np.ones((H, W), np.float32), np.linalg.inv(Mi), (W, H)) > 0.99
                        valid[:int(0.08 * H)] = False
                        valid[int(0.88 * H):] = False
                        w2 = cv2.GaussianBlur(warped, (0, 0), 1.2)
                        if valid.sum() > 50000:
                            errs.append(np.abs(w2 - ref_img)[valid].mean())
                    rows.append(f"{rng_name}: {np.mean(errs):5.1f}")
                print(f"   {fm:10s} {name:22s} " + " | ".join(rows))


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    eval_synthetic()
    photometric()
