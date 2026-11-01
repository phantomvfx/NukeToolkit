"""
Flux3Klein: masked paint / erase of one frame through ComfyUI (FLUX 3 Image Klein, json/Flux3Klein.json).

Generate button on the Flux3Klein group:
  1. finds the bounds of the edit mask (Mask channel, plus the Strokes alpha) on the current frame and pads them
     with context, snapped to multiples of 16 inside the format (or takes the whole frame)
  2. renders that crop of Source as a half-float EXR in the script's working space, untouched (raw), with the edit
     mask in alpha; the Strokes merged over Source as a second EXR; each connected Ref as a PNG
  3. uploads them, fills the graph (flux3klein_graph.build), queues and polls
  4. downloads the EXR patch (same size as the crop, colour matched and grained to the plate by Fit to Plate) and
     adds it as a new version; the group places it back and keymixes it over Source through the grown + feathered mask.

Targets Nuke 16 (Python 3.11 / PySide6).
"""
import json
import math
import os
import urllib.request
from datetime import datetime

import nuke

from NukeComfy.python.api import api_client
from NukeComfy.python.bridge import bridge
from NukeComfy.python.flux3klein_graph import build
from NukeComfy.python.inpaint import _run_on_main_thread
from NukeComfy.python.polling import start_comfy_process

SNAP = 16
REFS = ("Ref1", "Ref2", "Ref3", "Ref4")  # group inputs 3..6
FIRST_REF_INPUT = 3


def _check_server():
    try:
        urllib.request.urlopen(f"{api_client.base_url}/system_stats", timeout=5).close()
    except Exception as e:
        raise RuntimeError(f"ComfyUI is not reachable at {api_client.base_url} ({e}). Start it, or check COMFY_URL.")


def _temp_folder():
    # NUKECOMFY_TEMP wins, so an unsaved script does not write its renders to the home folder
    folder = os.environ.get("NUKECOMFY_TEMP") or bridge._resolve_temp_folder()
    os.makedirs(folder, exist_ok=True)
    return folder.replace("\\", "/")


def color_space(node):
    """The plate's primaries: the knob, or for Auto the script's working space."""
    choice = node["color"].value()
    if choice != "Auto":
        return choice
    root = nuke.root()
    if root.knob("colorManagement") and root["colorManagement"].value() == "OCIO":
        knob = root["workingSpaceLUT"]
        # the value is often a role ('scene_linear'); its menu item names the colourspace: "scene_linear\tscene_linear (ACEScg)"
        items = [v for v in knob.values() if v.split("\t")[0] == knob.value()]
        working = (items[0] if items else knob.value()).lower()
        if "aces2065" in working or "ap0" in working:
            raise RuntimeError(f"Working space '{working}' (AP0) is not supported: pick ACEScg or Linear Rec.709")
        if "acescg" in working or "ap1" in working:
            return "ACEScg"
    return "Linear Rec.709"  # Nuke's own colour management and plain 'linear' are Rec.709 primaries


def mask_bounds(group, frame):
    """Pixel bounds (x, y, r, t) of the edit mask's alpha on `frame`, in Nuke coordinates, or None when it is empty."""
    with group:
        curve = nuke.nodes.CurveTool(operation="Auto Crop", channels="alpha")
        curve.setInput(0, group.node("EditMask"))
        fmt = group.node("Source").format()
        curve["ROI"].setValue([0, 0, fmt.width(), fmt.height()])  # created from Python it is 0 x 0, and finds nothing
        try:
            nuke.execute(curve, frame, frame)
            box = [curve["autocropdata"].valueAt(frame, i) for i in range(4)]
        finally:
            nuke.delete(curve)
    x, y, r, t = (int(math.floor(box[0])), int(math.floor(box[1])), int(math.ceil(box[2])), int(math.ceil(box[3])))
    return None if r - x < 1 or t - y < 1 else (x, y, r, t)


def _snap_span(lo, hi, size, limit):
    """[lo, hi) grown to a multiple of `size` (at most `limit` wide) and shifted to stay inside [0, limit)."""
    width = min(limit // size * size, int(math.ceil((hi - lo) / size)) * size)
    lo = (lo + hi - width) // 2
    lo = max(0, min(lo, limit - width))
    return lo, lo + width


def crop_box(bounds, fmt_w, fmt_h, context, margin):
    """The crop sent to ComfyUI: the mask bounds padded by `context` % of their larger side (at least `margin` px)."""
    x, y, r, t = bounds
    pad = max(margin, int(max(r - x, t - y) * context / 100.0))
    x0, x1 = _snap_span(max(0, x - pad), min(fmt_w, r + pad), SNAP, fmt_w)
    y0, y1 = _snap_span(max(0, y - pad), min(fmt_h, t + pad), SNAP, fmt_h)
    return x0, y0, x1, y1


def caption_bbox(bounds, box):
    """Mask bounds as Klein's [y_min, x_min, y_max, x_max] on 0..1000 of the crop (top-left origin)."""
    x, y, r, t = bounds
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    clamp = lambda v: max(0, min(1000, v))
    return [clamp(int((y1 - t) / h * 1000)), clamp(int((x - x0) / w * 1000)),
            clamp(int(math.ceil((y1 - y) / h * 1000))), clamp(int(math.ceil((r - x0) / w * 1000)))]


def _render(group, upstream, path, frame, exr):
    with group:
        write = nuke.nodes.Write(file=path, raw=True, create_directories=True)
        write.setInput(0, group.node(upstream))
        if exr:  # working-space values as they are, mask in alpha
            write["file_type"].setValue("exr")
            write["channels"].setValue("rgba")
            write["datatype"].setValue("16 bit half")
            write["compression"].setValue("Zip (1 scanline)")
        else:  # a reference: what the artist sees, 8-bit
            write["file_type"].setValue("png")
            write["channels"].setValue("rgb")
            write["raw"].setValue(False)
        try:
            nuke.execute(write, frame, frame)
        finally:
            nuke.delete(write)


# ---------- versions: every result is kept; the Version pulldown picks the one shown ----------

def _versions(node):
    try:
        return json.loads(node["versions_data"].value() or "[]")
    except ValueError:
        return []


def show_version(node, index):
    versions = _versions(node)
    if not versions:
        return
    index = max(0, min(index, len(versions) - 1))
    v = versions[index]
    node.node("Result")["file"].setValue(v["file"])
    node["patch_x"].setValue(v["x"])
    node["patch_y"].setValue(v["y"])
    node["patch_frame"].setValue(v["frame"])  # the comp mask is held at the frame the patch was made from
    node["has_result"].setValue(True)


def _add_version(node, entry):
    versions = _versions(node) + [entry]
    node["versions_data"].setValue(json.dumps(versions))
    # "/" in a pulldown item makes a submenu
    labels = [f"v{i + 1}  {v['mode']}  seed {v['seed']}  {v['words'][:28]}".replace("/", "-") for i, v in enumerate(versions)]
    node["version"].setValues(labels)
    node["version"].setValue(len(versions) - 1)
    show_version(node, len(versions) - 1)


def sync_inputs(node):
    # with no Mask connected, picking a channel from it errors ("Missing input channel"): strokes alone are the mask
    sel = node.node("MaskSel")
    if sel is not None:  # None while the group is still being pasted
        sel["disable"].setValue(node.input(1) is None)


def knob_changed(node, knob):
    if knob.name() == "version":
        show_version(node, int(knob.getValue()))
    elif knob.name() == "inputChange":
        try:
            sync_inputs(node)
        except ValueError:  # "not attached to a node" during paste
            pass


# ---------- Generate ----------

def _make_patcher(params, files):
    def patch(workflow, node, input_files):
        uploaded = {}
        for key, path in files.items():
            name = api_client.upload_image(path, subfolder="nukecomfy")
            if not name:
                raise RuntimeError(f"Could not upload {path} to ComfyUI")
            uploaded[key] = name
        p = dict(params, source=uploaded["source"], guide=uploaded.get("guide"),
                 refs=[uploaded[r] for r in REFS if r in uploaded])
        return build(p)
    return patch


def _make_integrator(params, save_path):
    def integrate(node, output_data):
        def fail(message):
            _run_on_main_thread(lambda: node["status"].setValue(f"Error: {message}"))
        try:
            exrs = [img for out in output_data.values() for img in out.get("images", []) if img["filename"].endswith(".exr")]
            if not exrs:
                return fail("ComfyUI returned no EXR")
            info = exrs[0]
            if not api_client.download_output(info["filename"], info.get("subfolder", ""), info.get("type", "output"), save_path):
                return fail("Failed to download the result from ComfyUI")

            def apply():
                _add_version(node, {"file": save_path, "x": params["crop_x"], "y": params["crop_y"], "frame": params["frame"],
                                    "mode": params["mode"],
                                    "seed": params["seed"], "words": params["words"]})
                node["show_result"].setValue(True)
                node["status"].setValue(f"Done ({params['crop_w']}x{params['crop_h']}{', SeedVR' if params['seedvr'] else ''})")
            _run_on_main_thread(apply)
        except Exception as e:
            fail(str(e))
    return integrate


def prepare(node):
    """Renders the inputs of the current frame. Returns (params, files, result_path). Main thread."""
    if not node.input(0):
        raise RuntimeError("Connect a Source image")
    use_strokes = bool(node["use_strokes"].value()) and node.input(2) is not None
    if not node.input(1) and not use_strokes:
        raise RuntimeError("Connect a Mask (Roto, a keyer...), or Strokes with 'Use strokes' on")
    sync_inputs(node)

    frame = int(nuke.frame())
    fmt = node.node("Source").format()
    fw, fh = fmt.width(), fmt.height()
    bounds = mask_bounds(node, frame)
    if not bounds:
        raise RuntimeError(f"The mask is empty on frame {frame}")
    grow, feather = int(node["comp_grow"].value()), int(node["feather"].value())
    if node["region"].value() == "Full frame":
        box = (0, 0, fw, fh)
    else:
        box = crop_box(bounds, fw, fh, float(node["context"].value()), 64 + grow + feather)
    x0, y0, x1, y1 = box
    crop = node.node("CropBox")
    crop["box"].setValue([x0, y0, x1, y1])

    folder = _temp_folder()
    base = f"{node.name()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    files = {"source": f"{folder}/{base}_source.exr"}
    _render(node, "SendCrop", files["source"], frame, exr=True)
    if use_strokes:
        files["guide"] = f"{folder}/{base}_guide.exr"
        _render(node, "GuideCrop", files["guide"], frame, exr=True)
    for i, name in enumerate(REFS):
        if node.input(FIRST_REF_INPUT + i):
            files[name] = f"{folder}/{base}_{name.lower()}.png"
            _render(node, name, files[name], frame, exr=False)

    from NukeComfy.python.flux3klein_graph import use_seedvr
    params = {
        "color": color_space(node),
        "mode": "erase" if node["mode"].value() == "Erase" else "replace",
        "words": node["prompt_pos"].value().strip(),
        "bbox": caption_bbox(bounds, box),
        "seed": int(node["seed"].value()),
        "steps": int(node["steps"].value()),
        "megapixels": float(node["megapixels"].value()),
        "refine": float(node["refine"].value()),
        "grow": int(node["edit_grow"].value()),
        "upscale": node["upscale"].value(),
        "seedvr_model": node["seedvr_model"].value(),
        "match_color": node["match_color"].value(),
        "grain": float(node["grain"].value()),
        "crop_x": x0, "crop_y": y0, "crop_w": x1 - x0, "crop_h": y1 - y0, "frame": frame,
        "prefix": f"NukeComfy/{node.name()}",
    }
    params["seedvr"] = use_seedvr(params["upscale"], params["crop_w"], params["crop_h"], params["megapixels"])
    return params, files, f"{folder}/{base}_result.exr"


def generate(node):
    """Entry point of the Generate button. Returns True once the job has been queued."""
    try:
        _check_server()
        node["status"].setValue("Rendering...")
        params, files, result_path = prepare(node)
        node["status"].setValue(f"Generating {params['crop_w']}x{params['crop_h']}...")
        start_comfy_process(node, {i: p for i, p in enumerate(files.values())},
                            patcher=_make_patcher(params, files),
                            integrator=_make_integrator(params, result_path))
        return True
    except Exception as e:
        node["status"].setValue(f"Error: {e}")
        nuke.message(f"Flux3Klein: {e}")
        return False
