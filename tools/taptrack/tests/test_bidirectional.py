"""Tracking from a mid-shot reference frame: forward + backward, merged, ref frame pinned."""
import numpy as np
import pytest

import synth
from conftest import OUT, Scene

pytestmark = pytest.mark.gpu

MODELS = ["bootstapnext", "bootstapir", "bootstapir_online"]


@pytest.mark.parametrize("model", MODELS)
def test_mid_sequence_both_directions(scene_hd, model):
    res, _ = scene_hd.run(model)
    e = synth.errors(res, scene_hd.truth())
    ref = scene_hd.ref
    before, after = e[:, :ref], e[:, ref + 1:]
    print(f"{model}: before ref={before.mean():.3f}  after ref={after.mean():.3f}")
    assert before.mean() < 0.5 and after.mean() < 0.5
    # Error must not blow up at the far end of either direction.
    assert e[:, 0].mean() < 1.0 and e[:, -1].mean() < 1.0


@pytest.mark.parametrize("model", MODELS)
def test_ref_frame_equals_query(scene_hd, model):
    res, _ = scene_hd.run(model)
    got = np.asarray(res["tracks"])[:, scene_hd.ref]
    np.testing.assert_allclose(got, scene_hd.pts, atol=1e-3)
    assert all(v == 1 for v in np.asarray(res["visible"])[:, scene_hd.ref])


@pytest.mark.parametrize("ref", [0, 23])
def test_ref_at_sequence_ends(ref):
    sc = Scene(f"ends{ref}", 640, 360, 24, ref)
    res, _ = sc.run("bootstapnext")
    e = synth.errors(res, sc.truth())
    assert e.mean() < 0.6
    np.testing.assert_allclose(np.asarray(res["tracks"])[:, ref], sc.pts, atol=1e-3)


def test_queries_on_different_frames(scene_hd):
    """Points queried on two different frames are tracked both ways and pinned on their own frame."""
    sc = scene_hd
    a, b = 5, 17
    pts_a, pts_b = sc.pts[::2], sc.pts[1::2]
    qa = synth.truth_tracks(pts_a, sc.mats, sc.ref, sc.ph)[:, a]
    qb = synth.truth_tracks(pts_b, sc.mats, sc.ref, sc.ph)[:, b]
    queries = [[1001 + a, float(x), float(y)] for x, y in qa] + [[1001 + b, float(x), float(y)] for x, y in qb]
    job = sc.job("bootstapnext", 256)
    job["queries"] = queries
    res, _ = synth.run_worker(job, OUT / "run_multi")
    truth = np.concatenate([sc.truth(pts=pts_a), sc.truth(pts=pts_b)])
    assert synth.errors(res, truth).mean() < 0.6
    tr = np.asarray(res["tracks"])
    np.testing.assert_allclose(tr[:len(qa), a], qa, atol=1e-3)
    np.testing.assert_allclose(tr[len(qa):, b], qb, atol=1e-3)


def test_points_per_pass_splitting(scene_hd):
    res, _ = scene_hd.run("bootstapnext", points_per_pass=10)
    assert synth.errors(res, scene_hd.truth()).mean() < 0.6
