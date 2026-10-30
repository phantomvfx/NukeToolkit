"""The TAPTrack Group node: construction, knobs and button callbacks.

The node is a normal Group built from Python (it is saved into the script like any other node, so the
script needs the tool on the plugin path to *use* it; the nodes it exports - Tracker4 etc. - do not).

Inside:  plate(Input) -> tt_reformat -> tt_colorspace -> tt_write      (hidden render branch)
         plate(Input) -> Output                                        (pass-through)
         mask(Input)                                                   (grid-in-mask source)
"""
from __future__ import annotations

import functools
import threading
import traceback
from pathlib import Path
from typing import Optional

import nuke

from . import export, job

OUT_TYPES = ["Tracker4", "CornerPin2D", "Transform - Matchmove", "Transform - Stabilize"]
FIT_MODELS = {"Translate": "translate", "Translate+Rotate+Scale": "similarity", "Affine": "affine"}

# every button runs ``import taptrack.node as n; n.<callback>(nuke.thisNode())``
_CALL = "import taptrack.node as _n; _n.%s(nuke.thisNode())"


# --------------------------------------------------------------------------- errors
def show_error(err: job.TapTrackError) -> None:
    """Plain-language message plus a Details line; the log path is always there."""
    if not err.details:
        err.details = job.log("shown to the artist: " + err.message.replace(chr(10), " "))
    else:
        job.log("shown to the artist: %s | %s" % (err.message.replace(chr(10), " "), err.details))
    nuke.message("TAPTrack" + chr(10) + chr(10) + err.text())


def guarded(fn):
    """Button callbacks: expected problems show their message; anything else is logged with its traceback."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except job.TapTrackError as exc:
            show_error(exc)
        except Exception:  # noqa: BLE001
            path = job.log("unexpected error in %s:%s%s" % (fn.__name__, chr(10), traceback.format_exc()))
            show_error(job.TapTrackError("Something went wrong inside TAPTrack. Your script is not affected.", path))
    return wrapper


def quiet(fn):
    """Callbacks that run automatically (script open, knob edits): never pop up a dialog, just log."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            job.log("error in %s:%s%s" % (fn.__name__, chr(10), traceback.format_exc()))
    return wrapper


# --------------------------------------------------------------------------- construction
def default_colorspaces(cs_node) -> tuple:
    """(input, output) names for the OCIO stage: scene-linear -> sRGB display, whatever the config calls them."""
    values = cs_node["in_colorspace"].values()
    src = job.pick(values, ["scene_linear", "ACEScg", "linear", "Linear"]) or cs_node["in_colorspace"].value()
    dst = job.pick(cs_node["out_colorspace"].values(),
                   ["sRGB", "Output - sRGB", "sRGB - Display", "sRGB - Texture", "sRGB (Display)"])
    if dst is None:
        dst = next((v.split("\t")[0] for v in cs_node["out_colorspace"].values() if "srgb" in v.lower()), src)
    return src, dst


def _button(name: str, label: str, callback: str, tip: str = "", startline: bool = False):
    k = nuke.PyScript_Knob(name, label, _CALL % callback)
    k.setTooltip(tip)
    if not startline:
        k.clearFlag(nuke.STARTLINE)
    return k


@guarded
def create() -> "nuke.Node":
    """Create a TAPTrack node in the current graph and return it."""
    sel = nuke.selectedNode() if nuke.selectedNodes() else None
    g = nuke.nodes.Group(name="TAPTrack")
    g.begin()
    plate = nuke.nodes.Input(name="plate", number=0)
    mask = nuke.nodes.Input(name="mask", number=1)
    reformat = nuke.nodes.Reformat(name="tt_reformat", type="to box", box_width=256, box_height=256,
                                   box_fixed=True, resize="distort")
    reformat.setInput(0, plate)
    cs = nuke.nodes.OCIOColorSpace(name="tt_colorspace")
    cs.setInput(0, reformat)
    write = nuke.nodes.Write(name="tt_write", file_type="png")
    write.setInput(0, cs)
    out = nuke.nodes.Output()
    out.setInput(0, plate)
    mask.setXYpos(plate.xpos() + 150, plate.ypos())
    reformat.setXYpos(plate.xpos(), plate.ypos() + 80)
    cs.setXYpos(plate.xpos(), plate.ypos() + 140)
    write.setXYpos(plate.xpos(), plate.ypos() + 200)
    out.setXYpos(plate.xpos() - 150, plate.ypos() + 200)
    src_cs, dst_cs = default_colorspaces(cs)
    g.end()

    _add_knobs(g, src_cs, dst_cs)
    if sel is not None:
        g.setInput(0, sel)
    g["ref_frame"].setValue(int(nuke.frame()))
    set_range_from_input(g)
    g["onCreate"].setValue(_CALL % "refresh_status")
    g["knobChanged"].setValue("import taptrack.node as _n; _n.on_knob_changed(nuke.thisNode(), nuke.thisKnob())")
    refresh_status(g)
    return g


def _add_knobs(g, src_cs: str, dst_cs: str) -> None:
    add = g.addKnob
    add(nuke.Tab_Knob("tt_tracking", "Tracking"))
    add(nuke.Enumeration_Knob("model", "model", [m[0] for m in job.MODELS]))
    g["model"].setTooltip("BootsTAPNext: best default, runs at 256x256.\nBootsTAPIR (offline): runs at 512x512, slower.\n"
                          "TAPNext: older checkpoint, for comparison. BootsTAPIR (online): causal, slow with many points.")
    add(nuke.Int_Knob("first", "frame range"))
    add(nuke.Int_Knob("last", ""))
    g["last"].clearFlag(nuke.STARTLINE)
    add(_button("set_range", "Set from input", "set_range_from_input", "Use the plate's frame range."))
    add(nuke.Int_Knob("ref_frame", "reference frame"))
    add(_button("set_ref", "Current frame", "set_ref_to_current", "Track from the frame shown in the viewer."))
    add(nuke.Enumeration_Knob("work_res", "working resolution", job.WORK_CHOICES))
    g["work_res"].setTooltip("Frames are squashed to a square of this size for the network. Auto = the model's native size "
                             "(256 for TAPNext, 512 for TAPIR). TAPNext is fixed at 256.")
    add(nuke.Int_Knob("work_custom", "custom size"))
    g["work_custom"].setValue(512)
    add(nuke.Text_Knob("div1", ""))
    add(nuke.Enumeration_Knob("query_source", "points from", ["Tracker4", "Grid in mask", "Full-frame grid"]))
    add(nuke.String_Knob("tracker_node", "Tracker4 node"))
    add(_button("pick_tracker", "Pick selected", "pick_tracker", "Use the selected Tracker4's tracks at the reference frame."))
    add(nuke.Int_Knob("grid_spacing", "grid spacing (px)"))
    g["grid_spacing"].setValue(60)
    add(nuke.Int_Knob("max_points", "max points"))
    g["max_points"].setValue(500)
    add(nuke.Text_Knob("div2", ""))
    add(_button("track", "Track", "on_track", "Render the plate, run the tracker, keep the result in the script.", True))
    add(_button("cancel", "Cancel", "on_cancel", "Stop the running track."))
    add(_button("clear_cache", "Clear cache", "on_clear_cache", "Delete the cached frames and result for this setup."))
    add(nuke.String_Knob("status", "status"))
    g["status"].setEnabled(False)

    add(nuke.Tab_Knob("tt_export", "Export"))
    add(nuke.Enumeration_Knob("out_type", "output", OUT_TYPES))
    g["out_type"].setTooltip("Tracker4: every tracked point. CornerPin2D: four anchors (all points feed a robust fit). "
                             "Transform: one motion fitted to all points, as a matchmove or a stabilize.")
    add(nuke.Enumeration_Knob("fit_model", "transform model", list(FIT_MODELS)))
    add(nuke.Boolean_Knob("ransac", "reject outliers (RANSAC)"))
    g["ransac"].setValue(True)
    g["ransac"].setTooltip("Fit on the points that agree with each other on each frame. Switch off only to compare.")
    add(nuke.Boolean_Knob("prefer_continuous", "ignore re-acquired points when possible"))
    g["prefer_continuous"].setValue(True)
    g["prefer_continuous"].setTooltip("Points that were occluded and found again can come back slightly offset. Use only points "
                                      "that stayed visible since the reference frame, unless there are too few.")
    add(nuke.Enumeration_Knob("smoothing", "smoothing", ["none", "Gaussian"]))
    add(nuke.Int_Knob("smooth_frames", "smooth (frames)"))
    g["smooth_frames"].setValue(3)
    add(nuke.String_Knob("pin_points", "corner pin points"))
    g["pin_points"].setTooltip("Four point numbers (as in the Tracker4 rows, 1-based), e.g. '3 17 40 8'. Empty = pick four "
                               "well-spread, reliable points automatically.")
    add(nuke.Boolean_Knob("robust_pin", "corner pin from all points"))
    g["robust_pin"].setValue(True)
    g["robust_pin"].setTooltip("On: the corners follow a homography fitted to every point (survives occlusion). "
                               "Off: the corners follow the four raw tracks.")
    add(_button("export", "Export", "on_export", "Create the node below this one.", True))

    add(nuke.Tab_Knob("tt_settings", "Settings"))
    add(nuke.String_Knob("worker_python", "worker Python"))
    g["worker_python"].setValue(job.knob_path(job.default_python()))
    add(nuke.String_Knob("cache_dir", "cache folder"))
    g["cache_dir"].setValue(job.knob_path(job.default_cache_dir()))
    add(nuke.Enumeration_Knob("device", "device", ["auto", "cuda", "cpu"]))
    add(nuke.Enumeration_Knob("precision", "precision", ["fp16", "bf16", "fp32"]))
    add(nuke.String_Knob("ocio_in", "OCIO in"))
    g["ocio_in"].setValue(src_cs)
    add(nuke.String_Knob("ocio_out", "OCIO out"))
    g["ocio_out"].setValue(dst_cs)
    g["ocio_out"].setTooltip("The models were trained on display-referred sRGB video, so the plate is converted "
                             "from scene-linear to sRGB before tracking.")
    for hidden in ("result_path", "worker_pid", "cancelled"):
        k = nuke.String_Knob(hidden, hidden)
        k.setFlag(nuke.INVISIBLE)
        add(k)


# --------------------------------------------------------------------------- small callbacks
@guarded
def set_range_from_input(g) -> None:
    a, b = job.input_range(g)
    g["first"].setValue(a)
    g["last"].setValue(b)


@guarded
def set_ref_to_current(g) -> None:
    g["ref_frame"].setValue(int(nuke.frame()))


@guarded
def pick_tracker(g) -> None:
    with g.parent():           # the button runs inside TAPTrack, where only its internals are selectable
        sel = [n for n in nuke.selectedNodes() if n.Class() == "Tracker4"]
    if not sel:
        nuke.message("Select a Tracker4 node in the Node Graph first.")
        return
    g["tracker_node"].setValue(sel[0].fullName())
    g["query_source"].setValue("Tracker4")


@quiet
def on_knob_changed(g, knob) -> None:
    """Keep path knobs free of backslashes (Nuke would eat them as escapes when the script is saved)."""
    if knob is not None and knob.name() in ("cache_dir", "worker_python", "tracker_node"):
        fixed = job.knob_path(knob.value())
        if fixed != knob.value():
            knob.setValue(fixed)


@quiet
def refresh_status(g) -> None:
    """Show what the stored result is (called on script open and after every change)."""
    path = g["result_path"].value()
    if not path:
        g["status"].setValue("no result yet")
    elif not Path(path).is_file():
        g["status"].setValue("cached result is gone - press Track")
    else:
        try:
            g["status"].setValue(job.status_text(job.load_result(path)))
        except job.TapTrackError:
            g["status"].setValue("cached result unreadable - press Track")


# --------------------------------------------------------------------------- Track
def track_blocking(g, progress: Optional["callable"] = None) -> str:
    """Run the whole pipeline synchronously (terminal mode / tests). Returns the result path."""
    plan = job.prepare(g)
    if not plan.cached:
        job.check_environment(g["worker_python"].value(), plan.job["model"])
        job.render_frames(g, plan)
        (plan.job_dir / "job.json").write_text(_dump(plan.job), encoding="utf8")
        out = job.run_worker(g["worker_python"].value(), plan.job_dir, progress or (lambda f, m: None))
        if not out["ok"]:
            raise job.error_from_worker(out["error"]) if out["error"] else job.TapTrackError(
                "The tracker stopped unexpectedly.", job.log("worker exit %s" % out["returncode"]))
    g["result_path"].setValue(job.knob_path(plan.result_path))
    refresh_status(g)
    return job.knob_path(plan.result_path)


def _dump(obj) -> str:
    import json

    return json.dumps(obj, indent=1)


@guarded
def on_track(g) -> None:
    """Track button: render on the main thread, then track in a background thread with a progress bar."""
    try:
        plan = job.prepare(g)
        python = g["worker_python"].value()
        if plan.cached:
            g["result_path"].setValue(job.knob_path(plan.result_path))
            refresh_status(g)
            g["status"].setValue(g["status"].value() + " (cached)")
            return
        job.check_environment(python, plan.job["model"])
        job.render_frames(g, plan)
        (plan.job_dir / "job.json").write_text(_dump(plan.job), encoding="utf8")
    except job.TapTrackError as exc:
        show_error(exc)
        return

    g["cancelled"].setValue("")
    g["status"].setValue("tracking...")
    task = nuke.ProgressTask("TAPTrack")
    holder = [task]            # dropped in finish() so the progress panel closes
    node_name = g.fullName()

    def apply_progress(frac: float, msg: str) -> None:
        task.setProgress(int(frac * 100))
        task.setMessage(msg)

    def thread_main() -> None:
        def on_progress(frac: float, msg: str) -> None:
            nuke.executeInMainThread(apply_progress, args=(frac, msg))

        def on_started(pid: int) -> None:
            nuke.executeInMainThread(lambda: nuke.toNode(node_name)["worker_pid"].setValue(str(pid)))

        def should_cancel() -> bool:
            return bool(nuke.executeInMainThreadWithResult(task.isCancelled))

        try:
            out = job.run_worker(python, plan.job_dir, on_progress, on_started, should_cancel)
        except Exception as exc:  # noqa: BLE001
            out = {"ok": False, "cancelled": False, "error": {"message": "Could not start the tracker: %s" % exc,
                                                              "log": job.log("start failed: %s" % exc)}}
        nuke.executeInMainThread(finish, args=(out,))

    def finish(out: dict) -> None:
        holder.clear()
        node = nuke.toNode(node_name)
        if node is None:
            return
        node["worker_pid"].setValue("")
        cancelled = out["cancelled"] or node["cancelled"].value() == "1"
        if out["ok"]:
            node["result_path"].setValue(job.knob_path(plan.result_path))
            refresh_status(node)
        elif cancelled:
            node["status"].setValue("cancelled")
        else:
            node["status"].setValue("failed")
            show_error(job.error_from_worker(out["error"]) if out["error"] else job.TapTrackError(
                "The tracker stopped unexpectedly.", job.log("worker exit %s" % out.get("returncode"))))

    threading.Thread(target=thread_main, name="taptrack-worker", daemon=True).start()


@guarded
def on_cancel(g) -> None:
    pid = g["worker_pid"].value()
    if not pid:
        nuke.message("Nothing is tracking right now.")
        return
    g["cancelled"].setValue("1")
    job.kill_process(int(pid))


@guarded
def on_clear_cache(g) -> None:
    """Delete the cached frames + result this node points at (falls back to the current setup's folder)."""
    stored = g["result_path"].value()
    if stored:
        job.clear_cache(Path(stored).parent)
    else:
        try:
            job.clear_cache(job.prepare(g).job_dir)
        except job.TapTrackError:
            pass
    g["result_path"].setValue("")
    refresh_status(g)


# --------------------------------------------------------------------------- Export
def fit_options(g, result_path: str) -> dict:
    """The fit job for the Export tab's settings (see ``worker/fit.py`` for the keys)."""
    out_type = g["out_type"].value()
    model = "homography" if out_type == "CornerPin2D" else FIT_MODELS[g["fit_model"].value()]
    pins = [int(t) - 1 for t in g["pin_points"].value().replace(",", " ").split() if t.strip().isdigit()]
    if pins and len(pins) != 4:
        raise job.TapTrackError("Give exactly four corner pin points (or leave the field empty), e.g. '3 17 40 8'.")
    info = job.plate_info(g)
    opt = {"result": result_path, "model": model, "ref_frame": int(g["ref_frame"].value()),
           "pixel_aspect": info["pixel_aspect"], "method": "ransac" if g["ransac"].value() else "ls",
           "prefer_continuous": bool(g["prefer_continuous"].value()),
           "smoothing": "gaussian" if g["smoothing"].value() == "Gaussian" else "none",
           "smooth_sigma": float(g["smooth_frames"].value()), "robust_corners": bool(g["robust_pin"].value())}
    if pins:
        opt["corner_indices"] = pins
    return opt


def _announce(g, node, what: str, fit: Optional[dict] = None) -> None:
    """Feedback for an export that never blocks: select the new node and say on the status line what was made.

    (A modal dialog here froze Nuke behind the main window; warnings belong in the node label and status instead.)
    """
    for n in nuke.selectedNodes():
        n.setSelected(False)
    node.setSelected(True)
    weak = (fit or {}).get("weak_frames") or []
    text = "exported %s: %s" % (what, node.name())
    if weak:
        text += " | check frame(s) %s: the fit is not reliable there" % export.frame_ranges(weak)
    g["status"].setValue(text)
    job.log(text)


@guarded
def on_export(g) -> None:
    """Export button. Nuke runs a Group's button callbacks *inside* that Group, so everything here is done in the
    Group that holds TAPTrack: otherwise the new node lands inside TAPTrack, wired to TAPTrack itself."""
    with g.parent():
        _export(g)


def _export(g) -> None:
    path = g["result_path"].value()
    try:
        if not path:
            raise job.TapTrackError("Nothing to export yet - press Track first.")
        result = job.load_result(path)
        out_type = g["out_type"].value()
        if out_type == "Tracker4":
            tr4 = export.export_tracker4(result, source=g, ref_frame=int(g["ref_frame"].value()))
            _announce(g, tr4, "Tracker4 with %d tracks" % len(result["tracks"]))
            return
        job.check_environment(g["worker_python"].value(), dict(job.MODELS)[g["model"].value()])
        fit = job.run_fit(g["worker_python"].value(), Path(path).parent, fit_options(g, path))
        if out_type == "CornerPin2D":
            made = export.export_cornerpin(fit, source=g)
        else:
            made = export.export_transform(fit, stabilize=out_type.endswith("Stabilize"), source=g)
        _announce(g, made, out_type, fit)
    except job.TapTrackError as exc:
        show_error(exc)
