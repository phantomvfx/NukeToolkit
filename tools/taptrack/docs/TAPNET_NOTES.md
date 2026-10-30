# TAPNet notes (what the code actually does)

Source: `google-deepmind/tapnet` pinned at **`730cda1c730877cfedbe01bf87fb1cadb78a565d`** (`install/tapnet_commit.txt`),
cloned into `vendor/tapnet` by `install/setup_venv.*`. Everything below was read from that checkout
(`tapnet/tapnext/tapnext_torch.py`, `tapnext_torch_utils.py`, `tapnet/torch/tapir_model.py`, `tapnet/torch/utils.py`,
`colabs/torch_tapnext_demo.ipynb`, `colabs/torch_tapir_demo.ipynb`, `colabs/torch_causal_tapir_demo.ipynb`) and then
**verified empirically** where the source was ambiguous (marked ✔ below). Nothing here is from memory.

Only `torch`, `torchvision`, `einops`, `einshape`, `dm-tree`, `numpy` are needed to run the PyTorch models —
no jax/haiku. The `tapnet/__init__.py` is lazy so importing `tapnet.torch.tapir_model` / `tapnet.tapnext.tapnext_torch`
does not pull jax.

## Models and checkpoints

| Registry key | Class | Checkpoint (Apache-2.0, HF `google/tapnet`) | Size | Mode |
|---|---|---|---|---|
| `bootstapnext` | `tapnext_torch.TAPNext(image_size=(256,256))` | `bootstapnext_ckpt.npz` (Jax format, converted on load) | 777 MB | online (causal, recurrent) |
| `tapnext` | same | `tapnext_ckpt.npz` (GCS only; not on HF) | 777 MB | online |
| `bootstapir` | `tapir_model.TAPIR(pyramid_level=1)` | `bootstapir_checkpoint_v2.pt` | 219 MB | offline (whole clip) |
| `bootstapir_online` | `tapir_model.TAPIR(pyramid_level=1, use_casual_conv=True)` | `causal_bootstapir_checkpoint.pt` | 219 MB | online (causal) |

Loading:
* TAPNext: `restore_model_from_jax_checkpoint(TAPNext(image_size=(256,256)), "…npz")` (hard-wired to the 12×768 ViT/LRU
  architecture), then `.cuda().eval()`. HF also hosts `bootstapnext.pth` (978 MB, a torch file); the documented path is the npz, which is what we use.
* TAPIR: `model.load_state_dict(torch.load("….pt"))`.

Also in the repo but **not wired in** (candidates for a later phase): **TAPNext++** (`tapnextpp_ckpt.pt` 2.5 GB, 256²; `tapnextpp_512.ckpt`, 512², better re-detection after occlusion).

## Input

* Frames: float, RGB, **[-1, 1]** (`x/255*2-1`) for both families. Shape `[B, T, H, W, 3]` (channels last).
* **TAPNext is fixed at 256×256.** `image_pos_emb` is learned on a 32×32 patch grid (patch 8) and the coordinate head
  has **256 bins per axis** (`coordinate_head` outputs 512 = 256 x-bins + 256 y-bins, one bin per pixel). The source can
  bicubically interpolate the positional embedding to larger inputs, but the head can then still only emit coordinates
  in `[0, 256]`, so larger inputs are not usable. Input is **squashed** to 256×256 regardless of aspect (this is
  also what the paper's DAVIS evaluation does).
* **TAPIR** accepts any size that is a multiple of 8. Internally it runs one pass at `initial_resolution=(256,256)`
  and refinement passes at `generate_default_resolutions(video_hw, (256,256))` (log-spaced up to the input size; for 512² that
  is `[(256,256),(512,512)]`). BootsTAPIR is documented as best at 512×512 (README).
  ✔ **Non-square input is broken in the PyTorch port**: 512×288 and 640×360 gave 28–30 px mean error on our synthetic
  clip while 256², 512² gave 0.5/0.44 px. **Rule: every model is fed a squashed square frame**
  (TAPNext 256², TAPIR 512² by default). Plate↔work conversion stays a pure per-axis scale, so squashing costs nothing in coordinates.

## Query format

* Order is **`(t, y, x)`** for both families (verified in `embed_queries`: `query_points[..., :1]` is time,
  `[..., 1:]` is `(y, x)`; TAPIR demo `convert_select_points_to_query_points` builds `[t, y, x]`). ✔ (probed: feeding `(t, 190, 60)` returns
  a track starting at ≈(190, 60)).
* `t` is a frame index into the clip given to the model. For TAPNext's online API, queries are given on the first call and
  **only queries with `t == 0` of the first chunk are used**; later frames are fed with `state=` and no queries.
  To query frame k, start the clip at frame k.
* **Pixel-centre convention differs between the families.**
  * TAPNext: continuous, centres at **+0.5** (`grid_sample(align_corners=False)` on `pos/size*2-1`; heads add `+0.5` to the soft-argmax bin
    index). Same as Nuke. ✔ (frame-0 output equals the query to 0.1 px).
  * TAPIR: **integer-centred** — `utils.convert_grid_coordinates` adds 0.5, scales, subtracts 0.5 (pixel *i* has its centre at *i*).
    So the worker shifts queries by −0.5 and outputs by +0.5.

## Output

| | TAPNext (`model(video=…, query_points=…)` then `model(video=…, state=…)`) | TAPIR (`model(video, query_points)` / `estimate_trajectories`) |
|---|---|---|
| tracks | `[B, T, Q, 2]`, **order is (y, x)** | `tracks` `[B, Q, T, 2]`, **order is (x, y)** |
| visibility | `visible_logits [B,T,Q,1]`, visible = logit > 0 | `occlusion` logits `[B,Q,T]` (higher = occluded) |
| uncertainty | `track_logits [B,T,Q,512]` → `tracker_certainty(tracks, logits, radius=8)` = probability mass within 8 px of the prediction | `expected_dist` logits `[B,Q,T]` (higher = farther from truth) |
| state | `TAPNextTrackingState` (step, query_points, per-block LRU cache) | `causal_context` list (online model only) |

⚠ TAPNext's head names its two halves `tracks_x, tracks_y` and `torch.cat`s them in that order, but the logits are *indexed by the query's
coordinate order*, i.e. the result is **(y, x)** — the official notebooks undo this with `tracks[..., ::-1]`. ✔ (probed with an asymmetric query:
`query (t, 190, 60)` → output `(189.9, 60.0)`; our first synthetic run was off by ~145 px until this was fixed.)

Visibility used by the worker (the notebooks' defaults):
* TAPNext: `visible = visible_logit > 0`; `confidence = sigmoid(visible_logit) * certainty`.
* TAPIR: `confidence = (1-σ(occlusion))·(1-σ(expected_dist))`; `visible = confidence > 0.5` (from `postprocess_occlusions`).

## Offline / online, chunking

* TAPNext is recurrent: `state` carries the LRU cache, so the clip can be fed in chunks of any length `t` (first call: queries + `t` frames;
  later calls: `state` + `t` frames). ✔ chunk 1, 4, 24 give identical tracks (mean difference < 0.001 px).
  Memory scales with `t × (1024 patch tokens + Q point tokens)`.
* BootsTAPIR (offline) computes features for the whole clip and cost volumes per query chunk (`query_chunk_size=64`). The same API,
  fed `frames[ref:]` and the reversed `frames[ref::-1]`, gives the two passes.
* BootsTAPIR (online) = `get_feature_grids` + `get_query_features` on frame 0, then `estimate_trajectories(..., causal_context=state)` frame by frame.

## Bidirectional tracking (our design, all models)

`ref → last` forward and `ref → first` on the reversed sequence (query `t=0` in both), merged; the reference frame is pinned to the query
exactly (`visible=True`, `confidence=1`). Queries on different frames are grouped per frame. TAPIR offline could do this natively
(queries at any `t`), but using the same two-pass scheme for every model keeps behaviour consistent and halves peak memory.

## Precision

✔ Measured on the synthetic clip (mean error, 640×360 plate, 24 frames): BootsTAPNext fp16 0.261 / bf16 0.264 / fp32 0.261 px;
BootsTAPIR fp16 0.414 / bf16 0.440 / fp32 0.419 px. fp16 is the default for all models (fp32 is 3× slower on TAPNext).

## Known limits to expect in production

* Accuracy is bounded in **model** pixels (≈0.2–0.4 px at 256²/512²), so in **plate** pixels it scales with `plate_w / model_w`.
  On our 640-px plate that is ≈0.3 px; on a 1920-px plate expect ≈1–3 px from TAPNext@256 and ≈0.5–1.5 px from TAPIR@512.
  Good for matchmoves with a final snap/refinement, not a replacement for a sub-pixel planar tracker.
* Non-bootstrapped `tapnext` is clearly weaker than `bootstapnext`; it is kept for A/B only.

## Measured performance (RTX 3060 12 GB, 500 points x 200 frames, fp16, one bidirectional job)

| Model | Work res | torch peak (reserved) | nvidia-smi peak* | Track time | Speed |
|---|---|---|---|---|---|
| BootsTAPNext | 256² | 4.5 GB | 9.4 GB | 57 s | 3.5 frame-passes/s |
| BootsTAPIR offline (64-frame windows) | 512² | 3.8 GB | 8.7 GB | 70 s | 2.9 frame-passes/s |
| BootsTAPIR online | 512² | 3.0 GB | 7.9 GB | 1136 s | 0.2 frame-passes/s |

*includes ~4.7 GB already held by the desktop/other apps at idle; the worker's own footprint is ≈ the torch figure + CUDA context. All under the 11 GB limit.
Model load: 5-15 s. Offline TAPIR without windowing needs ~43 MB/frame (200 frames spills past 12 GB), hence `window` (default 64).
Online BootsTAPIR is impractically slow with many points; keep it for few-point jobs.
