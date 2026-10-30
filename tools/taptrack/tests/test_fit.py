"""worker/fit.py: Nuke-convention decomposition, robust fitting, drift handling, smoothing (CPU only)."""
import numpy as np
import pytest

import fit

RNG = np.random.default_rng(11)


def rand_params(skew=True):
    return {"tx": RNG.uniform(-80, 80), "ty": RNG.uniform(-80, 80), "rotate": RNG.uniform(-40, 40),
            "sx": RNG.uniform(0.7, 1.4), "sy": RNG.uniform(0.7, 1.4), "skewX": RNG.uniform(-0.3, 0.3) if skew else 0.0}


@pytest.mark.parametrize("pa", [1.0, 2.0, 0.75])
def test_nuke_params_roundtrip(pa):
    for _ in range(50):
        p = rand_params()
        c = (RNG.uniform(100, 900), RNG.uniform(100, 500))
        M = fit.matrix_from_nuke_params(p, c, pa)
        q = fit.nuke_params(M, c, pa)
        for k in p:
            assert q[k] == pytest.approx(p[k], abs=1e-6), k


def test_nuke_convention_matches_the_measured_matrices():
    """Numbers measured in Nuke 16.1 with a coordinate image (see the probe in tests/nuke_tests)."""
    M = fit.matrix_from_nuke_params({"tx": 0, "ty": 0, "rotate": 30, "sx": 1, "sy": 1, "skewX": 0}, (500, 300), 1.0)
    np.testing.assert_allclose(M[:2], [[0.8660, -0.5, 216.99], [0.5, 0.8660, -209.81]], atol=0.01)   # analytic; Nuke measured 216.8/-209.7
    M = fit.matrix_from_nuke_params({"tx": 0, "ty": 0, "rotate": 30, "sx": 1, "sy": 1, "skewX": 0}, (500, 250), 2.0)
    np.testing.assert_allclose(M[:2, :2], [[0.8660, -0.25], [1.0, 0.8660]], atol=1e-3)           # anamorphic: S^-1 R S
    np.testing.assert_allclose(M[0, 2], 129.5, atol=0.3)
    M = fit.matrix_from_nuke_params({"tx": 0, "ty": 0, "rotate": 0, "sx": 1, "sy": 1, "skewX": 0.3}, (500, 300), 1.0)
    np.testing.assert_allclose(M[:2], [[1, 0.3, -90.0], [0, 1, 0]], atol=1e-6)
    M = fit.matrix_from_nuke_params({"tx": 30, "ty": -10, "rotate": 25, "sx": 1.3, "sy": 0.8, "skewX": 0.2}, (500, 300), 1.0)
    np.testing.assert_allclose(M[:2, :2], [[1.1782, -0.1931], [0.5494, 0.7927]], atol=2e-4)       # R @ K @ S order


def make_points(n=60, w=1920, h=1080):
    return np.stack([RNG.uniform(0.1 * w, 0.9 * w, n), RNG.uniform(0.1 * h, 0.9 * h, n)], 1)


@pytest.mark.parametrize("model", ["translate", "similarity", "affine", "homography"])
def test_ransac_recovers_motion_with_outliers(model):
    src = make_points()
    p = rand_params(skew=model in ("affine", "homography"))
    if model == "translate":
        p.update(rotate=0, sx=1, sy=1, skewX=0)
    if model == "similarity":
        p.update(sy=p["sx"], skewX=0)
    c = tuple(src.mean(0))
    Mtrue = fit.matrix_from_nuke_params(p, c, 1.0)
    dst = fit.apply(Mtrue, src) + RNG.normal(0, 0.15, src.shape)
    out = RNG.random(len(src)) < 0.3
    dst[out] += RNG.uniform(15, 60, (out.sum(), 2)) * RNG.choice([-1, 1], (out.sum(), 2))
    M, inl = fit.fit_frame(src, dst, model, 2.0, 1.0)
    err = np.linalg.norm(fit.apply(M, src[~out]) - fit.apply(Mtrue, src[~out]), axis=1)
    assert err.mean() < 0.3, err.mean()
    assert inl[~out].mean() > 0.9 and inl[out].mean() < 0.1


def test_plain_least_squares_is_ruined_by_outliers():
    src = make_points()
    Mtrue = fit.matrix_from_nuke_params({"tx": 20, "ty": -10, "rotate": 5, "sx": 1.1, "sy": 1.1, "skewX": 0}, tuple(src.mean(0)), 1.0)
    dst = fit.apply(Mtrue, src)
    dst[:15] += 40
    ls, _ = fit.fit_frame(src, dst, "similarity", 2.0, 1.0, method="ls")
    rs, _ = fit.fit_frame(src, dst, "similarity", 2.0, 1.0)
    e = lambda M: np.linalg.norm(fit.apply(M, src[15:]) - dst[15:], axis=1).mean()  # noqa: E731
    assert e(rs) < 0.05 < 3 < e(ls)


def test_anamorphic_similarity_is_fitted_in_square_pixels():
    """A pure rotation in desqueezed space on a pixel-aspect-2 plate must come out as rotate = angle, sx = sy."""
    pa, ang = 2.0, 17.0
    src = make_points(50, 1000, 500)
    c = tuple(src.mean(0))
    Mtrue = fit.matrix_from_nuke_params({"tx": 12, "ty": -7, "rotate": ang, "sx": 1.05, "sy": 1.05, "skewX": 0}, c, pa)
    dst = fit.apply(Mtrue, src)
    M, _ = fit.fit_frame(src, dst, "similarity", 1.0, pa)
    p = fit.nuke_params(M, c, pa)
    assert p["rotate"] == pytest.approx(ang, abs=1e-3) and p["sx"] == pytest.approx(p["sy"], abs=1e-4)
    # fitting the same data with pa=1 would NOT give a rotation of `ang`: the pixel aspect matters
    M1, _ = fit.fit_frame(src, dst, "similarity", 4.0, 1.0)
    assert abs(fit.nuke_params(M1, c, 1.0)["rotate"] - ang) > 1.0


def test_continuity_marks_reacquired_points():
    vis = np.ones((3, 8), bool)
    vis[1, 5:7] = False
    vis[2, 1:3] = False
    cont = fit.continuity(vis, 4)
    assert cont[0].all()
    assert list(cont[1]) == [True] * 5 + [False] * 3        # lost at 5,6 -> everything after is 'reacquired'
    assert list(cont[2]) == [False, False, False, True, True, True, True, True]   # earlier gap blocks frames <= 2


def test_gaussian_smooth_preserves_linear_motion_to_the_ends():
    t = np.arange(60, dtype=float)[:, None]
    y = np.concatenate([5.0 * t, -2.0 * t + 7], 1)
    np.testing.assert_allclose(fit.gaussian_smooth(y, 4), y, atol=1e-9)


def test_gaussian_smooth_reduces_noise_and_keeps_constants():
    y = np.full((50, 2), 3.0)
    np.testing.assert_allclose(fit.gaussian_smooth(y, 4), y, atol=1e-9)
    noisy = np.sin(np.linspace(0, 3, 200))[:, None] + RNG.normal(0, 0.2, (200, 1))
    assert fit.gaussian_smooth(noisy, 3).std() < noisy.std() * 1.05
    assert np.abs(np.diff(fit.gaussian_smooth(noisy, 3), axis=0)).mean() < np.abs(np.diff(noisy, axis=0)).mean() / 3


def synthetic_result(T=40, Q=50, ref=15, occlude=None, drift=0.0, noise=0.2, pa=1.0):
    """Tracks from a known similarity animation; optionally occlude points on frames and make them drift on re-acquisition."""
    w, h = 1920, 1080
    src = make_points(Q, w, h)
    c = (w / 2, h / 2)
    mats = []
    for f in range(T):
        t = (f - ref) / T
        mats.append(fit.matrix_from_nuke_params({"tx": 300 * t, "ty": 80 * t, "rotate": 25 * t, "sx": 1 + 0.2 * t, "sy": 1 + 0.2 * t,
                                                 "skewX": 0}, c, pa) if f != ref else np.eye(3))
    tr = np.stack([fit.apply(M, src) for M in mats], 1) + RNG.normal(0, noise, (Q, T, 2))
    tr[:, ref] = src
    vis = np.ones((Q, T), bool)
    if occlude:
        for q, frames in occlude.items():
            for f in frames:
                vis[q, f] = False
            after = max(frames) + 1
            if drift:
                tr[q, after:] += drift * np.array([1.0, -0.6])
    res = {"first": 1001, "last": 1000 + T, "tracks": tr.tolist(), "visible": vis.astype(int).tolist(),
           "confidence": np.ones((Q, T)).tolist(),
           "meta": {"plate_w": w, "plate_h": h, "pixel_aspect": pa}}
    return res, mats, src


def test_run_fit_ref_frame_is_identity_and_matches_truth():
    res, mats, src = synthetic_result()
    out = fit.run_fit(res, {"model": "similarity", "ref_frame": 1016})
    M = np.asarray(out["matrix"])
    np.testing.assert_allclose(M[15], np.eye(3), atol=1e-12)
    err = [np.linalg.norm(fit.apply(M[f], src) - fit.apply(mats[f], src), axis=1).mean() for f in range(40)]
    assert max(err) < 0.2, max(err)
    assert all(out["valid"]) and out["weak_frames"] == []


def test_drift_robustness_prefers_continuous_points():
    """Half the points are occluded for a while and come back displaced by 12 px; the fit must ignore them."""
    occ = {q: list(range(22, 28)) for q in range(25)}
    res, mats, src = synthetic_result(occlude=occ, drift=12.0)
    base = {"model": "similarity", "ref_frame": 1016}

    def worst(opt):
        out = fit.run_fit(res, {**base, **opt})
        M = np.asarray(out["matrix"])
        return max(np.linalg.norm(fit.apply(M[f], src) - fit.apply(mats[f], src), axis=1).mean() for f in range(28, 40)), out

    e_ls, _ = worst({"method": "ls", "prefer_continuous": False})
    e_rb, _ = worst({"prefer_continuous": False})
    e_pref, out = worst({"prefer_continuous": True})
    print(f"post-occlusion error px: plain LS {e_ls:.2f}, RANSAC {e_rb:.2f}, RANSAC + continuity {e_pref:.2f}")
    assert e_ls > 3 * e_pref
    assert e_pref < 0.3
    assert set(out["source"][28:]) == {"A"}


def test_falls_back_to_reacquired_points_when_too_few_continuous():
    occ = {q: list(range(22, 28)) for q in range(47)}        # only 3 continuous points left -> not enough for similarity
    res, mats, src = synthetic_result(occlude=occ, drift=0.0)
    out = fit.run_fit(res, {"model": "similarity", "ref_frame": 1016})
    assert set(out["source"][28:]) == {"B"} and all(out["valid"][28:])
    assert [out["valid"][f] for f in (23, 24)] == [False, False]      # 3 visible points are not enough on the occluded frames


def test_invalid_frames_are_interpolated_and_reported():
    res, mats, src = synthetic_result()
    for q in range(len(src)):                                 # nothing visible on frames 30-32
        for f in (30, 31, 32):
            res["visible"][q][f] = 0
    out = fit.run_fit(res, {"model": "similarity", "ref_frame": 1016})
    assert [out["valid"][f] for f in (29, 30, 31, 32, 33)] == [True, False, False, False, True]
    assert [1031, 1032, 1033] == out["weak_frames"]
    M = np.asarray(out["matrix"])
    assert np.linalg.norm(fit.apply(M[31], src) - fit.apply(mats[31], src), axis=1).mean() < 0.6   # interpolated, still close


def test_smoothing_pins_reference_and_reduces_jitter():
    res, mats, src = synthetic_result(noise=1.0)
    raw = fit.run_fit(res, {"model": "similarity", "ref_frame": 1016})
    sm = fit.run_fit(res, {"model": "similarity", "ref_frame": 1016, "smoothing": "gaussian", "smooth_sigma": 3})
    np.testing.assert_allclose(np.asarray(sm["matrix"])[15], np.eye(3), atol=1e-9)
    j = lambda o: np.abs(np.diff(np.asarray(o["params"]["tx"]), 2)).mean()  # noqa: E731
    assert j(sm) < j(raw) / 2
    e = lambda o: np.mean([np.linalg.norm(fit.apply(np.asarray(o["matrix"])[f], src) - fit.apply(mats[f], src), axis=1).mean()  # noqa: E731
                           for f in range(40)])
    assert e(sm) <= e(raw) * 1.3                               # smoothing must not wreck accuracy on smooth motion


def test_homography_corners_and_anchor_selection():
    res, mats, src = synthetic_result()
    out = fit.run_fit(res, {"model": "homography", "ref_frame": 1016})
    a = np.asarray(out["corners_from"])
    assert len(out["anchor_indices"]) == 4 and len(set(out["anchor_indices"])) == 4
    # anchors are spread: they should span a large part of the point cloud's bounding box
    span = a.max(0) - a.min(0)
    assert span[0] > 0.6 * (src[:, 0].max() - src[:, 0].min()) and span[1] > 0.6 * (src[:, 1].max() - src[:, 1].min())
    corners = np.asarray(out["corners"])
    np.testing.assert_allclose(corners[15], a, atol=1e-9)
    truth = np.stack([fit.apply(M, a) for M in mats])
    assert np.linalg.norm(corners - truth, axis=-1).mean() < 0.5


def test_user_chosen_corners_and_raw_mode():
    res, mats, src = synthetic_result()
    out = fit.run_fit(res, {"model": "homography", "ref_frame": 1016, "corner_indices": [3, 9, 20, 41]})
    assert out["anchor_indices"] == [3, 9, 20, 41]
    raw = fit.run_fit(res, {"model": "homography", "ref_frame": 1016, "corner_indices": [3, 9, 20, 41], "robust_corners": False})
    tr = np.asarray(res["tracks"])
    np.testing.assert_allclose(np.asarray(raw["corners"])[20], tr[[3, 9, 20, 41], 20], atol=1e-9)
