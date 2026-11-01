"""
Flux3Klein: the ComfyUI side, without Nuke. Builds the caption and fills json/Flux3Klein.json, so it can be tested
from plain Python (L:\\ComfyUIClaude\\tools\\flux3klein_test.py) as well as used by flux3klein.py inside Nuke.

The captions and the masked-edit graph are adapted from AInVFX Paint a plate (web/logic.mjs caption(),
web/workflows/edit.json), Copyright AInVFX, Apache License 2.0. Modified: plain Python, crop coordinates, and a
replace caption for edits without strokes.
"""
import copy
import json
import math
import os

TEMPLATE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "json", "Flux3Klein.json")

COLOR_SPACES = {  # knob value -> (Load EXR color_space, Save EXR color_space)
    "ACEScg": ("Convert ACEScg to Linear sRGB", "Convert Linear sRGB to ACEScg"),
    "Linear Rec.709": ("Raw (Keep ACEScg)", "Raw (Keep Current)"),  # already linear sRGB primaries: no matrix
}
SEEDVR_MODELS = {"3B (fast)": "seedvr2_ema_3b-Q4_K_M.gguf", "7B sharp (slow)": "seedvr2_ema_7b_sharp_fp16.safetensors"}
SEEDVR_ABOVE = 1.25  # Auto upscale: SeedVR when the crop is this many times wider than the working size, else a resize
MAX_REFS = 4
QUICK_MP = 0.6  # Paint a plate's quick pass is 768 on the long side
REFINE_STEPS = 8
QUICK_REF_MP = 0.4


def caption(mode, words, bbox, strokes=False):
    """Klein's edit caption with its one-row object table. bbox: [y_min, x_min, y_max, x_max] on 0..1000 of the crop."""
    words = (words or "").strip()
    bbox = [int(v) for v in bbox]
    if mode == "erase":  # Klein's removal row: the object is in the reference (src_bbox) and nowhere in the result
        text = (f"Remove {f'the {words}' if words else 'the object'} <obj_1> completely, with its shadow and its "
                "reflection. Show the surface and the background that continue behind it, with matching detail, light, "
                "shadows and focus. Add no new object. Keep everything else unchanged.")
        row = {"id": "obj_1", "from": "ref_image_0", "src_bbox": bbox, "tgt_bbox": None, "desc": words or "the object to remove"}
        return f"{text} {json.dumps([row])}"
    elif strokes:
        text = (f"Replace the rough coloured drawing inside <obj_1> with {words or 'a realistic object of that shape and colour'}, "
                "in exactly the same style, lighting, perspective and grain as the rest of the image. Keep everything else unchanged.")
        desc = words or "the drawn object"
    else:
        text = (f"Replace what is inside <obj_1> with {words or 'what fits the scene'}, in exactly the same style, lighting, "
                "perspective and grain as the rest of the image. Keep everything else unchanged.")
        desc = words or "the new content"
    return f"{text} {json.dumps([{'id': 'obj_1', 'bbox': bbox, 'desc': desc}])}"


def use_seedvr(upscale, crop_w, crop_h, megapixels):
    if upscale == "SeedVR":
        return True
    if upscale != "Auto":
        return False
    return math.sqrt(crop_w * crop_h / (megapixels * 1e6)) > SEEDVR_ABOVE


def _by_title(workflow, title):
    for nid, node in workflow.items():
        if node.get("_meta", {}).get("title") == title:
            return nid
    raise KeyError(f"Flux3Klein.json has no node titled {title}")


def build(p, template=TEMPLATE):
    """Returns the API prompt for one edit. p: dict with
    source, guide (or None), refs (list of uploaded names), color, mode ("replace"/"erase"), words, bbox,
    seed, steps, megapixels, grow, upscale ("Auto"/"SeedVR"/"Resize"), seedvr_model, crop_w, crop_h, prefix,
    and optionally match_color ("Auto"/"On"/"Off") and grain."""
    with open(template, encoding="utf-8") as f:
        wf = json.load(f)
    wf = copy.deepcopy(wf)
    t = lambda title: wf[_by_title(wf, title)]
    load_cs, save_cs = COLOR_SPACES[p["color"]]

    t("NUKE_SOURCE")["inputs"].update(image_path=p["source"], color_space=load_cs)
    t("NUKE_WORKSIZE")["inputs"]["megapixels"] = float(p["megapixels"])
    t("NUKE_GROW")["inputs"]["expand"] = int(p["grow"])
    # Fit measures the colour match on the edge of what Klein regenerated. On the raw mask edge an erased object's own
    # colour leaks in (the brass plaque left a warm square, 31 Oct). Grow is in working pixels, Fit's in crop pixels.
    scale = math.sqrt(p["crop_w"] * p["crop_h"] / (float(p["megapixels"]) * 1e6))
    t("NUKE_FIT")["inputs"]["grow"] = max(8, round(int(p["grow"]) * scale))
    # Klein's own colour drifts a little just outside the edit; matching to it tinted erases (31 Oct), so Auto is replace only
    match = p.get("match_color", "Auto")
    t("NUKE_FIT")["inputs"]["match_color"] = match == "On" or (match == "Auto" and p["mode"] != "erase")
    t("NUKE_FIT")["inputs"]["grain"] = float(p.get("grain", 1.0))
    # Two Klein passes, as Paint a plate does: a quick one at about 0.6 MP (16 steps, the distilled model's own setting)
    # finds the edit, then a refine at the working size starts from it with a light denoise.
    quick_mp = min(QUICK_MP, float(p["megapixels"]))
    t("NUKE_QUICK_IMAGE")["inputs"]["megapixels"] = quick_mp
    t("NUKE_QUICK_GROW")["inputs"]["expand"] = max(4, round(int(p["grow"]) * math.sqrt(quick_mp / float(p["megapixels"]))))
    t("NUKE_QUICK_SAMPLER")["inputs"].update(seed=int(p["seed"]), steps=int(p["steps"]))
    t("NUKE_SAMPLER")["inputs"].update(seed=int(p["seed"]), steps=REFINE_STEPS, denoise=float(p.get("refine", 0.35)))
    t("NUKE_LEVEL")["inputs"]["enabled"] = p["mode"] == "erase"
    t("NUKE_PROMPT")["inputs"]["text"] = caption(p["mode"], p.get("words"), p["bbox"], strokes=bool(p.get("guide")))
    t("NUKE_OUTPUT")["inputs"].update(color_space=save_cs, filename_prefix=p["prefix"])

    refs_ids = (_by_title(wf, "NUKE_QUICK_REFS"), _by_title(wf, "NUKE_REFS"))
    guide_id, edit_id = _by_title(wf, "NUKE_GUIDE"), _by_title(wf, "NUKE_EDIT_IMAGE")
    edit_image = [edit_id, 0]
    if p.get("guide"):  # strokes over the source: the image Klein is shown and starts from
        wf[guide_id]["inputs"].update(image_path=p["guide"], color_space=load_cs)
    else:
        for nid in (guide_id, "21", edit_id):
            del wf[nid]
        edit_image = [_by_title(wf, "NUKE_WORKSIZE"), 0]
        t("NUKE_QUICK_IMAGE")["inputs"]["image"] = edit_image
    # the refine is shown the edit image, except for an erase: shown the original, Klein draws part of the object back
    # (Paint a plate, and the grey ghost plaque here, 31 Oct), so it is shown the quick pass, which has no object left
    wf[refs_ids[1]]["inputs"]["images.image0"] = [_by_title(wf, "NUKE_START_IMAGE"), 0] if p["mode"] == "erase" else edit_image

    # each ref at about 1 MP for the refine and 0.4 MP for the quick pass, whatever its size: 4 refs at 1 MP in both
    # passes took 228 s on the 3060 (31 Oct)
    for i, name in enumerate(p.get("refs", [])[:MAX_REFS], start=1):
        load = f"6{i}"
        wf[load] = {"class_type": "LoadImage", "inputs": {"image": name}, "_meta": {"title": f"NUKE_REF{i}"}}
        for rid, scale, mp in ((refs_ids[0], f"5{i}", QUICK_REF_MP), (refs_ids[1], f"7{i}", 1.0)):
            wf[scale] = {"class_type": "ImageScaleToTotalPixels",
                         "inputs": {"image": [load, 0], "upscale_method": "lanczos", "megapixels": mp, "resolution_steps": 16},
                         "_meta": {"title": f"Ref {i} at {mp} MP"}}
            wf[rid]["inputs"][f"images.image{i}"] = [scale, 0]

    seedvr_id = _by_title(wf, "NUKE_SEEDVR")
    if use_seedvr(p["upscale"], p["crop_w"], p["crop_h"], p["megapixels"]):
        short = min(p["crop_w"], p["crop_h"])
        wf[seedvr_id]["inputs"].update(resolution=short + short % 2, seed=int(p["seed"]))
        t("NUKE_SEEDVR_DIT")["inputs"]["model"] = SEEDVR_MODELS.get(p.get("seedvr_model"), SEEDVR_MODELS["3B (fast)"])
        if "7b" in t("NUKE_SEEDVR_DIT")["inputs"]["model"]:  # 16 GB of weights on a 12 GB card
            t("NUKE_SEEDVR_DIT")["inputs"].update(blocks_to_swap=36, swap_io_components=True)
    else:  # Fit to Plate resizes the working-size edit back to the crop
        level_id = _by_title(wf, "NUKE_LEVEL")
        for nid in (seedvr_id, _by_title(wf, "NUKE_SEEDVR_DIT"), "41"):
            del wf[nid]
        t("NUKE_TO_LINEAR")["inputs"]["image"] = [level_id, 0]
    return wf
