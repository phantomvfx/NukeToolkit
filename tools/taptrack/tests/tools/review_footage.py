"""Track a grid on real footage with several models and render an overlay video for visual review.

    .venv/Scripts/python.exe tests/tools/review_footage.py <video.mp4> --ref 1075 --out tests/_out/review

Frames are numbered 1001+. Visible points are drawn green, points the model flags occluded red (hollow),
points that left the frame are not drawn. Panels: one per model, side by side.
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import synth  # noqa: E402

FIRST = 1001


def extract(video: Path, out: Path):
    cap = cv2.VideoCapture(str(video))
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    h, w = frames[0].shape[:2]
    for side in (256, 512):
        d = out / f"f{side}"
        d.mkdir(parents=True, exist_ok=True)
        for i, f in enumerate(frames):
            cv2.imwrite(str(d / f"f.{FIRST + i:04d}.png"), cv2.resize(f, (side, side), interpolation=cv2.INTER_AREA))
    return frames, w, h, cap.get(cv2.CAP_PROP_FPS) or 24.0


def grid(w, h, nx, ny):
    xs = np.linspace(w * 0.08, w * 0.92, nx)
    ys = np.linspace(h * 0.12, h * 0.85, ny)           # image-space y (down); skip the pavement
    pts = np.array([(x, h - y) for y in ys for x in xs])  # Nuke convention (y up)
    return pts


def run(model, side, frames_dir, n, ref, w, h, pts, out):
    job = {"version": 1, "model": model, "frames_dir": str(frames_dir), "frame_pattern": "f.%04d.png",
           "first": FIRST, "last": FIRST + n - 1, "ref_frame": ref, "plate_w": w, "plate_h": h, "pixel_aspect": 1.0,
           "work_w": side, "work_h": side, "queries": [[ref, float(x), float(y)] for x, y in pts],
           "device": "cuda", "precision": "fp16", "chunk": 16, "window": 64}
    res, _ = synth.run_worker(job, out / model)
    return res


def draw(frame, res, k, h, w, label):
    img = frame.copy()
    tr = np.asarray(res["tracks"])[:, k]
    vis = np.asarray(res["visible"])[:, k]
    for (x, y), v in zip(tr, vis):
        px, py = int(round(x)), int(round(h - y))
        if not (0 <= px < w and 0 <= py < h):
            continue
        if v:
            cv2.circle(img, (px, py), 7, (0, 255, 0), -1, cv2.LINE_AA)
            cv2.circle(img, (px, py), 8, (0, 0, 0), 1, cv2.LINE_AA)
        else:
            cv2.circle(img, (px, py), 8, (0, 0, 255), 2, cv2.LINE_AA)
            cv2.line(img, (px - 6, py - 6), (px + 6, py + 6), (0, 0, 255), 2, cv2.LINE_AA)
            cv2.line(img, (px - 6, py + 6), (px + 6, py - 6), (0, 0, 255), 2, cv2.LINE_AA)
    inside = [(0 <= x < w and 0 <= h - y < h) for x, y in tr]
    nv = int(sum(1 for v, i in zip(vis, inside) if v and i))
    cv2.putText(img, f"{label}  f{FIRST + k}  visible {nv}/{len(vis)}", (24, 56), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 0, 0), 6)
    cv2.putText(img, f"{label}  f{FIRST + k}  visible {nv}/{len(vis)}", (24, 56), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (255, 255, 255), 2)
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--ref", type=int, required=True)
    ap.add_argument("--out", default=str(HERE.parent / "_out" / "review"))
    ap.add_argument("--nx", type=int, default=12)
    ap.add_argument("--ny", type=int, default=7)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    frames, w, h, fps = extract(Path(a.video), out)
    n = len(frames)
    pts = grid(w, h, a.nx, a.ny)
    models = [("bootstapnext", 256, "BootsTAPNext"), ("bootstapir", 512, "BootsTAPIR")]
    results = {}
    for key, side, label in models:
        res = run(key, side, out / f"f{side}", n, a.ref, w, h, pts, out)
        results[key] = res
        m = res["meta"]
        print(f"{label}: {m['points']} pts x {m['frames']} frames, track {m['track_s']}s, visible {m['percent_visible']}%")
    # agreement between the two models (px at 1920 wide) on points both call visible
    a_, b_ = (np.asarray(results[k]["tracks"]) for k, _, _ in models)
    va, vb = (np.asarray(results[k]["visible"]).astype(bool) for k, _, _ in models)
    both = va & vb
    d = np.linalg.norm(a_ - b_, axis=-1)
    stats = {"mean_disagreement_px": float(d[both].mean()), "median_px": float(np.median(d[both])),
             "p95_px": float(np.percentile(d[both], 95)), "frac_both_visible": float(both.mean())}
    print("agreement:", stats)
    (out / "stats.json").write_text(json.dumps({"agreement": stats, "ref": a.ref}, indent=1))
    pw, ph = 960, 540
    vw = cv2.VideoWriter(str(out / "overlay.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (pw * 2, ph))
    for k, f in enumerate(frames):
        panels = [cv2.resize(draw(f, results[key], k, h, w, label), (pw, ph), interpolation=cv2.INTER_AREA)
                  for key, _, label in models]
        vw.write(np.hstack(panels))
    vw.release()
    print("overlay:", out / "overlay.mp4")


if __name__ == "__main__":
    main()
