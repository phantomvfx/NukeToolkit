"""Matchmove / stabilize / corner pin exports checked against Nuke's own renderer.

A 'coordinate image' (R = pixel x, G = pixel y) is fed through the exported node. Sampling the output at the
tracked position of a point on frame f must return the point's position on the reference frame (matchmove), and
vice versa for stabilize. Nothing here relies on the formulas in worker/fit.py: Nuke evaluates the node.
"""
import json
import os
import shutil
import tempfile

import nuke

from taptrack import export, job, node
import test_pipeline as P

_CACHE = {}


def _tracked(pa=1.0):
    """Track the test plate once (cached) and return (result dict, result path, group)."""
    d = P._data()
    g = P._group(d)
    nuke.addFormat("640 360 0 0 640 360 1 tt_plate")
    nuke.addFormat("640 360 0 0 640 360 2 tt_anam")
    if pa != 1.0:
        g.input(0)["format"].setValue("tt_anam")
    stable = os.path.join(os.path.dirname(d["cache"]), "mm_result")
    if "result" not in _CACHE:
        path = node.track_blocking(g)
        shutil.rmtree(stable, ignore_errors=True)
        os.makedirs(stable)
        shutil.copy(path, os.path.join(stable, "result.json"))
        _CACHE["result"] = job.load_result(os.path.join(stable, "result.json"))
    path = job.knob_path(os.path.join(stable, "result.json"))
    g["result_path"].setValue(path)
    return _CACHE["result"], path, g, d


def _coord_source(fmt):
    nuke.root()["format"].setValue(fmt)
    c = nuke.nodes.Constant()
    e = nuke.nodes.Expression(expr0="x", expr1="y", expr2="0", expr3="1")
    e.setInput(0, c)
    return e


def _read_src(n, x, y, f):
    """Source coordinate (continuous) that ended up at output position (x, y) on frame f."""
    sx = n.sample("rgba.red", x, y, 0.5, 0.5, frame=f) + 0.5
    sy = n.sample("rgba.green", x, y, 0.5, 0.5, frame=f) + 0.5
    return sx, sy


def _fit_and_export(g, res, path, out_type, fit_model="Translate+Rotate+Scale", **knobs):
    g["out_type"].setValue(out_type)
    g["fit_model"].setValue(fit_model)
    for k, v in knobs.items():
        g[k].setValue(v)
    fit = job.run_fit(g["worker_python"].value(), os.path.dirname(path) and __import__("pathlib").Path(path).parent,
                      node.fit_options(g, path))
    if out_type == "CornerPin2D":
        n = export.export_cornerpin(fit)
    else:
        n = export.export_transform(fit, stabilize=out_type.endswith("Stabilize"))
    return n, fit


def _errors(n, res, d, fmt, stabilize):
    """Per (point, frame) distance between where Nuke sends the point and where it should go."""
    src = _coord_source(fmt)
    n.setInput(0, src)
    first = res["first"]
    ref_i = d["ref"] - first
    errs = []
    for q in range(len(res["tracks"])):
        p_ref = res["tracks"][q][ref_i]
        for f in range(res["first"], res["last"] + 1):
            p_f = res["tracks"][q][f - first]
            if not res["visible"][q][f - first]:
                continue
            if stabilize:
                got = _read_src(n, p_ref[0], p_ref[1], f)           # dest = reference position -> source = tracked position
                want = p_f
            else:
                got = _read_src(n, p_f[0], p_f[1], f)               # dest = tracked position -> source = reference position
                want = p_ref
            errs.append(((got[0] - want[0]) ** 2 + (got[1] - want[1]) ** 2) ** 0.5)
    return errs


def _check(errs, mean_max, max_max, label):
    mean, mx = sum(errs) / len(errs), max(errs)
    print("  %s: mean %.3f px, max %.3f px over %d samples" % (label, mean, mx, len(errs)))
    assert mean < mean_max and mx < max_max, (label, mean, mx)


def test_transform_matchmove_similarity():
    res, path, g, d = _tracked()
    n, fit = _fit_and_export(g, res, path, "Transform - Matchmove")
    assert fit["model"] == "similarity" and fit["weak_frames"] == []
    _check(_errors(n, res, d, "tt_plate", False), 1.0, 3.0, "matchmove similarity")


def test_transform_stabilize_similarity():
    res, path, g, d = _tracked()
    n, fit = _fit_and_export(g, res, path, "Transform - Stabilize")
    assert n["invert_matrix"].value()
    _check(_errors(n, res, d, "tt_plate", True), 1.0, 3.0, "stabilize similarity")


def test_transform_translate_model_only_keys_translate():
    res, path, g, d = _tracked()
    n, fit = _fit_and_export(g, res, path, "Transform - Matchmove", fit_model="Translate")
    assert n["translate"].isAnimated() and not n["rotate"].isAnimated() and not n["scale"].isAnimated()


def test_transform_affine_on_anamorphic_plate():
    """pixel aspect 2: Nuke applies the linear part in desqueezed space; the exported params must still reproduce the fit."""
    res, path, g, d = _tracked(pa=2.0)
    assert abs(job.plate_info(g)["pixel_aspect"] - 2.0) < 1e-9
    n, fit = _fit_and_export(g, res, path, "Transform - Matchmove", fit_model="Affine")
    assert fit["pixel_aspect"] == 2.0 and n["skewX"].isAnimated()
    _check(_errors(n, res, d, "tt_anam", False), 1.0, 3.0, "matchmove affine pa=2")
    n2, _ = _fit_and_export(g, res, path, "Transform - Stabilize", fit_model="Affine")
    _check(_errors(n2, res, d, "tt_anam", True), 1.0, 3.0, "stabilize affine pa=2")


def test_cornerpin_robust_and_raw_and_user_points():
    res, path, g, d = _tracked()
    n, fit = _fit_and_export(g, res, path, "CornerPin2D")
    assert len(set(fit["anchor_indices"])) == 4
    _check(_errors(n, res, d, "tt_plate", False), 1.5, 4.0, "cornerpin robust (auto anchors)")
    n2, fit2 = _fit_and_export(g, res, path, "CornerPin2D", pin_points="1 4 9 12")
    assert fit2["anchor_indices"] == [0, 3, 8, 11]
    n3, fit3 = _fit_and_export(g, res, path, "CornerPin2D", pin_points="1 4 9 12", robust_pin=False)
    # raw: the corners are exactly the tracked positions of those points
    ref_i = d["ref"] - res["first"]
    for k, q in enumerate([0, 3, 8, 11]):
        for f in (res["first"], d["ref"], res["last"]):
            got = n3["to%d" % (k + 1)].getValueAt(f)
            want = res["tracks"][q][f - res["first"]]
            assert abs(got[0] - want[0]) < 1e-3 and abs(got[1] - want[1]) < 1e-3
    assert abs(n3["from1"].value()[0] - res["tracks"][0][ref_i][0]) < 1e-3


def test_smoothing_keeps_reference_frame_exact_and_bad_pins_are_rejected():
    res, path, g, d = _tracked()
    n, fit = _fit_and_export(g, res, path, "Transform - Matchmove", smoothing="Gaussian", smooth_frames=2)
    src = _coord_source("tt_plate")
    n.setInput(0, src)
    ref_i = d["ref"] - res["first"]
    for q in (0, 5, 11):
        p = res["tracks"][q][ref_i]
        got = _read_src(n, p[0], p[1], d["ref"])
        assert abs(got[0] - p[0]) < 0.15 and abs(got[1] - p[1]) < 0.15, (got, p)   # sample() quantisation; the fit matrix itself is exactly identity
    g["pin_points"].setValue("1 2 3")
    try:
        node.fit_options(g, path)
        raise AssertionError("expected an error for three corner pin points")
    except job.TapTrackError as e:
        assert "four" in e.message
    g["pin_points"].setValue("")


def test_weak_frames_message_and_ranges():
    assert export.frame_ranges([1100, 1101, 1102, 1120, 1121, 1130]) == "1100-1102, 1120-1121, 1130"
    assert export.frame_ranges([]) == ""


def test_validity_node_switches_off_weak_frames():
    nuke.scriptClear()
    fit = {"first": 1, "last": 10, "weak_frames": [1, 2, 3, 9, 10]}
    n = export.export_validity(fit, "demo_valid")
    k = n["valid"]
    assert [round(k.getValueAt(f), 6) for f in range(1, 11)] == [0, 0, 0, 1, 1, 1, 1, 1, 0, 0]


def test_exports_create_a_validity_node_next_to_them():
    res, path, g, d = _tracked()
    n, fit = _fit_and_export(g, res, path, "Transform - Matchmove")
    v = nuke.toNode(n.name() + "_valid")
    assert v is not None and v["valid"].isAnimated()
    assert all(round(v["valid"].getValueAt(f), 6) == 1.0 for f in range(fit["first"], fit["last"] + 1) if f not in fit["weak_frames"])
    n2, _ = _fit_and_export(g, res, path, "CornerPin2D")
    assert nuke.toNode(n2.name() + "_valid") is not None


def test_export_button_puts_the_node_beside_taptrack_not_inside():
    """Regression: Nuke runs a Group's button callbacks with that Group as the current context, so the exports were
    created inside TAPTrack, wired to TAPTrack itself (nothing appeared in the graph; the GUI then hung)."""
    res, path, g, d = _tracked()
    g["result_path"].setValue(path)
    outer = nuke.nodes.Group(name="tt_outer")            # a TAPTrack nested in another Group must export into that Group
    outer.setInput(0, g.input(0))
    for n in nuke.selectedNodes():
        n.setSelected(False)
    for parent, tt in ((nuke.root(), g), (outer, None)):
        if tt is None:
            with outer:
                inp = nuke.nodes.Input(name="plate")
                tt = node.create()
                tt.setInput(0, inp)
            for k in ("first", "last", "ref_frame", "result_path"):
                tt[k].setValue(g[k].value())
        for out in ("Tracker4", "CornerPin2D", "Transform - Matchmove", "Transform - Stabilize"):
            tt["out_type"].setValue(out)
            inside = set(n.name() for n in tt.nodes())
            with tt:                                     # what pressing the Export button does
                node.on_export(tt)
            assert set(n.name() for n in tt.nodes()) == inside, (out, sorted(set(n.name() for n in tt.nodes()) - inside))
            made = nuke.selectedNodes() if parent is nuke.root() else [n for n in parent.nodes() if n.isSelected()]
            assert len(made) == 1, (out, [n.fullName() for n in made])
            m = made[0]
            assert m.parent() is not None and m.parent().fullName() == parent.fullName(), (out, m.fullName())
            assert m.input(0) is not None and m.input(0).fullName() == tt.fullName(), (out, m.input(0))
            if out != "Tracker4":
                v = nuke.toNode(m.fullName() + "_valid")
                assert v is not None, (out, m.fullName() + "_valid")
    nuke.delete(outer)


def test_pick_selected_and_track_work_from_inside_the_group():
    """Same trap as the Export button: 'Pick selected' and Track run inside the TAPTrack Group, where selectedNodes()
    and toNode() see only the Group's own internals."""
    d = P._data()
    g = P._group(d, source="Full-frame grid")
    t = P._tracker_with_points(d)
    for n in nuke.selectedNodes():
        n.setSelected(False)
    t.setSelected(True)
    msgs, old = [], nuke.message
    nuke.message = lambda m, *a, **k: msgs.append(m)
    try:
        with g:
            node.pick_tracker(g)
    finally:
        nuke.message = old
    assert msgs == [], msgs
    assert g["tracker_node"].value() == t.fullName() and g["query_source"].value() == "Tracker4"
    with g:
        plan = job.prepare(g)
    assert len(plan.job["queries"]) == len(d["pts"]), (len(plan.job["queries"]), len(d["pts"]))


def test_export_gives_feedback_and_never_pops_a_dialog():
    """Regression: the weak-frame notice used to be a modal dialog that froze Nuke (the tracked surface had left the frame)."""
    res, path, g, d = _tracked()
    bad = json.loads(open(path).read())
    T = len(bad["visible"][0])
    for q in range(len(bad["visible"])):
        for f in (T - 3, T - 2, T - 1):
            bad["visible"][q][f] = 0                       # nothing usable on the last three frames
    p = os.path.join(tempfile.mkdtemp(prefix="tt_weak_"), "result.json")
    json.dump(bad, open(p, "w"))
    g["result_path"].setValue(job.knob_path(p))
    msgs, old = [], nuke.message
    nuke.message = lambda m, *a, **k: msgs.append(m)
    try:
        for out in ("CornerPin2D", "Transform - Matchmove", "Transform - Stabilize", "Tracker4"):
            g["out_type"].setValue(out)
            before = set(n.name() for n in nuke.allNodes())
            node.on_export(g)
            new = [n for n in nuke.allNodes() if n.name() not in before]
            assert new, "nothing was created for " + out
            assert msgs == [], (out, msgs)
            status = g["status"].value()
            assert status.startswith("exported"), status
            if out != "Tracker4":
                assert "check frame" in status, status
                main = [n for n in new if not n.name().endswith("_valid")][0]
                assert "check frames" in main["label"].value()
            sel = nuke.selectedNodes()
            assert len(sel) == 1 and sel[0].name() in status, ([s.name() for s in sel], status)
    finally:
        nuke.message = old
