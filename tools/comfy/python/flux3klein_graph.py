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
UPSAMPLER_PROMPTS = os.path.join(os.path.dirname(TEMPLATE), "Flux3Klein_upsampler.json")

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

# BFL's prompt upsampler (flux-3-image-klein-pup, Qwen3.5 9B, Apache-2.0) in Comfy-Org's w4a8 file, run by core
# TextGenerateV2. Its i2i_bbox mode takes the mask box as a locked row and writes Klein's edit record around it.
UPSAMPLER_MODEL = "qwen3.5_9b_flux3_prompt_upsampler_w4a8.safetensors"
UPSAMPLER_MP = {1: 1.0, 2: 0.52, 3: 0.26}  # what BFL shows it: 1 MP for one image, 724^2 for two, 512^2 for 3 or more
LOCKED = ("LOCKED BOUNDING-BOX ANNOTATIONS ([y_min, x_min, y_max, x_max] on a 0-1000 canvas, with (0, 0) at the "
          "top-left). This is an authoritative JSON list of dictionaries; preserve every supplied identity, provenance "
          "value, null, and coordinate exactly:")


def caption_parts(mode, words, bbox, strokes=False):
    """(instruction, row) of Klein's edit caption. bbox: [y_min, x_min, y_max, x_max] on 0..1000 of the crop."""
    words = (words or "").strip()
    bbox = [int(v) for v in bbox]
    if mode == "erase":  # Klein's removal row: the object is in the reference (src_bbox) and nowhere in the result
        text = (f"Remove {f'the {words}' if words else 'the object'} <obj_1> completely, with its shadow and its "
                "reflection. Show the surface and the background that continue behind it, with matching detail, light, "
                "shadows and focus. Add no new object. Keep everything else unchanged.")
        return text, {"id": "obj_1", "from": "ref_image_0", "src_bbox": bbox, "tgt_bbox": None, "desc": words or "the object to remove"}
    elif strokes:
        text = (f"Replace the rough coloured drawing inside <obj_1> with {words or 'a realistic object of that shape and colour'}, "
                "in exactly the same style, lighting, perspective and grain as the rest of the image. Keep everything else unchanged.")
        desc = words or "the drawn object"
    else:
        text = (f"Replace what is inside <obj_1> with {words or 'what fits the scene'}, in exactly the same style, lighting, "
                "perspective and grain as the rest of the image. Keep everything else unchanged.")
        desc = words or "the new content"
    return text, {"id": "obj_1", "bbox": bbox, "desc": desc}


def caption(mode, words, bbox, strokes=False):
    """Klein's edit caption with its one-row object table."""
    text, row = caption_parts(mode, words, bbox, strokes)
    return f"{text} {json.dumps([row])}"


def upsampler_request(mode, words, bbox, strokes=False):
    """The user text for the upsampler's i2i_bbox mode (BFL flux3/pup.py I2I_SCAFFOLD + flux3/bbox.py
    upsampling_input). The mask box goes in as a locked edit row: an erase keeps its src_bbox row, new content is a
    "target" row with a tgt_bbox."""
    text, row = caption_parts(mode, words, bbox, strokes)
    if "bbox" in row:
        row = {"id": row["id"], "from": "target", "src_bbox": None, "tgt_bbox": row["bbox"], "desc": row["desc"]}
    request = f"ORIGINAL PROMPT:\n{text}\n\n{LOCKED}\n{json.dumps([row], ensure_ascii=False)}"
    return ("Reference images follow in attachment order: ref_image_0, ref_image_1, ...\n"
            f"User editing instruction: {request}\n"
            "Decide the edit, imagine the target image, and output the annotation record.")


def _add_upsampler(wf, p, images):
    """Puts the upsampler in front of NUKE_PROMPT. images: links in reference order (ref_image_0 first)."""
    with open(UPSAMPLER_PROMPTS, encoding="utf-8") as f:
        system = json.load(f)["i2i_bbox"]
    mp = UPSAMPLER_MP[min(len(images), 3)]
    wf["100"] = {"class_type": "CLIPLoader", "_meta": {"title": "Load prompt upsampler"},
                 "inputs": {"clip_name": UPSAMPLER_MODEL, "type": "flux3", "device": "default"}}
    wf["101"] = {"class_type": "PrimitiveStringMultiline", "_meta": {"title": "Upsampler system prompt (BFL i2i_bbox)"},
                 "inputs": {"value": system}}
    gen = {"clip": ["100", 0], "max_length": 3584, "sampling_mode": "on",
           # BFL: greedy, top_p 0.95, presence penalty 0 for edits; top_k 1 is greedy here
           "sampling_mode.temperature": 1.0, "sampling_mode.top_k": 1, "sampling_mode.top_p": 0.95,
           "sampling_mode.min_p": 0.0, "sampling_mode.repetition_penalty": 1.0, "sampling_mode.seed": 0,
           "sampling_mode.presence_penalty": 0.0, "thinking": False, "use_default_template": True, "mtp": "off",
           "system_prompt": ["101", 0],
           "prompt": upsampler_request(p["mode"], p.get("words"), p["bbox"], strokes=bool(p.get("guide")))}
    for i, link in enumerate(images):
        wf[f"10{i + 2}"] = {"class_type": "ImageScaleToTotalPixels", "_meta": {"title": f"Upsampler image {i}"},
                            "inputs": {"image": link, "upscale_method": "lanczos", "megapixels": mp, "resolution_steps": 16}}
        gen[f"images.image{i + 1}"] = [f"10{i + 2}", 0]
    wf["110"] = {"class_type": "TextGenerateV2", "_meta": {"title": "NUKE_UPSAMPLER"}, "inputs": gen}
    wf["111"] = {"class_type": "PreviewAny", "_meta": {"title": "NUKE_CAPTION"}, "inputs": {"source": ["110", 0]}}
    wf[_by_title(wf, "NUKE_PROMPT")]["inputs"]["text"] = ["110", 0]


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
    and optionally match_color ("Auto"/"On"/"Off"), grain and upsample (True: BFL's prompt upsampler writes the
    caption from the request, the mask box and the images)."""
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

    if p.get("upsample"):  # it sees what Klein's refine pass is shown first (the crop, or the strokes), then the refs
        ref_links = [wf[refs_ids[1]]["inputs"][f"images.image{i}"] for i in range(1, len(p.get("refs", [])[:MAX_REFS]) + 1)]
        _add_upsampler(wf, p, [edit_image] + [wf[link[0]]["inputs"]["image"] for link in ref_links])

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
