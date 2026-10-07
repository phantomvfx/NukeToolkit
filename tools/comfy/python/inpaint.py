"""
QwenInpaint: mask-based inpaint through ComfyUI (Qwen-Image 2.1, json/QwenInpaint.json).

Generate button on the QwenInpaint group:
  1. renders the current frame of Source (display-encoded 8-bit PNG) and the shaped
     Mask (raw 8-bit PNG, mask value in R/G/B)
  2. uploads both to ComfyUI, patches the NUKE_* markers of the template, queues and polls
  3. downloads the generated frame next to the other NukeComfy renders and points the
     group's inner Result Read at it. The group composites it over the untouched Source
     inside Nuke (float, through the grown + feathered mask).

Targets Nuke 16 (Python 3.11 / PySide6).
"""
import threading
from datetime import datetime

import nuke

from NukeComfy.python.api import api_client
from NukeComfy.python.bridge import bridge
from NukeComfy.python.polling import start_comfy_process

SNAP = 32  # the model wants sides that are multiples of 32


def working_size(width, height, long_edge):
    """Source size scaled so the long edge is `long_edge`, each side snapped to a multiple of SNAP."""
    scale = float(long_edge) / max(width, height)
    return tuple(max(SNAP, int(round(side * scale / SNAP)) * SNAP) for side in (width, height))


def _run_on_main_thread(fn):
    """Runs fn on Nuke's main thread and returns its result, re-raising its exception."""
    def guarded():
        try:
            return {"value": fn()}
        except Exception as e:
            return {"error": e}

    if threading.current_thread() is threading.main_thread():
        out = guarded()
    else:
        out = nuke.executeInMainThreadWithResult(guarded)
    if out is None:
        raise RuntimeError("Main-thread call failed")
    if "error" in out:
        raise out["error"]
    return out["value"]


def _render_png(group, upstream_name, path, frame, raw):
    """Renders one frame of a node inside the group to an 8-bit RGB PNG."""
    with group:
        write = nuke.nodes.Write(file=path, file_type="png", channels="rgb")
        write.setInput(0, group.node(upstream_name))
        write["datatype"].setValue("8 bit")
        write["create_directories"].setValue(True)
        if raw:
            write["raw"].setValue(True)
        try:
            nuke.execute(write, frame, frame)
        finally:
            nuke.delete(write)


def _set_input(workflow, marker, key, value):
    node_id = api_client._find_node(workflow, marker)
    if not node_id:
        raise KeyError(f"Workflow template is missing the {marker} marker")
    workflow[node_id]["inputs"][key] = value


def _make_patcher(params):
    """Patcher for ComfyPollingThread: uploads the PNGs and fills the template from a knob snapshot."""
    def patch(workflow, node, input_files):
        source = api_client.upload_image(input_files[0])
        mask = api_client.upload_image(input_files[1])
        if not (source and mask):
            raise RuntimeError("Could not upload the Source/Mask PNGs to ComfyUI")

        _set_input(workflow, "NUKE_SOURCE", "image", source)
        _set_input(workflow, "NUKE_MASK", "image", mask)
        _set_input(workflow, "NUKE_WORK_SIZE", "width", params["width"])
        _set_input(workflow, "NUKE_WORK_SIZE", "height", params["height"])
        _set_input(workflow, "NUKE_GROW", "expand", params["grow"])
        _set_input(workflow, "NUKE_PROMPT", "prompt", params["prompt"])
        _set_input(workflow, "NUKE_SAMPLER", "seed", params["seed"])
        _set_input(workflow, "NUKE_SAMPLER", "steps", params["steps"])
        _set_input(workflow, "NUKE_SAMPLER", "cfg", params["cfg"])
        return workflow
    return patch


def _make_integrator(params, save_path):
    """Integrator for the polling thread: downloads the result and wires it into the group."""
    def integrate(node, output_data):
        def fail(message):
            _run_on_main_thread(lambda: node["status"].setValue(f"Error: {message}"))

        try:
            images = [img for out in output_data.values() for img in out.get("images", [])]
            if not images:
                return fail("No output images found")

            info = images[0]
            if not api_client.download_output(info["filename"], info.get("subfolder", ""),
                                              info.get("type", "output"), save_path):
                return fail("Failed to download output from ComfyUI")

            def apply():
                result = node.node("Result")
                result["file"].setValue(save_path)
                result["label"].setValue(f"{params['prompt'][:30]}... | Seed: {params['seed']}")
                node["has_result"].setValue(True)
                node["show_result"].setValue(True)
                node["status"].setValue("Done")
            _run_on_main_thread(apply)
        except Exception as e:
            fail(str(e))
    return integrate


def generate(node):
    """Entry point of the Generate button. Returns True once the job has been queued."""
    try:
        if not node.input(0):
            raise RuntimeError("Connect a Source image")
        if not node.input(1):
            raise RuntimeError("Connect a Mask (alpha, or pick the channel in Mask channel)")

        node["status"].setValue("Rendering...")
        frame = int(nuke.frame())
        temp_folder = bridge._resolve_temp_folder()
        base = f"{node.name()}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        source_path = f"{temp_folder}/{base}_source.png"
        mask_path = f"{temp_folder}/{base}_mask.png"
        result_path = f"{temp_folder}/{base}_result.png"

        _render_png(node, "Source", source_path, frame, raw=False)
        _render_png(node, "MaskPNG", mask_path, frame, raw=True)

        fmt = node.node("Source").format()
        width, height = working_size(fmt.width(), fmt.height(), node["work_edge"].value())
        params = {
            "width": width,
            "height": height,
            "grow": int(node["grow"].value()),
            "prompt": node["prompt_pos"].value(),
            "seed": int(node["seed"].value()),
            "steps": int(node["steps"].value()),
            "cfg": float(node["cfg"].value()),
        }

        start_comfy_process(node, {0: source_path, 1: mask_path},
                            patcher=_make_patcher(params),
                            integrator=_make_integrator(params, result_path))
        return True
    except Exception as e:
        node["status"].setValue(f"Error: {e}")
        nuke.message(f"QwenInpaint: {e}")
        return False
