# NukeComfy (tools/comfy)

Bridge from Nuke to a ComfyUI server (local, Tailscale or RunPod). Each tool
is a `.nk` Group with a **Generate** button. Generate renders the inputs,
uploads them, runs a ComfyUI API workflow from `json/`, polls for the result,
and loads it back into the Group.

## Layout

| Path | What |
|---|---|
| `gizmos/*.nk` | The tools in the menu: Flux3Klein (main edit tool), SAM3Matte (SAM3 mattes), DepthAny, NormalCrafter, QwenInpaint |
| `gizmos/SAM3Matte.nk` | SAM3Matte: per-object mattes, `sam.m0..m7` channels, `CryptoSAM00` layer, Make Roto. Built, never hand-edited |
| `build/build_sam3matte.py` | Builds `gizmos/SAM3Matte.nk` with `Nuke16.1.exe -t`. Edit it, not the `.nk` |
| `python/sam3matte.py` | SAM3Matte Nuke side: Generate / Cancel / knob callbacks, plate render, Select and Stencil sampling, versions |
| `python/sam3matte_runner.py` | SAM3Matte job runner (no Nuke): chunks, upload, ComfyUI prompts, mask download. Runs on a worker thread |
| `python/sam3matte_graph.py` | SAM3Matte ComfyUI graphs (Detect and Video Track), no Nuke. Holds the three settings constants measured live |
| `python/sam3matte_roto.py`, `sam3matte_roto_core.py` | Make Roto (Nuke side) and the mask-to-shape maths (pure) |
| `python/sam3matte_{chunks,crypto,hints,png,versions}.py` | Pure leaves: chunk plan and tracking size, Cryptomatte maths, Select/Stencil hints, stdlib PNG reader, versions and the Objects parser |
| `tests/` | `test_*.py` and `fake_comfy.py`: pure pytest, no Nuke or ComfyUI. `tests/nuke/`: headless Nuke scripts. `tests/live/`: needs a running ComfyUI |
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

## SAM3Matte rules

- The SAM3 nodes (`SAM3_Detect`, `SAM3_VideoTrack`, `SAM3_TrackToMask`) are core
  ComfyUI (`comfy_extras/nodes_sam3.py`, ComfyUI v0.43 or later). The graph also
  needs `VHS_LoadImages` (ComfyUI-VideoHelperSuite) and `ReverseImageBatch`
  (ComfyUI-KJNodes). The model is `sam3.1_multiplex_fp16.safetensors` in
  `models/checkpoints`, loaded with `CheckpointLoaderSimple`. The non-multiplex
  `sam3.safetensors` cannot find new objects while tracking.
- ComfyUI has no route to delete uploaded inputs. Frames and seeds stay in
  `input/nukecomfy*`; the status line says how many files were left. Do not add
  a delete call that guesses at a route.
- Cryptomatte IDs need a float32 EXR. Half float breaks the IDs. A Write of the
  layer needs channels `all`, metadata `all metadata`, 32-bit float.
- Masks are 8-bit PNGs at tracking size (default width 1024) in
  `<temp>/<job>/obj<k>.####.png`. The Group reads them back, so a result never
  needs ComfyUI again. Don't move them into the repo or onto `C:`.
- The three constants in `sam3matte_graph.py` (`UPLOAD_NESTED`,
  `WHOLE_RANGE_SEED_WITH_TEXT`, `SPLIT_DETECT`) were set from live checks
  (`tests/live/check_sam3matte_graph.py`). Re-run that script before changing one.
- Knob names on the SAM3Matte Group are API once shipped, like every other tool.

## Testing

- Pure tests (no Nuke, no ComfyUI), from `L:\Nuke`:
  `python -m pytest tools/comfy/tests/test_*.py -v`. Use the `test_*.py` glob:
  pointing pytest at the whole `tests` folder also collects `tests/nuke/`, which
  import `nuke` and fail. The runner is tested against `tests/fake_comfy.py`, a fake ComfyUI.
- Headless Nuke: each script in `tests/nuke/` runs with
  `& "L:\Apps\Nuke16.1v4\Nuke16.1.exe" -t L:\Nuke\tools\comfy\tests\nuke\<file>.py`
  (`test_sam3matte_group.py`, `test_sam3matte_nuke.py`, `test_sam3matte_roto.py`, `test_sam3matte_e2e.py`: Generate through the real Group and runner against `fake_comfy.py`).
  Build the `.nk` first with `build/build_sam3matte.py`. Each prints PASS/FAIL and exits 1 on a failure.
- Live ComfyUI: `python tools/comfy/tests/live/check_sam3matte_graph.py`. Needs the
  model and the spike plate (paths in its docstring).
- None of these cover the Nuke GUI. Checked by hand in Nuke 16.1 on 2026-11-02:
  Generate (key-frame mode), the Cryptomatte click-picker and Make Roto. Not yet:
  Cancel and resume in the GUI, save and reopen.
- The other tools (Flux3Klein, DepthAny, ...) have no automated tests. Verify them
  against a running ComfyUI: the `comfy-mcp` MCP tools can check the server
  (`server_info`) and run a workflow JSON directly.
- Local ComfyUI is at `L:\ComfyUI`. Required models for each tool are listed in `README.md`.
