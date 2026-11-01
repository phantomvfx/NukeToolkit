"""
SAM3Matte live graph checks (Task 8). Needs ComfyUI running (COMFY_URL, default http://127.0.0.1:8188), the
sam3.1_multiplex_fp16 checkpoint, and the spike plate/masks. Not a pytest file: run it as a script.

    python tools/comfy/tests/live/check_sam3matte_graph.py [check_name ...]
    python tools/comfy/tests/live/check_sam3matte_graph.py --selftest   (offline: the 1536 px comparison helper)

It proves the risky parts of the ComfyUI side that sam3matte_graph.py assumes, against the real server, and prints a
results table. The three settings constants in sam3matte_graph.py (UPLOAD_NESTED, WHOLE_RANGE_SEED_WITH_TEXT,
SPLIT_DETECT) are set from the outcome; this script applies each fallback in memory and prints the final values, and
they are then written into the settings block by hand.

Only stdlib + the repo's own pure modules (api.py, sam3matte_*.py). Runs ONE prompt at a time.
Scratch (rendered frames, downloads) lives on D: (never C:). Nothing is deleted from ComfyUI; uploads stay in
input/nukecomfy*.

Environment overrides (all optional):
  NUKECOMFY_T8_SCRATCH   scratch root           (default D:/ComfyUI_Cache/temp/sam3matte_t8)
  SAM3_SPIKE_PLATE       full-size plate folder (default D:/ComfyUI_Cache/temp/sam3_spike/plate, plate_1001..1200.png)
  SAM3_SPIKE_OUT         spike masks root       (default L:/ComfyUI/output/sam3_spike; run1/obj0_#####_.png, run2/{fwd,bwd}_#####_.png)
  NUKE_EXE               Nuke 16.1 (only used to re-render frames at another width)
Tracking-size frames: <scratch>/frames1024/f_<frame-1001:05d>.png (1024x540, rendered once by Nuke -t, see render_frames).

Tested: run against ComfyUI 127.0.0.1:8188 on 2026-11-01 (see the Task 8 report for the results).
"""
import json
import os
import shutil
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "python"))

import api  # noqa: E402  (bare module: stdlib only, no nuke)
import sam3matte_chunks as ch  # noqa: E402
import sam3matte_graph as g  # noqa: E402
import sam3matte_png as png  # noqa: E402

SCRATCH = os.environ.get("NUKECOMFY_T8_SCRATCH", "D:/ComfyUI_Cache/temp/sam3matte_t8").replace("\\", "/")
PLATE = os.environ.get("SAM3_SPIKE_PLATE", "D:/ComfyUI_Cache/temp/sam3_spike/plate").replace("\\", "/")
SPIKE = os.environ.get("SAM3_SPIKE_OUT", "L:/ComfyUI/output/sam3_spike").replace("\\", "/")
NUKE_EXE = os.environ.get("NUKE_EXE", "L:/Apps/Nuke16.1v4/Nuke16.1.exe")
URL = (os.environ.get("COMFY_URL") or "http://127.0.0.1:8188").rstrip("/")

FIRST, LAST, KEY = 1001, 1200, 1120
POS = [{"x": 962, "y": 538}, {"x": 983, "y": 744}]
NEG = [{"x": 300, "y": 200}]
TW, TH = ch.tracking_size(2048, 1080)  # 1024 x 540
STAMP = time.strftime("%m%d%H%M%S")
JOB = "t8_" + STAMP

client = api.ComfyAPIClient(URL)
_cache = {}


# ------------------------------------------------------------------ plumbing

def log(*a):
    print(*a, flush=True)


def http_json(path, data=None, timeout=30):
    req = urllib.request.Request(URL + path, data=None if data is None else json.dumps(data).encode("utf-8"),
                                 headers={"Content-Type": "application/json"} if data is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"raw": body}


def run_prompt(wf, label, timeout=1200):
    """Queue one prompt, wait for it. Returns dict(ok, error, history, seconds)."""
    t0 = time.time()
    code, body = http_json("/prompt", {"prompt": wf})
    if code != 200:
        err = json.dumps(body)[:1500]
        log("  [%s] rejected (HTTP %s): %s" % (label, code, err))
        return {"ok": False, "error": "HTTP %s %s" % (code, err), "history": None, "seconds": time.time() - t0,
                "rejected": True}
    pid = body["prompt_id"]
    while True:
        if time.time() - t0 > timeout:
            return {"ok": False, "error": "timeout", "history": None, "seconds": time.time() - t0}
        time.sleep(2)
        c, hist = http_json("/history/" + pid)
        entry = hist.get(pid) if c == 200 else None
        if entry and entry.get("status", {}).get("completed") is not None:
            st = entry["status"]
            ok = st.get("status_str") == "success"
            err = None
            if not ok:
                msgs = [m for m in st.get("messages", []) if m and m[0] == "execution_error"]
                err = json.dumps(msgs[-1][1])[:1500] if msgs else json.dumps(st)[:1500]
            secs = time.time() - t0
            log("  [%s] %s in %.0fs" % (label, "ok" if ok else "ERROR " + str(err)[:300], secs))
            return {"ok": ok, "error": err, "history": entry, "seconds": secs, "prompt_id": pid}


def node_images(res, node_id):
    """History image list of one SaveImage node (empty list when absent)."""
    if not res or not res.get("history"):
        return []
    return res["history"].get("outputs", {}).get(node_id, {}).get("images", [])


def download(images, dest_dir):
    """Download a node's images in order; returns local paths (position = frame/object index)."""
    os.makedirs(dest_dir, exist_ok=True)
    paths = []
    for i, im in enumerate(images):
        q = urllib.parse.urlencode({"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                    "type": im.get("type", "output")})
        out = os.path.join(dest_dir, "%05d.png" % i)
        with urllib.request.urlopen(URL + "/view?" + q, timeout=60) as r, open(out, "wb") as f:
            f.write(r.read())
        paths.append(out)
    return paths


def upload(path, subfolder):
    v = client.upload_image(path, subfolder)
    if not v:
        raise RuntimeError("upload failed: %s -> %s" % (path, subfolder))
    return v


def frame_png(frame, folder=None):
    return "%s/f_%05d.png" % (folder or (SCRATCH + "/frames1024"), frame - FIRST)


def upload_dir(tag, a, b, folder=None):
    """Upload frames a..b (ascending) of the tracking-size set; returns (VHS directory value, seconds)."""
    key = (tag, a, b, g.UPLOAD_NESTED, folder)
    if key in _cache:
        return _cache[key]
    sub = ("nukecomfy/%s_%s" % (JOB, tag)) if g.UPLOAD_NESTED else ("nukecomfy_%s_%s" % (JOB, tag))
    t0 = time.time()
    for f in range(a, b + 1):
        upload(frame_png(f, folder), sub)
    _cache[key] = (sub, time.time() - t0)
    return _cache[key]


def key_upload(name="key"):
    """Full-size key frame 1120 uploaded as nukecomfy/<job>_<name>.png."""
    if ("key", name) in _cache:
        return _cache[("key", name)]
    os.makedirs(SCRATCH + "/up", exist_ok=True)
    local = "%s/up/%s_%s.png" % (SCRATCH, JOB, name)
    shutil.copyfile("%s/plate_%d.png" % (PLATE, KEY), local)
    _cache[("key", name)] = upload(local, "nukecomfy")
    return _cache[("key", name)]


def detect(label, text="", positive=(), negative=(), threshold=0.5):
    """Run build_detect on the full-size key frame; returns (res, [local mask paths])."""
    wf, outs = g.build_detect({"key_image": key_upload(), "text": text, "positive": list(positive),
                               "negative": list(negative), "threshold": threshold,
                               "prefix": "NukeComfy/%s/detect_%s/obj" % (JOB, label)})
    res = run_prompt(wf, "detect " + label)
    paths = download(node_images(res, "detect_save"), "%s/dl/%s" % (SCRATCH, label)) if res["ok"] else []
    return res, paths


def track(label, directory, seeds=(), text="", reverse=False, n_save=1, max_objects=4, tw=TW, th=TH):
    wf, outs = g.build_track({"directory": directory, "reverse": reverse, "track_w": tw, "track_h": th,
                              "seeds": list(seeds), "text": text, "max_objects": max_objects, "n_save": n_save,
                              "prefix": "NukeComfy/%s/%s/obj" % (JOB, label), "pass_name": "text" if text else "fwd"})
    res = run_prompt(wf, "track " + label)
    per_obj = {}
    if res["ok"]:
        for j in range(n_save):
            per_obj[j] = download(node_images(res, "save%d" % j), "%s/dl/%s/obj%d" % (SCRATCH, label, j))
    return res, per_obj


def nonempty(data):
    return any(v >= 128 for v in data)


def iou_stats(pairs):
    """pairs of (maskA, maskB) bytes; mean/min IoU over frames where either is non-empty."""
    vals = []
    for a, b in pairs:
        if nonempty(a) or nonempty(b):
            vals.append(ch.mask_iou(a, b))
    if not vals:
        return None, None, 0
    return sum(vals) / len(vals), min(vals), len(vals)


def render_frames(width, height, dest):
    """Render <dest>/f_<frame-1001:05d>.png at width x height with Nuke -t (Read -> Reformat distort -> raw 8-bit PNG)."""
    os.makedirs(dest, exist_ok=True)
    script = "%s/resize_%d.py" % (SCRATCH, width)
    with open(script, "w") as f:
        f.write(
            "import nuke\n"
            "r = nuke.nodes.Read(file='%s/plate_####.png', first=1001, last=1200, origfirst=1001, origlast=1200, raw=True)\n"
            "rf = nuke.nodes.Reformat(inputs=[r], type='to box', box_width=%d, box_height=%d, box_fixed=True, resize='distort')\n"
            "w = nuke.nodes.Write(inputs=[rf], file='%s/f_####.png', file_type='png', datatype='8 bit', raw=True, channels='rgb')\n"
            "nuke.execute(w, 1001, 1200)\n" % (PLATE, width, height, dest))
    subprocess.run([NUKE_EXE, "-t", script], check=True, capture_output=True)
    for n in sorted(os.listdir(dest)):  # Nuke wrote f_<frame>.png; rename to f_<frame-first:05d>.png
        if n.startswith("f_") and len(n) == 10 and int(n[2:6]) >= FIRST:
            os.rename(os.path.join(dest, n), os.path.join(dest, "f_%05d.png" % (int(n[2:6]) - FIRST)))


def spike_obj0(i):
    return "%s/run1/obj0_%05d_.png" % (SPIKE, i + 1)


def read(path, width=0):
    return png.read_mask(path, width)


def resample(m, nw, nh):
    """Nearest-neighbour resample of an (w, h, data) 8-bit mask to nw x nh (stdlib only)."""
    w, h, data = m
    if (w, h) == (nw, nh):
        return m
    cols = [min(w - 1, (x * w) // nw) for x in range(nw)]
    out = []
    last_y, last_row = None, None
    for y in range(nh):
        sy = min(h - 1, (y * h) // nh)
        if sy != last_y:
            row = data[sy * w:(sy + 1) * w]
            last_row = bytes(row[c] for c in cols)
            last_y = sy
        out.append(last_row)
    return nw, nh, b"".join(out)


def common_iou(a, b):
    """IoU of two (w, h, data) masks of possibly different sizes: the smaller one is brought up to the larger
    (nearest) so a 2048-wide spike mask and a 1536-wide result are compared at 2048, never raising on size."""
    if a[0] * a[1] < b[0] * b[1]:
        a = resample(a, b[0], b[1])
    else:
        b = resample(b, a[0], a[1])
    return ch.mask_iou(a[2], b[2])


def selftest():
    """Offline (no GPU, no server): the 1536 comparison path on a spike mask vs itself resampled to 1536x810."""
    ok = True
    for i in (0, 60, 119, 180):
        full = read(spike_obj0(i))  # 2048x1080, as the spike wrote it
        small = resample(full, 1536, 810)
        v = common_iou(full, small)
        same = common_iou(full, full)
        good = full[0] == 2048 and small[:2] == (1536, 810) and (not nonempty(full[2]) or (same == 1.0 and v >= 0.97))
        ok = ok and good
        log("selftest frame %d: %dx%d vs %dx%d non-empty=%s IoU(full, 1536 resample)=%.4f IoU(full, full)=%.4f %s" % (
            i + FIRST, full[0], full[1], small[0], small[1], nonempty(full[2]), v, same, "ok" if good else "BAD"))
    log("selftest " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# ------------------------------------------------------------------ checks
# every check returns (True|False, measurement string) and may flip the g.<CONSTANT> fallbacks in memory

def check_vhs_nested_dir():
    """VHS_LoadImages accepts directory 'nukecomfy/<job>_c0' (nested). Else try top-level 'nukecomfy_<job>_c0'."""
    notes = []
    for nested in (True, False):
        g.UPLOAD_NESTED = nested
        d, secs = upload_dir("c0_5f", KEY, KEY + 4)
        res, per = track("nested%d" % nested, d, text="person", n_save=1)
        n = len(per.get(0, []))
        ok = res["ok"] and n == 5
        notes.append("dir=%s -> %s (%d images)" % (d, "ok" if ok else "FAIL " + str(res["error"])[:200], n))
        if ok:
            return True, "; ".join(notes) + ("" if nested else "; UPLOAD_NESTED=False")
    g.UPLOAD_NESTED = False
    return False, "; ".join(notes) + " (both layouts rejected: BLOCKED)"


def check_detect_text_plus_points():
    """One Detect request with text AND points returns >= 2 images; image 0 == the points-only result (IoU >= 0.98)."""
    res_p, paths_p = detect("points", positive=POS, negative=NEG)
    if not res_p["ok"] or not paths_p:
        return False, "points-only Detect failed: %s" % res_p["error"]
    _cache["detect_points"] = paths_p[0]
    ref = read(paths_p[0])
    msg = []
    for split in (False, True):
        g.SPLIT_DETECT = split
        res, paths = detect("textpts_split%d" % split, text="person", positive=POS, negative=NEG)
        if not res["ok"]:
            msg.append("split=%s: run failed %s" % (split, str(res["error"])[:200]))
            continue
        if len(paths) < 2:
            msg.append("split=%s: only %d image(s)" % (split, len(paths)))
            continue
        w, h, d0 = read(paths[0])
        iou = ch.mask_iou(ref[2], d0) if (w, h) == (ref[0], ref[1]) else 0.0
        msg.append("split=%s: %d images, image0 vs points-only IoU %.4f" % (split, len(paths), iou))
        if iou >= 0.98:
            return True, "; ".join(msg) + ("" if not split else "; SPLIT_DETECT=True")
    return False, "; ".join(msg) + " (BLOCKED)"


def check_detect_nothing_found():
    """Record what the server does for a zero-object Detect batch (T9 must handle exactly this).

    SAM3 happily "finds" a unicorn (it returns the person), so zero-object cases are searched for: text 'ball'
    (not in the plate), 'person' at threshold 0.999, and the same with points next to it.
    """
    out = []
    zero = None
    cases = (("unicorn", dict(text="unicorn")),
             ("ball", dict(text="ball")),
             ("person@0.999", dict(text="person", threshold=0.999)),
             ("ball+points", dict(text="ball", positive=POS, negative=NEG)))
    for label, kw in cases:
        res, paths = detect("nothing_" + label.replace("+", "_").replace("@", "_"), **kw)
        h = res["history"]
        outputs = (h or {}).get("outputs", {})
        entry = outputs.get("detect_save")
        shown = "MISSING" if entry is None else json.dumps({"images": [i["filename"] for i in entry.get("images", [])]})
        out.append("%s: ok=%s error=%s outputs_keys=%s detect_save=%s downloaded=%d status=%s" % (
            label, res["ok"], str(res["error"])[:200], sorted(outputs.keys()), shown, len(paths),
            json.dumps((h or {}).get("status", {}).get("status_str"))))
        if zero is None and ((res["ok"] and (entry is None or not entry.get("images"))) or
                             (not res["ok"] and '"node_id": "detect_save"' in str(res["error"]) and "IndexError" in str(res["error"]))):
            zero = label
    out.append("zero-object case reproduced with: %s (a zero-object batch FAILS the prompt: execution_error on node "
               "detect_save, IndexError 'index 0 is out of bounds for dimension 0 with size 0', no outputs entry)" % zero)
    return zero is not None, " | ".join(out)  # recording check: PASS when a zero-object case was reproduced


def _seed_for_tracking():
    """The points-only Detect mask on the key frame (full size) uploaded as a seed."""
    if "seed_value" not in _cache:
        if "detect_points" not in _cache:
            res, paths = detect("points", positive=POS, negative=NEG)
            if not res["ok"] or not paths:
                raise RuntimeError("seed Detect failed: %s" % res["error"])
            _cache["detect_points"] = paths[0]
        local = _cache["detect_points"]
        _cache["seed_local"] = local
        _cache["seed_value"] = _upload_named(local, "%s_seed0_0.png" % JOB)
    return _cache["seed_value"], _cache["seed_local"]


def _upload_named(local, name):
    os.makedirs(SCRATCH + "/up", exist_ok=True)
    dst = "%s/up/%s" % (SCRATCH, name)
    shutil.copyfile(local, dst)
    return upload(dst, "nukecomfy")


def check_initial_mask_plus_text():
    """Seeded Video Track + text 'person' over 1120..1179: run completes, obj 0 on the first frame ~ the seed."""
    seed, seed_local = _seed_for_tracking()
    d, _ = upload_dir("c0_60f", KEY, KEY + 59)
    res, per = track("seed_text", d, seeds=[seed], text="person", n_save=4)
    if not res["ok"]:
        g.WHOLE_RANGE_SEED_WITH_TEXT = False
        return False, "run failed: %s; WHOLE_RANGE_SEED_WITH_TEXT=False" % str(res["error"])[:300]
    sw, sh, sd = read(seed_local, TW)
    w, h, o0 = read(per[0][0])
    iou = ch.mask_iou(sd, o0) if (w, h) == (sw, sh) else 0.0
    counts = {j: len(per[j]) for j in per}
    firsts = {j: nonempty(read(per[j][0])[2]) for j in per}
    ok = iou >= 0.9 and counts[0] == 60
    if not ok:
        g.WHOLE_RANGE_SEED_WITH_TEXT = False
    return ok, "frame-0 obj0 vs seed IoU %.4f (out %dx%d, seed@%dx%d); frames/obj %s; non-empty on first frame per obj %s%s" % (
        iou, w, h, sw, sh, counts, firsts, "" if ok else "; WHOLE_RANGE_SEED_WITH_TEXT=False")


def check_seed_size():
    """Seed at full size (2048x1080), frames at 1024x540, seed-only track: runs and masks are sane."""
    seed, seed_local = _seed_for_tracking()
    fw, fh, _ = read(seed_local)
    d, _ = upload_dir("c0_5f", KEY, KEY + 4)
    res, per = track("seed_only", d, seeds=[seed], n_save=1)
    if not res["ok"]:
        return False, "run failed: %s" % str(res["error"])[:300]
    sw, sh, sd = read(seed_local, TW)
    ious = []
    sizes = set()
    for p in per[0]:
        w, h, m = read(p)
        sizes.add((w, h))
        ious.append(ch.mask_iou(sd, m) if (w, h) == (sw, sh) else 0.0)
    ok = sizes == {(TW, TH)} and len(per[0]) == 5 and ious[0] >= 0.9 and min(ious) >= 0.8
    return ok, "seed %dx%d, frames %dx%d, out sizes %s, IoU vs seed per frame %s" % (
        fw, fh, TW, TH, sorted(sizes), ["%.3f" % v for v in ious])


def check_seeded_keyframe_path():
    """Detect(full size) -> mask re-uploaded as seed -> forward 1120..1200 + reversed 1001..1120 at 1024x540 vs spike run 2."""
    seed, seed_local = _seed_for_tracking()
    dfw, _ = upload_dir("fwd", KEY, LAST)
    dbw, _ = upload_dir("bwd", FIRST, KEY)
    res_f, per_f = track("kf_fwd", dfw, seeds=[seed], n_save=1)
    if not res_f["ok"]:
        return False, "forward run failed: %s" % str(res_f["error"])[:300]
    res_b, per_b = track("kf_bwd", dbw, seeds=[seed], reverse=True, n_save=1)
    if not res_b["ok"]:
        return False, "backward run failed: %s" % str(res_b["error"])[:300]
    nf, nb = len(per_f[0]), len(per_b[0])
    if nf != LAST - KEY + 1 or nb != KEY - FIRST + 1:
        return False, "frame counts fwd %d (want %d) bwd %d (want %d)" % (nf, LAST - KEY + 1, nb, KEY - FIRST + 1)
    pairs_f, pairs_b = [], []
    for i in range(nf):  # forward image i = frame KEY + i
        pairs_f.append((read("%s/run2/fwd_%05d_.png" % (SPIKE, i + 1), TW)[2], read(per_f[0][i])[2]))
    for j in range(nb):  # reversed run: image j = frame KEY - j; frame KEY itself comes from the forward pass
        pairs_b.append((read("%s/run2/bwd_%05d_.png" % (SPIKE, j + 1), TW)[2], read(per_b[0][j])[2]))
    allp = pairs_f + pairs_b[1:]
    mean, mn, n = iou_stats(allp)
    mf, nf_, _ = iou_stats(pairs_f)
    mb, nb_, _ = iou_stats(pairs_b[1:])
    ok = mean is not None and mean >= 0.98
    return ok, "stitched %d frames: mean IoU %.4f (min %.4f, %d non-empty frames); fwd mean %.4f min %.4f; bwd mean %.4f min %.4f" % (
        len(allp), mean or 0, mn or 0, n, mf or 0, nf_ or 0, mb or 0, nb_ or 0)


def _ensure_full():
    """Upload all 200 tracking-size frames once; also the upload-speed measurement."""
    d, secs = upload_dir("full", FIRST, LAST)
    _cache["upload_secs"] = secs
    return d


def check_tracking_width_fidelity():
    """Whole-range text 'person' over 200 frames at 1024x540 vs spike run 1 obj0 (2048 wide, shrunk to 1024)."""
    d = _ensure_full()
    res, per = track("fidelity1024", d, text="person", n_save=4, max_objects=8)
    if not res["ok"]:
        return False, "run failed: %s" % str(res["error"])[:300]
    n = len(per[0])
    pairs = [(read(spike_obj0(i), TW)[2], read(per[0][i])[2]) for i in range(min(n, LAST - FIRST + 1))]
    mean, mn, cnt = iou_stats(pairs)
    objs = {j: sum(1 for p in per[j] if not png.is_blank(p)) for j in per}
    ok = n == 200 and mean is not None and mean >= 0.98
    msg = "obj0 mean IoU %.4f (min %.4f over %d non-empty frames, %d images); non-blank frames per slot %s" % (
        mean or 0, mn or 0, cnt, n, objs)
    if not ok:
        w15, h15 = ch.tracking_size(2048, 1080, 1536)
        dest = SCRATCH + "/frames1536"
        if not os.path.exists(dest + "/f_00199.png"):
            render_frames(w15, h15, dest)
        sub = "nukecomfy/%s_full1536" % JOB if g.UPLOAD_NESTED else "nukecomfy_%s_full1536" % JOB
        for f in range(FIRST, LAST + 1):
            upload(frame_png(f, dest), sub)
        res2, per2 = track("fidelity1536", sub, text="person", n_save=1, max_objects=8, tw=w15, th=h15)
        if res2["ok"]:
            vals = [(read(spike_obj0(i)), read(per2[0][i])) for i in range(len(per2[0]))]  # 2048 wide vs 1536 wide
            ious2 = [common_iou(a, b) for a, b in vals if nonempty(a[2]) or nonempty(b[2])]
            m2, mn2, c2 = (sum(ious2) / len(ious2), min(ious2), len(ious2)) if ious2 else (None, None, 0)
            msg += "; at %dx%d: mean IoU %.4f (min %.4f, %d frames)" % (w15, h15, m2 or 0, mn2 or 0, c2)
        else:
            msg += "; 1536 run failed: %s" % str(res2["error"])[:200]
    return ok, msg


def check_subject_order():
    """Can the batch order tell which subject an object came from? Report only."""
    out = []
    for text in ("person, ball", "ball, person", "person, sky", "sky, person", "person:2, sky"):
        res, paths = detect("order_" + text.replace(", ", "_").replace(":", "x").replace(" ", ""), text=text)
        if not res["ok"]:
            out.append("%r: failed %s" % (text, str(res["error"])[:150]))
            continue
        sizes = []
        for p in paths:
            w, h, m = read(p, TW)
            sizes.append(sum(1 for v in m if v >= 128))
        out.append("%r -> %d images, mask px (1024 grid) %s" % (text, len(paths), sizes))
    return True, " | ".join(out)


def check_upload_speed():
    """Seconds per frame to upload 1024x540 PNGs (local server)."""
    if "upload_secs" not in _cache:
        _ensure_full()
    n = LAST - FIRST + 1
    mb = sum(os.path.getsize(frame_png(f)) for f in range(FIRST, LAST + 1)) / 1e6
    s = _cache["upload_secs"]
    return True, "%d frames, %.1f MB in %.1f s = %.3f s/frame (%.1f MB/s) -- LOCAL server, loopback" % (n, mb, s, s / n, mb / s)


CHECKS = [check_vhs_nested_dir, check_detect_text_plus_points, check_detect_nothing_found,
          check_initial_mask_plus_text, check_seed_size, check_seeded_keyframe_path,
          check_tracking_width_fidelity, check_subject_order, check_upload_speed]


def main(argv):
    if "--selftest" in argv:
        return selftest()
    code, stats = http_json("/system_stats")
    if code != 200:
        log("ComfyUI not reachable at %s" % URL)
        return 2
    if not os.path.exists(frame_png(FIRST)):
        log("rendering 1024x540 frames with Nuke ...")
        render_frames(TW, TH, SCRATCH + "/frames1024")
    wanted = set(argv)
    rows = []
    for fn in CHECKS:
        if wanted and fn.__name__ not in wanted:
            continue
        log("\n== %s" % fn.__name__)
        try:
            ok, msg = fn()
        except Exception:
            ok, msg = False, "EXCEPTION " + traceback.format_exc().strip().splitlines()[-1]
            traceback.print_exc()
        rows.append((fn.__name__, "PASS" if ok else "FAIL", msg))
        log("   %s: %s" % ("PASS" if ok else "FAIL", msg))
    log("\n" + "=" * 100 + "\nJOB %s\n" % JOB)
    for name, status, msg in rows:
        log("%-34s %-5s %s" % (name, status, msg))
    log("\nFinal constants: UPLOAD_NESTED=%s WHOLE_RANGE_SEED_WITH_TEXT=%s SPLIT_DETECT=%s" % (
        g.UPLOAD_NESTED, g.WHOLE_RANGE_SEED_WITH_TEXT, g.SPLIT_DETECT))
    return 0 if all(s == "PASS" for _, s, _ in rows) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
