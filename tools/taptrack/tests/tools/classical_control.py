"""Control for the Kling clip: classical SIFT+RANSAC homography alignment, same photometric metric as drift_experiment."""
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "worker"))
import fit  # noqa: E402

cap = cv2.VideoCapture(str(ROOT / "tests" / "footage" / "kling25_wall_handheld.mp4"))
frames = []
while True:
    ok, f = cap.read()
    if not ok:
        break
    frames.append(cv2.cvtColor(f, cv2.COLOR_BGR2GRAY))
H, W = frames[0].shape
REF = 74
ref_f = frames[REF].astype(np.float32)
ref_blur = cv2.GaussianBlur(ref_f, (0, 0), 1.2)
sift = cv2.SIFT_create(4000)
kr, dr = sift.detectAndCompute(frames[REF], None)
bf = cv2.BFMatcher()
res = {m: json.loads((ROOT / "tests" / "_out" / "review" / m / "result.json").read_text()) for m in ("bootstapnext", "bootstapir")}
fits = {m: np.asarray(fit.run_fit(res[m], {"model": "homography", "ref_frame": 1075})["matrix"]) for m in res}
Fl = np.array([[1, 0, 0], [0, -1, H], [0, 0, 1]], float)


def photometric(Mi, f):
    warped = cv2.warpPerspective(frames[f].astype(np.float32), np.linalg.inv(Mi), (W, H))
    valid = cv2.warpPerspective(np.ones((H, W), np.float32), np.linalg.inv(Mi), (W, H)) > 0.99
    valid[:int(0.08 * H)] = False
    valid[int(0.88 * H):] = False
    return float(np.abs(cv2.GaussianBlur(warped, (0, 0), 1.2) - ref_blur)[valid].mean()) if valid.sum() > 50000 else float("nan")


print("frame | SIFT-homography (classical) | BootsTAPNext homography | BootsTAPIR homography   [photometric mean abs diff]")
rows = {"pre": [], "post1": [], "post2": []}
for f in list(range(9, 70, 12)) + list(range(109, 240, 12)):
    kf, df = sift.detectAndCompute(frames[f], None)
    good = [m for m, n in bf.knnMatch(dr, df, k=2) if m.distance < 0.7 * n.distance]
    if len(good) < 12:
        print(1001 + f, "too few matches"); continue
    src = np.float32([kr[m.queryIdx].pt for m in good]); dst = np.float32([kf[m.trainIdx].pt for m in good])
    Hm, inl = cv2.findHomography(src, dst, cv2.RANSAC, 3.0)
    c = photometric(Hm, f)
    a = photometric(Fl @ fits["bootstapnext"][f] @ Fl, f)
    b = photometric(Fl @ fits["bootstapir"][f] @ Fl, f)
    print(f"{1001 + f} | {c:6.1f} (inliers {int(inl.sum()):4d}/{len(good)}) | {a:6.1f} | {b:6.1f}")
