"""SAM3Matte Nuke layer (python/sam3matte.py), headless in Nuke 16.1. Pastes gizmos/SAM3Matte.nk (build it first with
build/build_sam3matte.py).

    & "L:\\Apps\\Nuke16.1v4\\Nuke16.1.exe" -t L:\\Nuke\\tools\\comfy\\tests\\nuke\\test_sam3matte_nuke.py

Prints PASS name / FAIL name for each check and exits 1 if any failed. Scratch files go to D:/ComfyUI_Cache/temp/sam3matte_t11.
run_job is replaced by a stub (the runner has its own tests against the fake ComfyUI); no ComfyUI is contacted.

The worker thread is real, but nuke.executeInMainThread / executeInMainThreadWithResult are replaced by a queue that THIS
(main) thread drains after joining the worker: headless Nuke has no main loop for them, and knobs must not be touched from a
second thread. sam3matte's own _on_main / _post_status wrappers (and their error handling) run for real; Nuke's two
functions themselves are not exercised here.
"""
import os, sys, runpy
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")).replace("\\", "/")
runpy.run_path(ROOT + "/init.py")   # path setup + the NukeComfy alias

import json
import queue
import shutil
import struct
import threading
import time

import nuke

SCRATCH = "D:/ComfyUI_Cache/temp/sam3matte_t11"
os.environ["NUKECOMFY_TEMP"] = SCRATCH + "/jobs"      # before any generate: otherwise renders go to the C: home folder
os.makedirs(os.environ["NUKECOMFY_TEMP"], exist_ok=True)

sys.path.insert(0, ROOT + "/tools/comfy/python")
sys.path.insert(0, ROOT + "/tools/comfy/tests")
import fake_comfy as fc
import sam3matte_png as png
import sam3matte_crypto as crypto
from NukeComfy.python import sam3matte as s

NK = ROOT + "/tools/comfy/gizmos/SAM3Matte.nk"
VER = SCRATCH + "/v1"
F0, F1 = 1001, 1200
W, H = 2048, 1080

failed = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))
    if not ok:
        failed.append(name)


# ---------------------------------------------------------------- harness
class Pump:
    """Stands in for the main thread's event queue: nuke.executeInMainThread* only queue the function here, and this
    (main) thread runs them after the worker is joined. sam3matte's own _on_main / _post_status wrappers stay real."""

    def __init__(self):
        self.q = queue.Queue()

    def put(self, func):
        self.q.put(func)

    def drain(self):
        while not self.q.empty():
            self.q.get()()


pump = Pump()
nuke.executeInMainThread = pump.put
nuke.executeInMainThreadWithResult = pump.put
real_check_server = s._check_server
s._check_server = lambda: None
real_run_job = s.run_job
messages = []
s._message = messages.append
calls = []
behaviour = {"fn": None}


def stub(job, report, cancel, client=None, resume=None, poll_s=2.0):
    calls.append({"job": job, "resume": resume, "client": client})
    return behaviour["fn"](job, report, cancel, client, resume)


s.run_job = stub


def result_for(job, partial=False, error=None, cancelled=False, resume=None):
    folder = resume["folder"] if resume else job["folder"]
    os.makedirs(folder, exist_ok=True)
    return {"folder": folder, "first": job["first"], "last": job["last"], "key": job["key"], "mode": job["mode"],
            "text": job["text"], "width": job["track_w"], "height": job["track_h"],
            "objects": [{"index": 0, "name": "person_1", "first": job["first"], "last": job["last"]}],
            "names": {"0": "person_1"}, "next_free": 1, "completed_chunks": [0], "n_chunks": 2 if partial else 1,
            "partial": partial, "cancelled": cancelled, "error": error, "dropped": 0,
            "hints": {"select": bool(job["positive"]), "stencil": job["stencil"] is not None, "text": bool(job["text"])},
            "message": "1 object, 1 chunk."}


def set_behaviour(fn):
    behaviour["fn"] = fn
    del calls[:]
    del messages[:]


def run(g, join_s=30):
    """generate + join the worker + drain the main-thread queue. Returns generate's result."""
    ok = s.generate(g)
    entry = s._RUNNING.get(g.fullName())
    if entry:
        entry["thread"].join(join_s)
        if entry["thread"].is_alive():
            check("worker_finished", False, "worker still running after %ss" % join_s)
    pump.drain()
    return ok


def status(g):
    return g["status"].value()


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    return struct.unpack(">II", head[16:24])


def plate_format(w, h):
    return nuke.addFormat("%d %d 1 t11_%dx%d" % (w, h, w, h))


def fresh(w=W, h=H, color=(0.2, 0.4, 0.6), small=True, first=1001, last=1005, key=1003):
    """Clear the script, paste the Group, add a plate. With `small` the knobs are set up for a cheap 5-frame run."""
    nuke.scriptClear()
    nuke.root()["first_frame"].setValue(F0)
    nuke.root()["last_frame"].setValue(F1)
    nuke.root()["format"].setValue(plate_format(w, h))
    nuke.nodePaste(NK)
    g = nuke.toNode("SAM3Matte")
    c = nuke.nodes.Constant(channels="rgb")
    c["color"].setValue([color[0], color[1], color[2], 1])
    c["format"].setValue(plate_format(w, h))
    g.setInput(0, c)
    g["prompt"].setValue("person")
    g["source_range"].setValue(False)     # a Constant's own range is 1-1
    g["first"].setValue(first)
    g["last"].setValue(last)
    g["key_current"].setValue(False)
    g["key_frame"].setValue(key)
    pump.drain()
    return g, c


def alpha_node(w=W, h=H, box=None):
    """Constant with alpha 0, plus (box = [x, y, r, t] in Nuke pixels) a Rectangle of alpha 1."""
    c = nuke.nodes.Constant(channels="rgba")
    c["color"].setValue([0, 0, 0, 0])
    c["format"].setValue(plate_format(w, h))
    if box is None:
        return c
    r = nuke.nodes.Rectangle(inputs=[c])
    r["area"].setValue(box)
    r["color"].setValue([1, 1, 1, 1])
    return r


# ---------------------------------------------------------------- fixture: tiny mask sequences (64x36)
def make_masks():
    os.makedirs(VER, exist_ok=True)
    for k, x0 in ((0, 16), (2, 4)):
        for frame in range(1001, 1004):
            data = bytearray(64 * 36)
            for y in range(9, 27):
                for x in range(x0, x0 + 32 if x0 == 16 else x0 + 12):
                    data[y * 64 + x] = 255
            png.write_gray_png("%s/obj%d.%04d.png" % (VER, k, frame), 64, 36, bytes(data))


def version(folder=VER, objects=((0, "person_1"),), key=1002, first=F0, last=F1, partial=False, text="person"):
    return {"folder": folder, "first": first, "last": last, "key": key, "mode": "key", "text": text, "width": 64,
            "height": 36, "objects": [{"index": i, "name": n, "first": first, "last": last} for i, n in objects],
            "names": {str(i): n for i, n in objects}, "next_free": 1 + max(i for i, _ in objects),
            "completed_chunks": [0], "n_chunks": 1, "partial": partial, "cancelled": False, "error": None, "dropped": 0,
            "hints": {"select": False, "stencil": False, "text": True}, "message": "x",
            "created": "2026-11-01 12:00:00",
            "params": {"mode": "key", "key": key, "first": first, "last": last, "text": text, "positive": [],
                       "negative": [], "max_objects": 4, "chunk_size": 250, "track_w": 1024, "track_h": 540}}


def put_versions(g, vs):
    s._store_versions(g, vs, 0 if vs else None)


def main():
    make_masks()
    sample = lambda node, x, y, frame=1001: node.sample("rgba.alpha", x, y, 1, 1, frame)

    # ------------------------------------------------------------ show_version_sets_reads
    g, _ = fresh()
    put_versions(g, [version(objects=((0, "person_1"), (2, "ball_1")))])
    s.show_version(g, 0)
    r0, r2 = nuke.toNode("SAM3Matte.Obj0Read"), nuke.toNode("SAM3Matte.Obj2Read")
    ok = (r0["file"].value().endswith(("obj0.####.png", "obj0.%04d.png")) and r2["file"].value().endswith(("obj2.####.png", "obj2.%04d.png"))
          and all(int(r0[k].value()) == v for k, v in (("first", F0), ("last", F1), ("origfirst", F0), ("origlast", F1))))
    flags = [bool(g["obj_present%d" % k].value()) for k in range(8)]
    ok = ok and flags == [True, False, True, False, False, False, False, False] and g["has_result"].value()
    ok = ok and g["mask_first"].value() == F0 and g["mask_last"].value() == F1 and g["stencil_frame"].value() == 1002
    ok = ok and bool(g["obj_on0"].value()) and bool(g["obj_on2"].value()) and not g["obj_on1"].value()
    centre, corner = sample(g, 1024, 540), sample(g, 50, 50)
    check("show_version_sets_reads", ok and centre > 0.99 and corner == 0,
          "file %s flags %s centre %.3f corner %.3f" % (r0["file"].value(), flags, centre, corner))

    # ------------------------------------------------------------ show_version_updates_crypto
    put_versions(g, [version(objects=((0, "person_1"), (2, "ball_1"))), version(objects=((0, "person_1"),))])
    s.show_version(g, 0)
    expr0 = nuke.toNode("SAM3Matte.CryptoExpr")["expr0"].value()
    meta = nuke.toNode("SAM3Matte.CryptoMeta")["metadata"].toScript()
    ids = {n: "%08x" % crypto.name_to_id(n)[0] for n in ("person_1", "ball_1")}
    floats = {n: "%.9g" % crypto.name_to_id(n)[1] for n in ("person_1", "ball_1")}
    ok1 = floats["person_1"] in expr0 and floats["ball_1"] in expr0 and all(i in meta for i in ids.values())
    s.show_version(g, 1)
    expr0b = nuke.toNode("SAM3Matte.CryptoExpr")["expr0"].value()
    metab = nuke.toNode("SAM3Matte.CryptoMeta")["metadata"].toScript()
    ok2 = floats["person_1"] in expr0b and floats["ball_1"] not in expr0b and ids["person_1"] in metab \
        and ids["ball_1"] not in metab
    check("show_version_updates_crypto", ok1 and ok2, "v0 both names %s; v1 only person_1 %s" % (ok1, ok2))

    # ------------------------------------------------------------ show_version(i) uses version i, not the pulldown's
    g, _ = fresh()
    put_versions(g, [version(objects=((0, "person_1"),)), version(objects=((2, "ball_1"), (3, "cup_1")))])
    g["version"].setValue(0)
    s.show_version(g, 1)
    on = [k for k in range(8) if g["obj_on%d" % k].value()]
    pres = [k for k in range(8) if g["obj_present%d" % k].value()]
    check("show_version_index_not_pulldown", on == [2, 3] and pres == [2, 3], "obj_on %s present %s" % (on, pres))

    # ------------------------------------------------------------ test_missing_folder_status
    g, _ = fresh()
    put_versions(g, [version(folder=SCRATCH + "/does_not_exist")])
    try:
        s.show_version(g, 0)
        err = None
    except Exception as e:
        err = e
    a = sample(g, 1024, 540)
    check("test_missing_folder_status", err is None and status(g).startswith("Masks not found") and a == 0
          and not g["has_result"].value() and not any(g["obj_present%d" % k].value() for k in range(8)),
          "err %r status %r alpha %s" % (err, status(g), a))

    # ------------------------------------------------------------ knob_changed_version
    g, _ = fresh()
    put_versions(g, [version(), version(objects=((0, "person_1"), (2, "ball_1")))])
    seen = []
    real_show = s.show_version
    s.show_version = lambda node, index: seen.append(index)
    try:
        g["version"].setValue(1)
        del seen[:]                       # setting the knob may already have called knobChanged
        s.knob_changed(g, g["version"])
    finally:
        s.show_version = real_show
    check("knob_changed_version", seen == [1], "show_version calls %s" % seen)

    # ------------------------------------------------------------ apply_objects_follows_knob
    g, _ = fresh()
    put_versions(g, [version(objects=((0, "person_1"), (2, "ball_1")))])
    s.show_version(g, 0)

    def on():
        return [k for k in range(8) if g["obj_on%d" % k].value()]
    results = {}
    err = None
    try:
        for text in ("person_1", "ball_1", "7", "all", "0, 2", "", "nobody"):
            g["objects"].setValue(text)
            s.knob_changed(g, g["objects"])
            results[text] = on()
    except Exception as e:
        err = e
    check("apply_objects_follows_knob", err is None and results == {
        "person_1": [0], "ball_1": [2], "7": [], "all": [0, 2], "0, 2": [0, 2], "": [0, 2], "nobody": []},
          "err %r results %s" % (err, results))

    # ------------------------------------------------------------ test_key_outside_range_error
    g, _ = fresh(first=F0, last=F1, key=5000)
    set_behaviour(lambda *a: None)
    ok = run(g)
    check("test_key_outside_range_error", ok is False and status(g).startswith("Key frame 5000 is outside 1001-1200")
          and not calls and messages and messages[0].startswith("SAM3Matte: Key frame 5000"),
          "ok %s status %r messages %s" % (ok, status(g), messages))

    # ------------------------------------------------------------ test_empty_select_error
    g, _ = fresh(first=F0, last=F1, key=1120)
    g.setInput(1, alpha_node())
    set_behaviour(lambda *a: None)
    ok = run(g)
    jobs = os.listdir(os.environ["NUKECOMFY_TEMP"])
    check("test_empty_select_error", ok is False and status(g) == "Select is empty on frame 1120" and not calls,
          "ok %s status %r calls %d" % (ok, status(g), len(calls)))

    # ------------------------------------------------------------ test_whole_range_with_select
    g, _ = fresh()
    g.setInput(1, alpha_node(box=[0, 0, 500, 500]))
    g["mode"].setValue("Whole range")
    set_behaviour(lambda *a: None)
    ok = run(g)
    check("test_whole_range_with_select", ok is False and "From key frame" in status(g) and not calls,
          "ok %s status %r" % (ok, status(g)))

    # ------------------------------------------------------------ test_no_prompt_no_select
    g, _ = fresh()
    g["prompt"].setValue("")
    set_behaviour(lambda *a: None)
    ok = run(g)
    check("test_no_prompt_no_select", ok is False and "Prompt" in status(g) and "Select" in status(g) and not calls,
          "ok %s status %r" % (ok, status(g)))

    # ------------------------------------------------------------ range-mode and other input errors
    g, _ = fresh(first=-3, last=5)
    set_behaviour(lambda *a: None)
    ok1 = run(g)
    st1 = status(g)
    g, _ = fresh(first=1005, last=1001)
    ok2 = run(g)
    st2 = status(g)
    g, _ = fresh()
    g.setInput(0, None)
    ok3 = run(g)
    st3 = status(g)
    check("input_errors", ok1 is False and "negative" in st1 and ok2 is False and "after Last" in st2
          and ok3 is False and st3 == "Connect a Source image" and not calls,
          "%r | %r | %r" % (st1, st2, st3))

    # ------------------------------------------------------------ test_render_plate_files
    g, _ = fresh()
    folder = SCRATCH + "/plate_a"
    shutil.rmtree(folder, ignore_errors=True)
    before = set(n.fullName() for n in nuke.allNodes(recurseGroups=True))
    frames_dir, key_png = s.render_plate(g, 1001, 1003, 1002, 1024, 540, folder)
    names = sorted(os.listdir(frames_dir))
    sizes = {n: png_size(frames_dir + "/" + n) for n in names if n.startswith("f_")}
    leftovers = [n for n in nuke.allNodes(recurseGroups=True) if n.fullName() not in before]
    check("test_render_plate_files",
          names == ["f_00000.png", "f_00001.png", "f_00002.png", "key.png"] and set(sizes.values()) == {(1024, 540)}
          and png_size(key_png) == (2048, 1080) and key_png.endswith("/_plate/key.png") and not leftovers,
          "names %s sizes %s key %s leftover nodes %s" % (names, set(sizes.values()), png_size(key_png),
                                                          [n.name() for n in leftovers]))
    fd2, kp2 = s.render_plate(g, 1001, 1001, None, 1024, 540, SCRATCH + "/plate_b")
    check("render_plate_range_mode_no_key", kp2 is None and not os.path.exists(fd2 + "/key.png"))

    # odd width: tracking_size(1023, 500) is (1024, 500); the PNG must be exactly that
    g, _ = fresh(w=1023, h=500)
    import sam3matte_chunks as chunks
    tw, th = chunks.tracking_size(1023, 500)
    fd3, kp3 = s.render_plate(g, 1001, 1001, 1001, tw, th, SCRATCH + "/plate_c")
    check("render_plate_odd_width", (tw, th) == (1024, 500) and png_size(fd3 + "/f_00000.png") == (tw, th)
          and png_size(kp3) == (1023, 500), "tracking %s frame %s key %s" % ((tw, th), png_size(fd3 + "/f_00000.png"),
                                                                              png_size(kp3)))

    # ------------------------------------------------------------ plate_tone_mapped (decision 7)
    g, _ = fresh(color=(1.5, 1.5, 1.5))
    root = nuke.root()
    root["colorManagement"].setValue("OCIO")
    root["OCIO_config"].setValue(1)      # fn-nuke_cg-config-v2.2.0: sRGB - Display / ACES 1.0 - SDR Video
    pair = s._ocio_display_view()
    _, kp = s.render_plate(g, 1001, 1001, 1001, 1024, 540, SCRATCH + "/plate_d")
    v_ocio = png.read_mask(kp)[2][0]
    root["colorManagement"].setValue("Nuke")
    check("plate_tone_mapped", pair == ("sRGB - Display", "ACES 1.0 - SDR Video") and v_ocio < 255 and v_ocio > 150,
          "pair %s, scene-linear 1.5 renders as %d" % (pair, v_ocio))
    g, _ = fresh(color=(1.5, 1.5, 1.5))
    nuke.root()["colorManagement"].setValue("Nuke")
    _, kp = s.render_plate(g, 1001, 1001, 1001, 1024, 540, SCRATCH + "/plate_e")
    v_nuke = png.read_mask(kp)[2][0]
    check("plate_fallback_srgb_no_ocio", s._ocio_display_view() is None and v_nuke == 255,
          "with colour management off 1.5 renders as %d (plain sRGB encode)" % v_nuke)

    # ------------------------------------------------------------ test_sample_input_grid
    g, _ = fresh()
    none_select = s.sample_input(g, 1, 1003)
    g.setInput(1, alpha_node(box=[0, 0, W // 2, H // 2]))      # Nuke y is up: lower-left quadrant
    t0 = time.time()
    grid = s.sample_input(g, 1, 1003)
    dt = time.time() - t0
    rows, cols = len(grid), len(grid[0])
    top_ok = all(v == 0 for r in grid[:rows // 2 - 1] for v in r)
    right_ok = all(v == 0 for r in grid for v in r[cols // 2 + 1:])
    bottom_left_ok = all(v == 1.0 for r in grid[rows // 2 + 2:] for v in r[:cols // 2 - 1])
    check("test_sample_input_grid", none_select is None and (cols, rows) == (48, 25) and top_ok and right_ok
          and bottom_left_ok, "unconnected %s, %dx%d, %.2fs, top %s right %s bottom-left %s" % (
              none_select, cols, rows, dt, top_ok, right_ok, bottom_left_ok))

    # ------------------------------------------------------------ job content (Select + Stencil), run to the stub
    g, _ = fresh(first=1001, last=1003, key=1002)
    g.setInput(1, alpha_node(box=[900, 400, 1100, 700]))
    g.setInput(2, alpha_node(box=[0, 0, 600, H]))       # left strip
    set_behaviour(lambda job, report, cancel, client, resume: result_for(job))
    ok = run(g)
    j = calls[0]["job"] if calls else {}
    pos, neg = j.get("positive", []), j.get("negative", [])
    names = sorted(os.listdir(j["frames_dir"])) if calls and os.path.isdir(j.get("frames_dir", "")) else []
    check("job_content", ok is True and calls and j["mode"] == "key" and j["key"] == 1002 and j["first"] == 1001
          and j["last"] == 1003 and j["text"] == "person" and (j["track_w"], j["track_h"]) == (1024, 540)
          and pos and all(880 <= p["x"] <= 1120 and 380 <= p["y"] <= 700 for p in pos) and len(pos) <= 8
          and neg and all(p["x"] < 650 for p in neg)
          and j["stencil"]["cols"] == 48 and j["stencil"]["rows"] == len(j["stencil"]["values"])
          and j["max_objects"] == 4 and j["chunk_size"] == 250 and j["job"].startswith("SAM3Matte_")
          and j["folder"].endswith(j["job"]) and "key.png" in names and "f_00002.png" in names
          and calls[0]["resume"] is None,
          "ok %s pos %s neg %d stencil %s frames %s" % (ok, pos, len(neg), bool(j.get("stencil")), names[:4]))
    vs = s._versions(g)
    check("version_added_and_shown", len(vs) == 1 and vs[0]["params"]["key"] == 1002 and vs[0]["params"]["positive"] == pos
          and g["version"].getValue() == 0 and g["version"].value().startswith("v1  person  key 1002")
          and g["has_result"].value() and g["obj_present0"].value() and status(g).startswith("Done"),
          "versions %d label %r status %r" % (len(vs), g["version"].value(), status(g)))

    # ------------------------------------------------------------ range mode job
    g, _ = fresh(first=1001, last=1003)
    g["mode"].setValue("Whole range")
    set_behaviour(lambda job, report, cancel, client, resume: result_for(job))
    ok = run(g)
    j = calls[0]["job"] if calls else {}
    check("range_job", ok is True and j.get("mode") == "range" and j["key"] is None and j["key_png"] is None
          and j["positive"] == [] and j["stencil"] is None, "ok %s job %s" % (ok, {k: j.get(k) for k in ("mode", "key", "key_png")}))

    # ------------------------------------------------------------ test_nothing_found_status
    g, _ = fresh()
    def nothing(job, report, cancel, client, resume):
        raise s.NothingFound("SAM3 found no object for this prompt.")
    set_behaviour(nothing)
    ok = run(g)
    folder_gone = not os.path.exists(calls[0]["job"]["folder"]) if calls else False
    check("test_nothing_found_status", ok is True and status(g).startswith('Nothing found for "person"')
          and g["versions_data"].value() == "[]" and folder_gone and not s._RUNNING,
          "status %r versions %r folder removed %s" % (status(g), g["versions_data"].value(), folder_gone))

    # ------------------------------------------------------------ test_partial_result_adds_version (+ uploads note)
    g, _ = fresh()
    def partial(job, report, cancel, client, resume):
        report("Chunk 1/2: tracking...")
        client.uploaded += 3          # what ComfyClient.upload would have counted
        return result_for(job, partial=True, error="ComfyUI went away")
    set_behaviour(partial)
    ok = run(g)
    vs = s._versions(g)
    check("test_partial_result_adds_version", ok is True and len(vs) == 1 and vs[0]["partial"]
          and g["version"].value().endswith("partial") and status(g).startswith("Error: ComfyUI went away")
          and "3 uploaded files remain in ComfyUI input/nukecomfy" in status(g),
          "versions %d label %r status %r" % (len(vs), g["version"].value(), status(g)))

    # ------------------------------------------------------------ test_resume_replaces_version
    first_folder = vs[0]["folder"]
    def finish_it(job, report, cancel, client, resume):
        return result_for(job, resume=resume)
    set_behaviour(finish_it)
    ok = run(g)
    vs2 = s._versions(g)
    rs = calls[0]["resume"] if calls else None
    check("test_resume_replaces_version", ok is True and rs is not None and rs["folder"] == first_folder
          and len(vs2) == 1 and not vs2[0]["partial"] and vs2[0]["folder"] == first_folder
          and calls[0]["job"]["folder"] == first_folder and not g["version"].value().endswith("partial"),
          "resume %s versions %d partial %s" % (bool(rs), len(vs2), vs2[0]["partial"] if vs2 else None))

    # a different prompt must not resume
    g["prompt"].setValue("ball")
    set_behaviour(finish_it)
    run(g)
    check("no_resume_when_params_differ", calls and calls[0]["resume"] is None and len(s._versions(g)) == 2,
          "versions %d" % len(s._versions(g)))

    # ------------------------------------------------------------ cancel
    g, _ = fresh()
    s.cancel(g)
    check("test_cancel_noop_when_idle", status(g) == "Nothing is running" and not s._RUNNING, status(g))

    gate = threading.Event()
    def blocker(job, report, cancel, client, resume):
        gate.wait(15)
        r = result_for(job, partial=True, cancelled=True)
        r["message"] = "Cancelled after 1 of 2 chunks."
        return r
    set_behaviour(blocker)
    ok1 = s.generate(g)
    ok2 = s.generate(g)                                  # while the first still runs
    refused = status(g)
    refused_msg = list(messages)
    s.cancel(g)
    entry = s._RUNNING.get(g.fullName())
    cancel_set = entry["cancel"].is_set() if entry else None
    gate.set()
    if entry:
        entry["thread"].join(15)
    pump.drain()
    check("test_double_generate_refused", ok1 is True and ok2 is False and refused.startswith("Already running")
          and refused_msg and not s._RUNNING and len(calls) == 1,
          "ok1 %s ok2 %s status %r calls %d" % (ok1, ok2, refused, len(calls)))
    check("cancel_sets_event_and_keeps_partial", cancel_set is True and status(g).startswith("Cancelled")
          and len(s._versions(g)) == 1, "cancel set %s status %r" % (cancel_set, status(g)))

    # ------------------------------------------------------------ a failure while adding the version is shown, not swallowed
    g, _ = fresh()
    set_behaviour(lambda job, report, cancel, client, resume: result_for(job))
    real_add = s._add_result
    def broken(*a, **k):
        raise KeyError("boom")
    s._add_result = broken
    try:
        ok = run(g)
    finally:
        s._add_result = real_add
    check("add_result_failure_shown", ok is True and status(g).startswith("Error: adding the version failed")
          and "KeyError" in status(g) and not s._RUNNING, "status %r" % status(g))

    # ------------------------------------------------------------ deleted node during the run must not raise in the thread
    g, _ = fresh()
    gate2 = threading.Event()
    boom = []
    def slow(job, report, cancel, client, resume):
        gate2.wait(15)
        report("late status")
        return result_for(job)
    set_behaviour(slow)
    s.generate(g)
    entry = s._RUNNING.get(g.fullName())
    nuke.delete(g)
    gate2.set()
    entry["thread"].join(15)
    try:
        pump.drain()                 # the queued callbacks meet a deleted node
        err = None
    except Exception as e:
        err = e
    check("deleted_node_no_exception", err is None and not entry["thread"].is_alive() and not s._RUNNING, repr(err))

    # ------------------------------------------------------------ knob toggles keep the UI in step
    g, _ = fresh()
    g["key_current"].setValue(True)
    s.knob_changed(g, g["key_current"])
    off_key = g["key_frame"].enabled()
    g["key_current"].setValue(False)
    s.knob_changed(g, g["key_current"])
    on_key = g["key_frame"].enabled()
    g["mode"].setValue("Whole range")
    s.knob_changed(g, g["mode"])
    range_key = g["key_current"].enabled() or g["key_frame"].enabled()
    g["source_range"].setValue(True)
    s.knob_changed(g, g["source_range"])
    check("knob_toggles_enable_state", off_key is False and on_key is True and range_key is False
          and g["first"].enabled() is False and g["last"].enabled() is False,
          "key_frame enabled: current-on %s current-off %s; range mode any key %s" % (off_key, on_key, range_key))

    # ------------------------------------------------------------ end to end: real runner against the fake ComfyUI
    pw, ph = 128, 72
    g, _ = fresh(w=pw, h=ph, first=1001, last=1003, key=1002)
    g.setInput(1, alpha_node(w=pw, h=ph, box=[40, 20, 90, 50]))
    white = fc.rgb_png_bytes(pw, ph, bytes([255]) * (pw * ph))
    holder = {}

    def responder(wf, n):
        if fc.nodes_of(wf, "SAM3_Detect"):
            return {"detect_save": [white]}
        info = fc.track_info(wf)
        nframes = len(holder["fake"].uploaded_names(info["directory"]))
        out = {}
        for j in range(info["n_save"]):
            sub, _, name = info["seeds"][j].rpartition("/")
            out["save%d" % j] = [holder["fake"].upload_data[(sub, name)]] * nframes
        return out

    fake = fc.FakeComfy(responder).start()
    holder["fake"] = fake
    old_url = s.api.api_client.base_url
    s.api.api_client.base_url = fake.url
    s.run_job = real_run_job
    s._check_server = real_check_server
    del messages[:]
    try:
        ok = run(g, join_s=60)
    finally:
        s.api.api_client.base_url = old_url
        s._check_server = lambda: None
        s.run_job = stub
        fake.stop()
    vs = s._versions(g)
    v = vs[0] if vs else {}
    files = sorted(os.listdir(v["folder"])) if v and os.path.isdir(v["folder"]) else []
    mask_ok = v and png_size("%s/obj0.1002.png" % v["folder"]) == (pw, ph)
    centre = sample(g, 64, 36, 1002) if v else None
    check("end_to_end_fake_comfy", ok is True and len(vs) == 1 and v["objects"] and v["objects"][0]["name"] == "select"
          and files == ["obj0.1001.png", "obj0.1002.png", "obj0.1003.png"] and mask_ok and centre and centre > 0.99
          and "%d uploaded files remain in ComfyUI input/nukecomfy" % len(fake.uploads) in status(g) and fake.uploads
          and not os.path.exists(v["folder"] + "/_plate"),
          "ok %s status %r files %s centre %s uploads %d" % (ok, status(g), files, centre, len(fake.uploads)))

    print("FAILED: %s" % failed if failed else "ALL PASSED")
    sys.exit(1 if failed else 0)


main()
