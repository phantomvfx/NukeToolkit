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

3. Restart Nuke. You'll now see a "NukeComfy" menu inside the top menu bar containing `.gizmo` nodes such as `KleinEdit`, `DepthAny`, and `Normals`.

## QwenInpaint (mask-based inpaint)

`gizmos/QwenInpaint.nk` is a single Group (built for Nuke 16, `.nk` rather than `.gizmo` so the whole tool
is embedded in the script). It shows up in the NukeComfy menu, or import it with File > Import Script /
`nuke.nodePaste()`.

- **Source**: the image to repaint. **Mask**: alpha by default (pick another channel in *Mask channel*), white = repaint.
- **Generate** renders the current frame of both to PNG, uploads them to ComfyUI, runs `json/QwenInpaint.json`
  and loads the generated frame inside the group.
- The group composites the result over your untouched Source in Nuke (float / linear) through the mask grown by
  *Grow* px (also the sampling mask sent to ComfyUI) and blurred by *Feather* px, so changing Grow / Feather
  afterwards does not need another generation. *Show result* toggles between Source and the composite.
- Working size: Source is scaled so its long edge is *Working size* (default 1920) and each side snapped to a multiple of 32.

ComfyUI needs the Qwen-Image 2.1 setup the template references (`QwenImage21Cache`, `TextEncodeQwenImage21`
and the `qwen_image_2.1_*` / `qwen3vl_8b_*` models).

## Customization
The ComfyUI address is read from the `COMFY_URL` environment variable (set by `Nuke_ComfyUI.bat`), defaulting to
`http://127.0.0.1:8188`.

If your ComfyUI saves outputs to a specific directory, the Nuke bridge expects to pick it up locally to feed the images back visually. You can define this directory by setting an environment variable on your OS:
`COMFYUI_OUTPUT_DIR=C:\Your\ComfyUI\output`

*(Default Fallback: `C:\ComfyUI\output`)*
