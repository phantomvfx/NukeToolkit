"""SAM3Matte Group, headless in Nuke 16.1. Pastes gizmos/SAM3Matte.nk (build it first with build/build_sam3matte.py).

    & "L:\\Apps\\Nuke16.1v4\\Nuke16.1.exe" -t L:\\Nuke\\tools\\comfy\\tests\\nuke\\test_sam3matte_group.py

Prints PASS name / FAIL name for each check and exits 1 if any failed. Scratch files go to D:/ComfyUI_Cache/temp/sam3matte_t10.
The fixture is a fake version made from the spike masks (only obj0 has pixels there; obj1 is obj0 moved 600 px by a Write).
"""
import os, sys, runpy
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..")).replace("\\", "/")
runpy.run_path(ROOT + "/init.py")   # path setup + the NukeComfy alias

import json
import shutil
import struct

import nuke

sys.path.insert(0, ROOT + "/tools/comfy/python")
import sam3matte_crypto as crypto

NK = ROOT + "/tools/comfy/gizmos/SAM3Matte.nk"
SPIKE = "L:/ComfyUI/output/sam3_spike/run1"
SCRATCH = "D:/ComfyUI_Cache/temp/sam3matte_t10"
VER = SCRATCH + "/v1"
F0, F1, FK = 1001, 1200, 1100
W, H = 2048, 1080
PLATE = (0.2, 0.4, 0.6)

failed = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ((" | " + detail) if detail else ""))
    if not ok:
        failed.append(name)


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def close(a, b, rel=1e-6, ab=1e-9):
    return abs(a - b) <= max(ab, rel * max(abs(a), abs(b)))


# ---------------------------------------------------------------- fixture
def make_fixture():
    os.makedirs(VER, exist_ok=True)
    for i in range(1, 201):
        dst = "%s/obj0.%04d.png" % (VER, F0 + i - 1)
        if not os.path.exists(dst):
            shutil.copyfile("%s/obj0_%05d_.png" % (SPIKE, i), dst)
    if os.path.exists("%s/obj1.%04d.png" % (VER, F1)):
        return
    nuke.scriptClear()
    r = nuke.nodes.Read(file=VER + "/obj0.####.png", first=F0, last=F1, origfirst=F0, origlast=F1, raw=True)
    t = nuke.nodes.Transform(inputs=[r])
    t["translate"].setValue([600, 0])
    w = nuke.nodes.Write(inputs=[t], file=VER + "/obj1.####.png", file_type="png", raw=True, channels="rgb")
    w["datatype"].setValue("8 bit")
    nuke.execute(w, F0, F1)


def fresh(plate=True):
    """Clear the script, paste the Group, add a plate. Returns (group, plate)."""
    nuke.scriptClear()
    nuke.root()["first_frame"].setValue(F0)
    nuke.root()["last_frame"].setValue(F1)
    nuke.root()["format"].setValue(nuke.addFormat("%d %d 1 sam2k" % (W, H)))
    nuke.nodePaste(NK)
    g = nuke.toNode("SAM3Matte")
    c = None
    if plate:
        c = nuke.nodes.Constant(channels="rgb")
        c["color"].setValue([PLATE[0], PLATE[1], PLATE[2], 1])
        c["format"].setValue(nuke.addFormat("%d %d 1 sam2k" % (W, H)))
        g.setInput(0, c)
    return g, c


def version(g, present, on=None):
    """Set the knobs the way show_version will."""
    g["has_result"].setValue(True)
    g["mask_first"].setValue(F0)
    g["mask_last"].setValue(F1)
    for k in range(8):
        on_k = present[k] if on is None else on[k]
        g["obj_present%d" % k].setValue(bool(present[k]))
        g["obj_on%d" % k].setValue(bool(on_k))
        if present[k]:
            rd = nuke.toNode("SAM3Matte.Obj%dRead" % k)
            rd["file"].setValue("%s/obj%d.####.png" % (VER, k))
            for kn, v in (("first", F0), ("last", F1), ("origfirst", F0), ("origlast", F1)):
                rd[kn].setValue(v)


def S(node, chan, x, y, frame=FK):
    return node.sample(chan, x, y, 1, 1, frame)


def find_pixels(r0, r1):
    """A: inside obj0 only, B: inside obj1 only, BG: neither. Coarse scan of frame FK on the direct Reads."""
    A = B = BG = None
    A2 = None
    for y in range(10, H, 20):
        for x in range(10, W, 20):
            v0, v1 = S(r0, "red", x, y), S(r1, "red", x, y)
            if A is None and v0 > 0.99 and v1 < 0.01:
                A = (x, y)
            elif A is not None and A2 is None and v0 > 0.99 and v1 < 0.01 and abs(x - A[0]) + abs(y - A[1]) > 300:
                A2 = (x, y)
            if B is None and v1 > 0.99 and v0 < 0.01:
                B = (x, y)
            if BG is None and v0 < 0.001 and v1 < 0.001 and 300 < x < 1500:
                BG = (x, y)
    return A, A2, B, BG


def run_extent(node, chan, x, y, vertical=False, thr=0.5, span=500):
    """Pixels with value > thr in the unbroken run through (x, y)."""
    n = 0
    for sgn in (1, -1):
        for d in range(0 if sgn == 1 else 1, span):
            xx, yy = (x, y + sgn * d) if vertical else (x + sgn * d, y)
            if not (0 <= xx < W and 0 <= yy < H) or S(node, chan, xx, yy) <= thr:
                break
            n += 1
    return n


def frac_run(node, chan, x, y, vertical=False, span=60):
    """Number of pixels with 0.02 < value < 0.98 within +-span of (x, y)."""
    n = 0
    for d in range(-span, span + 1):
        xx, yy = (x, y + d) if vertical else (x + d, y)
        if 0 <= xx < W and 0 <= yy < H:
            v = S(node, chan, xx, yy)
            if 0.02 < v < 0.98:
                n += 1
    return n


def edge_point(r0, x, y):
    """The first pixel going right from (x, y) where obj0 drops below 0.5."""
    while S(r0, "red", x, y) > 0.5 and x < W - 1:
        x += 1
    return x, y


def cryptomatte_after(src):
    cm = nuke.createNode("Cryptomatte", inpanel=False)
    cm.setInput(0, src)
    return cm


def pick(cm, name, pt):
    cm["matteList"].setValue(name)
    return S(cm, "rgba.alpha", pt[0], pt[1])


def main():
    make_fixture()

    # ---------------------------------------------------------- paste_ok
    nuke.scriptClear()
    layers_before = list(nuke.layers())
    nuke.nodePaste(NK)
    g = nuke.toNode("SAM3Matte")
    names = (["Source", "Select", "Stencil", "CryptoExpr", "CryptoMeta", "Union", "Erode", "Soften", "StencilHold", "Output1"]
             + ["Obj%dRead" % k for k in range(8)] + ["Obj%dSlot" % k for k in range(8)])
    missing = [n for n in names if nuke.toNode("SAM3Matte." + n) is None]
    knobs = (["mode", "prompt", "max_objects", "source_range", "first", "last", "key_current", "key_frame", "tracking_width",
              "chunk_size", "generate", "cancel", "version", "status", "objects", "erode", "blur", "hard_stencil", "make_roto",
              "roto_points", "roto_step", "versions_data", "has_result", "stencil_frame", "mask_first", "mask_last"]
             + ["obj_present%d" % k for k in range(8)] + ["obj_on%d" % k for k in range(8)])
    missing_knobs = [k for k in knobs if k not in g.knobs()]
    check("paste_ok", g is not None and g.Class() == "Group" and not missing and not missing_knobs,
          "missing nodes %s knobs %s" % (missing, missing_knobs))

    # ---------------------------------------------------------- fresh_script_layers
    lay = list(nuke.layers())
    check("fresh_script_layers", "sam" in lay and "CryptoSAM00" in lay,
          "layers before paste: %s; after: sam=%s CryptoSAM00=%s" % (
              [x for x in layers_before if x in ("sam", "CryptoSAM00")], "sam" in lay, "CryptoSAM00" in lay))

    # ---------------------------------------------------------- no_version_black
    g, plate = fresh()
    r0 = nuke.nodes.Read(file=VER + "/obj0.####.png", first=F0, last=F1, origfirst=F0, origlast=F1, raw=True)
    r1 = nuke.nodes.Read(file=VER + "/obj1.####.png", first=F0, last=F1, origfirst=F0, origlast=F1, raw=True)
    A, A2, B, BG = find_pixels(r0, r1)
    print("pixels A=%s A2=%s B=%s BG=%s" % (A, A2, B, BG))
    check("fixture_pixels", all((A, A2, B, BG)))
    pts = [A, A2, B, BG, (5, 5), (W - 5, H - 5)]
    vals = [S(g, "rgba.alpha", x, y) for x, y in pts]
    sams = [S(g, "sam.m%d" % k, A[0], A[1]) for k in range(8)]
    bad = [n.name() for n in g.nodes() if n.hasError()]
    check("no_version_black", all(v == 0 for v in vals) and all(v == 0 for v in sams) and not bad,
          "alpha %s sam %s errors %s" % (vals, sams, bad))

    # ---------------------------------------------------------- alpha_union
    g, plate = fresh()
    r0 = nuke.nodes.Read(file=VER + "/obj0.####.png", first=F0, last=F1, origfirst=F0, origlast=F1, raw=True)
    r1 = nuke.nodes.Read(file=VER + "/obj1.####.png", first=F0, last=F1, origfirst=F0, origlast=F1, raw=True)
    version(g, [1, 1, 0, 0, 0, 0, 0, 0], on=[1, 0, 0, 0, 0, 0, 0, 0])
    a_A, a_bg, a_B = S(g, "rgba.alpha", *A), S(g, "rgba.alpha", *BG), S(g, "rgba.alpha", *B)
    ok1 = a_A > 0.99 and a_bg == 0 and a_B == 0
    for k, v in ((1, 1), (0, 1)):
        g["obj_on%d" % k].setValue(bool(v))
    b_A, b_B, b_bg = S(g, "rgba.alpha", *A), S(g, "rgba.alpha", *B), S(g, "rgba.alpha", *BG)
    m0A, m1B = S(g, "sam.m0", *A), S(g, "sam.m1", *B)
    ok2 = b_A >= m0A - 1e-6 and b_B >= m1B - 1e-6 and b_A > 0.99 and b_B > 0.99 and b_bg == 0
    check("alpha_union", ok1 and ok2,
          "on0 only: A=%.4f bg=%.4f B=%.4f; both: A=%.4f B=%.4f bg=%.4f" % (a_A, a_bg, a_B, b_A, b_B, b_bg))

    # ---------------------------------------------------------- per_object_channels
    d0 = [S(g, "sam.m0", x, y) for x, y in (A, A2, B, BG)]
    e0 = [S(r0, "red", x, y) for x, y in (A, A2, B, BG)]
    d1 = [S(g, "sam.m1", x, y) for x, y in (A, A2, B, BG)]
    e1 = [S(r1, "red", x, y) for x, y in (A, A2, B, BG)]
    ex, ey = edge_point(r0, A[0], A[1])
    edge_ok = True
    for dx in range(-3, 4):
        edge_ok &= close(S(g, "sam.m0", ex + dx, ey), S(r0, "red", ex + dx, ey), 1e-5)
    m7 = [S(g, "sam.m7", x, y) for x, y in (A, B)]
    check("per_object_channels",
          all(close(a, b, 1e-5) for a, b in zip(d0, e0)) and all(close(a, b, 1e-5) for a, b in zip(d1, e1)) and edge_ok
          and all(v == 0 for v in m7) and d0[0] > 0.99 and d1[2] > 0.99,
          "m0 %s vs %s; m1 %s vs %s; edge ok %s; m7 %s" % (d0, e0, d1, e1, edge_ok, m7))

    # ---------------------------------------------------------- eight_channel_layer
    ch = g.channels()
    miss = [c for c in crypto.SAM_CHANNELS if c not in ch]
    out = nuke.toNode("SAM3Matte.Output1")
    miss_out = [c for c in crypto.SAM_CHANNELS if c not in out.channels()]
    check("eight_channel_layer", not miss and not miss_out, "missing at group %s, at Output1 %s; layers %s" % (
        miss, miss_out, sorted(set(c.split(".")[0] for c in ch))))

    # ---------------------------------------------------------- erode_blur
    base_row = run_extent(g, "rgba.alpha", A[0], A[1])
    base_col = run_extent(g, "rgba.alpha", A[0], A[1], vertical=True)
    base_frac = frac_run(g, "rgba.alpha", ex, ey)
    sam_before = [S(g, "sam.m0", ex + dx, ey) for dx in range(-4, 5)]
    g["erode"].setValue(3)
    er_row = run_extent(g, "rgba.alpha", A[0], A[1])
    er_col = run_extent(g, "rgba.alpha", A[0], A[1], vertical=True)
    sam_er = [S(g, "sam.m0", ex + dx, ey) for dx in range(-4, 5)]
    g["erode"].setValue(0)
    g["blur"].setValue(5)
    bl_frac = frac_run(g, "rgba.alpha", ex, ey)
    bl_edge = [S(g, "rgba.alpha", ex + dx, ey) for dx in range(-8, 9)]
    sam_bl = [S(g, "sam.m0", ex + dx, ey) for dx in range(-4, 5)]
    g["blur"].setValue(0)
    shrink_row, shrink_col = base_row - er_row, base_col - er_col
    check("erode_blur",
          3 <= shrink_row <= 9 and 3 <= shrink_col <= 9 and bl_frac >= 6 and bl_frac > 2 * base_frac
          and any(0 < v < 1 for v in bl_edge) and sam_before == sam_er == sam_bl,
          "erode=3 shrinks row run %d->%d (%d px), column run %d->%d (%d px); fractional px at edge: blur0=%d blur5=%d; "
          "sam.m0 unchanged=%s" % (base_row, er_row, shrink_row, base_col, er_col, shrink_col, base_frac, bl_frac,
                                   sam_before == sam_er == sam_bl))

    # ---------------------------------------------------------- hard_stencil
    box = nuke.nodes.Constant(channels="rgba")
    box["color"].setValue([0, 0, 0, 1])
    box["format"].setValue(nuke.addFormat("%d %d 1 sam2k" % (W, H)))
    cr = nuke.nodes.Crop(inputs=[box], reformat=False)
    cr["box"].setValue([A[0] - 25, A[1] - 25, A[0] + 25, A[1] + 25])
    cr["crop"].setValue(True)
    g.setInput(2, cr)
    g["stencil_frame"].setValue(FK)
    off = (S(g, "rgba.alpha", *A), S(g, "rgba.alpha", *A2), S(g, "rgba.alpha", *B))
    g["hard_stencil"].setValue(True)
    on = (S(g, "rgba.alpha", *A), S(g, "rgba.alpha", *A2), S(g, "rgba.alpha", *B), S(g, "rgba.alpha", *BG))
    g["hard_stencil"].setValue(False)
    after_off = (S(g, "rgba.alpha", *A), S(g, "rgba.alpha", *A2), S(g, "rgba.alpha", *B))
    check("hard_stencil", on[0] == 0 and close(on[1], off[1]) and close(on[2], off[2]) and on[3] == 0
          and off[0] > 0.99 and after_off == off,
          "off %s; on(A,A2,B,BG) %s; off again %s" % (off, on, after_off))
    g.setInput(2, None)
    g["hard_stencil"].setValue(True)
    unc = S(g, "rgba.alpha", *A)
    g["hard_stencil"].setValue(False)
    check("hard_stencil_unconnected", unc > 0.99, "hard_stencil on, Stencil not connected: alpha at A = %.3f" % unc)

    # ---------------------------------------------------------- rgb_passthrough
    px = [(S(g, "rgba.red", *p), S(g, "rgba.green", *p), S(g, "rgba.blue", *p)) for p in (A, B, BG)]
    check("rgb_passthrough", all(all(close(v, f32(c), 1e-6) for v, c in zip(t, PLATE)) for t in px), "rgb %s" % (px,))

    # ---------------------------------------------------------- (a) summed comparisons are numbers
    e = nuke.nodes.Expression(channel0="rgba.red", expr0="(1 >= 0) + (2 > 3) + (4 > 3) + ((0.5 >= 0.5) ? 10 : 20)")
    e.setInput(0, plate)
    val = S(e, "rgba.red", 10, 10)
    check("expr_sum_of_comparisons", close(val, 12.0), "(1>=0)+(2>3)+(4>3)+10 = %s (expected 12)" % val)

    # ---------------------------------------------------------- crypto_native
    g, plate = fresh()
    r0 = nuke.nodes.Read(file=VER + "/obj0.####.png", first=F0, last=F1, origfirst=F0, origlast=F1, raw=True)
    version(g, [1, 1, 0, 0, 0, 0, 0, 0])
    cex, cme = nuke.toNode("SAM3Matte.CryptoExpr"), nuke.toNode("SAM3Matte.CryptoMeta")
    crypto.apply_to_nodes(cex, cme, ["obj0", "obj1"] + [None] * 6)
    cm = cryptomatte_after(g)
    cm["cryptoLayer"].setValue("CryptoSAM")
    choices = cm["cryptoLayerChoice"].values() if "cryptoLayerChoice" in cm.knobs() else []
    a0 = [pick(cm, "obj0", p) for p in (A, BG, B)]
    a1 = [pick(cm, "obj1", p) for p in (A, BG, B)]
    check("crypto_native", any("CryptoSAM" in str(c) for c in choices) and a0[0] > 0.99 and a0[1] == 0 and a0[2] == 0
          and a1[2] > 0.99 and a1[1] == 0 and a1[0] == 0,
          "layer choices %s; matteList obj0 -> A,BG,B = %s; obj1 -> %s" % (choices, a0, a1))

    # (e) metadata survives save + reopen
    man_before = cme.metadata().get("exr/cryptomatte/%s/manifest" % crypto.layer_key())
    os.makedirs(SCRATCH, exist_ok=True)
    nk = SCRATCH + "/t10_roundtrip.nk"
    nuke.scriptSaveAs(nk, 1)
    nuke.scriptClear()
    nuke.scriptOpen(nk)
    g2 = nuke.toNode("SAM3Matte")
    cm2 = [n for n in nuke.allNodes("Cryptomatte")][0]
    cme2 = nuke.toNode("SAM3Matte.CryptoMeta")
    man_after = cme2.metadata().get("exr/cryptomatte/%s/manifest" % crypto.layer_key())
    p0, p1 = pick(cm2, "obj0", A), pick(cm2, "obj1", B)
    p0b = pick(cm2, "obj1", A)
    check("metadata_save_reopen", man_before and man_before == man_after and p0 > 0.99 and p1 > 0.99 and p0b == 0,
          "manifest %s ; before==after %s; obj0 at A=%.3f obj1 at B=%.3f obj1 at A=%.3f" % (man_after, man_before == man_after, p0, p1, p0b))

    # ---------------------------------------------------------- crypto_exr_roundtrip
    out = {}
    for label, dt in (("f32", "32 bit float"), ("half", "16 bit half")):
        wr = nuke.nodes.Write(inputs=[g2], file="%s/rt_%s.exr" % (SCRATCH, label), file_type="exr")
        wr["channels"].setValue("all")
        wr["datatype"].setValue(dt)
        wr["compression"].setValue("none")
        wr["metadata"].setValue("all metadata")
        print("INFO write metadata knob:", wr["metadata"].value(), wr["metadata"].values())
        nuke.execute(wr, FK, FK)
        rb = nuke.nodes.Read(file="%s/rt_%s.exr" % (SCRATCH, label), first=FK, last=FK, origfirst=FK, origlast=FK, raw=True)
        cmx = cryptomatte_after(rb)
        cmx["cryptoLayer"].setValue("CryptoSAM")
        print("INFO %s metadata keys: %s" % (label, [k for k in rb.metadata() if "rypto" in k]))
        idv = S(rb, "CryptoSAM00.red", A[0], A[1])
        out[label] = (pick(cmx, "obj0", A), pick(cmx, "obj1", A), idv, "sam.m7" in rb.channels(), rb.metadata().get(
            "exr/cryptomatte/%s/name" % crypto.layer_key()))
    want = crypto.name_to_id("obj0")[1]
    f = out["f32"]
    check("crypto_exr_roundtrip", f[0] > 0.99 and f[1] == 0 and close(f[2], want, 0, 0) and f[3],
          "float32: obj0 pick alpha=%.3f, obj1 pick alpha=%.3f, id0 %r (expected %r), sam.m7 present %s, meta name %s" % (
              f[0], f[1], f[2], want, f[3], f[4]))
    h = out["half"]
    print("INFO crypto_exr_half: obj0 pick alpha=%.3f, obj1 pick alpha=%.3f, id0 %r (expected %r), IDs survive: %s" % (
        h[0], h[1], h[2], want, close(h[2], want, 0, 0)))

    # ---------------------------------------------------------- crypto_matches_reference (+ (b) 8-slot expression)
    def overlap_case(label, values, names, ramps=None):
        g, plate = fresh()
        version(g, [1] * 8)
        with g:
            for k in range(8):
                slot = nuke.toNode("SAM3Matte.Obj%dSlot" % k)
                if ramps and k in ramps:
                    (x0, y0, x1, y1) = ramps[k]
                    n = nuke.nodes.Ramp(type="linear")
                    n["p0"].setValue([x0, y0])
                    n["p1"].setValue([x1, y1])
                    n["color"].setValue([1, 1, 1, 1])
                else:
                    n = nuke.nodes.Constant(channels="rgb")
                    n["color"].setValue([values[k]] * 3 + [1])
                if "format" in n.knobs():
                    n["format"].setValue(nuke.addFormat("%d %d 1 sam2k" % (W, H)))
                slot.setInput(1, n)
        cex, cme = nuke.toNode("SAM3Matte.CryptoExpr"), nuke.toNode("SAM3Matte.CryptoMeta")
        crypto.apply_to_nodes(cex, cme, names)
        elen = max(len(cex["expr%d" % i].value()) for i in range(4))
        stored = all(cex["expr%d" % i].value() == crypto.expressions(names)["expr%d" % i] for i in range(4))
        for k in range(8):   # warm-up: the first Node.sample() on a freshly edited graph can return 0 for a channel it has not validated yet
            S(g, "sam.m%d" % k, 0, 0)
        S(g, "CryptoSAM00.alpha", 0, 0)
        bad = []
        pts = [(100, 100), (1000, 500), (1900, 1000), (500, 900), (1500, 200), (1024, 540), (3, 1077), (2040, 5)]
        for (x, y) in pts:
            x, y = x + 0.5, y + 0.5   # pixel centre: an integer coordinate is a pixel corner and Nuke interpolates 4 pixels (IDs would blend)
            vs = [f32(S(g, "sam.m%d" % k, x, y)) for k in range(8)]
            ref = crypto.reference(vs, names)
            got = tuple(S(g, "CryptoSAM00.%s" % c, x, y) for c in ("red", "green", "blue", "alpha"))
            if not all(close(a, f32(b), 1e-6, 1e-12) for a, b in zip(got, ref)):
                bad.append(((x, y), vs[:3], got, ref))
        e = [n.name() for n in g.nodes() if n.hasError()]
        return elen, stored, bad, e

    names3 = ["obj0", "obj1", "obj2"] + [None] * 5
    r_a = overlap_case("0.7/0.4/0.2", [0.7, 0.4, 0.2, 0, 0, 0, 0, 0], names3)
    r_b = overlap_case("tie", [0.5, 0.5, 0.1, 0, 0, 0, 0, 0], names3)
    names8 = ["obj%d" % k for k in range(8)]
    r_c = overlap_case("eight", [0.1, 0.9, 0.3, 0.5, 0.7, 0.2, 0.6, 0.4], names8)
    ramps = {0: (0, 0, W, 0), 1: (W, 0, 0, 0), 2: (0, 0, 0, H), 3: (0, H, W, H), 4: (0, 0, W, H), 5: (W, H, 0, 0),
             6: (0, 0, W, 0), 7: (0, 0, 0, H)}   # 0/6 and 1/7... differ in direction, 6 duplicates 0 (ties across pixels)
    r_d = overlap_case("ramps", [0] * 8, names8, ramps)
    for label, r in (("0.7/0.4/0.2", r_a), ("tie 0.5/0.5/0.1", r_b), ("8 slots constants", r_c), ("8 slots ramps", r_d)):
        print("INFO overlap %s: expr length %d, stored verbatim %s, mismatches %d, node errors %s" % (label, r[0], r[1], len(r[2]), r[3]))
        for m in r[2][:3]:
            print("   mismatch", m)
    check("crypto_matches_reference", all(r[1] and not r[2] and not r[3] for r in (r_a, r_b, r_c, r_d)),
          "8 pixels each in 4 overlap cases; 8-slot expression length %d chars accepted" % max(r_c[0], r_d[0]))

    # ---------------------------------------------------------- mask_scale_up (masks at tracking size, Source bbox != format)
    HALF = SCRATCH + "/v_half"
    os.makedirs(HALF, exist_ok=True)
    if not os.path.exists("%s/obj0.%04d.png" % (HALF, FK)):
        nuke.scriptClear()
        nuke.root()["format"].setValue(nuke.addFormat("%d %d 1 sam2k" % (W, H)))
        r = nuke.nodes.Read(file=VER + "/obj0.####.png", first=F0, last=F1, origfirst=F0, origlast=F1, raw=True)
        rf = nuke.nodes.Reformat(inputs=[r], type="to format", resize="distort")
        rf["format"].setValue(nuke.addFormat("%d %d 1 sam_half" % (W // 2, H // 2)))
        w = nuke.nodes.Write(inputs=[rf], file=HALF + "/obj0.####.png", file_type="png", raw=True, channels="rgb")
        w["datatype"].setValue("8 bit")
        nuke.execute(w, FK - 1, FK + 1)
    g, plate = fresh()
    r0 = nuke.nodes.Read(file=VER + "/obj0.####.png", first=F0, last=F1, origfirst=F0, origlast=F1, raw=True)
    A, A2, B, BG = find_pixels(r0, nuke.nodes.Read(file=VER + "/obj1.####.png", first=F0, last=F1, origfirst=F0, origlast=F1, raw=True))
    ex, ey = edge_point(r0, A[0], A[1])
    cropn = nuke.nodes.Crop(inputs=[plate], reformat=False)
    cropn["box"].setValue([300, 200, 1700, 900])      # bbox 1400x700 inside a 2048x1080 format
    g.setInput(0, cropn)
    g["obj_present0"].setValue(True)
    rd = nuke.toNode("SAM3Matte.Obj0Read")
    rd["file"].setValue(HALF + "/obj0.####.png")
    for kn, v in (("first", FK - 1), ("last", FK + 1), ("origfirst", FK - 1), ("origlast", FK + 1)):
        rd[kn].setValue(v)
    half_fmt = (rd.width(), rd.height())
    sc = nuke.toNode("SAM3Matte.Obj0Scale")
    S(g, "sam.m0", 0, 0)   # warm-up
    gf = g.format()
    src_bbox = (cropn.bbox().w(), cropn.bbox().h())
    sc_fmt = (sc.format().width(), sc.format().height())
    sc_bbox = (sc.bbox().w(), sc.bbox().h())
    inter = union_n = 0
    for y in range(10, H, 20):
        for x in range(10, W, 20):
            a_ = S(g, "sam.m0", x + 0.5, y + 0.5) > 0.5
            b_ = S(r0, "red", x + 0.5, y + 0.5) > 0.5
            inter += a_ and b_
            union_n += a_ or b_
    iou = inter / float(union_n) if union_n else 0.0
    edge = [(S(g, "sam.m0", ex + dx + 0.5, ey + 0.5), S(r0, "red", ex + dx + 0.5, ey + 0.5)) for dx in range(-4, 5)]
    edge_dev = max(abs(a_ - b_) for a_, b_ in edge)
    inside = S(g, "sam.m0", A[0] + 0.5, A[1] + 0.5)
    outside = S(g, "sam.m0", BG[0] + 0.5, BG[1] + 0.5)
    alpha_in = S(g, "rgba.alpha", A[0] + 0.5, A[1] + 0.5)
    check("mask_scale_up",
          half_fmt == (W // 2, H // 2) and sc_fmt == (W, H) and sc_bbox == (W, H) and (gf.width(), gf.height()) == (W, H)
          and src_bbox[0] < 1500 and src_bbox[1] < 800 and union_n > 50 and iou >= 0.97 and inside > 0.99 and outside == 0 and alpha_in > 0.99
          and edge_dev < 0.3,
          "Read %s -> Obj0Scale format %s bbox %s; Group format %dx%d; Source bbox %s (format stays %dx%d); "
          "IoU vs full-size obj0 on a 20 px grid = %.4f (%d px union); inside %.3f outside %.3f alpha %.3f; max edge deviation %.3f" % (
              half_fmt, sc_fmt, sc_bbox, gf.width(), gf.height(), src_bbox, W, H, iou, union_n, inside, outside, alpha_in, edge_dev))

    print("\n%s" % ("ALL PASS" if not failed else "FAILED: " + ", ".join(failed)))
    sys.exit(1 if failed else 0)


main()
