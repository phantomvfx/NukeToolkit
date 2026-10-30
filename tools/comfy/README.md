# NukeComfy

A streamlined integration bridge between Nuke and ComfyUI. This repository provides Nuke Gizmos and Python scripts to push Nuke's node trees, images, and variables to a local ComfyUI instance, executing AI workflows transparently, and retrieving the results directly back into the Nuke composite.

## Installation

1. Clone or download this repository into your `.nuke` directory.
   ```bash
   cd ~/.nuke  # or C:\Users\<YourUser>\.nuke on Windows
   git clone https://github.com/phantomvfx/NukeComfy.git
   ```

2. Open your `.nuke/init.py` (create it if it doesn't exist) and add the following line:
   ```python
   nuke.pluginAddPath('./NukeComfy')
   ```

3. Restart Nuke. You'll now see a "NukeComfy" menu inside the top menu bar containing the tools `KleinEdit`, `DepthAny`, `NormalCrafter` and `QwenInpaint`. They are `.nk` files (single Group snippets pasted into the script, not `.gizmo` files), so the node lives in the script itself. Scripts saved with the old `.gizmo` versions of the first three need those nodes replaced by the `.nk` groups (same knobs).

## QwenInpaint (object removal / replacement)

`gizmos/QwenInpaint.nk` is a single Group (built for Nuke 16, `.nk` rather than `.gizmo` so the whole tool
is embedded in the script). It shows up in the NukeComfy menu, or import it with File > Import Script /
`nuke.nodePaste()`.

- **Source**: the image to edit. **Mask**: alpha by default (pick another channel in *Mask channel*), white = the area to edit.
- **The model cannot see the mask.** The area under the Mask (grown by *Grow* px) is tinted red in the image the
  model receives, and the prompt refers to it: *"Remove the object highlighted in red and fill the area with the
  surrounding background…"*, or *"Replace the object highlighted in red with …"*. Naming the object is not needed.
- **Generate** renders the current frame of both to PNG, uploads them to ComfyUI, runs `json/QwenInpaint.json`
  (a whole-frame instruction edit) and loads the generated frame inside the group. The node label shows the status.
- The group composites the result over your untouched Source in Nuke (float / linear) through the grown mask
  blurred by *Feather* px, so changing Grow / Feather afterwards does not need another generation, and pixels
  outside the mask are never changed. *Show result* toggles between Source and the composite.
- Working size: Source is scaled so its long edge is *Working size* (default 1920) and each side snapped to a multiple of 32.

ComfyUI needs the Qwen-Image 2.1 setup the template references (`QwenImage21Cache`, `TextEncodeQwenImage21`
and the `qwen_image_2.1_*` / `qwen3vl_8b_*` models).

## Customization
The ComfyUI address is read from the `COMFY_URL` environment variable (set by `Nuke_ComfyUI.bat`), defaulting to
`http://127.0.0.1:8188`.

If your ComfyUI saves outputs to a specific directory, the Nuke bridge expects to pick it up locally to feed the images back visually. You can define this directory by setting an environment variable on your OS:
`COMFYUI_OUTPUT_DIR=C:\Your\ComfyUI\output`

*(Default Fallback: `C:\ComfyUI\output`)*
