"""Smoke test for gizmos/AOV_CameraData.nk.   Run: Nuke16.1.exe -t python/smoke_aov_cameradata.py

Pastes the .nk into a fresh script, feeds synthetic depth / N / P and checks sampled values.
"""
import os
import nuke

NK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "gizmos", "AOV_CameraData.nk")

nuke.Layer("depth", ["depth.Z"])
nuke.Layer("N", ["N.red", "N.green", "N.blue"])
nuke.Layer("P", ["P.red", "P.green", "P.blue"])

c = nuke.nodes.Constant(channels="rgba", format="64 64 1.0")
d = nuke.nodes.Expression(inputs=[c])
d["channel0"].setValue("depth.Z")
d["expr0"].setValue("x < 32 ? 10 : (x < 48 ? 0 : 1e12)")      # distance, bg = 0 / huge
for i in (1, 2, 3):
    d["channel%d" % i].setValue("none")
n = nuke.nodes.Expression(inputs=[d])
n["channel0"].setValue("N.red"); n["channel1"].setValue("N.green"); n["channel2"].setValue("N.blue")
n["channel3"].setValue("none")
n["expr0"].setValue("y < 32 ? 0 : 1")    # y<32 -> N=(0,1,0) ; else N=(1,0,0)
n["expr1"].setValue("y < 32 ? 1 : 0")
n["expr2"].setValue("0")
p = nuke.nodes.Expression(inputs=[n])
p["channel0"].setValue("P.red"); p["channel1"].setValue("P.green"); p["channel2"].setValue("P.blue")
p["channel3"].setValue("none")
p["expr0"].setValue("x"); p["expr1"].setValue("y"); p["expr2"].setValue("0")

nuke.selectAll()
for nd in nuke.allNodes():
    nd["selected"].setValue(False)
nuke.nodePaste(NK)
g = nuke.toNode("AOV_CameraData")
g.setInput(0, p)
g["p_center"].setValue([10, 10, 0])
g["p_radius"].setValue(5)
g["p_falloff"].setValue(5)

fails = []


def check(label, got, want, tol=1e-3):
    ok = abs(got - want) <= tol
    print("%-34s got %-10.5f want %-8.5f %s" % (label, got, want, "ok" if ok else "FAIL"))
    if not ok:
        fails.append(label)


check("depth 10 -> 1/10", g.sample("depth.Z", 10.5, 10.5), 0.1)
check("depth 0 (bg) -> 0", g.sample("depth.Z", 40.5, 10.5), 0.0)
check("depth 1e12 (bg) -> 0", g.sample("depth.Z", 60.5, 10.5), 0.0)
check("normal +Y mask (aligned)", g.sample("maskN.alpha", 10.5, 10.5), 1.0)
check("normal +X vs +Y mask", g.sample("maskN.alpha", 10.5, 50.5), 0.0)
check("position sphere centre", g.sample("maskP.alpha", 10.5, 10.5), 1.0)
check("position sphere far", g.sample("maskP.alpha", 40.5, 40.5), 0.0)
check("position sphere falloff mid", g.sample("maskP.alpha", 17.5, 10.5), 0.6, 1e-2)   # x=17 -> dist 7
g["n_invert"].setValue(True)
check("normal mask inverted", g.sample("maskN.alpha", 10.5, 10.5), 0.0)
g["p_shape"].setValue("box")
g["p_size"].setValue([5, 5, 5])
check("position box inside", g.sample("maskP.alpha", 12.5, 12.5), 1.0)
check("position box outside", g.sample("maskP.alpha", 40.5, 12.5), 0.0)

# ZDefocus hookup must be valid
z = nuke.nodes.ZDefocus2(inputs=[g])
z["z_channel"].setValue("depth.Z")
z["math"].setValue("far=0")
print("ZDefocus error:", z.error())
print("RESULT:", "FAIL " + ", ".join(fails) if fails else "ALL OK")
