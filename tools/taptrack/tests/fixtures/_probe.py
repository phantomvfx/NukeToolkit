"""Probe Nuke's Transform convention: image whose R,G = pixel x,y; after the node, sampling at
destination pixel d returns the SOURCE coordinate that landed there, so we can fit the inverse map."""
import json
import sys

import nuke

OUT = {}


def make_source(fmt="HD_1080"):
    nuke.scriptClear()
    nuke.root()["format"].setValue(fmt)
    c = nuke.nodes.Constant()
    e = nuke.nodes.Expression(expr0="x", expr1="y", expr2="0", expr3="1")
    e.setInput(0, c)
    return e


def inverse_affine(node, pts=None, _old=((300.5, 200.5), (900.5, 300.5), (500.5, 800.5), (1500.5, 700.5), (100.5, 100.5), (1700.5, 900.5))):
    """Least-squares affine dst->src from samples; returns (2x3 as list, max residual)."""
    cx, cy = node["center"].value()
    pts = [(cx + a + 0.5, cy + b + 0.5) for a, b in ((-120, -80), (110, -90), (-100, 100), (130, 90), (0, 0), (60, -30), (-40, 60))]
    rows, rhs_x, rhs_y = [], [], []
    for dx, dy in pts:
        sx = node.sample("rgba.red", dx, dy)
        sy = node.sample("rgba.green", dx, dy)
        rows.append([dx, dy, 1.0])
        rhs_x.append(sx)
        rhs_y.append(sy)
    # solve with plain normal equations (no numpy needed)
    def solve(rhs):
        ata = [[sum(r[i] * r[j] for r in rows) for j in range(3)] for i in range(3)]
        atb = [sum(r[i] * b for r, b in zip(rows, rhs)) for i in range(3)]
        # gaussian elimination
        m = [ata[i] + [atb[i]] for i in range(3)]
        for i in range(3):
            p = max(range(i, 3), key=lambda k: abs(m[k][i]))
            m[i], m[p] = m[p], m[i]
            for k in range(i + 1, 3):
                f = m[k][i] / m[i][i]
                m[k] = [a - f * b for a, b in zip(m[k], m[i])]
        x = [0.0] * 3
        for i in (2, 1, 0):
            x[i] = (m[i][3] - sum(m[i][j] * x[j] for j in range(i + 1, 3))) / m[i][i]
        return x
    ax, ay = solve(rhs_x), solve(rhs_y)
    res = max(abs(ax[0] * dx + ax[1] * dy + ax[2] - sx) + abs(ay[0] * dx + ay[1] * dy + ay[2] - sy)
              for (dx, dy), sx, sy in zip(pts, rhs_x, rhs_y))
    return [[round(v, 5) for v in ax], [round(v, 5) for v in ay]], round(res, 5)


def invert3(m):
    (a, b, c), (d, e, f) = m
    det = a * e - b * d
    ia, ib, id_, ie = e / det, -b / det, -d / det, a / det
    return [[ia, ib, -(ia * c + ib * f)], [id_, ie, -(id_ * c + ie * f)]]


def probe(label, fmt="HD_1080", setup=None):
    src = make_source(fmt)
    t = nuke.nodes.Transform()
    t.setInput(0, src)
    t["filter"].setValue("cubic")
    if setup:
        setup(t)
    inv, res = inverse_affine(t)
    inv[0][2] += 0.5   # expression x is the pixel index; continuous coords = index + 0.5
    inv[1][2] += 0.5
    # src_cont = A d_idx_cont ... convert: samples taken at d_cont, outputs are src index -> +0.5 gives src_cont
    fwd = invert3(inv)
    print(label, "FWD", [[round(v, 4) for v in r] for r in fwd], "res", res, flush=True)


def k(name, *vals):
    def f(t):
        for n, v in zip(name, vals):
            pass
    return f


def setter(**kw):
    def f(t):
        for n, v in kw.items():
            if isinstance(v, (list, tuple)):
                for i, x in enumerate(v):
                    t[n].setValue(x, i)
            else:
                t[n].setValue(v)
    return f


def corner_probe(fmt, frm, to):
    src = make_source(fmt)
    c = nuke.nodes.CornerPin2D()
    c.setInput(0, src)
    c["filter"].setValue("cubic")
    for i in range(4):
        c["from%d" % (i + 1)].setValue(frm[i][0], 0)
        c["from%d" % (i + 1)].setValue(frm[i][1], 1)
        c["to%d" % (i + 1)].setValue(to[i][0], 0)
        c["to%d" % (i + 1)].setValue(to[i][1], 1)
    worst = 0
    for i in range(4):
        # sample slightly inside the destination corner, towards the quad centre (outside the quad the image is black)
        cx = sum(p[0] for p in to) / 4; cy = sum(p[1] for p in to) / 4
        dx, dy = to[i][0] + (cx - to[i][0]) * 0.02, to[i][1] + (cy - to[i][1]) * 0.02
        sx = c.sample("rgba.red", dx, dy) + 0.5; sy = c.sample("rgba.green", dx, dy) + 0.5
        # expected src: from_i moved 2% towards its own centre (homography is not linear, so just report)
        print("  corner", i + 1, "dest", (round(dx, 1), round(dy, 1)), "src", (round(sx, 1), round(sy, 1)), "from", frm[i])


frm = [(200, 150), (800, 150), (800, 450), (200, 450)]
to = [(230, 170), (790, 140), (770, 470), (210, 440)]
print("HD pa=1 CornerPin2D (dest slightly inside to_i should read src ~ from_i):")
corner_probe("HD_1080", frm, to)
nuke.addFormat("1000 500 0 0 1000 500 2 anam2")
print("anam pa=2:")
corner_probe("anam2", [(100, 100), (400, 100), (400, 300), (100, 300)], [(120, 110), (390, 90), (380, 320), (110, 290)])
