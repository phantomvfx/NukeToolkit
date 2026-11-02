"""
SAM3Matte roto core: per-frame object mask -> a fixed-count closed shape, for "Make Roto".

Pure Python (stdlib only, no nuke), so it is testable outside Nuke. A mask is `data: bytes, w: int, h: int`,
row-major 8-bit grey, top-left origin. Points are (x, y) floats in mask pixels, top-left origin.

  largest_outline  outer boundary of the largest 4-connected region of pixels >= threshold (holes ignored)
  resample_closed  n points equally spaced by arc length along a closed polyline
  align_start      rotate a point list so point 0 follows the previous frame's point 0
  shape_for_frame  largest_outline -> resample_closed -> clockwise on screen -> align_start
  bezier_handles   per-point OUT tangent offset (next - prev) / 6; the in tangent is its negative
  to_plate         scale mask pixels to plate pixels and flip Y to Nuke's bottom-left origin

Regions are labelled by runs per row joined with a union-find (iterative, no recursion), then the outer
loop is traced along pixel edges and smoothed (circular moving average, window 5, twice). Lattice vertices
on the frame border are not smoothed, so a region cut by the frame keeps its straight edge and corners.

Target Nuke 16.1 (Python 3.11). Tested outside Nuke only (tools/comfy/tests/test_sam3matte_roto_core.py).
"""
import re

_RUN = re.compile(rb"\x01+")

# directions: 0 east, 1 south, 2 west, 3 north (y down, so +1 is a right turn on screen)
_DX = (1, 0, -1, 0)
_DY = (0, 1, 0, -1)
# for an edge leaving a vertex in direction d: the pixel on its right and on its left, relative to the vertex
_RIGHT = ((0, 0), (-1, 0), (-1, -1), (0, -1))
_LEFT = ((0, -1), (0, 0), (-1, 0), (-1, -1))


def _find(parent, i):
    while parent[i] != i:
        parent[i] = parent[parent[i]]
        i = parent[i]
    return i


def _largest_region_mask(data, w, h, threshold):
    """Return (padded 0/1 bytearray of the largest region, its area, (x, y) of its topmost-leftmost pixel)."""
    if threshold < 1:
        threshold = 1
    table = bytes(1 if v >= threshold else 0 for v in range(256))
    binary = data.translate(table)
    if b"\x01" not in binary:
        return None

    runs = []          # (y, x0, x1) with x1 exclusive, in row-major order
    parent = []
    prev = []          # indices of the previous row's runs
    for y in range(h):
        row = binary[y * w:(y + 1) * w]
        cur = []
        if b"\x01" in row:
            for m in _RUN.finditer(row):
                cur.append(len(runs))
                runs.append((y, m.start(), m.end()))
                parent.append(len(parent))
        i = j = 0
        while i < len(prev) and j < len(cur):
            a = runs[prev[i]]
            b = runs[cur[j]]
            if a[1] < b[2] and b[1] < a[2]:
                ra, rb = _find(parent, prev[i]), _find(parent, cur[j])
                if ra != rb:
                    parent[rb] = ra
            if a[2] <= b[2]:
                i += 1
            else:
                j += 1
        prev = cur

    area = {}
    first = {}
    for idx, (y, x0, x1) in enumerate(runs):
        r = _find(parent, idx)
        area[r] = area.get(r, 0) + (x1 - x0)
        first.setdefault(r, idx)
    best = max(area, key=area.get)   # ties: the first region found, i.e. the topmost-leftmost
    pw = w + 2
    mask = bytearray(pw * (h + 2))
    for idx, (y, x0, x1) in enumerate(runs):
        if _find(parent, idx) == best:
            base = (y + 1) * pw + 1
            mask[base + x0:base + x1] = b"\x01" * (x1 - x0)
    fy, fx0, _ = runs[first[best]]
    return mask, area[best], (fx0, fy)


def _smooth(pts, w, h):
    """Circular moving average, window 5, applied twice. Vertices on the frame border stay put."""
    m = len(pts)
    pinned = [p[0] == 0 or p[0] == w or p[1] == 0 or p[1] == h for p in pts]
    for _ in range(2):
        out = []
        for i in range(m):
            if pinned[i]:
                out.append(pts[i])
                continue
            sx = sy = 0.0
            for k in (-2, -1, 0, 1, 2):
                px, py = pts[(i + k) % m]
                sx += px
                sy += py
            out.append((sx / 5.0, sy / 5.0))
        pts = out
    return pts


def largest_outline(data, w, h, threshold=128, min_area=16):
    """Outer boundary (clockwise on screen) of the largest 4-connected region of pixels >= threshold.

    None if there is no such region or it has fewer than min_area pixels. Holes are ignored.
    The polyline has one vertex per unit pixel edge (collinear vertices included).
    """
    if len(data) != w * h:
        raise ValueError("mask data is %d bytes, expected %d x %d" % (len(data), w, h))
    found = _largest_region_mask(data, w, h, threshold)
    if found is None:
        return None
    mask, area, (sx, sy) = found
    if area < min_area:
        return None

    pw = w + 2

    def inside(px, py):
        return mask[(py + 1) * pw + px + 1]

    # start on the top edge of the topmost-leftmost pixel, region on the right-hand side (below it)
    x, y, d = sx, sy, 0
    pts = []
    while True:
        pts.append((x, y))
        x += _DX[d]
        y += _DY[d]
        if x == sx and y == sy:
            break
        for nd in ((d + 1) & 3, d, (d - 1) & 3):      # right, straight, left
            rx, ry = _RIGHT[nd]
            lx, ly = _LEFT[nd]
            if inside(x + rx, y + ry) and not inside(x + lx, y + ly):
                d = nd
                break
        else:
            raise RuntimeError("contour tracing lost the boundary at (%d, %d)" % (x, y))
    return [(float(px), float(py)) for px, py in _smooth(pts, w, h)]


def resample_closed(points, n):
    """n points equally spaced by arc length along the closed polyline, the first one at points[0]."""
    m = len(points)
    if m == 0 or n < 1:
        raise ValueError("resample_closed needs points and n >= 1")
    seg = []
    cum = []
    total = 0.0
    for i in range(m):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % m]
        d = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        cum.append(total)
        seg.append(d)
        total += d
    if total == 0.0:
        return [(float(points[0][0]), float(points[0][1]))] * n
    out = []
    i = 0
    for k in range(n):
        t = total * k / n
        while i < m - 1 and cum[i + 1] <= t:
            i += 1
        f = (t - cum[i]) / seg[i] if seg[i] > 0 else 0.0
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % m]
        out.append((x0 + (x1 - x0) * f, y0 + (y1 - y0) * f))
    return out


def _signed_area(points):
    s = 0.0
    m = len(points)
    for i in range(m):
        x0, y0 = points[i]
        x1, y1 = points[(i + 1) % m]
        s += x0 * y1 - x1 * y0
    return s / 2.0   # > 0 is clockwise on screen (y down)


def align_start(points, prev):
    """Rotate so points[0] is the point nearest prev[0]; prev None: start at the topmost (ties: leftmost) point."""
    if not points:
        return []
    if prev:
        tx, ty = prev[0]
        k = min(range(len(points)), key=lambda i: (points[i][0] - tx) ** 2 + (points[i][1] - ty) ** 2)
    else:
        k = min(range(len(points)), key=lambda i: (points[i][1], points[i][0]))
    return list(points[k:]) + list(points[:k])


def shape_for_frame(data, w, h, n, prev=None, threshold=128):
    """The n-point clockwise outline of the largest region, started near prev[0]; None if the frame is empty."""
    outline = largest_outline(data, w, h, threshold)
    if outline is None:
        return None
    pts = resample_closed(outline, n)
    if _signed_area(pts) < 0:
        pts.reverse()
    return align_start(pts, prev)


def bezier_handles(points):
    """Per-point OUT tangent offset, (next - prev) / 6. The in tangent is its negative."""
    m = len(points)
    out = []
    for i in range(m):
        px, py = points[(i - 1) % m]
        nx, ny = points[(i + 1) % m]
        out.append(((nx - px) / 6.0, (ny - py) / 6.0))
    return out


def to_plate(points, mask_w, mask_h, plate_w, plate_h):
    """Scale mask pixels to plate pixels and flip Y to Nuke's bottom-left origin."""
    sx = plate_w / mask_w
    sy = plate_h / mask_h
    return [(float(x * sx), float(plate_h - y * sy)) for x, y in points]
