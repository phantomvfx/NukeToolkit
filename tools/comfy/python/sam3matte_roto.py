"""SAM3Matte Make Roto: turn the current version's saved masks into one animated Roto node.

What it does: for every object picked by the Objects knob it builds one Bezier shape in a new Roto node placed
next to the SAM3Matte Group. The shape has a fixed number of points (knob roto_points) and a key every roto_step
frames from the object's first to its last frame (the last frame is always keyed). The outline of the largest
region of each key frame's mask is traced and resampled by sam3matte_roto_core, with point 0 kept near the
previous key's point 0 so the points do not swim. Frames where the mask is empty key the shape's opacity to 0.
No ComfyUI is needed: it reads the PNGs in the version folder.

Inputs:  the Group (its Source input gives the plate format; knobs objects, roto_points, roto_step, version,
         versions_data). Masks are at tracking resolution and are mapped to the plate format, not the mask size.
Output:  one Roto node (name <group>_Roto, format = the plate format, no input connected), returned.
         Status text on the Group: "Roto: <n> shapes, <points> points, <keys> keys in <s> s", where points is the
         total control points over all shapes and keys the total keyed frames over all shapes.
Winding: every shape is clockwise as seen in the viewer (shoelace area negative in Nuke's y-up coordinates).
Handles: bezier_handles gives the out tangent in mask space; here it is scaled to plate pixels with dy negated,
         right tangent = out, left tangent = -out.

Called by the Group's Make Roto button: NukeComfy.python.sam3matte_roto.make_roto(nuke.thisNode()).
Python 3.11, stdlib + nuke only. Target Nuke 16.1.
Tested: headless in Nuke 16.1 (tools/comfy/tests/nuke/test_sam3matte_roto.py); not tried in a GUI session.
"""
import json
import re
import struct
import time

import nuke
import nuke.rotopaint as rp

try:
    from . import sam3matte_png as png
    from . import sam3matte_roto_core as core
    from . import sam3matte_versions as versions
except ImportError:
    import sam3matte_png as png
    import sam3matte_roto_core as core
    import sam3matte_versions as versions

TRACE_WIDTH = 512        # masks are shrunk to at most this wide before tracing


def _set_status(group, text):
    try:
        group["status"].setValue(text)
    except Exception:
        pass
    print("SAM3Matte " + text)


def _current_version(group):
    """The version picked in the pulldown (0-based position into versions_data), or None."""
    try:
        data = json.loads(group["versions_data"].value() or "[]")
    except ValueError:
        return None
    if not isinstance(data, list) or not data:
        return None
    try:
        idx = int(group["version"].getValue())
    except Exception:
        idx = len(data) - 1
    if idx < 0 or idx >= len(data):
        idx = len(data) - 1
    return data[idx]


def _plate_format(group):
    """(width, height, format name) of the Group's Source input, else the script format."""
    inp = None
    try:
        inp = group.input(0)
    except Exception:
        pass
    f = inp.format() if inp is not None else nuke.root().format()
    name = f.name()
    if not name:
        name = nuke.addFormat("%d %d %s sam3matte_plate" % (f.width(), f.height(), f.pixelAspect()))
    return f.width(), f.height(), name


def _png_size(path):
    """Width and height from the PNG header (None if unreadable)."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n":
            return None
        return struct.unpack(">II", head[16:24])
    except OSError:
        return None


def _key_frames(first, last, step):
    frames = list(range(first, last + 1, step))
    if not frames or frames[-1] != last:
        frames.append(last)
    return frames


def _shape_name(obj, used):
    base = re.sub(r"[^A-Za-z0-9_]", "_", str(obj.get("name") or "")) or "obj%d" % obj["index"]
    if base[0].isdigit():
        base = "obj_" + base
    name, n = base, 2
    while name in used:
        name = "%s_%d" % (base, n)
        n += 1
    used.add(name)
    return name


def _write_opacity(shape, flags):
    """flags: [(frame, on)] ascending. Opacity 1 where on, 0 where off, with a one-frame ramp at each change."""
    attrs = shape.getAttributes()
    op = attrs.kOpacityAttribute
    if not flags:
        return
    prev_frame, prev_on = None, None
    for frame, on in flags:
        if prev_on is None:
            if on:
                attrs.addKey(float(frame - 1), op, 0.0)      # off just before the first key
        elif on != prev_on and frame - 1 > prev_frame:
            attrs.addKey(float(frame - 1), op, 1.0 if prev_on else 0.0)   # hold, then change within one frame
        attrs.addKey(float(frame), op, 1.0 if on else 0.0)
        prev_frame, prev_on = frame, on
    if prev_on:
        attrs.addKey(float(prev_frame + 1), op, 0.0)         # off after the last key


def make_roto(group):
    """Build the Roto node for the Group's current version. Returns the Roto, or None when there is nothing to build."""
    t0 = time.time()
    v = _current_version(group)
    if v is None:
        _set_status(group, "Roto: no version to build from. Generate first.")
        return None
    objects = v.get("objects") or []
    slots = versions.parse_objects(group["objects"].value(), objects)
    if not slots:
        _set_status(group, "Roto: no object selected (Objects knob: %r)." % group["objects"].value())
        return None
    by_index = {o["index"]: o for o in objects}
    folder = str(v.get("folder", "")).replace("\\", "/")
    n_pts = max(3, int(group["roto_points"].value()))
    step = max(1, int(group["roto_step"].value()))
    plate_w, plate_h, fmt_name = _plate_format(group)

    plan = [(s, _key_frames(int(by_index[s]["first"]), int(by_index[s]["last"]), step)) for s in slots]
    total = sum(len(fr) for _, fr in plan)

    # mask size in the PNGs, and the factor read_mask shrinks them by
    mask_size = None
    for s, fr in plan:
        for f in fr:
            mask_size = _png_size("%s/obj%d.%04d.png" % (folder, s, f))
            if mask_size:
                break
        if mask_size:
            break
    if mask_size is None:
        mask_size = (int(v.get("width") or plate_w), int(v.get("height") or plate_h))
    shrink = -(-mask_size[0] // TRACE_WIDTH) if mask_size[0] > TRACE_WIDTH else 1
    map_w, map_h = mask_size[0] / float(shrink), mask_size[1] / float(shrink)
    sx, sy = plate_w / map_w, plate_h / map_h

    with group.parent():
        roto = nuke.nodes.Roto(name="%s_Roto" % group.name())
        roto["format"].setValue(fmt_name)
        roto.setXYpos(group.xpos() + 110, group.ypos())
    ck = roto["curves"]
    layer = ck.rootLayer

    n_shapes = n_keys = done = 0
    used = set()
    cancelled_at = None
    task = nuke.ProgressTask("SAM3Matte: Make Roto")
    try:
        for slot, frames in plan:
            obj = by_index[slot]
            shape = rp.Shape(ck, type="bezier")
            shape.name = _shape_name(obj, used)
            for _ in range(n_pts):
                shape.append(rp.ShapeControlPoint())
            layer.append(shape)
            n_shapes += 1
            prev = None                  # last traced points, mask space
            held = None                  # last written (plate points, plate out-handles)
            pending = []                 # key frames before the first non-empty mask
            flags = []

            def write(frame, plate_pts, handles):
                f = float(frame)
                for i in range(n_pts):
                    cp = shape[i]
                    cp.center.addPositionKey(f, plate_pts[i])
                    cp.rightTangent.addPositionKey(f, handles[i])
                    cp.leftTangent.addPositionKey(f, (-handles[i][0], -handles[i][1]))

            for frame in frames:
                if task.isCancelled():
                    cancelled_at = frame
                    break
                task.setProgress(int(100 * done / max(1, total)))
                task.setMessage("%s: frame %d" % (obj.get("name", slot), frame))
                done += 1
                pts = None
                try:
                    mw, mh, data = png.read_mask("%s/obj%d.%04d.png" % (folder, slot, frame), TRACE_WIDTH)
                    pts = core.shape_for_frame(data, mw, mh, n_pts, prev)
                except (OSError, ValueError):
                    pts = None           # missing or unreadable file counts as an empty mask
                if pts is not None:
                    prev = pts
                    plate_pts = core.to_plate(pts, map_w, map_h, plate_w, plate_h)
                    handles = [(hx * sx, -hy * sy) for hx, hy in core.bezier_handles(pts)]
                    held = (plate_pts, handles)
                    for pf in pending:
                        write(pf, plate_pts, handles)
                        n_keys += 1
                    pending = []
                    write(frame, plate_pts, handles)
                    n_keys += 1
                elif held is not None:
                    write(frame, held[0], held[1])   # hold the pose while the shape is off
                    n_keys += 1
                else:
                    pending.append(frame)
                flags.append((frame, pts is not None))
            _write_opacity(shape, flags)
            if cancelled_at is not None:
                break
        ck.changed()
    finally:
        del task

    if cancelled_at is not None:
        _set_status(group, "Roto cancelled at frame %d" % cancelled_at)
    else:
        _set_status(group, "Roto: %d shapes, %d points, %d keys in %.1f s" % (
            n_shapes, n_shapes * n_pts, n_keys, time.time() - t0))
    return roto
