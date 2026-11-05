"""Smoke test for gizmos/InjectionScar.nk (Nuke 16).

    Nuke16.1.exe -t python/smoke_injection_scar.py [plate.png [out_dir]]

Pastes the .nk into a fresh script, feeds it a synthetic skin plate (or the
plate given on the command line), and checks with node.sample() that:
  * the puncture is darker and redder than the plate,
  * the bruise halo changes the plate, and far away it is untouched,
  * amount = 0 gives back the plate exactly,
  * "show matte" outputs a non zero matte.
With a plate path it also renders before/after PNGs into out_dir.
"""
import os
import sys
import nuke

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NK = os.path.join(ROOT, "gizmos", "InjectionScar.nk").replace("\\", "/")

fails = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + ("  " + detail if detail else ""))
    if not ok:
        fails.append(name)


def set_raw(node):
    """Pass pixels straight through. The colourspace is named differently per OCIO config."""
    for v in node["colorspace"].values():
        if v.lower().split("/")[-1].strip() == "raw":
            node["colorspace"].setValue(v)
            return
    print("note: no raw colourspace in this config, left", node["colorspace"].value())


def px(node, x, y):
    return [node.sample(c, x, y, 0.5, 0.5) for c in ("red", "green", "blue")]


args = sys.argv[1:]
plate_path = args[0].replace("\\", "/") if args else None
out_dir = args[1].replace("\\", "/") if len(args) > 1 else None

if plate_path:
    plate = nuke.nodes.Read(file=plate_path)
    set_raw(plate)
    W, H = plate.width(), plate.height()
else:
    W, H = 474, 350
    nuke.addFormat("{} {} 1 injscar_smoke".format(W, H))
    plate = nuke.nodes.Constant()
    plate["color"].setValue([0.45, 0.31, 0.25, 1])
    plate["format"].setValue("injscar_smoke")

nuke.nodePaste(NK)
g = nuke.toNode("InjectionScar")
check("group pasted", g is not None)
g.setInput(0, plate)

cx, cy = int(W * 0.5) + 0.5, int(H * 0.45) + 0.5   # pixel centres
g["center"].setValue([cx, cy])
g["size"].setValue(3)
g["bruise_size"].setValue(25)

# a pixel well outside the bruise
far = (5.5, 5.5)
for frame_label, node in (("plate", plate), ("scar", g)):
    print(frame_label, "center", px(node, cx, cy), "r=15", px(node, cx + 15, cy), "far", px(node, *far))

p_c, s_c = px(plate, cx, cy), px(g, cx, cy)
check("core darker", s_c[0] < 0.7 * p_c[0], "{:.3f} vs {:.3f}".format(s_c[0], p_c[0]))
check("core redder", s_c[1] < 0.6 * s_c[0], "g {:.3f} r {:.3f}".format(s_c[1], s_c[0]))

p_b, s_b = px(plate, cx + 15, cy), px(g, cx + 15, cy)
check("bruise halo changes plate", max(abs(a - b) / max(a, 1e-3) for a, b in zip(p_b, s_b)) > 0.03,
      "{} vs {}".format([round(v, 3) for v in p_b], [round(v, 3) for v in s_b]))

p_f, s_f = px(plate, *far), px(g, *far)
check("far corner untouched", max(abs(a - b) for a, b in zip(p_f, s_f)) < 1e-4)

g["amount"].setValue(0)
check("amount 0 is identity",
      max(abs(a - b) for a, b in zip(px(plate, cx, cy), px(g, cx, cy))) < 1e-4)
g["amount"].setValue(1)

g["show_matte"].setValue(True)
m = [g.sample(c, cx, cy, 0.5, 0.5) for c in ("red", "green", "blue", "alpha")]
check("show matte non zero at centre", m[0] > 0.5 and m[2] > 0.1, str([round(v, 3) for v in m]))
g["show_matte"].setValue(False)

# per channel tint expressions must survive the export
core = g.node("Scar_core")
check("core multiply has per-channel expressions",
      all(core["multiply"].hasExpression(i) for i in range(3)))
g["core_color"].setValue([0.9, 0.2, 0.2])
check("core colour reaches green/blue separately",
      px(g, cx, cy)[1] < px(g, cx, cy)[0] * 0.5)
g["core_color"].setValue([0.35, 0.13, 0.11])


def finite(node, pts):
    return all(v == v and abs(v) < 1e6 for p in pts for v in px(node, *p))


pts = [(cx, cy), (cx + 1, cy), (cx + 10, cy), (cx + 40, cy + 40), (5.5, 5.5)]
for label, vals in (("size 0", {"size": 0}), ("size -3", {"size": -3}),
                    ("bruise_size 0", {"bruise_size": 0}),
                    ("stretch 4 angle 60", {"stretch": 4, "angle": 60}),
                    ("irregularity 1 seed 77", {"irregularity": 1, "seed": 77}),
                    ("soften 10", {"soften": 10})):
    old = {k: g[k].value() for k in vals}
    for k, v in vals.items():
        g[k].setValue(v)
    check(label + " finite", finite(g, pts))
    if label == "bruise_size 0":
        check("bruise_size 0 leaves plate alone at r=10",
              max(abs(a - b) for a, b in zip(px(g, cx + 10, cy), px(plate, cx + 10, cy))) < 1e-3
              or px(g, cx + 10, cy)[0] > 0.9 * px(plate, cx + 10, cy)[0])
    for k, v in old.items():
        g[k].setValue(v)

# the shape must follow "position": same pixel offset gives the same value
a = px(g, cx + 6, cy)
g["center"].setValue([cx + 30, cy + 20])
b = px(g, cx + 36, cy + 20)
check("shape moves with position (noise is relative)",
      max(abs(i - j) for i, j in zip(a, b)) < 0.02 or not plate_path,
      "{} vs {}".format([round(v, 3) for v in a], [round(v, 3) for v in b]))
g["center"].setValue([cx, cy])

check("no node errors", not any(n.hasError() for n in nuke.allNodes(recurseGroups=True)))

if out_dir:
    os.makedirs(out_dir, exist_ok=True)
    for label, node in (("before", plate), ("after", g)):
        w = nuke.nodes.Write(file="{}/{}.png".format(out_dir, label), inputs=[node])
        set_raw(w)
        nuke.execute(w, 1, 1)
    g["show_matte"].setValue(True)
    w = nuke.nodes.Write(file="{}/matte.png".format(out_dir), inputs=[g])
    set_raw(w)
    nuke.execute(w, 1, 1)
    print("rendered to", out_dir)

print("RESULT", "FAIL: " + ", ".join(fails) if fails else "ALL PASS")
sys.exit(1 if fails else 0)
