"""SAM3Matte key-frame hints (pure Python, no nuke import).

What it does: turns the artist's Select / Stencil alpha on the key frame into
data the ComfyUI graph can use. A sampled grid becomes include/exclude click
points, and detected object masks are dropped when the Stencil covers them.

Grid convention (shared with sam3matte_png.to_grid): list[list[float]], row 0
is the TOP of the image, values 0..1. Only `sample_grid` talks to Nuke's
y-up coordinates; the flip happens there and nowhere else. Points are pixels
with a top-left origin.

Target: Nuke 16.1 (Python 3.11). Tested: pytest only (tools/comfy/tests/
test_sam3matte_hints.py); not run inside Nuke.
"""

GRID_COLS = 48
MAX_POINTS = 8


def grid_size(width, height, cols=GRID_COLS):
    """(cols, rows) for an image, rows following the aspect."""
    return cols, max(1, round(cols * height / width))


def sample_grid(sampler, width, height, cols, rows):
    """Sample `sampler(x, y)` at every cell centre. x, y are in Nuke
    coordinates (y up); the returned grid has row 0 at the top."""
    values = []
    for r in range(rows):
        y = height - (r + 0.5) * height / rows
        values.append([float(sampler((c + 0.5) * width / cols, y)) for c in range(cols)])
    return values


def hint_points(values, width, height, cap=MAX_POINTS, threshold=0.5):
    """Up to `cap` well-spread pixel points inside the cells >= threshold."""
    rows = len(values)
    cols = len(values[0]) if rows else 0
    inside = {(r, c) for r in range(rows) for c in range(cols) if values[r][c] >= threshold}
    if not inside or cap < 1:
        return []
    eroded = {(r, c) for (r, c) in inside
              if (r - 1, c) in inside and (r + 1, c) in inside
              and (r, c - 1) in inside and (r, c + 1) in inside}
    pool = sorted(eroded or inside)

    cy = sum(r for r, _ in pool) / len(pool)
    cx = sum(c for _, c in pool) / len(pool)
    chosen = [min(pool, key=lambda p: (p[0] - cy) ** 2 + (p[1] - cx) ** 2)]
    while len(chosen) < min(cap, len(pool)):
        chosen.append(max(
            (p for p in pool if p not in chosen),
            key=lambda p: min((p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 for q in chosen)))

    return [{"x": int(round((c + 0.5) * width / cols)),
             "y": int(round((r + 0.5) * height / rows))} for r, c in chosen]


def fraction_inside(mask, stencil):
    """Fraction of mask cells >= .5 that are also >= .5 in stencil."""
    total = hit = 0
    for mrow, srow in zip(mask, stencil):
        for m, s in zip(mrow, srow):
            if m >= 0.5:
                total += 1
                if s >= 0.5:
                    hit += 1
    return hit / total if total else 0.0


def drop_inside_stencil(masks, stencil, max_fraction=0.5):
    """Indices of `masks` to keep; dropped only when more than max_fraction
    of the mask lies inside the stencil."""
    return [i for i, m in enumerate(masks) if fraction_inside(m, stencil) <= max_fraction]
