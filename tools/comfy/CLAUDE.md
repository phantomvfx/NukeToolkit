# NukeComfy (tools/comfy)

Bridge from Nuke to a ComfyUI server (local, Tailscale or RunPod). Each tool
is a `.nk` Group with a **Generate** button. Generate renders the inputs,
uploads them, runs a ComfyUI API workflow from `json/`, polls for the result,
and loads it back into the Group.

## Layout

| Path | What |
|---|---|
| `gizmos/*.nk` | The tools: KleinEdit, DepthAny, NormalCrafter, QwenInpaint, Flux3Edit |
| `json/*.json` | ComfyUI workflows in **API format**, one per tool (NormalCrafter uses `Normals.json`) |
| `python/api.py` | HTTP client (`COMFY_URL`, default `http://127.0.0.1:8188`), template load and patch, upload, download |
| `python/bridge.py` | Generic render-inputs-and-generate path (KleinEdit, DepthAny, NormalCrafter) |
| `python/polling.py` | `ComfyPollingThread`: background polling, then result integration on the main thread |
| `python/inpaint.py`, `flux3edit.py` | Tool-specific patchers and integrators (QwenInpaint, Flux3Edit) |
| `menu.py` | "NukeComfy" menu: one `nuke.nodePaste` entry per `.nk` in `gizmos/` |
| `Nuke_ComfyUI.bat` | Launcher. Sets `COMFY_URL` (local Tailscale or RunPod), then starts Nuke 17 |

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

## Testing

- No automated tests. Verify against a running ComfyUI: the `comfy-mcp` MCP
  tools can check the server (`server_info`) and run a workflow JSON directly.
- Local ComfyUI is at `L:\ComfyUI`. Required models for each tool are listed in `README.md`.
