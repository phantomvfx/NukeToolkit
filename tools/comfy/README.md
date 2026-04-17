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

## Customization
If your ComfyUI saves outputs to a specific directory, the Nuke bridge expects to pick it up locally to feed the images back visually. You can define this directory by setting an environment variable on your OS:
`COMFYUI_OUTPUT_DIR=C:\Your\ComfyUI\output`

*(Default Fallback: `C:\ComfyUI\output`)*
