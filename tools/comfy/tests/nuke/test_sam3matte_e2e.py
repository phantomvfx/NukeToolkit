"""SAM3Matte end to end, headless in Nuke 16.1: one artist path through the REAL Group, the REAL sam3matte.generate and the
REAL runner, against the in-process fake ComfyUI (tests/fake_comfy.py). Nothing here contacts a real ComfyUI.

    & "L:\\Apps\\Nuke16.1v4\\Nuke16.1.exe" -t L:\\Nuke\\tools\\comfy\\tests\\nuke\\test_sam3matte_e2e.py

Build the .nk first (build/build_sam3matte.py). Prints PASS name / FAIL name per check and exits 1 if any failed.
Scratch files go to D:/ComfyUI_Cache/temp/sam3matte_t13 (version folders under .../jobs).

The fake serves the spike masks (L:/ComfyUI/output/sam3_spike/run1/obj0_00001_.png..obj0_00200_.png, 2048x1080, unscaled,
file n = frame 1000+n) as the SaveImage outputs, picking the files that match the frame list of each chunk (the frames
the runner uploaded, reversed for a backward chunk); the Detect prompt gets the mask of the key frame. A Constant
2048x1080 (project range 1001-1200) stands in for the plate. The model is not run: the masks are the spike's.

What is real: the pasted Group, Generate's input checks, plate render (Write + PNG), Select sampling, job building, the worker
thread, the runner (uploads, /prompt, /history polling, /view downloads, chunk placement, scan), version storage, show_version,
the Reads, the Cryptomatte metadata (picked by the native Cryptomatte node), and Make Roto.

What is NOT real (said plainly): headless `-t` Nuke has no main loop, and nuke.executeInMainThread / executeInMainThreadWithResult
raise "Invalid function call in non-gui mode" here (probed). This script therefore replaces both with a queue that THIS (main)
thread drains while it waits for the worker. executeInMainThreadWithResult BLOCKS the worker until the main thread has run the
function, like the real one. sam3matte's own _on_main / _post_status wrappers and the guarded version-adding path run for real; Nuke's
two functions themselves are not exercised, and the Generate/Cancel buttons are not pressed (the module entry points are
called, as the button does). The Nuke GUI is not covered.
"""
import os, sys, runpy
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")).replace("\\", "/")
runpy.run_path(ROOT + "/init.py")   # path setup + the NukeComfy alias

import json
import queue
import re
import shutil
import struct
import threading
import time
import zlib

import nuke

SCRATCH = "D:/ComfyUI_Cache/temp/sam3matte_t13"
JOBS = SCRATCH + "/jobs"
shutil.rmtree(SCRATCH, ignore_errors=True)
os.makedirs(JOBS, exist_ok=True)
os.environ["NUKECOMFY_TEMP"] = JOBS      # before any generate: otherwise renders go to the C: home folder

sys.path.insert(0, ROOT + "/tools/comfy/python")
sys.path.insert(0, ROOT + "/tools/comfy/tests")
import fake_comfy as fc
import sam3matte_png as png
import sam3matte_crypto as crypto

# COMFY_URL is read when NukeComfy.python.api is imported. Start a fake first so the env var is valid from the start;
# every case then starts its own fake and re-points api_client (the env var alone would be fixed at import time).
_first_fake = fc.FakeComfy(lambda wf, n: {}).start()
os.environ["COMFY_URL"] = _first_fake.url
from NukeComfy.python import sam3matte as s
from NukeComfy.python import sam3matte_roto as roto_mod

NK = ROOT + "/tools/comfy/gizmos/SAM3Matte.nk"
SPIKE = "L:/ComfyUI/output/sam3_spike/run1"
F0, F1 = 1001, 1200
W, H = 2048, 1080

failed = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))
    sys.stdout.flush()
    if not ok:
        failed.append(name)


def info(text):
    print("INFO " + text)
    sys.stdout.flush()


# ------------------------------------------------------------------------------------------------ main-thread pump
class Pump:
    """Stands in for Nuke's main-thread event queue (see the module docstring)."""

    def __init__(self):
        self.q = queue.Queue()
        self.main = threading.current_thread()

    def post(self, func):                       # nuke.executeInMainThread: fire and forget
        self.q.put((func, None))

    def call(self, func):                       # nuke.executeInMainThreadWithResult: the caller waits for the result
        if threading.current_thread() is self.main:
            return func()
        box = {"done": threading.Event()}
        self.q.put((func, box))
        if not box["done"].wait(120):
            raise RuntimeError("the main thread did not run the call within 120 s")
        if "exc" in box:
            raise box["exc"]
        return box.get("result")

    def drain(self):
        n = 0
        while True:
            try:
                func, box = self.q.get_nowait()
            except queue.Empty:
                return n
            n += 1
            if box is None:
                try:
                    func()
                except Exception as e:           # what the real queue does with a failing callback: print and go on
                    print("main-thread callback raised: %r" % (e,))
                continue
            try:
                box["result"] = func()
            except BaseException as e:
                box["exc"] = e
            box["done"].set()


pump = Pump()
nuke.executeInMainThread = pump.post
nuke.executeInMainThreadWithResult = pump.call
messages = []
s._message = messages.append               # nuke.message would block a headless run


def wait_until(pred, timeout, what):
    """Drain the main-thread queue until pred() is true. Returns True, or False after `timeout` seconds."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        pump.drain()
        if pred():
            return True
        time.sleep(0.02)
    pump.drain()
    check("wait: " + what, False, "timed out after %ss" % timeout)
    return False


def run_generate(g, timeout=180, hook=None):
    """Generate as the button does, then pump until the worker is done. Returns (generate's result, seconds).
    `hook(entry)` is called once, right after generate returned, while the worker thread is running."""
    t0 = time.time()
    ok = s.generate(g)
    entry = s._RUNNING.get(g.fullName())
    if hook and entry:
        hook(entry)
    if entry:
        wait_until(lambda: not entry["thread"].is_alive(), timeout, "worker finished")
    pump.drain()
    return ok, time.time() - t0


# ------------------------------------------------------------------------------------------------ the fake's responses
_spike_cache = {}


def spike_png(frame):
    n = frame - 1000
    if n not in _spike_cache:
        with open("%s/obj0_%05d_.png" % (SPIKE, n), "rb") as f:
            _spike_cache[n] = f.read()
    return _spike_cache[n]


def _chunk(tag, body):
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)


BLANK = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
         + _chunk(b"IDAT", zlib.compress((b"\x00" + bytes(W * 3)) * H)) + _chunk(b"IEND", b""))


class Case:
    """Mutable behaviour of the current fake: which frame Detect answers for, hooks per prompt number."""

    def __init__(self, first=F0, detect_frame=None, detect_empty=False):
        self.first = first
        self.detect_frame = detect_frame
        self.detect_empty = detect_empty
        self.hooks = {}                 # prompt_no -> callable(workflow) run in the server thread before the answer
        self.errors = {}                # prompt_no -> FakeError to raise
        self.detect_workflows = []


def make_responder(case, holder):
    def responder(wf, n):
        if n in case.errors:
            raise case.errors[n]
        if n in case.hooks:
            case.hooks[n](wf)
        if fc.nodes_of(wf, "SAM3_Detect"):
            case.detect_workflows.append(wf)
            if case.detect_empty:
                return {"detect_save": []}
            return {"detect_save": [spike_png(case.detect_frame)]}
        ti = fc.track_info(wf)
        names = holder["fake"].uploaded_names(ti["directory"])          # upload order = ascending frames
        frames = [case.first + int(nm[2:7]) for nm in names]
        if ti["reverse"]:
            frames.reverse()                                            # a backward chunk comes back newest-first
        out = {}
        for j in range(ti["n_save"]):
            out["save%d" % j] = [spike_png(f) if j == 0 else BLANK for f in frames]
        return out
    return responder


def new_fake(case):
    holder = {}
    fake = fc.FakeComfy(make_responder(case, holder)).start()
    holder["fake"] = fake
    s.api.api_client.base_url = fake.url
    return fake


# ------------------------------------------------------------------------------------------------ Nuke fixtures
def plate_format():
    return nuke.addFormat("%d %d 1 t13_2k" % (W, H))


def fresh(prompt="person", mode="From key frame", key=1120, chunk=60, max_objects=4, first=None, last=None):
    """Clear the script, paste the Group (File > Import equivalent), connect a Constant plate as Source."""
    nuke.scriptClear()
    s._RUNNING.clear()
    root = nuke.root()
    root["first_frame"].setValue(F0)
    root["last_frame"].setValue(F1)
    root["fps"].setValue(24)
    root["format"].setValue(plate_format())
    nuke.nodePaste(NK)
    g = nuke.toNode("SAM3Matte")
    c = nuke.nodes.Constant(channels="rgb", name="SH050")
    c["color"].setValue([0.2, 0.4, 0.6, 1])
    c["format"].setValue(plate_format())
    g.setInput(0, c)
    g["prompt"].setValue(prompt)
    g["mode"].setValue(mode)
    if first is not None:                      # a short range; otherwise Source range (project range, the Constant has none)
        g["source_range"].setValue(False)
        g["first"].setValue(first)
        g["last"].setValue(last)
    g["key_current"].setValue(False)
    g["key_frame"].setValue(key)
    g["chunk_size"].setValue(chunk)
    g["max_objects"].setValue(max_objects)
    pump.drain()
    del messages[:]
    return g


def inside_box(frame, half_w=30, half_h=10):
    """[x, y, r, t] (Nuke pixels, y up) of a small box fully inside the spike mask of `frame`, and its centre."""
    w, h, data = png.read_mask("%s/obj0_%05d_.png" % (SPIKE, frame - 1000), 0)
    best = (0, 0, 0)
    for y in range(h):
        for m in re.finditer(rb"[\xf0-\xff]+", data[y * w:(y + 1) * w]):
            if m.end() - m.start() > best[0]:
                best = (m.end() - m.start(), (m.start() + m.end()) // 2, y)
    run, cx, cy = best
    hw = min(half_w, run // 3)
    hh = half_h
    while hh > 0 and not all(min(data[(cy + dy) * w + cx - hw:(cy + dy) * w + cx + hw]) >= 0xf0
                             for dy in range(-hh, hh + 1) if 0 <= cy + dy < h):
        hh -= 1
    nx, ny = cx + 0.5, H - (cy + 0.5)
    return [nx - hw, ny - hh, nx + hw, ny + hh], (nx, ny)


def select_node(box):
    c = nuke.nodes.Constant(channels="rgba", name="SelectBase")
    c["color"].setValue([0, 0, 0, 0])
    c["format"].setValue(plate_format())
    r = nuke.nodes.Rectangle(inputs=[c], name="SelectBox")
    r["area"].setValue(box)
    r["color"].setValue([1, 1, 1, 1])
    return r


def sample(node, chan, x, y, frame):
    return node.sample(chan, x, y, 1, 1, frame)


def versions_of(g):
    return s._versions(g)


def status(g):
    return g["status"].value()


def mask_files(folder):
    return sorted(f for f in os.listdir(folder) if re.match(r"^obj\d\.\d+\.png$", f)) if os.path.isdir(folder) else []


def frame_fidelity(folder, frames, slot=0):
    """Frames whose stored V/obj<slot>.<frame>.png is not byte-identical to the spike mask of that frame. The fake serves the
    spike bytes unchanged and the runner moves the downloaded file into place, so equality means every frame landed on the
    right file (a shifted chunk, or a forward chunk stored reversed, shows up here)."""
    bad = []
    for f in frames:
        path = "%s/obj%d.%04d.png" % (folder, slot, f)
        try:
            with open(path, "rb") as fh:
                same = fh.read() == spike_png(f)
        except OSError:
            same = False
        if not same:
            bad.append(f)
    return bad


def reads_ready(g):
    pump.drain()
    return g["has_result"].value()


def main():
    # ============================================================================================ key_mode_end_to_end
    info("Nuke colour management in this headless script: %s" % nuke.root()["colorManagement"].value())
    box, centre = inside_box(1120)
    g = fresh()
    g.setInput(1, select_node(box))
    case = Case(detect_frame=1120)
    fake = new_fake(case)
    gate = threading.Event()
    entered = threading.Event()

    def hold_first_chunk(wf):                     # runs in the server thread: Generate has returned, the worker is mid-run
        entered.set()
        gate.wait(60)
    case.hooks[1] = hold_first_chunk

    ui = {}

    def watch_ui(entry):
        """Right after generate returned: the worker is running (blocked in the fake at chunk 1); the main thread is free."""
        ui["alive_at_return"] = entry["thread"].is_alive()
        ok = wait_until(entered.is_set, 60, "worker reached chunk 1")
        pump.drain()
        t0 = time.time()
        try:
            nuke.frame(1150)
            ui["frame"] = nuke.frame()
            ui["node"] = nuke.toNode("SAM3Matte") is not None
            ui["knob"] = g["mode"].value()
            ui["render"] = sample(g, "rgba.red", 10, 10, 1150)
            ui["secs"] = time.time() - t0
        except Exception as e:
            ui["err"] = repr(e)
        pump.drain()
        ui["status"] = status(g)
        ui["alive_mid"] = entry["thread"].is_alive()
        gate.set()

    nuke.frame(1120)
    ok, secs = run_generate(g, hook=watch_ui)
    check("ui_stays_responsive", ok is True and ui.get("alive_at_return") and ui.get("alive_mid") and ui.get("frame") == 1150
          and ui.get("node") and "err" not in ui and abs(ui.get("render", 0) - 0.2) < 1e-3 and "tracking" in ui.get("status", ""),
          "generate returned %s with the worker alive; while it ran: frame %s, toNode ok %s, render %.3f in %.2fs, "
          "status %r, error %s" % (ok, ui.get("frame"), ui.get("node"), ui.get("render", -1), ui.get("secs", -1),
                                   ui.get("status"), ui.get("err")))
    nuke.frame(1120)

    vs = versions_of(g)
    v = vs[0] if vs else {}
    folder = v.get("folder", "")
    files = mask_files(folder)
    expect = ["obj0.%04d.png" % f for f in range(F0, F1 + 1)]
    missing = sorted(set(expect) - set(files))
    # Key 1120, chunk 60: the last backward chunk is [1002, 1001]. The player is not on frame 1002 (it enters at 1012), so that
    # chunk has no seed and the runner skips it on purpose ("Chunk 5/5 skipped"): frame 1001 gets no file and the Read shows black.
    expect_missing = ["obj0.1001.png"]
    info("key mode finished in %.1fs; prompts %d (1 detect + %d tracks), uploads %d; status: %s" % (
        secs, len(fake.prompts), len(fake.prompts) - 1, len(fake.uploads), status(g)))
    check("key_generate_one_version", ok is True and len(vs) == 1 and not v.get("partial") and v.get("error") is None
          and v.get("mode") == "key" and v.get("key") == 1120 and (v["first"], v["last"]) == (F0, F1)
          and [o["name"] for o in v["objects"]] == ["select"] and g["version"].getValue() == 0
          and g["version"].value().startswith("v1  person  key 1120  1 object")
          and v["completed_chunks"] == list(range(v["n_chunks"])) and v["n_chunks"] == 5,
          "versions %d, label %r, objects %s, chunks %s/%s" % (len(vs), g["version"].value(), v.get("objects"),
                                                                v.get("completed_chunks"), v.get("n_chunks")))
    check("key_masks_exist_1002_1200", missing == expect_missing and set(files) == set(expect) - set(expect_missing)
          and "Chunk 5/5 skipped" in status(g),
          "%d obj0 files, missing %s (by design: the last backward chunk [1002, 1001] has no object to seed it)" % (
              len(files), missing[:5]))
    # fwd chunks: 1120-1179, 1179-1200; bwd chunks: 1120-1061, 1061-1002. Both sides of every boundary, the mask's first
    # frames (1011 blank / 1012 first pixels), 1041/1042 (the spike changes there), and the ends.
    key_frames = [1002, 1011, 1012, 1041, 1042, 1060, 1061, 1062, 1100, 1119, 1120, 1121, 1150, 1178, 1179, 1180, 1200]
    bad = frame_fidelity(folder, key_frames)
    check("key_frames_match_spike", not bad and v["objects"][0]["first"] == 1012 and v["objects"][0]["last"] == 1200,
          "%d frames compared byte for byte with the spike mask of the same frame, mismatches %s; objects[0] %d-%d" % (
              len(key_frames), bad, v["objects"][0]["first"], v["objects"][0]["last"]))
    check("key_no_error_message", messages == [], "messages %s" % messages)
    check("key_status_done", status(g).startswith("Done") and "uploaded files remain in ComfyUI input/nukecomfy" in status(g)
          and "%d uploaded files" % len(fake.uploads) in status(g) and not os.path.exists(folder + "/_plate"),
          "status %r; fake saw %d uploads" % (status(g), len(fake.uploads)))
    dw = [wf for wf in case.detect_workflows]
    pts = []
    if dw:
        for n in dw[0].values():
            if n["class_type"] == "PrimitiveStringMultiline":
                pts.append(json.loads(n["inputs"]["value"]))
    pos = pts[0] if pts else []
    check("select_points_reached_detect", len(dw) == 1 and pos and all(
        box[0] - 40 <= p["x"] <= box[2] + 40 and (H - box[3]) - 40 <= p["y"] <= (H - box[1]) + 40 for p in pos),
          "Detect saw positive points %s for the Select box %s (top-left pixels)" % (pos, box))

    # the Group: alpha, sam.m*, the Cryptomatte layer
    cx, cy = centre
    px, py = 5.5, 5.5                                # plate corner, never part of the player
    a_in = sample(g, "rgba.alpha", cx, cy, 1100)
    a_out = sample(g, "rgba.alpha", px, py, 1100)
    a_pre = sample(g, "rgba.alpha", cx, cy, 1005)    # frames before the player enters hold empty masks
    a_gone = sample(g, "rgba.alpha", cx, cy, 1001)   # no mask file at all on 1001: the Read must give black, not an error
    check("group_alpha", a_in > 0.99 and a_out == 0 and a_pre == 0 and a_gone == 0,
          "alpha inside the player at 1100 %.3f (centre of the 1120 box %.0f,%.0f), corner %.3f, same pixel at 1005 %.3f, "
          "at 1001 (no file) %.3f" % (a_in, cx, cy, a_out, a_pre, a_gone))
    rgb = [sample(g, "rgba.%s" % c, cx, cy, 1100) for c in ("red", "green", "blue")]
    check("group_rgb_passthrough", all(abs(a - b) < 1e-3 for a, b in zip(rgb, (0.2, 0.4, 0.6))), "rgb %s" % (rgb,))
    m0 = sample(g, "sam.m0", cx, cy, 1100)
    m1 = sample(g, "sam.m1", cx, cy, 1100)
    check("group_sam_channels", m0 > 0.99 and m1 == 0 and "sam.m0" in g.channels() and "CryptoSAM00.red" in g.channels(),
          "sam.m0 %.3f, sam.m1 %.3f at the same pixel" % (m0, m1))
    idv = sample(g, "CryptoSAM00.red", cx, cy, 1100)
    want = crypto.name_to_id("select")[1]
    cm = nuke.createNode("Cryptomatte", inpanel=False)
    cm.setInput(0, g)
    cm["cryptoLayer"].setValue("CryptoSAM")
    cm["matteList"].setValue("select")
    p_sel = sample(cm, "rgba.alpha", cx, cy, 1100)
    p_sel_out = sample(cm, "rgba.alpha", px, py, 1100)
    cm["matteList"].setValue("nobody")
    p_none = sample(cm, "rgba.alpha", cx, cy, 1100)
    check("cryptomatte_native_pick", abs(idv - want) <= 1e-9 * abs(want) and p_sel > 0.99 and p_sel_out == 0 and p_none == 0,
          "CryptoSAM00.red %r (expected %r); native Cryptomatte matteList 'select': inside %.3f, corner %.3f; 'nobody': %.3f" % (
              idv, want, p_sel, p_sel_out, p_none))
    nuke.delete(cm)

    # Make Roto on the result (step 10 keeps full-size masks quick; the roto test file covers step 1 and the geometry)
    g["roto_step"].setValue(10)
    t0 = time.time()
    roto = roto_mod.make_roto(g)
    dt = time.time() - t0
    shapes = [sh for sh in roto["curves"].rootLayer] if roto is not None else []
    ra_in = sample(roto, "rgba.alpha", cx, cy, 1100) if roto is not None else -1
    ra_out = sample(roto, "rgba.alpha", px, py, 1100) if roto is not None else -1
    ra_pre = sample(roto, "rgba.alpha", cx, cy, 1005) if roto is not None else -1
    check("make_roto_on_result", roto is not None and [sh.name for sh in shapes] == ["select"] and len(shapes[0]) == 32
          and status(g).startswith("Roto: 1 shapes, 32 points") and ra_in > 0.9 and ra_out == 0 and ra_pre == 0,
          "%.1fs; shapes %s; status %r; Roto alpha inside %.3f, corner %.3f, empty frame 1005 %.3f" % (
              dt, [sh.name for sh in shapes], status(g), ra_in, ra_out, ra_pre))

    # ============================================================================================ range_mode_text
    g = fresh(mode="Whole range", prompt="person")
    case = Case()
    fake = new_fake(case)
    ok, secs = run_generate(g)
    vs = versions_of(g)
    v = vs[0] if vs else {}
    files = mask_files(v.get("folder", ""))
    detect_seen = any(fc.nodes_of(wf, "SAM3_Detect") for wf in fake.prompts)
    info("range mode finished in %.1fs; prompts %d, uploads %d; status: %s" % (secs, len(fake.prompts), len(fake.uploads), status(g)))
    check("range_mode_text", ok is True and len(vs) == 1 and v.get("mode") == "range" and v.get("key") is None
          and v["objects"] and v["objects"][0]["first"] == 1012 and v["objects"][0]["last"] == F1
          and v["objects"][0]["name"] == "person_1" and not v["partial"] and v["n_chunks"] == 4
          and g["version"].value().startswith("v1  person  range 1001-1200  1 object") and not detect_seen
          and files == ["obj0.%04d.png" % f for f in range(F0, F1 + 1)] and status(g).startswith("Done"),
          "objects %s, chunks %s, label %r, %d files, detect prompt %s" % (v.get("objects"), v.get("n_chunks"),
                                                                           g["version"].value(), len(files), detect_seen))
    # chunks: 1001-1060, 1060-1119, 1120-1178 (1119 is the previous chunk's last), 1179-1200
    range_frames = [1001, 1011, 1012, 1041, 1042, 1059, 1060, 1061, 1100, 1118, 1119, 1120, 1121, 1178, 1179, 1180, 1200]
    bad = frame_fidelity(v.get("folder", ""), range_frames)
    check("range_frames_match_spike", not bad, "%d frames compared with the spike masks, mismatches %s" % (len(range_frames), bad))
    check("range_no_error_message", messages == [], "messages %s" % messages)
    a_in = sample(g, "rgba.alpha", cx, cy, 1100)
    check("range_mode_alpha", a_in > 0.99, "alpha at the player's pixel at 1100: %.3f" % a_in)

    # ============================================================================================ nothing_found
    g = fresh(prompt="unicorn", key=1010, first=1001, last=1020)
    case = Case(detect_empty=True, detect_frame=1010)
    fake = new_fake(case)
    ok, secs = run_generate(g, timeout=120)
    jobs_left = [d for d in os.listdir(JOBS) if os.path.isdir(JOBS + "/" + d) and d.startswith("SAM3Matte_") and
                 os.path.exists(JOBS + "/" + d + "/_plate")]
    check("nothing_found", ok is True and status(g).startswith('Nothing found for "unicorn"') and g["versions_data"].value() == "[]"
          and not g["has_result"].value() and len(fake.prompts) == 1 and not s._RUNNING and not jobs_left,
          "status %r, versions_data %r, prompts %d, plate scratch left %s" % (status(g), g["versions_data"].value(),
                                                                                len(fake.prompts), jobs_left))

    # ============================================================================================ cancel_midway (+ resume)
    g = fresh()
    case = Case(detect_frame=1120)
    fake = new_fake(case)
    reached = threading.Event()

    def hold_second_chunk(wf):                    # chunk 2 stays "running" on the server until released
        fake.hold = "running"
        reached.set()
    case.hooks[2] = hold_second_chunk

    def cancel_when_reached(entry):
        wait_until(reached.is_set, 60, "second chunk queued")
        wait_until(lambda: len(fake.queue_running) == 1, 10, "second chunk shown as running")
        s.cancel(g)
        ui["cancel_status"] = status(g)

    ok, secs = run_generate(g, hook=cancel_when_reached)
    fake.hold = None
    vs = versions_of(g)
    v = vs[0] if vs else {}
    pid = "fake-0002"
    check("cancel_midway_partial", ok is True and len(vs) == 1 and v.get("partial") and v.get("cancelled")
          and v["completed_chunks"] == [0] and v["n_chunks"] == 5 and "Cancelled" in status(g)
          and g["version"].value().endswith("partial") and ui.get("cancel_status") == "Cancelling..."
          and pid in fake.deleted and {"prompt_id": pid} in fake.interrupt_bodies and not s._RUNNING,
          "completed %s/%s, status %r, label %r, queue delete %s, interrupt %s" % (
              v.get("completed_chunks"), v.get("n_chunks"), status(g), g["version"].value(), fake.deleted,
              fake.interrupt_bodies))
    first_folder = v.get("folder")
    partial_files = len(mask_files(first_folder))
    n_detect_before = sum(1 for wf in fake.prompts if fc.nodes_of(wf, "SAM3_Detect"))
    fake.release()
    case.hooks.clear()
    ok2, secs2 = run_generate(g)
    vs2 = versions_of(g)
    v2 = vs2[0] if vs2 else {}
    n_detect_after = sum(1 for wf in fake.prompts if fc.nodes_of(wf, "SAM3_Detect"))
    files2 = mask_files(first_folder)
    check("cancel_then_resume_completes", ok2 is True and len(vs2) == 1 and not v2.get("partial") and not v2.get("cancelled")
          and v2.get("folder") == first_folder and v2["completed_chunks"] == [0, 1, 2, 3, 4] and v2["objects"]
          and n_detect_before == 1 and n_detect_after == 1 and len(files2) > partial_files
          and not g["version"].value().endswith("partial") and status(g).startswith("Done"),
          "versions %d, partial %s, chunks %s, detect prompts %d -> %d, mask files %d -> %d, status %r" % (
              len(vs2), v2.get("partial"), v2.get("completed_chunks"), n_detect_before, n_detect_after, partial_files,
              len(files2), status(g)))
    a_res = sample(g, "rgba.alpha", cx, cy, 1100)
    bad = frame_fidelity(first_folder, [1002, 1060, 1061, 1119, 1120, 1121, 1179, 1180, 1200])
    check("resumed_frames_match_spike", not bad, "mismatches after cancel + resume: %s" % bad)
    check("resumed_version_shown", a_res > 0.99, "alpha at the player's pixel at 1100 after the resume: %.3f" % a_res)

    # ============================================================================================ server_error_midway
    g = fresh()
    case = Case(detect_frame=1120)
    case.errors[3] = fc.FakeError("SAM3_VideoTrack", "CUDA out of memory", node_id="9", exception_type="OutOfMemoryError")
    fake = new_fake(case)
    ok, secs = run_generate(g)
    vs = versions_of(g)
    v = vs[0] if vs else {}
    check("server_error_midway", ok is True and len(vs) == 1 and v.get("partial") and not v.get("cancelled")
          and v["completed_chunks"] == [0, 1] and status(g).startswith("Error:") and "out of memory" in status(g)
          and g["version"].value().endswith("partial") and "Partial version kept" in status(g) and not s._RUNNING,
          "completed %s/%s, status %r, label %r" % (v.get("completed_chunks"), v.get("n_chunks"), status(g), g["version"].value()))

    print("\n%s" % ("ALL PASSED" if not failed else "FAILED: " + ", ".join(failed)))
    sys.stdout.flush()
    sys.exit(1 if failed else 0)


main()
