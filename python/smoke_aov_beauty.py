"""Smoke test for gizmos/AOV_Beauty.nk.   Run: Nuke16.1.exe -t python/smoke_aov_beauty.py

Pastes the .nk into a fresh script, feeds constant synthetic AOVs and checks the rebuild,
the per-AOV toggles/grades, alpha preservation and the difference view.
"""
import os
import nuke

NK = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                  "gizmos", "AOV_Beauty.nk")

VALUES = {
    "diffuse_direct": 0.20, "diffuse_indirect": 0.10, "albedo": 0.50,
    "specular_direct": 0.05, "specular_indirect": 0.01, "coat": 0.02, "sheen": 0.03,
    "sss": 0.04, "transmission": 0.06, "emission": 0.07,
    "volume_direct": 0.08, "volume_indirect": 0.09,
}
# albedo multiply is OFF by default (Arnold diffuse AOVs already contain albedo)
EXPECTED = (0.20 + 0.10) + sum(v for k, v in VALUES.items()
                               if k not in ("diffuse_direct", "diffuse_indirect", "albedo"))

# synthetic input: constants per layer, alpha 0.7 in rgba
node = nuke.nodes.Constant(channels="rgba", format="8 8 1.0")
node["color"].setValue([0, 0, 0, 0.7])
for layer in list(VALUES) + ["RGBA_default"]:
    nuke.Layer(layer, [layer + "." + c for c in ("red", "green", "blue")])
    e = nuke.nodes.Expression(inputs=[node])
    for i, c in enumerate(("red", "green", "blue")):
        e["channel%d" % i].setValue("%s.%s" % (layer, c))
    e["channel3"].setValue("none")
    v = VALUES.get(layer, EXPECTED)
    for i in range(3):
        e["expr%d" % i].setValue(str(v))
    node = e

nuke.nodePaste(NK)
g = nuke.toNode("AOV_Beauty")
g.setInput(0, node)

fails = []


def val(ch="rgba.red"):
    return g.sample(ch, 4.5, 4.5)


def check(label, got, want, tol=1e-4):
    ok = abs(got - want) <= tol
    print("%-40s got %-9.5f want %-9.5f %s" % (label, got, want, "ok" if ok else "FAIL"))
    if not ok:
        fails.append(label)


check("rebuild (all AOVs)", val(), EXPECTED)
check("rebuild green == red", val("rgba.green"), EXPECTED)
check("alpha preserved (0.7)", val("rgba.alpha"), 0.7)

g["view"].setValue("difference (x gain)")
check("difference vs RGBA_default ~ 0", val(), 0.0)
g["view"].setValue("original (RGBA_default)")
check("original view", val(), EXPECTED)
g["view"].setValue("input untouched")
check("input untouched view (rgba=0)", val(), 0.0)
g["view"].setValue("rebuilt")

g["diffuse_direct_on"].setValue(False)
check("diffuse_direct off", val(), EXPECTED - 0.20)
g["diffuse_direct_on"].setValue(True)
check("albedo default is off", float(g["albedo_on"].value()), 0.0)
g["albedo_on"].setValue(True)
check("albedo on (multiply diffuse)", val(), EXPECTED - 0.30 * 0.50)
g["albedo_on"].setValue(False)
g["sss_on"].setValue(False)
check("sss off", val(), EXPECTED - 0.04)
g["sss_on"].setValue(True)
g["sss_mult"].setValue(2.0)
check("sss gain x2", val(), EXPECTED + 0.04)
g["sss_mult"].setValue(1.0)
g["emission_sat"].setValue(0.0)
check("emission sat 0 (grey stays same)", val(), EXPECTED)
g["master_gain"].setValue(2.0)
check("master gain x2", val(), EXPECTED * 2)
g["master_gain"].setValue(1.0)
g["diffuse_indirect_on"].setValue(False)
g["view"].setValue("difference (x gain)")
check("difference after disabling an AOV", val(), 0.10 * 10, 1e-3)

# a second paste (renamed instance) must keep its own links
nuke.nodePaste(NK)
g2 = nuke.toNode("AOV_Beauty1")
g2.setInput(0, node)
g2["sss_mult"].setValue(3.0)
check("renamed instance own link", g2.sample("rgba.red", 4.5, 4.5), EXPECTED + 0.08)
check("first instance unaffected", g["sss_mult"].value(), 1.0)

print("RESULT:", "FAIL " + ", ".join(fails) if fails else "ALL OK")
