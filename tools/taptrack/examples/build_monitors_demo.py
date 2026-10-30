"""Track tests/footage/monitors.mp4 with TAPTrack, time every stage, and save examples/monitors_demo.nk.

    Nuke16.1.exe -t examples/build_monitors_demo.py

Two surfaces are tracked (each with its own TAPTrack node):
  * the video wall   - reference frame 1, a flat rectangle visible until the camera tilts away (about frame 90)
  * the desk monitor - reference frame 150, a flat panel the camera moves past, with a person partly in front
Each gets a checkerboard 'screen replacement' on a CornerPin2D exported from the tracked points.
Timings go to tests/_out/monitors_timings.json.
"""
import json
import os
import pathlib
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "nuke"))
import nuke

from taptrack import export, job, node

VIDEO = os.path.join(ROOT, "tests", "footage", "monitors.mp4").replace(chr(92), "/")
OUT = os.path.join(ROOT, "examples", "monitors_demo.nk")
TIMINGS = os.path.join(ROOT, "tests", "_out", "monitors_timings.json")
T = {}


def timed(label, fn):
    t0 = time.time()
    r = fn()
    T[label] = round(time.time() - t0, 2)
    print("  %-34s %6.2f s" % (label, T[label]), flush=True)
    return r


nuke.root()["first_frame"].setValue(1)
nuke.root()["last_frame"].setValue(232)
nuke.root()["fps"].setValue(25)
nuke.root()["format"].setValue("HD_1080")

plate = nuke.nodes.Read(name="Plate", file=VIDEO)
plate["first"].setValue(1)
plate["last"].setValue(232)
if "sRGB" in [v.split(chr(9))[0] for v in plate["colorspace"].values()]:
    plate["colorspace"].setValue("sRGB")


def track(name, mask_area, ref, spacing, ypos):
    """Create a masked TAPTrack node and run it stage by stage with timings. Returns (group, result, result_path, fit)."""
    print("== %s (ref frame %d)" % (name, ref), flush=True)
    mask = nuke.nodes.Rectangle(name=name + "_Mask")
    mask["area"].setValue(mask_area)
    g = node.create()
    g["name"].setValue(name)
    g.setInput(0, plate)
    g.setInput(1, mask)
    g["query_source"].setValue("Grid in mask")
    g["grid_spacing"].setValue(spacing)
    g["max_points"].setValue(300)
    g["first"].setValue(1)
    g["last"].setValue(232)
    g["ref_frame"].setValue(ref)
    plan = timed(name + ": plan + sample mask", lambda: job.prepare(g))
    job.check_environment(g["worker_python"].value(), plan.job["model"])
    timed(name + ": render plate frames (Nuke)", lambda: job.render_frames(g, plan))
    (plan.job_dir / "job.json").write_text(json.dumps(plan.job))
    t0 = time.time()
    out = job.run_worker(g["worker_python"].value(), plan.job_dir, lambda f, m: None)
    T[name + ": tracker process (wall)"] = round(time.time() - t0, 2)
    print("  %-34s %6.2f s" % (name + ": tracker process (wall)", T[name + ": tracker process (wall)"]), flush=True)
    if not out["ok"]:
        raise job.error_from_worker(out["error"])
    g["result_path"].setValue(job.knob_path(plan.result_path))
    node.refresh_status(g)
    res = job.load_result(str(plan.result_path))
    m = res["meta"]
    for k, label in (("load_s", "model load"), ("frames_s", "read frames"), ("track_s", "tracking (forward+backward)")):
        T["%s:   %s" % (name, label)] = m[k]
        print("      %-32s %6.2f s" % (label, m[k]), flush=True)
    T[name + ": peak VRAM (MB)"] = m.get("peak_vram_reserved_mb")
    g["out_type"].setValue("CornerPin2D")
    g["fit_model"].setValue("Affine")
    fit = timed(name + ": fit homography (RANSAC)", lambda: job.run_fit(g["worker_python"].value(), pathlib.Path(plan.result_path).parent,
                                                                          node.fit_options(g, str(plan.result_path))))
    g.setXYpos(0, ypos)
    mask.setXYpos(-200, ypos)
    return g, res, str(plan.result_path), fit


wall, wall_res, wall_path, wall_fit = track("TAPTrack_Wall", [610, 495, 1550, 1020], 1, 70, -100)
mon, mon_res, mon_path, mon_fit = track("TAPTrack_Monitor", [930, 330, 1500, 770], 150, 50, 150)


def sticker(name, area, colors):
    """Checkerboard limited to ``area`` (drawn in the reference frame's space)."""
    cb = nuke.nodes.CheckerBoard2(name=name + "_Checker", boxsize=48)
    cb["color0"].setValue(colors[0])
    cb["color1"].setValue(colors[1])
    cb["color2"].setValue(colors[0])
    cb["color3"].setValue(colors[1])
    rect = nuke.nodes.Rectangle(name=name + "_Shape")
    rect["area"].setValue(area)
    mk = nuke.nodes.Merge2(name=name + "_Cut", operation="mask")
    mk.setInput(0, cb)
    mk.setInput(1, rect)
    return mk, rect, cb


t0 = time.time()
wall_pin = export.export_cornerpin(wall_fit, name="Wall_CornerPin")
mon_pin = export.export_cornerpin(mon_fit, name="Monitor_CornerPin")
wall_tr4 = export.export_tracker4(wall_res, source=wall, ref_frame=1)
wall_tr4["name"].setValue("Wall_Tracker4")
mon_tr4 = export.export_tracker4(mon_res, source=mon, ref_frame=150)
mon_tr4["name"].setValue("Monitor_Tracker4")
T["export: 2 CornerPin2D + 2 Tracker4 nodes"] = round(time.time() - t0, 2)
print("  %-34s %6.2f s" % ("export nodes", T["export: 2 CornerPin2D + 2 Tracker4 nodes"]), flush=True)

wall_src, wall_shape, wall_cb = sticker("Wall", [632, 505, 1538, 1008], [[0.9, 0.2, 0.2, 1], [0.1, 0.1, 0.1, 1]])
mon_src, mon_shape, mon_cb = sticker("Monitor", [940, 340, 1410, 760], [[0.1, 0.5, 1, 1], [0.1, 0.1, 0.1, 1]])
wall_pin.setInput(0, wall_src)
mon_pin.setInput(0, mon_src)
m1 = nuke.nodes.Merge2(name="Merge_Wall", operation="over", mix=0.65)
m1.setInput(0, plate)
m1.setInput(1, wall_pin)
m1["mix"].setExpression("0.65 * Wall_CornerPin_valid.valid")        # off where the wall is out of frame / the fit is weak
m2 = nuke.nodes.Merge2(name="Merge_Monitor", operation="over", mix=0.65)
m2.setInput(0, m1)
m2.setInput(1, mon_pin)
m2["mix"].setExpression("0.65 * Monitor_CornerPin_valid.valid")

note = nuke.nodes.StickyNote(name="README_Note")
note["label"].setValue(chr(10).join([
    "TAPTRACK on real footage (monitors.mp4, 232 frames @ 25 fps)",
    "Viewer 1 = Merge_Monitor: a RED checkerboard locked to the video wall (CornerPin2D from TAPTrack_Wall, ref frame 1)",
    "and a BLUE checkerboard locked to the desk monitor's document panel (CornerPin2D from TAPTrack_Monitor, ref frame 150).",
    "The wall leaves the top of the frame around frame 90; the monitor enters from the bottom around frame 20.",
    "Merge mix is linked to Wall_CornerPin_valid / Monitor_CornerPin_valid (1 = reliable fit, 0 = element off).",
    "Select Wall_Tracker4 / Monitor_Tracker4 to see every tracked point; select a TAPTrack node to re-Track or re-Export."]))
for n, x, y in ((plate, 0, -300), (wall_pin, 250, 250), (mon_pin, 600, 250), (wall_tr4, -300, 80), (mon_tr4, -300, 330),
                (wall_src, 250, 120), (mon_src, 600, 120), (wall_shape, 400, 40), (mon_shape, 750, 40), (wall_cb, 250, 40),
                (mon_cb, 600, 40), (m1, 0, 420), (m2, 0, 500), (note, -600, -300)):
    n.setXYpos(x, y)
viewer = nuke.nodes.Viewer(name="Viewer1")
viewer.setInput(0, m2)
nuke.scriptSaveAs(OUT, overwrite=1)
print("saved", OUT, flush=True)

# --- render the composite for every frame (this is what you see in the Viewer) and time it
outdir = os.path.join(ROOT, "tests", "_out", "monitors_render")
os.makedirs(outdir, exist_ok=True)
w = nuke.nodes.Write(name="TimingWrite", file=(outdir + "/comp.%04d.png").replace(chr(92), "/"), file_type="png", datatype="8 bit")
w.setInput(0, m2)
t0 = time.time()
nuke.execute(w, 1, 232)
T["comp: render 232 frames 1920x1080 PNG"] = round(time.time() - t0, 2)
T["comp: seconds per frame"] = round(T["comp: render 232 frames 1920x1080 PNG"] / 232, 3)
print("  comp render: %.1f s (%.3f s/frame)" % (T["comp: render 232 frames 1920x1080 PNG"], T["comp: seconds per frame"]), flush=True)
nuke.delete(w)
nuke.scriptSave()          # keep the demo without the timing Write node

for tag, fit, res in (("wall", wall_fit, wall_res), ("monitor", mon_fit, mon_res)):
    valid = [i for i, v in enumerate(fit["valid"]) if v]
    T[tag + ": points"] = res["meta"]["points"]
    T[tag + ": percent visible"] = res["meta"]["percent_visible"]
    T[tag + ": frames with a valid fit"] = "%d/%d" % (len(valid), len(fit["valid"]))
    T[tag + ": median inliers per frame"] = sorted(fit["inliers"])[len(fit["inliers"]) // 2]
    T[tag + ": median fit rms px"] = sorted(r for r in fit["rms"] if r > 0)[len([r for r in fit["rms"] if r > 0]) // 2]
    T[tag + ": weak frames"] = export.frame_ranges(fit["weak_frames"]) or "none"
json.dump(T, open(TIMINGS, "w"), indent=1)
print(json.dumps(T, indent=1))
