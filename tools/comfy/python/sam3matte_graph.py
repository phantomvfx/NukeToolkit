"""
SAM3Matte: the ComfyUI graphs, without Nuke. Pure leaf module (no nuke, no sibling imports), so it is tested from
plain Python (tools/comfy/tests/test_sam3matte_graph.py) and used by sam3matte_runner.py inside Nuke.

Two API-format prompts are built here:
  build_detect(p)  one SAM3_Detect on the key frame -> one SaveImage "detect_save" (image i = object i)
  build_track(p)   VHS_LoadImages -> SAM3_VideoTrack (seeded by masks and/or text) -> one SaveImage per slot

Both return (workflow, outputs); outputs maps each SaveImage node id to {"obj": int | None, "pass": str}.
Node classes and inputs follow L:/ComfyUI/comfy_extras/nodes_sam3.py and core ComfyUI.
"""
import json

CKPT = "sam3.1_multiplex_fp16.safetensors"
MAX_SLOTS = 8
# --- settings block: T8 may edit these three constants after measuring ---
UPLOAD_NESTED = True               # VHS_LoadImages directory "nukecomfy/<job>_c1" accepted by the server
WHOLE_RANGE_SEED_WITH_TEXT = True  # Video Track accepts initial_mask + conditioning together
SPLIT_DETECT = False               # Detect with text AND points in one node works


class _Graph:
    """API-format prompt under construction: string node ids from a counter."""

    def __init__(self):
        self.wf = {}
        self._n = 0

    def add(self, class_type, inputs, node_id=None):
        if node_id is None:
            self._n += 1
            node_id = str(self._n)
        self.wf[node_id] = {"class_type": class_type, "inputs": inputs}
        return node_id

    def checkpoint(self):
        return self.add("CheckpointLoaderSimple", {"ckpt_name": CKPT})

    def text(self, ckpt, text):
        return self.add("CLIPTextEncode", {"text": text, "clip": [ckpt, 1]})

    def points(self, pts):
        return self.add("PrimitiveStringMultiline", {"value": json.dumps(list(pts))})

    def save(self, mask_id, prefix, node_id=None):
        img = self.add("MaskToImage", {"mask": [mask_id, 0]})
        return self.add("SaveImage", {"images": [img, 0], "filename_prefix": prefix}, node_id)


def _detect_node(g, ckpt, image, p, text=None, positive=(), negative=()):
    inputs = {"model": [ckpt, 0], "image": [image, 0], "threshold": p.get("threshold", 0.5),
              "refine_iterations": 2, "individual_masks": True}
    if text:
        inputs["conditioning"] = [g.text(ckpt, text), 0]
    if positive:
        inputs["positive_coords"] = [g.points(positive), 0]
        if negative:  # negative points alone return a stray mask, so they only count next to positives
            inputs["negative_coords"] = [g.points(negative), 0]
    return g.add("SAM3_Detect", inputs)


def build_detect(p):
    text = (p.get("text") or "").strip()
    positive = list(p.get("positive") or [])
    negative = list(p.get("negative") or [])
    if not text and not positive:
        raise ValueError("Detect needs a text prompt or at least one positive point.")
    g = _Graph()
    ckpt = g.checkpoint()
    image = g.add("LoadImage", {"image": p["key_image"]})
    if SPLIT_DETECT and text and positive:
        pts = _detect_node(g, ckpt, image, p, positive=positive, negative=negative)
        txt = _detect_node(g, ckpt, image, p, text=text)
        a = g.add("MaskToImage", {"mask": [pts, 0]})  # points object first
        b = g.add("MaskToImage", {"mask": [txt, 0]})
        both = g.add("ImageBatch", {"image1": [a, 0], "image2": [b, 0]})
        masks = g.add("ImageToMask", {"image": [both, 0], "channel": "red"})
    else:
        masks = _detect_node(g, ckpt, image, p, text=text, positive=positive, negative=negative)
    g.save(masks, p["prefix"], "detect_save")
    return g.wf, {"detect_save": {"obj": None, "pass": "key"}}


def build_track(p):
    seeds = list(p.get("seeds") or [])
    text = (p.get("text") or "").strip()
    if not seeds and not text:
        raise ValueError("Track needs seed masks or a text prompt.")
    n_save = max(1, min(MAX_SLOTS, int(p["n_save"])))
    g = _Graph()
    ckpt = g.checkpoint()
    frames = g.add("VHS_LoadImages", {"directory": p["directory"], "image_load_cap": 0})
    if p.get("reverse"):
        frames = g.add("ReverseImageBatch", {"images": [frames, 0]})
    inputs = {"images": [frames, 0], "model": [ckpt, 0], "detection_threshold": 0.5, "detect_interval": 1,
              "max_objects": min(64, max(int(p.get("max_objects", 1)), len(seeds), 1))}
    if seeds:
        batch = None
        for value in seeds:
            load = g.add("LoadImage", {"image": value})
            batch = load if batch is None else g.add("ImageBatch", {"image1": [batch, 0], "image2": [load, 0]})
        scaled = g.add("ImageScale", {"image": [batch, 0], "upscale_method": "bilinear", "width": int(p["track_w"]),
                                      "height": int(p["track_h"]), "crop": "disabled"})
        red = g.add("ImageToMask", {"image": [scaled, 0], "channel": "red"})
        inputs["initial_mask"] = [g.add("ThresholdMask", {"mask": [red, 0], "value": 0.5}), 0]
    if text:
        inputs["conditioning"] = [g.text(ckpt, text), 0]
    track = g.add("SAM3_VideoTrack", inputs)
    outputs = {}
    for j in range(n_save):
        mask = g.add("SAM3_TrackToMask", {"track_data": [track, 0], "object_indices": str(j)})
        g.save(mask, f"{p['prefix']}{j}", f"save{j}")
        outputs[f"save{j}"] = {"obj": j, "pass": p["pass_name"]}
    return g.wf, outputs
