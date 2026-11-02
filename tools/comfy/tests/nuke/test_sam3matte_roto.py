"""SAM3Matte Make Roto, headless in Nuke 16.1. Pastes gizmos/SAM3Matte.nk (build it first with build/build_sam3matte.py).

    & "L:\\Apps\\Nuke16.1v4\\Nuke16.1.exe" -t L:\\Nuke\\tools\\comfy\\tests\\nuke\\test_sam3matte_roto.py

Prints PASS name / FAIL name for each check and exits 1 if any failed. Scratch files go to D:/ComfyUI_Cache/temp/sam3matte_t12.
The fixture is a fake version made from the spike masks (obj0 has pixels; obj1 is obj0 moved 600 px by a Write).
Masks are 2048x1080 (spike); the speed check uses a half-size copy (1024x540), like real tracking-size masks.
"""
import os, sys, runpy
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")).replace("\\", "/")
runpy.run_path(ROOT + "/init.py")   # path setup + the NukeComfy alias

import json
import re
import shutil
import time

import nuke

sys.path.insert(0, ROOT + "/tools/comfy/python")
import sam3matte_png as png
import sam3matte_roto_core as core
from NukeComfy.python import sam3matte_roto as roto_mod

NK = ROOT + "/tools/comfy/gizmos/SAM3Matte.nk"
SPIKE = "L:/ComfyUI/output/sam3_spike/run1"
SCRATCH = "D:/ComfyUI_Cache/temp/sam3matte_t12"
VER = SCRATCH + "/v1"
HALF = SCRATCH + "/v_half"
F0, F1, FK = 1001, 1200, 1100
FIRST, LAST = 1012, 1200
W, H = 2048, 1080

failed = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))
    if not ok:
        failed.append(name)


# ---------------------------------------------------------------- fixtures
def make_fixture():
    os.makedirs(VER, exist_ok=True)
    for i in range(1, 201):
        dst = "%s/obj0.%04d.png" % (VER, F0 + i - 1)
        if not os.path.exists(dst):
            shutil.copyfile("%s/obj0_%05d_.png" % (SPIKE, i), dst)
    if not os.path.exists("%s/obj1.%04d.png" % (VER, F1)):
        nuke.scriptClear()
        r = nuke.nodes.Read(file=VER + "/obj0.####.png", first=F0, last=F1, origfirst=F0, origlast=F1, raw=True)
        t = nuke.nodes.Transform(inputs=[r])
        t["translate"].setValue([600, 0])
        w = nuke.nodes.Write(inputs=[t], file=VER + "/obj1.####.png", file_type="png", raw=True, channels="rgb")
        w["datatype"].setValue("8 bit")
        nuke.execute(w, F0, F1)
    os.makedirs(HALF, exist_ok=True)
    if not os.path.exists("%s/obj0.%04d.png" % (HALF, F1)):
        nuke.scriptClear()
        nuke.root()["format"].setValue(nuke.addFormat("%d %d 1 sam2k" % (W, H)))
        r = nuke.nodes.Read(file=VER + "/obj0.####.png", first=F0, last=F1, origfirst=F0, origlast=F1, raw=True)
        rf = nuke.nodes.Reformat(inputs=[r], type="to format", resize="distort")
        rf["format"].setValue(nuke.addFormat("%d %d 1 sam_half" % (W // 2, H // 2)))
        w = nuke.nodes.Write(inputs=[rf], file=HALF + "/obj0.####.png", file_type="png", raw=True, channels="rgb")
        w["datatype"].setValue("8 bit")
        nuke.execute(w, F0, F1)


def fresh():
    """Clear the script, paste the Group, add a plate. Returns the Group."""
    nuke.scriptClear()
    nuke.root()["first_frame"].setValue(F0)
    nuke.root()["last_frame"].setValue(F1)
    nuke.root()["format"].setValue(nuke.addFormat("%d %d 1 sam2k" % (W, H)))
    nuke.nodePaste(NK)
    g = nuke.toNode("SAM3Matte")
    c = nuke.nodes.Constant(channels="rgb")
    c["format"].setValue(nuke.addFormat("%d %d 1 sam2k" % (W, H)))
    g.setInput(0, c)
    return g


def objects_list(n=1, first=FIRST, last=LAST):
    return [{"index": k, "name": "obj%d" % k, "first": first, "last": last} for k in range(n)]


def set_version(g, folder, objs, w=W, h=H):
    v = {"folder": folder, "first": F0, "last": F1, "key": FK, "mode": "key", "text": "person", "width": w, "height": h,
         "objects": objs, "names": {}, "next_free": len(objs), "completed_chunks": [], "n_chunks": 1, "partial": False,
         "cancelled": False, "error": None, "dropped": 0, "hints": {}, "message": "", "created": "2026-11-01 12:00:00",
         "params": {}}
    g["versions_data"].setValue(json.dumps([v]))
    g["version"].setValues(["v1  person  key %d  %d objects" % (FK, len(objs))])
    g["version"].setValue(0)
    g["has_result"].setValue(True)
    return v


# ---------------------------------------------------------------- measurements
def shapes_of(r):
    return [s for s in r["curves"].rootLayer]


def centroid_of_shape(shape, frame, sub=16):
    """Area centroid of the shape's evaluated Bezier outline (sub samples per segment) -> (x, y, signed area, y up)."""
    cv = shape.evaluate(0, float(frame))
    n = len(shape) * sub
    pts = []
    for k in range(n):
        p = cv.getPoint(k / float(n))
        pts.append((p.x, p.y))
    a = cx = cy = 0.0
    for i in range(len(pts)):
        x0, y0 = pts[i]
        x1, y1 = pts[(i + 1) % len(pts)]
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    a /= 2.0
    return cx / (6 * a), cy / (6 * a), a


def mask_centroid_nuke(path, thr=128):
    """Centroid of ALL pixels >= thr (every region), in Nuke plate coordinates."""
    w, h, data = png.read_mask(path, 0)
    n = sx = sy = 0
    for y in range(h):
        row = data[y * w:(y + 1) * w]
        for m in re.finditer(rb"[\x80-\xff]+", row):
            a, b = m.start(), m.end()
            cnt = b - a
            n += cnt
            sx += (a + b - 1) / 2.0 * cnt + 0.5 * cnt
            sy += (y + 0.5) * cnt
    return sx / n * (W / float(w)), H - sy / n * (H / float(h))


def largest_region_centroid_nuke(path):
    """Centroid of the largest 4-connected region's outline (what Make Roto traces), in Nuke plate coordinates."""
    w, h, data = png.read_mask(path, 0)
    o = core.largest_outline(data, w, h)
    a = cx = cy = 0.0
    for i in range(len(o)):
        x0, y0 = o[i]
        x1, y1 = o[(i + 1) % len(o)]
        c = x0 * y1 - x1 * y0
        a += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    a /= 2.0
    return cx / (6 * a) * (W / float(w)), H - cy / (6 * a) * (H / float(h))


def inside_point_nuke(path):
    """A pixel centre deep inside the largest region (middle of its longest horizontal run), in Nuke plate coordinates."""
    w, h, data = png.read_mask(path, 0)
    o = core.largest_outline(data, w, h)
    y0, y1 = int(min(q[1] for q in o)), int(max(q[1] for q in o)) + 1
    x0, x1 = int(min(q[0] for q in o)), int(max(q[0] for q in o)) + 1
    best = (0, 0, 0)
    for y in range(y0, y1, 2):
        for m in re.finditer(b"[" + bytes([0xf0]) + b"-" + bytes([0xff]) + b"]+", data[y * w + x0:y * w + x1]):
            if m.end() - m.start() > best[0]:
                best = (m.end() - m.start(), x0 + (m.start() + m.end()) / 2.0, y + 0.5)
    return best[1] * (W / float(w)), H - best[2] * (H / float(h))


def key_counts(shape):
    return [len(shape[i].center.getControlPointKeyTimes()) for i in range(len(shape))]


def opacity_at(shape, frame):
    a = shape.getAttributes()
    return a.getValue(float(frame), a.kOpacityAttribute)


def run(g, **kn):
    for k, v in kn.items():
        g[k].setValue(v)
    before = set(n.name() for n in nuke.root().nodes())
    t = time.time()
    r = roto_mod.make_roto(g)
    return r, time.time() - t, set(n.name() for n in nuke.root().nodes()) - before


def main():
    make_fixture()

    # ---------------------------------------------------------- roto_no_version
    g = fresh()
    r, _, new = run(g)
    st = g["status"].value()
    check("roto_no_version", r is None and not new and "no version" in st.lower(), "returned %r, status %r" % (r, st))

    # ---------------------------------------------------------- roto_missing_folder
    g = fresh()
    missing = "D:/ComfyUI_Cache/temp/sam3matte_final/no_such_version_folder"   # never created
    set_version(g, missing, objects_list(1))
    r, _, new = run(g)
    st = g["status"].value()
    check("roto_missing_folder", r is None and not new and not nuke.allNodes("Roto") and st == "Masks folder not found: " + missing,
          "returned %r, new nodes %s, status %r" % (r, sorted(new), st))

    # ---------------------------------------------------------- roto_unnamed_format_names_include_size
    # two unnamed Source formats of different sizes must not share a format name (the second used to reuse the first)
    sizes = []
    for (fw, fh) in ((640, 360), (800, 450)):
        g = fresh()
        g.input(0)["format"].setValue(nuke.addFormat("%d %d 1" % (fw, fh)))
        set_version(g, VER, objects_list(1))
        r, _, _ = run(g, roto_step=100)
        sizes.append((r and (r.format().width(), r.format().height()), r and r.format().name()))
    check("roto_unnamed_format_names_include_size",
          sizes[0][0] == (640, 360) and sizes[1][0] == (800, 450) and sizes[0][1] == "sam3matte_640x360"
          and sizes[1][1] == "sam3matte_800x450", "roto formats %s" % (sizes,))

    # ---------------------------------------------------------- roto_node_created / shape_count / points / keys
    g = fresh()
    g.setXYpos(300, 400)
    set_version(g, VER, objects_list(1))
    r, secs, new = run(g)
    inside = [n.name() for n in g.nodes() if n.Class() == "Roto"]
    rotos = nuke.allNodes("Roto")
    near = r is not None and abs(r.xpos() - g.xpos()) <= 200 and abs(r.ypos() - g.ypos()) <= 200
    fmt_ok = r is not None and (r.format().width(), r.format().height()) == (W, H)
    check("roto_node_created", r is not None and len(rotos) == 1 and len(new) == 1 and not inside
          and "SAM3Matte" in r.name() and near and fmt_ok,
          "roots %s inside group %s name %s pos (%s,%s) vs group (%s,%s) format %s; status: %s" % (
              [n.name() for n in rotos], inside, r and r.name(), r and r.xpos(), r and r.ypos(), g.xpos(), g.ypos(),
              r and (r.format().width(), r.format().height()), g["status"].value()))
    sh = shapes_of(r)
    check("roto_shape_count_1", len(sh) == 1 and sh[0].name == "obj0", "shapes %s" % [s.name for s in sh])
    s0 = sh[0]
    kc = key_counts(s0)
    check("roto_point_count_fixed_32", len(s0) == 32 and all(c == 189 for c in kc) and
          all(len(s0[i].rightTangent.getControlPointKeyTimes()) == 189 for i in range(32)),
          "points %d, center keys per point min/max %d/%d" % (len(s0), min(kc), max(kc)))
    check("roto_keys_step1", all(c == 189 for c in kc) and s0[0].center.getControlPointKeyTimes()[0] == FIRST
          and s0[0].center.getControlPointKeyTimes()[-1] == LAST, "keys %d first %s last %s" % (
              kc[0], s0[0].center.getControlPointKeyTimes()[0], s0[0].center.getControlPointKeyTimes()[-1]))
    print("INFO status: " + g["status"].value())
    check("roto_status", re.match(r"^Roto: 1 shapes, 32 points, 189 keys in \d+\.\d s$", g["status"].value()) is not None,
          g["status"].value())

    # ---------------------------------------------------------- roto_follows_object
    cx, cy, area = centroid_of_shape(s0, FK)
    mx, my = mask_centroid_nuke("%s/obj0.%04d.png" % (VER, FK))
    d = ((cx - mx) ** 2 + (cy - my) ** 2) ** 0.5
    # other frames: the mask can have several regions or holes, so compare with the largest region (what is traced)
    worst, worst_all = 0.0, 0.0
    for f in (1012, 1050, 1100, 1150, 1200):
        a, b, _ = centroid_of_shape(s0, f)
        c, e = largest_region_centroid_nuke("%s/obj0.%04d.png" % (VER, f))
        c2, e2 = mask_centroid_nuke("%s/obj0.%04d.png" % (VER, f))
        worst = max(worst, ((a - c) ** 2 + (b - e) ** 2) ** 0.5)
        worst_all = max(worst_all, ((a - c2) ** 2 + (b - e2) ** 2) ** 0.5)
    check("roto_follows_object", d <= 6.0 and worst <= 6.0,
          "frame %d shape centroid (%.1f, %.1f) mask centroid (%.1f, %.1f) distance %.2f px; worst of 5 frames vs the largest "
          "region %.2f px (vs all mask pixels %.2f px: frames 1012 and 1050 have extra regions)" % (FK, cx, cy, mx, my, d, worst, worst_all))

    # ---------------------------------------------------------- roto_winding (clockwise in the viewer = negative shoelace in y-up)
    areas = [centroid_of_shape(s0, f)[2] for f in range(FIRST, LAST + 1, 7)]
    check("roto_winding", all(a < 0 for a in areas), "signed areas (y up) min %.0f max %.0f over %d frames" % (
        min(areas), max(areas), len(areas)))

    # ---------------------------------------------------------- roto_handles
    # expected out tangent = (next - prev) / 6 on the plate points themselves (the same affine map as the points: scale and
    # Y flip), left = -right; and the evaluated Bezier midpoints must match the analytic midpoints built from them.
    N = len(s0)
    pos = [s0[i].center.getPosition(float(FK)) for i in range(N)]
    rt = [s0[i].rightTangent.getPosition(float(FK)) for i in range(N)]
    lt = [s0[i].leftTangent.getPosition(float(FK)) for i in range(N)]
    cv = s0.evaluate(0, float(FK))
    err_t = err_l = err_c = 0.0
    for i in range(N):
        pm, pp, p0, p1 = pos[(i - 1) % N], pos[(i + 1) % N], pos[i], pos[(i + 1) % N]
        ex_, ey_ = (pp.x - pm.x) / 6.0, (pp.y - pm.y) / 6.0
        err_t = max(err_t, abs(rt[i].x - ex_), abs(rt[i].y - ey_))
        err_l = max(err_l, abs(lt[i].x + rt[i].x), abs(lt[i].y + rt[i].y))
        o0, i1 = rt[i], lt[(i + 1) % N]
        mx_ = (p0.x + 3 * (p0.x + o0.x) + 3 * (p1.x + i1.x) + p1.x) / 8.0
        my_ = (p0.y + 3 * (p0.y + o0.y) + 3 * (p1.y + i1.y) + p1.y) / 8.0
        m = cv.getPoint((i + 0.5) / N)
        err_c = max(err_c, abs(m.x - mx_), abs(m.y - my_))
    check("roto_handles", err_t < 0.05 and err_l < 1e-6 and err_c < 0.05,
          "max |right tangent - (next-prev)/6| %.4f, |left + right| %.6f, |Nuke curve midpoint - analytic| %.4f px" % (err_t, err_l, err_c))

    # ---------------------------------------------------------- roto_empty_frames_off
    o5, o1100, o1011, o1012, o1200, o1201 = (opacity_at(s0, f) for f in (1005, 1100, 1011, 1012, 1200, 1201))
    check("roto_empty_frames_off", o5 == 0 and o1100 == 1 and o1012 == 1 and o1011 == 0,
          "opacity at 1005 %.2f, 1011 %.2f, 1012 %.2f, 1100 %.2f, 1200 %.2f, 1201 %.2f" % (o5, o1011, o1012, o1100, o1200, o1201))

    # ---------------------------------------------------------- roto_point_count_fixed (16) and roto_keys_step10
    r16, _, _ = run(g, roto_points=16, roto_step=10)
    s16 = shapes_of(r16)[0]
    kc16 = key_counts(s16)
    times = s16[0].center.getControlPointKeyTimes()
    check("roto_point_count_fixed_16", len(s16) == 16 and all(c == len(times) for c in kc16), "points %d keys %s" % (len(s16), len(times)))
    check("roto_keys_step10", 18 <= len(times) <= 21 and times[0] == FIRST and times[-1] == LAST,
          "keys per point %d (frames %s ... %s)" % (len(times), times[:3], times[-3:]))
    check("roto_opacity_step10", opacity_at(s16, 1005) == 0 and opacity_at(s16, 1100) == 1,
          "opacity 1005 %.2f 1100 %.2f" % (opacity_at(s16, 1005), opacity_at(s16, 1100)))

    # ---------------------------------------------------------- roto_shape_count (two objects) + Objects knob
    g = fresh()
    set_version(g, VER, objects_list(2))
    r2, _, _ = run(g, roto_step=20)
    names = [s.name for s in shapes_of(r2)]
    mx0, _ = mask_centroid_nuke("%s/obj0.%04d.png" % (VER, FK))
    mx1, _ = mask_centroid_nuke("%s/obj1.%04d.png" % (VER, FK))
    c0, c1 = (centroid_of_shape(s, 1101)[0] for s in shapes_of(r2))
    check("roto_shape_count_2", names == ["obj0", "obj1"] and 450 < (c1 - c0) < 750,
          "shapes %s, centroid x %.0f vs %.0f (mask x %.0f vs %.0f); status: %s" % (names, c0, c1, mx0, mx1, g["status"].value()))
    r3, _, _ = run(g, objects="obj1")
    check("roto_objects_knob", [s.name for s in shapes_of(r3)] == ["obj1"], "shapes %s" % [s.name for s in shapes_of(r3)])
    r4, _, _ = run(g, objects="7")
    check("roto_no_object_selected", r4 is None and "no object" in g["status"].value().lower(), g["status"].value())

    # ---------------------------------------------------------- roto_cancel
    g = fresh()
    set_version(g, VER, objects_list(1))
    real = nuke.ProgressTask

    class FakeTask(object):
        def __init__(self, msg):
            self.calls = 0

        def isCancelled(self):
            self.calls += 1
            return self.calls > 5

        def setProgress(self, p):
            pass

        def setMessage(self, m):
            pass

    nuke.ProgressTask = FakeTask
    try:
        rc, _, _ = run(g, roto_step=1)
    finally:
        nuke.ProgressTask = real
    sc = shapes_of(rc)[0] if rc is not None else None
    nk = len(key_counts(sc) and sc[0].center.getControlPointKeyTimes()) if sc is not None else -1
    check("roto_cancel", rc is not None and g["status"].value().startswith("Roto cancelled at frame ") and nk == 5,
          "keys written %d, status %r" % (nk, g["status"].value()))

    # ---------------------------------------------------------- roto_gap_frames: empty masks inside the object's range
    GAP = SCRATCH + "/v_gap"
    os.makedirs(GAP, exist_ok=True)
    for f in range(F0, F1 + 1):
        src = "%s/obj0.%04d.png" % (HALF, 1005 if 1100 <= f <= 1104 else f)
        dst = "%s/obj0.%04d.png" % (GAP, f)
        if not os.path.exists(dst) or f in range(1100, 1105):
            shutil.copyfile(src, dst)
    g = fresh()
    set_version(g, GAP, objects_list(1), w=W // 2, h=H // 2)
    rg, _, _ = run(g, roto_step=1)
    sg = shapes_of(rg)[0]
    kg = len(sg[0].center.getControlPointKeyTimes())
    p99, p102 = sg[5].center.getPosition(1099.0), sg[5].center.getPosition(1102.0)
    p105 = sg[5].center.getPosition(1105.0)
    ops = [opacity_at(sg, f) for f in (1099, 1100, 1102, 1104, 1105)]
    check("roto_gap_frames", kg == 189 and ops[0] == 1 and ops[1] == 0 and ops[2] == 0 and ops[3] == 0 and ops[4] == 1
          and (p99.x, p99.y) == (p102.x, p102.y), "keys %d, opacity at 1099/1100/1102/1104/1105 = %s, point 5 at 1099 (%.1f, %.1f) "
          "at 1102 (%.1f, %.1f) at 1105 (%.1f, %.1f)" % (kg, ops, p99.x, p99.y, p102.x, p102.y, p105.x, p105.y))
    gx, gy = inside_point_nuke("%s/obj0.%04d.png" % (VER, 1099))
    a99 = rg.sample("rgba.alpha", gx, gy, 1, 1, 1099)
    a102 = rg.sample("rgba.alpha", gx, gy, 1, 1, 1102)
    check("roto_gap_render", a99 > 0.9 and a102 == 0, "Roto alpha at (%.0f, %.0f): frame 1099 %.3f, frame 1102 (empty mask) %.3f" % (gx, gy, a99, a102))

    # ---------------------------------------------------------- roto_speed: half-size masks, 1 object, every frame
    g = fresh()
    set_version(g, HALF, objects_list(1), w=W // 2, h=H // 2)
    rs, secs, _ = run(g, roto_step=1)
    ss = shapes_of(rs)[0]
    ksp = len(ss[0].center.getControlPointKeyTimes())
    hx, hy, _ = centroid_of_shape(ss, FK)
    fx, fy = mask_centroid_nuke("%s/obj0.%04d.png" % (VER, FK))
    check("roto_speed", secs < 60 and ksp == 189, "%.1f s total for %d keys (%.0f ms per frame); status: %s" % (
        secs, ksp, 1000.0 * secs / max(1, ksp), g["status"].value()))
    check("roto_half_size_maps_to_plate", ((hx - fx) ** 2 + (hy - fy) ** 2) ** 0.5 <= 6.0,
          "half-size mask shape centroid (%.1f, %.1f) vs full mask centroid (%.1f, %.1f)" % (hx, hy, fx, fy))
    ix, iy = inside_point_nuke("%s/obj0.%04d.png" % (VER, FK))
    jx, jy = inside_point_nuke("%s/obj0.%04d.png" % (VER, FIRST))
    a_in, a_out = rs.sample("rgba.alpha", ix, iy, 1, 1, FK), rs.sample("rgba.alpha", 5.5, 5.5, 1, 1, FK)
    a_pre, a_first = rs.sample("rgba.alpha", jx, jy, 1, 1, 1005), rs.sample("rgba.alpha", jx, jy, 1, 1, FIRST)
    check("roto_render_alpha", a_in > 0.9 and a_out == 0 and a_pre == 0 and a_first > 0.9,
          "Roto alpha: inside the mask at 1100 %.3f, outside %.3f, inside the first pose at 1005 (empty) %.3f and at %d %.3f" % (
              a_in, a_out, a_pre, FIRST, a_first))


    print("\n%s" % ("ALL PASS" if not failed else "FAILED: " + ", ".join(failed)))
    sys.exit(1 if failed else 0)


main()
