"""Nuke-side pipeline in terminal mode: OCIO stage, queries, a real tracking job, export, cache, cancel, errors.

Needs the worker venv + weights for the job tests. The pytest wrapper (tests/test_nuke.py) prepares a
10-frame plate with ground truth and passes it in via TAPTRACK_TEST_DATA (a json file).
"""
import json
import os
import tempfile
import time

import nuke

import tracker4_expected as E
from taptrack import export, job, node, queries

DATA = os.environ.get("TAPTRACK_TEST_DATA")


def _data():
    if not DATA:
        raise AssertionError("TAPTRACK_TEST_DATA not set (run via pytest tests/test_nuke.py)")
    with open(DATA) as f:
        return json.load(f)


def _fresh():
    nuke.scriptClear()
    nuke.root()["first_frame"].setValue(1001)
    nuke.root()["last_frame"].setValue(1010)


def _make_plate(d):
    """Read of the generated sequence (frames are display-referred sRGB PNGs)."""
    r = nuke.nodes.Read(file=d["pattern"].replace("\\", "/"), first=d["first"], last=d["last"], origfirst=d["first"],
                        origlast=d["last"])
    r["colorspace"].setValue("sRGB")
    return r


def _tracker_with_points(d):
    t = nuke.nodes.Tracker4(name="seed")
    export.write_tracker4_table(t["tracks"], [
        export.TrackData("p%d" % i, {d["ref"]: x}, {d["ref"]: y}, {d["ref"]: 1.0}) for i, (x, y) in enumerate(d["pts"])])
    return t


def _group(d, source="Tracker4"):
    import shutil
    shutil.rmtree(d["cache"], ignore_errors=True)      # every test starts without cached results
    _fresh()
    plate = _make_plate(d)
    g = node.create()
    g.setInput(0, plate)
    node.set_range_from_input(g)
    g["ref_frame"].setValue(d["ref"])
    g["cache_dir"].setValue(d["cache"])
    if source == "Tracker4":
        t = _tracker_with_points(d)
        g["tracker_node"].setValue(t.name())
    g["query_source"].setValue(source)
    return g


# ----------------------------------------------------------------------------------------- no worker needed
def test_ocio_stage_linear_to_srgb():
    _fresh()
    c = nuke.nodes.Constant()
    c["color"].setValue([0.5, 0.5, 0.5, 1.0])
    g = node.create()
    g.setInput(0, c)
    g["query_source"].setValue("Full-frame grid")
    g["first"].setValue(1001)
    g["last"].setValue(1002)
    g["ref_frame"].setValue(1001)
    g["cache_dir"].setValue(tempfile.mkdtemp(prefix="tt_ocio_"))
    plan = job.prepare(g)
    job.render_frames(g, plan)
    assert plan.side == 256
    files = sorted(os.listdir(str(plan.frames_dir)))
    assert files == ["f.1001.png", "f.1002.png"], files
    r = nuke.nodes.Read(file=(plan.frames_dir / "f.####.png").as_posix(), first=1001, last=1002)
    r["colorspace"].setValue("linear")             # identity: we want the raw 8-bit values
    assert (r.width(), r.height()) == (256, 256)
    v = r.sample("rgba.red", 128.5, 128.5)
    assert abs(v - 0.7354) < 0.006, v              # sRGB encoding of 0.5 linear


def test_query_tracker4_respects_enable_curve():
    _fresh()
    nuke.nodePaste(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "fixtures", "tracker4_reference.nk")))
    t = nuke.toNode("Tracker4_ref")
    pts, names = queries.tracker4_points(t, 3)
    assert len(pts) == 3 and abs(pts[0][0] - E.TRACKS[0][1][0] - 0) < 60
    pts7, names7 = queries.tracker4_points(t, 7)
    assert names7 == ["track 1", "track 3"], names7        # track 2 is disabled on frames 6-9


def test_query_grid_in_mask():
    _fresh()
    nuke.root()["format"].setValue("HD_1080")
    base = nuke.nodes.Constant()
    g = node.create()
    g.setInput(0, base)
    rect = nuke.nodes.Rectangle()                  # left half of the HD format is the mask
    rect["area"].setValue([0, 0, 540, 1080])
    g.setInput(1, rect)
    g["query_source"].setValue("Grid in mask")
    g["grid_spacing"].setValue(120)
    g["max_points"].setValue(1000)
    info = job.plate_info(g)
    pts, _ = job.collect_queries(g, "Grid in mask", 1001, info)
    assert pts and all(p[0] < 540 for p in pts), pts[:3]
    full, _ = job.collect_queries(g, "Full-frame grid", 1001, info)
    assert len(full) == 16 * 9 and len(pts) < len(full)
    assert abs(full[0][0] - 60) < 1e-9 and abs(full[0][1] - 60) < 1e-9     # centred grid, 120 px spacing


def test_work_side_rules():
    assert job.work_side("bootstapnext", "512", 512) == 256            # fixed-size net ignores the choice
    assert job.work_side("bootstapir", "Auto", 512) == 512
    assert job.work_side("bootstapir", "384", 512) == 384
    assert job.work_side("bootstapir", "Custom", 510) == 512           # snapped to a multiple of 8


def test_plain_language_errors():
    old = os.environ.get("TAPTRACK_CHECKPOINTS")
    try:
        job.check_environment(os.path.join(tempfile.gettempdir(), "no_such_python.exe"), "bootstapnext")
        raise AssertionError("expected an error")
    except job.TapTrackError as e:
        assert "environment is not installed" in e.message and "no_such_python" in e.details
    os.environ["TAPTRACK_CHECKPOINTS"] = tempfile.mkdtemp(prefix="tt_empty_")
    try:
        import sys
        job.check_environment(sys.executable, "bootstapnext")
        raise AssertionError("expected an error")
    except job.TapTrackError as e:
        assert "weights missing" in e.message and "download_weights" in e.message
    finally:
        if old is None:
            os.environ.pop("TAPTRACK_CHECKPOINTS", None)
        else:
            os.environ["TAPTRACK_CHECKPOINTS"] = old


def test_cache_key_changes_with_inputs():
    k = job.cache_key("bootstapnext", 1, 10, 256, "fp16", "sig", [[1, 1.0, 2.0]])
    assert k == job.cache_key("bootstapnext", 1, 10, 256, "fp16", "sig", [[1, 1.0, 2.0]])
    for other in (job.cache_key("tapnext", 1, 10, 256, "fp16", "sig", [[1, 1.0, 2.0]]),
                  job.cache_key("bootstapnext", 1, 11, 256, "fp16", "sig", [[1, 1.0, 2.0]]),
                  job.cache_key("bootstapnext", 1, 10, 512, "fp16", "sig", [[1, 1.0, 2.0]]),
                  job.cache_key("bootstapnext", 1, 10, 256, "fp16", "sig2", [[1, 1.0, 2.0]]),
                  job.cache_key("bootstapnext", 1, 10, 256, "fp16", "sig", [[1, 1.0, 2.5]])):
        assert other != k


# ----------------------------------------------------------------------------------------- real worker
def test_roundtrip_track_export_cache_restore():
    d = _data()
    g = _group(d)
    t0 = time.time()
    path = node.track_blocking(g)
    print("  track_blocking: %.1fs" % (time.time() - t0))
    res = job.load_result(path)
    Q, T = len(d["pts"]), d["last"] - d["first"] + 1
    assert len(res["tracks"]) == Q and len(res["tracks"][0]) == T

    # accuracy vs ground truth (plate px, Nuke convention) - the whole chain: Read -> OCIO -> PNG -> worker -> result
    err = [((res["tracks"][q][f][0] - d["truth"][q][f][0]) ** 2 + (res["tracks"][q][f][1] - d["truth"][q][f][1]) ** 2) ** 0.5
           for q in range(Q) for f in range(T)]
    mean = sum(err) / len(err)
    print("  mean error vs truth: %.3f px" % mean)
    assert mean < 1.0, mean
    ref_i = d["ref"] - d["first"]
    for q in range(Q):                                              # reference frame equals the query
        assert abs(res["tracks"][q][ref_i][0] - d["pts"][q][0]) < 1e-3

    # export: values in the Tracker4 match result.json
    tr4 = export.export_tracker4(res, source=g, ref_frame=d["ref"])
    assert export.table_rows(tr4["tracks"]) == Q
    for q in (0, Q // 2, Q - 1):
        for f in (d["first"], d["ref"], d["last"]):
            k = f - d["first"]
            assert abs(export.evaluate(tr4, q, "track_x", f) - res["tracks"][q][k][0]) < 1e-3
            assert abs(export.evaluate(tr4, q, "track_y", f) - res["tracks"][q][k][1]) < 1e-3
            assert export.evaluate(tr4, q, "enable", f) == float(res["visible"][q][k])
    assert tr4.input(0) is g

    # second Track is a cache hit (no new worker run)
    mtime = os.path.getmtime(path)
    t0 = time.time()
    plan_b = job.prepare(g)
    assert plan_b.cached, ("second prepare is not a cache hit", plan_b.key, os.path.basename(os.path.dirname(path)))
    assert node.track_blocking(g) == path and os.path.getmtime(path) == mtime
    assert time.time() - t0 < 5

    # result survives save / reopen
    script = os.path.join(tempfile.mkdtemp(prefix="tt_script_"), "restore.nk")
    nuke.scriptSaveAs(script, overwrite=1)
    nuke.scriptClear()
    nuke.scriptOpen(script)
    g2 = nuke.toNode("TAPTrack")
    assert g2["result_path"].value() == path, (g2.name(), g2["result_path"].value(), path)
    assert "points" in g2["status"].value() and "visible" in g2["status"].value(), g2["status"].value()
    plan2 = job.prepare(g2)
    assert plan2.cached and job.knob_path(plan2.result_path) == path, "cache key must survive save/reopen"
    # and clearing the cache is reflected
    node.on_clear_cache(g2)
    assert not os.path.exists(path) and g2["result_path"].value() == ""


def test_cancel_kills_worker():
    d = _data()
    g = _group(d)
    plan = job.prepare(g)
    job.render_frames(g, plan)
    (plan.job_dir / "job.json").write_text(json.dumps(plan.job))
    pids = []
    out = job.run_worker(g["worker_python"].value(), plan.job_dir, lambda f, m: None,
                         on_started=pids.append, should_cancel=lambda: True)
    assert out["cancelled"] and not out["ok"]
    time.sleep(1.0)
    assert not _alive(pids[0]), "worker still running after cancel"
    assert not plan.result_path.exists()


def test_worker_error_is_reported_plainly():
    d = _data()
    g = _group(d)
    plan = job.prepare(g)
    job.render_frames(g, plan)
    bad = dict(plan.job, first=plan.first - 5)             # frames that were never rendered
    (plan.job_dir / "job.json").write_text(json.dumps(bad))
    out = job.run_worker(g["worker_python"].value(), plan.job_dir, lambda f, m: None)
    assert not out["ok"] and out["error"]["code"] == "FRAMES_MISSING", out
    err = job.error_from_worker(out["error"])
    assert "was not rendered" in err.message and err.details.endswith(".log")


def _alive(pid):
    if os.name == "nt":
        import subprocess
        o = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid], capture_output=True, text=True).stdout
        return str(pid) in o
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
