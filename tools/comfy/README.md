# NukeComfy

A streamlined integration bridge between Nuke and ComfyUI. This repository provides Nuke Gizmos and Python scripts to push Nuke's node trees, images, and variables to a local ComfyUI instance, executing AI workflows transparently, and retrieving the results directly back into the Nuke composite.

## Installation

NukeComfy lives in `tools/comfy/` of the Nuke monorepo (it was previously `phantomvfx/NukeComfy`).

1. Clone the monorepo (on this machine it lives at `L:\Nuke`).

2. Add one line to your `~/.nuke/init.py`:
   ```python
   nuke.pluginAddPath('L:/Nuke')   # the repo root, not tools/comfy
   ```
   The root `init.py` puts `tools/comfy` on the plugin path and makes it importable as the
   `NukeComfy` package. The Generate buttons in saved scripts use `import NukeComfy.python.…`,
   so don't add `tools/comfy` on its own.

3. Restart Nuke. You'll now see a "NukeComfy" menu inside the top menu bar containing the tools `Flux3Klein`, `SAM3Matte`, `DepthAny`, `NormalCrafter` and `QwenInpaint`. They are `.nk` files (single Group snippets pasted into the script, not `.gizmo` files), so the node lives in the script itself. Scripts saved with the old `.gizmo` versions of DepthAny and NormalCrafter need those nodes replaced by the `.nk` groups (same knobs).

   `KleinEdit` and `Flux3Edit` are retired in favour of Flux3Klein (2026-10-31). Their `.nk` files are in
   `gizmos/legacy/` (out of the menu); their Python and JSON stay, so Generate still works in scripts that contain them.

## Flux3Klein (paint, replace, erase: the main edit tool)

`gizmos/Flux3Klein.nk` is built by `build/build_flux3klein.py` (run it with `Nuke16.1.exe -t`); edit the script, not
the `.nk`. Python: `python/flux3klein.py` (Nuke side) and `python/flux3klein_graph.py` (the ComfyUI graph, no Nuke
needed). Workflow: `json/Flux3Klein.json`.

- **Inputs**: Source, Mask (Roto, a keyer...: pick the channel in *Mask channel*; white = edit), Strokes (RGBA paint,
  e.g. a RotoPaint on black: Klein turns the drawing into what the prompt says), Ref1-Ref4 (name them in the prompt as
  `<ref_image_1>` ...). The edit mask is Mask plus the Strokes' alpha, so Strokes alone are enough.
- **Mode**: *Replace or add* paints what the prompt describes inside the mask; *Erase* removes it and continues the
  background. The prompt is just the object ("a red enamel star", "the lamp post"); the tool writes Klein's full
  instruction with the mask's box in Klein's own caption format.
- **Generate** (current frame): finds the mask's bounds, pads them with *Context* (%, at least 64 px), snaps to
  multiples of 16 inside the format, and renders that crop of Source as a half-float EXR in the script's working space,
  untouched (raw), with the edit mask in alpha. *Region: Full frame* sends the whole frame instead.
- **In ComfyUI**: ACEScg (or linear Rec.709) to linear sRGB to sRGB for Klein. Two Klein passes, as Paint a plate does:
  a quick pass at about 0.6 MP (*Steps*, 16) finds the edit, then a refine at *Working size* (2 MP) with denoise
  *Refine strength* (0.35, 8 steps). An erase's refine is shown the quick pass, not the original, or Klein draws the
  object back. Then back to the crop's size: SeedVR2 (3B GGUF) when the crop is more than 1.25x the working size
  (*Upscale: Auto*), else a resize. Fit to Plate aligns it, matches colour at the edge (*Colour match*: Auto = on for
  Replace, off for Erase) and adds grain to the plate's level. Saved as a half EXR back in the plate's primaries.
- **Back in Nuke**: every result is a new entry in *Version*. The patch is placed back at the crop's position and
  keymixed over Source through the edit mask held at the generated frame, grown by *Grow* and softened by *Feather*
  (no new Generate needed). Pixels outside that mask are never changed, alpha included.
- **Colour**: *Plate primaries: Auto* reads the script: ACEScg when the OCIO working space is ACEScg, else linear
  Rec.709 (Nuke's default `linear`). The data is always scene-linear; values above 1 inside the edit come back
  clipped to what the model can make.
- **One frame**: the edit is one frame, held. Track or transform it in Nuke for a shot.
- **Times** (RTX 3060 12 GB, 2 MP working size): about 55 s per edit; with strokes and 1 reference about 80 s, with 4
  references about 140 s. The first edit with a new number of references can add a few minutes while ComfyUI prepares
  the model for it. A full 2048² frame through SeedVR: about 90 s. Tested in Nuke 16.1 and 17.0 (terminal mode).
- **Renders** go next to the script (`../prerender/airender`), or to `NUKECOMFY_TEMP` if set (set it: an unsaved
  script otherwise writes to the home folder).

ComfyUI needs: ComfyUI-EXR-ACEScg, ComfyUI-PlateFit (phantomvfx), ainvfx-paintaplate (Level Fill),
ComfyUI-SeedVR2_VideoUpscaler, and the models `flux3-image-klein-int8-convrot`, `qwen3vl_4b_int8_convrot`,
`flux2-vae`, `seedvr2_ema_3b-Q4_K_M.gguf`, `ema_vae_fp16` (optionally `seedvr2_ema_7b_sharp_fp16`).
The captions and the two-pass masked edit are adapted from AInVFX Paint a plate (Apache-2.0, see `NOTICE`).
Test without Nuke: `L:\ComfyUIClaude\tools\flux3klein_test.py`.

## SAM3Matte (object mattes, Cryptomatte and roto from SAM3)

`gizmos/SAM3Matte.nk` is built by `build/build_sam3matte.py` (run it with `Nuke16.1.exe -t`); edit the script, not the
`.nk`. It is a Group snippet: import it from the NukeComfy menu, File > Import Script or `nuke.nodePaste(path)`. It is
not in the node toolbar. Python: `python/sam3matte.py` (Nuke side), `python/sam3matte_runner.py` (talks to ComfyUI, no
Nuke needed), `python/sam3matte_graph.py` (the ComfyUI graphs), `python/sam3matte_roto.py` (Make Roto), plus small
pure helpers (`sam3matte_chunks`, `_crypto`, `_hints`, `_png`, `_versions`, `_roto_core`).

- **Inputs**: *Source* (the plate), *Select* (any alpha marking what to matte: a RotoPaint, a Roto, a keyer), *Stencil*
  (any alpha marking what to leave out). Select and Stencil are read on the key frame only. *Prompt* names what to
  find, comma separated: `person:2, ball`. `:N` is the most objects to keep for that subject. At least one of Prompt or
  Select is needed.
- **Mode**:

  | | From key frame (default) | Whole range |
  |---|---|---|
  | Uses | Prompt, Select, Stencil, in any mix | Prompt only |
  | How | SAM3 finds the objects on the key frame, then tracks them both ways | SAM3 tracks the prompt over the range in one forward pass |
  | Objects entering later | Not found. Generate again from a later key frame | Found as they enter |
  | `person:2` caps | Honoured | Ignored, only *Max objects* applies |
  | Stencil | A hint (with Select it gives exclude points; with a prompt only, detected objects mostly inside it are dropped) and an optional hard cut | Hard cut only (*Hard stencil* is always on) |

  *Whole range* with Select connected is an error: switch to *From key frame*.
- **Generate** renders the plate as 8-bit PNGs through the script's default display and view (what you see in the
  Viewer, not a clipped linear encode), uploads them to ComfyUI (`/upload/image`, so any `COMFY_URL` works, no shared
  disk), runs SAM3 and downloads one mask sequence per object. Every Generate is a new entry in *Version*. **Cancel**
  interrupts the running chunk within a couple of seconds and keeps the chunks already finished as a partial
  version; Generate with the same settings resumes it.
- **Outputs** (one stream): RGB is Source, untouched. `rgba.alpha` is the union of the chosen *Objects* (all, a
  comma list of slot numbers, or object names), then *Erode*, *Blur* and, if *Hard stencil* is on, the Stencil cut out.
  `sam.m0` to `sam.m7` are one raw mask per object (8 channels in one layer). `CryptoSAM00` plus the manifest metadata
  let a Nuke Cryptomatte node after the Group pick objects by name. `sam.*` and `CryptoSAM00` have no erode, blur or
  stencil. Names: `select` for the Select object, `<subject>_<n>` when the prompt has one subject, else `obj<k>`.
- **Write Cryptomatte to disk** as a 32-bit float EXR with channels `all` and metadata `all metadata`. Half float breaks
  the IDs, and the default metadata setting drops the `exr/cryptomatte/*` lines the Cryptomatte node needs.
- **Make Roto** builds one animated Roto node next to the Group from the saved masks (no ComfyUI needed): one Bezier
  shape per chosen object with a fixed point count (*Points per shape*, 32) and a key every *Key every N frames*.
  Frames where the object is empty key the shape's opacity to 0.
- **Flux3Klein**: SAM3Matte's alpha plugs into Flux3Klein's *Mask* input (*Mask channel* = alpha). Flux3Klein is
  unchanged.
- **Long and 4K shots**: SAM3 tracks at about 1024 px width (*Tracking width*) whatever the plate; only the key frame
  goes at full size. That matched full-resolution tracking (IoU 0.995 in a test), so a 4K plate costs the same as 2K.
  Masks come back at that size and are scaled to the plate in the Group. Shots are cut into chunks of *Chunk size*
  frames (250). Each chunk is its own ComfyUI run that starts on the last frame of the one before and is seeded with
  its masks, so object numbers hold. *Cancel* keeps the finished chunks as a partial version (shown as `partial`), and
  Generate with the same settings resumes it. At most 8 objects over the whole shot.
- **Times** (RTX 3060 12 GB): 200 frames at 2K took about 1 min 45 s of tracking; a whole run with model load and
  saving the masks takes a few minutes. Uploading 200 PNGs to a remote ComfyUI adds to that; the status line shows
  the progress (plate render, upload, chunk n/N).
- **Limits**: the plate render runs frame by frame on Nuke's main thread before anything is uploaded, so Nuke is busy during it and *Cancel* cannot stop it (it works once the upload starts). *Make Roto* decodes the masks in pure Python, about 0.1 s per mask per object: on a long shot raise *Key every N frames*.
- **Nothing found**: if SAM3 finds no object, the status line says `Nothing found for "..."` and no version is added.
- **Where the masks live**: `<temp>/<node>_<timestamp>/obj<k>.####.png`, 8-bit, numbered with the plate's frame
  numbers. `<temp>` is `NUKECOMFY_TEMP` if set, else next to the script (`../prerender/airender`), as for Flux3Klein.
  The Group reads these files, so a version works without ComfyUI. Keep the folder with the script.
- **Left behind in ComfyUI**: ComfyUI has no API to delete uploaded inputs, so the frames stay in its
  `input/nukecomfy*` folders. The final status line says how many files were left. Clear them by hand when needed.

ComfyUI needs: core SAM3 nodes (ComfyUI v0.43 or later), the model `sam3.1_multiplex_fp16.safetensors` in
`models/checkpoints`, ComfyUI-VideoHelperSuite (`VHS_LoadImages`) and ComfyUI-KJNodes (`ReverseImageBatch`).

Tested (Nuke 16.1, ComfyUI at `127.0.0.1:8188`): the pure tests, including the runner against a fake ComfyUI server;
the Group, the Nuke layer (Generate against a stub runner) and Make Roto in headless Nuke 16.1; the two ComfyUI graphs
against the real server and the real model, on the 200-frame 2K test plate (`tests/live/check_sam3matte_graph.py`). A
Cryptomatte node reading the layer was checked headless. Full Generates from headless Nuke against the real ComfyUI
on that plate (2026-11-02): key frame from a Select (IoU 0.995 against the spike), Whole range "person" (0.994), and
chunks of 60 (0.999 against the unchunked run). In the Nuke 16.1 GUI (2026-11-02): Generate in key-frame mode, the
Cryptomatte click-picker and Make Roto worked. **Not run yet**: Cancel and resume in the GUI, save and reopen, a
500+ frame shot, a 4K plate.

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
