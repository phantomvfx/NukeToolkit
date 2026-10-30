# Post-occlusion drift: what we know, what we do

## The failure mode
After a point has been occluded and the tracker finds it again ("re-acquired"), the new position can be offset. If many
points are re-acquired together, the offsets can be *coherent* (all shifted the same way), and then a plain robust fit
can prefer the shifted group over the points that never lost their lock.

## What the exports do about it (`worker/fit.py`)
1. **Visible points only** per frame (the tracker's own occlusion flag).
2. **RANSAC per frame** (threshold 0.15 % of plate width, min 1.5 px) rejects points inconsistent with the consensus.
3. **Continuity preference** (`prefer_continuous`, on by default, "ignore re-acquired points when possible"): a point that was
   not visible on every frame between the reference and the current frame is *class B*. The fit uses *class A* (continuously
   visible) points whenever there are enough of them for the chosen model (translate 2, similarity 4, affine 6, homography 8),
   and falls back to all visible points otherwise.
4. **Gap handling:** frames where no valid fit exists are interpolated from their neighbours and reported
   (`weak_frames`; the Export button tells the artist which frames to check).
5. **Smoothing** (optional Gaussian) works on the motion parameters, preserves straight-line motion exactly to the ends
   (odd-reflection padding) and keeps the reference frame exactly the identity.

## Evidence
* `tests/test_fit.py::test_drift_robustness_prefers_continuous_points` - half the points come back displaced by 12 px as a coherent
  group: plain least squares 7.0 px, plain RANSAC **14.0 px (worse: the displaced half wins the vote)**, RANSAC + continuity **0.13 px**.
* `tests/tools/drift_experiment.py`, synthetic occluder (real trackers, ground truth): even though re-acquired raw tracks are
  off by 8 px (BootsTAPNext) / 19 px (BootsTAPIR) on average, the fitted matrices stay within ~0.6 px of the truth after the
  occlusion. In that scene the occluder covered every point at some time, so class A was empty and all policies tie.
* Real-footage check (`tests/tools/drift_experiment.py`, `classical_control.py`) on the Kling 2.5 wall clip: **inconclusive for the
  policies, and it corrected an earlier claim.** The alignment error (photometric) rises 3x after the person passes for *every*
  policy, but an independent classical SIFT + RANSAC homography shows the same rise, and its feature inliers collapse from ~2900 to ~30.
  The generated video does not keep the wall consistent after the person passes, so that clip cannot measure tracker drift.
  An earlier statement that the clip showed "real post-occlusion drift" was confounded by this.

## Open question
We have no real footage with an occlusion and a rigid background. Until we do, the continuity rule is justified by the
constructed coherent-drift case, not by a measured real-world gain. It costs nothing when there is no occlusion (identical results).
