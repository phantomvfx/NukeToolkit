"""
Flux3Edit: reference-based image edit through ComfyUI (FLUX 3 Image Klein distilled, json/Flux3Edit.json).

Generate button on the Flux3Edit group:
  1. renders the current frame of Source and of each connected Ref input (display-encoded 8-bit PNG)
  2. uploads them to ComfyUI, patches the NUKE_* markers of the template (Source is <ref_image_0> in the
     prompt, Ref1 is <ref_image_1> ... Ref4 is <ref_image_4>), queues and polls
  3. downloads the generated frame next to the other NukeComfy renders and points the group's inner
     Result Read at it.

Targets Nuke 16 (Python 3.11 / PySide6).
"""
import urllib.request
from datetime import datetime

import nuke

from NukeComfy.python.api import api_client
from NukeComfy.python.bridge import bridge
from NukeComfy.python.inpaint import _make_integrator, _render_png, _set_input
from NukeComfy.python.polling import start_comfy_process

# Input index -> (inner Input node name, id of the LoadImage node added for it in the workflow)
REF_INPUTS = {1: ("Ref1", "12"), 2: ("Ref2", "13"), 3: ("Ref3", "14"), 4: ("Ref4", "15")}


def _check_server():
    """Fails fast with a readable message when ComfyUI is not reachable at api_client.base_url."""
    try:
        urllib.request.urlopen(f"{api_client.base_url}/system_stats", timeout=5).close()
    except Exception as e:
        raise RuntimeError(f"ComfyUI is not reachable at {api_client.base_url} ({e}). Start it, or check COMFY_URL.")


def _make_patcher(params):
    """Patcher for ComfyUI polling thread: uploads the PNGs and fills the template from a knob snapshot."""
    def patch(workflow, node, input_files):
        refs = [("11", input_files[0])] + [(REF_INPUTS[i][1], input_files[i]) for i in sorted(REF_INPUTS) if i in input_files]
        for slot, (load_id, path) in enumerate(refs):
            uploaded = api_client.upload_image(path)
            if not uploaded:
                raise RuntimeError(f"Could not upload {path} to ComfyUI")
            if load_id == "11":
                _set_input(workflow, "NUKE_SOURCE", "image", uploaded)
            else:
                workflow[load_id] = {"class_type": "LoadImage", "inputs": {"image": uploaded},
                                     "_meta": {"title": f"NUKE_REF{slot}"}}
            # reference slot n is <ref_image_n> in the prompt, for the editor and for the prompt upsampler
            workflow["17"]["inputs"][f"images.image{slot}"] = [load_id, 0]
            workflow["50"]["inputs"][f"images.image{slot}"] = [load_id, 0]

        _set_input(workflow, "NUKE_REFS", "resolution.megapixels", params["megapixels"])
        _set_input(workflow, "NUKE_PROMPT", "value", params["prompt"])
        _set_input(workflow, "NUKE_UPSAMPLE", "switch", not params["upsample"])  # switch on = raw prompt
        _set_input(workflow, "NUKE_SAMPLER", "seed", params["seed"])
        _set_input(workflow, "NUKE_SAMPLER", "steps", params["steps"])
        _set_input(workflow, "NUKE_SAMPLER", "cfg", params["cfg"])
        return workflow
    return patch


def generate(node):
    """Entry point of the Generate button. Returns True once the job has been queued."""
    try:
        if not node.input(0):
            raise RuntimeError("Connect a Source image")
        _check_server()

        node["status"].setValue("Rendering...")
        frame = int(nuke.frame())
        temp_folder = bridge._resolve_temp_folder()
        base = f"{node.name()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        result_path = f"{temp_folder}/{base}_result.png"

        input_files = {}
        for index, inner in [(0, "Source")] + [(i, REF_INPUTS[i][0]) for i in sorted(REF_INPUTS)]:
            if index == 0 or node.input(index):
                path = f"{temp_folder}/{base}_{inner.lower()}.png"
                _render_png(node, inner, path, frame, raw=False)
                input_files[index] = path

        params = {
            "megapixels": float(node["megapixels"].value()),
            "prompt": node["prompt_pos"].value(),
            "upsample": bool(node["upsample"].value()),
            "seed": int(node["seed"].value()),
            "steps": int(node["steps"].value()),
            "cfg": float(node["cfg"].value()),
        }

        start_comfy_process(node, input_files,
                            patcher=_make_patcher(params),
                            integrator=_make_integrator(params, result_path))
        return True
    except Exception as e:
        node["status"].setValue(f"Error: {e}")
        nuke.message(f"Flux3Edit: {e}")
        return False
