"""Synthetic accuracy: known similarity animation, grid of points, mean error at PLATE resolution."""
import numpy as np
import pytest

import synth
from conftest import Scene

pytestmark = pytest.mark.gpu

# (model, mean-error limit in plate px). Plain TAPNext is the non-bootstrapped checkpoint and is
# measurably weaker (see docs/TAPNET_NOTES.md); the 0.5 px limit applies to the rest.
LIMITS = [("bootstapnext", 0.5), ("bootstapir", 0.5), ("bootstapir_online", 0.5), ("tapnext", 1.0)]


@pytest.mark.parametrize("model,limit", LIMITS)
def test_mean_error(scene_hd, model, limit):
    res, _ = scene_hd.run(model)
    e = synth.errors(res, scene_hd.truth())
    print(f"{model}: mean={e.mean():.3f} p95={np.percentile(e, 95):.3f} px (plate {scene_hd.pw}x{scene_hd.ph})")
    assert e.mean() < limit


@pytest.mark.parametrize("pw,ph", [(960, 404), (400, 480), (641, 359)])
def test_aspects_and_odd_sizes(pw, ph):
    """Plate aspect != work aspect (distort), tall and odd-sized plates: x/y scaled separately."""
    sc = Scene(f"ar{pw}x{ph}", pw, ph, 16, 8)
    res, _ = sc.run("bootstapnext")
    e = synth.errors(res, sc.truth())
    print(f"{pw}x{ph}: mean={e.mean():.3f}")
    assert e.mean() < 0.7


def test_fp16_matches_fp32(scene_hd):
    a, _ = scene_hd.run("bootstapnext", precision="fp16")
    b, _ = scene_hd.run("bootstapnext", precision="fp32")
    d = np.linalg.norm(np.asarray(a["tracks"]) - np.asarray(b["tracks"]), axis=-1)
    assert d.mean() < 0.15


def test_chunking_is_equivalent(scene_hd):
    a, _ = scene_hd.run("bootstapnext", chunk=1)
    b, _ = scene_hd.run("bootstapnext", chunk=24)
    d = np.linalg.norm(np.asarray(a["tracks"]) - np.asarray(b["tracks"]), axis=-1)
    assert d.mean() < 0.05


def test_result_schema_and_progress(scene_hd):
    res, lines = scene_hd.run("bootstapnext")
    Q, T = len(scene_hd.pts), scene_hd.n
    assert np.asarray(res["tracks"]).shape == (Q, T, 2)
    assert np.asarray(res["visible"]).shape == (Q, T)
    assert np.asarray(res["confidence"]).shape == (Q, T)
    m = res["meta"]
    for k in ("model", "checkpoint", "checkpoint_sha256", "tapnet_commit", "elapsed_s"):
        assert m[k]
    prog = [l["progress"] for l in lines if "progress" in l]
    assert prog[-1] == 1.0 and prog == sorted(prog)


def test_offline_tapir_windowing_stays_accurate(scene_hd):
    """Windows of 8 frames (chained re-queries) must not degrade accuracy noticeably."""
    a, _ = scene_hd.run("bootstapir", window=8)
    e = synth.errors(a, scene_hd.truth())
    print(f"window=8 mean={e.mean():.3f}")
    assert e.mean() < 0.6
