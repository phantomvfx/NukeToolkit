# SAM3Matte: design

Date: 2026-11-01. Status: draft for review. Target: Nuke 16.1 (baseline), NukeComfy (`tools/comfy`).

## Why

Compositors on 2K shots of about 200 frames need quick object mattes: a person, a ball, a car. Today that means
roto by hand or a keyer that doesn't fit. SAM3 (Segment Anything 3) can find and track an object from a text prompt
or a few clicks, and it already runs in the local ComfyUI. SAM3Matte puts that behind one Generate button inside Nuke,
returning mattes an artist can use straight away: as alpha, per object, through Nuke's own Cryptomatte picker, as the
Mask of Flux3Klein, or as an editable Roto.

## What it is

A NukeComfy tool: a single `.nk` Group (`tools/comfy/gizmos/SAM3Matte.nk`), built by a build script, with a Generate
button that sends the plate to ComfyUI, runs SAM3 over a frame range, and brings back one mask sequence per object.

### Inputs

| Input | What | Notes |
|---|---|---|
| Source | the plate | required |
| Select | any alpha marking what to segment (RotoPaint, Roto, a keyer…) | read on the key frame only |
| Stencil | any alpha marking what to leave out | read on the key frame only |

Plus a **Prompt** text knob. At least one of Prompt or Select is required (user decision: both text and viewer
input; Select/Stencil can each be fed by RotoPaint or a keyer).

### Mode (my call, from the builder check)

A **Mode** knob picks between the two ways SAM3 can work, because they can't do the same things:

| | **From key frame** (default) | **Whole range** |
|---|---|---|
| Uses | Prompt, Select, Stencil, in any mix | Prompt only |
| How | `SAM3 Detect` on the key frame, then track both ways | `SAM3 Video Track` with text over the range, one forward pass |
| Objects entering later | not found (generate again from a later key frame) | found as they enter |
| `person:2` caps | honoured (Detect reads them) | ignored: only the global Max objects applies (`SAM3_VideoTrack` drops the caps) |
| Stencil | hint (exclude points with Select; with text only, drop detected objects mostly inside Stencil) and optional hard cut | hard cut only (forced on, greyed hint) |

Whole range with Select connected is an error that tells the artist to switch mode. Text + Stencil without Select in
key-frame mode: Detect with text alone (no points: negative-only points make Detect return a stray mask), then drop
objects with more than half their key-frame area inside Stencil.

### Outputs (all on the Group's single output, one stream)

- **rgba.alpha**: union of the chosen objects (an Objects knob, default all), with Nuke-side erode and edge blur
  knobs. SAM3 edges are hard and a few pixels loose (spike, below), so softening happens here, no regeneration.
- **Per-object channels**: one channel per object (up to 8).
- **Cryptomatte**: a `CryptoSAM00` layer (id0, cov0, id1, cov1) plus the manifest in metadata, so Nuke's own
  Cryptomatte node, plugged after the Group, picks objects by name or click.
- **Hard stencil** checkbox: when on, the Stencil alpha (key frame, held) is also cut out of the final alpha. Off by
  default; Stencil is otherwise only a hint to SAM3.

RGB passes through untouched.

### Buttons

- **Generate**: render, upload, run, download, add a version.
- **Cancel**: stops a running Generate after the current chunk (see Long and large shots).
- **Make Roto**: from the current version's saved masks, builds an animated Roto node next to the Group. No ComfyUI
  needed.
- **Version** pulldown, like Flux3Klein: every Generate is kept; pick the one shown.

## How Generate works

1. **Render the plate** for the frame range (knobs First/Last, default the Source's range) as an **8-bit sRGB PNG
   sequence**, through the script's default display/view (what the artist sees). Not linear EXR: SAM3 is trained on
   display-referred images. **Resolution (my call, from the builder check):** the key frame goes at full resolution
   (Detect scales its mask to the input size before thresholding, so full res gives cleaner edges); the tracked
   frames go at a **tracking width of about 1024 px** whatever the plate (half res for 2K, quarter for 4K). The
   tracker resizes every frame to 1008² internally and `Track to Mask` only upscales a low-resolution binary mask to
   the batch size, so full-res tracked frames buy nothing but upload and RAM (a 200-frame 2K float batch is ~5 GB,
   doubled by the reversed copy). Masks come back at tracking res and are scaled up in the Group. **Verify** on SH050
   that the edge matches spike run 1 (IoU ≥ 0.98); if not, raise the tracking width.
2. **Read Select and Stencil on the key frame** (knob, default the current frame) at low resolution with `nuke.sample`
   on a grid: inside cells become include points (Select) or exclude points (Stencil), spread out, capped (around 8
   each); Select's bounding box becomes a box hint. Coordinates are pixels, top-left origin, as SAM3 Detect expects
   (Nuke is bottom-left: flip Y).
3. **Upload** the PNGs and run a graph built in code (`sam3matte_graph.py`, no Nuke import, testable in plain Python,
   like `flux3klein_graph.py`). Two shapes:
   - **Whole range**: `SAM3 Video Track` over the whole range with text conditioning. It finds objects as they enter
     (spike run 1). One forward pass.
   - **From key frame**: `SAM3 Detect` on the key frame (points, box, negative points, text if any) gives clean masks; those are the `initial_mask` of two `SAM3 Video Track` passes, key → last and
     key → first on reversed frames (`ReverseImageBatch`). The passes are stitched in Nuke (or in the graph) with the
     key frame from the forward pass (spike run 2).
4. **Per-object masks out**: one `SAM3 Track to Mask` (index k) → `MaskToImage` → `SaveImage` per object slot, 8 slots,
   fixed in the graph. Empty slots save black frames (or are detected and skipped).
5. **Download** into `<temp folder>/<group>_<timestamp>/obj<k>.####.png` (the temp folder is Flux3Klein's
   `_temp_folder()`: `NUKECOMFY_TEMP`, else `../prerender/airender` next to the script), renumbered to the plate's
   frame numbers, and add a version. Empty objects are dropped from the version.

## Long and large shots (user decision: longer shots and 4K are in scope)

Typical is 2K and about 200 frames, but the tool must also handle longer ranges and 4K plates, with no hard length
limit.

- **4K** is handled by the fixed tracking width above: a 4K plate costs the same to track as a 2K one. Only the key
  frame (one image) goes at full 4K, for Detect. The masks are scaled up ×4 in the Group; SAM3 edges are already
  coarse, so this costs nothing visible, but check it on a 4K plate.
- **Long ranges are split into chunks** (knob Chunk size, default 250 frames at tracking res, about 1.6 GB of frames
  per chunk; the builder tunes the default from measurements). Each chunk is its own ComfyUI prompt, run one after
  another from the same background thread, so ComfyUI frees memory between them and the status line can say
  `chunk 2/5`.
  - **From key frame**: chunks run outward from the key frame in both directions. Each chunk starts on the last frame
    of the previous one (one frame of overlap) and is seeded with that frame's masks as `initial_mask`, one per object,
    so object order holds across chunks. The overlap frame is taken from the earlier chunk.
  - **Whole range**: chunk 1 runs with text only. Later chunks start with one frame of overlap and must both carry
    the known objects and keep finding new ones. First try `initial_mask` (previous chunk's last frame) plus text
    conditioning in the same Video Track (untested). If that fails, run each chunk with text only and match objects
    across the overlap frame by IoU (≥ 0.5 = same object; unmatched = new object, next free slot).
  - Objects capped at 8 across the whole shot. Past that, new objects are dropped and the status line says so.
- **Cancel**: a Cancel button stops after the current chunk (and tells ComfyUI to interrupt). Finished chunks are kept
  as a partial version, and Generate can resume from there.
- Uploads per chunk only, so a long shot never sits fully in ComfyUI's input folder. Delete each chunk's uploaded
  frames from the server-side input folder when it finishes, if the API allows; otherwise note the leftovers.

## Model and nodes (from the spike)

- Model: `sam3.1_multiplex_fp16.safetensors` through `CheckpointLoaderSimple` (MODEL + CLIP); prompt through
  `CLIPTextEncode` with that CLIP. The non-multiplex `sam3.safetensors` cannot detect new objects during tracking.
- Prompt syntax: `person:2, ball:1` (comma-separated subjects, `:N` = max objects per subject). Max 32 tokens. The
  `:N` caps only work in From key frame mode; the tooltip says so.
- All SAM3 nodes are core ComfyUI (`comfy_extras/nodes_sam3.py`, ComfyUI v0.43.2). `ReverseImageBatch` is KJNodes.
  Image loading: see Open risks.

## Versions

Each version stores (in a hidden JSON knob, as Flux3Klein does): mask folder, frame range, key frame, object list
(index, name, first/last frame with pixels), prompt, and which hints were used. The pulldown label reads like
`v3  person:2  key 1120  2 objects`.

**Object names** feed the Cryptomatte manifest and the Roto shape names: `<subject>_<n>` when the object can be traced
to a prompt subject, `select` for the Select object, else `obj<k>`.

## Cryptomatte (spike-verified, headless)

Built inside the Group from the per-object channels:

- `CryptoSAM00.red/green` = id and coverage of the strongest object at each pixel; `blue/alpha` = the second.
  One Expression node, one channel per expression (setting `channel0` to the whole layer applies expr0 to all four
  channels: that bug cost one spike run).
- IDs: MurmurHash3_32 of the object name, exponent bits 0 or 255 flipped (`h ^= 1 << 23`), reinterpreted as float32.
  Expression constants written with `%.9g` survive the round trip.
- Metadata via ModifyMetaData: `exr/cryptomatte/<key>/name`, `/hash` = `MurmurHash3_32`, `/conversion` =
  `uint32_to_float32`, `/manifest` = JSON name → 8-digit hex. `<key>` = first 7 hex digits of mmh3("CryptoSAM").
- Verified in Nuke 16.1 `-t`: the native Cryptomatte node lists `CryptoSAM`, `matteList obj0` gives alpha 1 on the
  object and 0 elsewhere, another name gives 0. **Not yet verified:** the click picker in the GUI, and the layer
  surviving a Write to EXR (it must be float32: half breaks the IDs).

## Make Roto

- One Bezier shape per chosen object, named after the object, in one new Roto node created in the Group's parent
  (`with node.parent():`), placed next to the Group, its format set to the plate's.
- Fixed point count per shape (knob, default 32), so the shape animates instead of popping. Per frame: outer contour
  of the largest region of the mask, resampled to N points by arc length, rotated so point 0 is the one nearest the
  previous frame's point 0. A key every frame by default (knob: every N frames).
- Frames where the object is empty: the shape is keyed off (opacity 0 or lifetime), not deformed to a point.
- **Constraint:** Nuke-side code is stdlib + `nuke` only. Reading the masks means decoding 8-bit PNGs with `zlib`
  (stdlib) or sampling a reduced Reformat of the mask with `nuke.sample`. Trace at reduced resolution (around 512
  wide) and scale back up. Target: under a minute for 200 frames × 1 object. Progress via `nuke.ProgressTask`,
  cancellable.

## Flux3Klein

No change to Flux3Klein. SAM3Matte's alpha plugs into Flux3Klein's Mask input (Mask channel = alpha). Note in both
tools' headers.

## Constraints

- Repo rules (root and `tools/comfy/CLAUDE.md`): `.nk` Group built by `build/build_sam3matte.py` with `Nuke16.1.exe -t`,
  never hand-edited; `import NukeComfy.python…` paths; no absolute `L:/` paths in code; no backslashes in string knobs;
  button callbacks create nodes in `with node.parent():`; network and polling off the main thread, DAG changes through
  `executeInMainThreadWithResult`; knob names are API once shipped.
- The Group evaluates without ComfyUI: it reads the saved masks; Generate is the only thing that needs the server.
- Must work against `COMFY_URL` targets other than localhost (`Nuke_ComfyUI.bat` sets RunPod / Tailscale): frames go
  through `/upload/image`, results through `/view`. No shared filesystem assumed.
- Hardware: RTX 3060 12 GB, often with only 4–9 GB free (other apps). Spike ran fine in that state.
- Typical shot: 2K (2048×1080), about 200 frames. Longer shots and 4K must also work (see Long and large shots).

## Spike results (2026-11-01, SH050_crowd Clip-1, frames 1001–1200)

| Run | Setup | Result |
|---|---|---|
| 1 | text "person", Video Track forward, 200 frames | player found as he enters (frame 1012) and held to 1200; tracking 1:42 (≈2 fps); whole prompt 228 s incl. model load and saving 1,000 PNGs |
| 2 | key frame 1120: 2 include points + 1 exclude point → Detect → Video Track forward (81 f) and backward (120 f, reversed) | Detect mask vs. tracker key frame IoU 0.993; stitched sequence vs. run 1 IoU 0.98–0.99; the backward pass finds the entry on its own; seam frame-to-frame IoU 0.94, same as anywhere else (no pop); 127 s |
| 3 | Cryptomatte from run 1 masks in Nuke 16.1 `-t` | works (see above) |

Masks from `Track to Mask` are bilinear-upscaled (256 grey levels at the edge) from a low-resolution binary mask:
8-bit PNG loses nothing. Spike files: `D:\ComfyUI_Cache\temp\sam3_spike` (scripts, graphs) and
`L:\ComfyUI\output\sam3_spike` (masks); throwaway.

## Open risks for the builder to settle (verify, don't assume)

1. **Image loading on a remote ComfyUI.** The spike used `VHS_LoadImagesPath` with a local path, which fails on RunPod.
   Use an input-folder loader (e.g. `VHS_LoadImages` on the uploaded `nukecomfy/<job>` subfolder) and check a
   just-uploaded folder validates; otherwise find another way. Uploading 200 × 2K PNGs (~3–4 MB each) to RunPod is
   slow: measure, and say it in the status line.
2. **Detect with text plus points together** (key-frame mode, Select + Prompt) is untested. If it fails, run Detect
   twice (text; points) and merge the objects. In key-frame mode Video Track gets only `initial_mask`, never text
   conditioning, so it adds no objects of its own and the `:N` caps hold.
3. **Several objects from Select.** v1 (my call): all Select strokes form one object. Multiple objects come from the
   text prompt.
4. **Object order and names** across the forward and backward passes and from text detection: check the indices line
   up when stitching, and whether track data exposes which subject an object came from.
5. **8 channels in one layer**: check Nuke accepts an 8-channel `sam` layer through a Group output and a Write;
   otherwise two 4-channel layers.
6. **Cryptomatte GUI picker** and the EXR Write (float32) round trip.
7. **Long runs**: about 4 minutes per 200 frames, more for long shots. Polling has no timeout (`polling.py`), good;
   the status line must show progress (chunk, frames uploaded, then "Tracking…"), and Nuke must stay usable.
   `start_comfy_process` runs one prompt; chunking needs a thread that runs several in turn.
8. **GPU memory over a long chunk**: the spike only measured 200 frames. Measure VRAM and speed on a 500+ frame run
   before fixing the default chunk size.

## Testing

- `sam3matte_graph.py`: plain-Python tests for both graph shapes (text-only, Select with/without Stencil and text).
- Headless (`/smoke-test`): build the `.nk`, paste it, load the spike masks as a fake version, check alpha, the
  per-object channels, Cryptomatte via a native Cryptomatte node, and Make Roto output (shape count, point count, keys).
- Live acceptance on SH050 frames 1001–1200: text "person" and key-frame Select runs; results match the spike; Viewer
  click-pick in Cryptomatte; Flux3Klein fed by the alpha.
- Chunking: run SH050 with a forced small chunk size (e.g. 60) in both modes and compare with the unchunked run (no
  pop at chunk seams, same object order). Then one long shot (500+ frames) and one 4K plate, real or upscaled.
- Say plainly what was and wasn't run in Nuke.

## Non-goals

- Tracker4 / tracking-point export. That becomes a separate tool built on TAPTrack (follow-up, not designed here).
- Motion-blur or hair detail: SAM3 returns hard edges. Softening is erode/blur only.
- EXR or higher-than-8-bit mask transport.
- Nuke 13 support.

## Anti-goals (would be a failure even if it works)

- Nuke freezing for the length of a run.
- A matte that only exists while ComfyUI is up, or masks written to `C:` or into the repo.
- Shapes in Make Roto that change point count frame to frame.
- Renaming knobs after release.

## Follow-up

- **TAPTrack companion tool** (user, 2026-11-01): "we will need later another nuke tool to use with TAPTrack". Scope
  not defined yet; likely seeds TAPTrack points from a SAM3Matte object and exports Tracker4.
