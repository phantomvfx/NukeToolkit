# NukeComfy (tools/comfy)

Bridge from Nuke to a ComfyUI server (local, Tailscale or RunPod). Each tool
is a `.nk` Group with a **Generate** button. Generate renders the inputs,
uploads them, runs a ComfyUI API workflow from `json/`, polls for the result,
and loads it back into the Group.

## Layout

| Path | What |
|---|---|
| `gizmos/*.nk` | The tools in the menu: Flux3Klein (main edit tool), DepthAny, NormalCrafter, QwenInpaint |
| `gizmos/legacy/` | KleinEdit, Flux3Edit: retired 2026-10-31, out of the menu; their Python/JSON stay for old scripts |
| `build/build_flux3klein.py` | Builds `gizmos/Flux3Klein.nk` with `Nuke16.1.exe -t`. Edit it, not the `.nk` |
| `python/flux3klein.py`, `flux3klein_graph.py` | Flux3Klein: Nuke side (crop, render, versions) and the graph builder (no Nuke import, testable from plain Python) |
| `json/*.json` | ComfyUI workflows in **API format**, one per tool (NormalCrafter uses `Normals.json`) |
| `python/api.py` | HTTP client (`COMFY_URL`, default `http://127.0.0.1:8188`), template load and patch, upload, download |
| `python/bridge.py` | Generic render-inputs-and-generate path (KleinEdit, DepthAny, NormalCrafter) |
| `python/polling.py` | `ComfyPollingThread`: background polling, then result integration on the main thread |
| `python/inpaint.py`, `flux3edit.py` | Tool-specific patchers and integrators (QwenInpaint, Flux3Edit) |
| `menu.py` | "NukeComfy" menu: one `nuke.nodePaste` entry per `.nk` in `gizmos/` |
| `Nuke_ComfyUI.bat` | Launcher. Sets `COMFY_URL` (local `127.0.0.1:8188` or RunPod), then starts Nuke 17 |

## Rules

- **The package is imported as `NukeComfy`** (`from NukeComfy.python.api import …`),
  and saved comps embed that import in their Generate buttons. The root
  `L:\Nuke\init.py` aliases this folder to `NukeComfy`. Keep the imports as they are.
- **Workflows are patched by node title markers, not node IDs.** Each JSON
  node that Nuke feeds carries a `_meta.title` such as `NUKE_PROMPT`,
  `NUKE_SEED`, `NUKE_SOURCE`, `NUKE_MASK` or `NUKE_OUTPUT`. When you
  re-export a workflow from ComfyUI, keep the markers, or patching falls back
  to matching by class type.
- Network and polling run off the main thread. Any `nuke.*` call that touches
  the DAG goes through `nuke.executeInMainThreadWithResult` (see `inpaint._run_on_main_thread`).
- The model never sees a mask. QwenInpaint tints the masked area red and the
  prompt refers to "the object highlighted in red". The final composite is
  done in Nuke, so Grow and Feather don't need a regeneration.
- Output pickup: `COMFYUI_OUTPUT_DIR` (default `C:\ComfyUI\output`). Scratch
  renders (`curb_results/`, `tmp_*.json`) are gitignored.

## Contract with ComfyUI-EXR-ACEScg (`L:\ComfyUIClaude`)

The EXR node pack is developed in the sister project `L:\ComfyUIClaude`. Its
own sessions may change it, so check its `git log` before relying on a
behaviour. NukeComfy depends on:

| Node class | Inputs NukeComfy sets | Used by |
|---|---|---|
| `Load EXR (ACEScg)` | `image_path`, `color_space`, `load_mode`, `frame_index`, `sequence_limit`; output 1 = straight alpha (Flux3Klein's edit mask) | `DepthAny.json`, `Normals.json`, `Flux3Klein.json`; `api.py` patches `image_path` on the `NUKE_INPUT` node |
| `Save EXR (ACEScg)` | `images`, `filename_prefix`, `color_space`; optional `alpha`, `precision`, `compression` | `DepthAny.json`, `Normals.json`, `Flux3Klein.json` |
| `PlateFit_FitToPlate` (ComfyUI-PlateFit) | `plate`, `image`, `mask`, `align`, `max_shift`, `match_color`, `grain`, `grow`, `feather`, `steady`; outputs IMAGE, MASK | `Flux3Klein.json` |
| `PaintAPlate_LevelFill` (ainvfx-paintaplate, third party) | `image`, `alpha`, `base`, `paint`, `enabled` | `Flux3Klein.json` |

- Class names and these input names must not change. New inputs should be
  optional, so the existing API-format JSONs keep validating.
- Output pickup: `polling.py` first looks for the saved file in the
  history `ui` output, then falls back to the newest `*.exr` in
  `COMFYUI_OUTPUT_DIR`. The 2026-10-30 rework of the pack reports
  `{"ui": {"images": [...]}}` and names files `<prefix>_00001.exr`.
- Lint a workflow with `python L:\ComfyUIClaude\tools\workflow_lint.py tools/comfy/json/<file>.json`.
  With ComfyUI running it also flags missing node packs.

## Testing

- No automated tests. Verify against a running ComfyUI: the `comfy-mcp` MCP
  tools can check the server (`server_info`) and run a workflow JSON directly.
- Local ComfyUI is at `L:\ComfyUI`. Required models for each tool are listed in `README.md`.
