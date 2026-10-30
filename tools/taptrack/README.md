# TAPTrack for Nuke

Track points on a plate with DeepMind's point trackers (BootsTAPNext, TAPNext, BootsTAPIR) and get **ordinary Nuke nodes**
back: a **Tracker4**, a **CornerPin2D**, or a **Transform** (matchmove or stabilize). The nodes you export have no dependency on this
tool: they work on a machine that doesn't have it.

1. Plug a Read into **TAPTrack**.
2. Say where the points come from: a Tracker4 you've placed, a Roto mask filled with a grid, or a grid over the whole frame.
3. Press **Track**.
4. Press **Export**.

The AI model never runs inside Nuke. TAPTrack renders your plate, hands it to a separate tracker program on your GPU, and reads the result
back. Nuke stays responsive while it works.

---

## Install

You need: Nuke 16 or 17 (both pass the full automated test suite in terminal mode; the Track button's progress/cancel path was also run in the 16.1 GUI, and the 17 GUI has not been tried), an NVIDIA graphics card with 8 GB or more
(12 GB recommended), Git, and Python 3.10-3.12 (only used to build the tracker's private environment). Windows is the tested platform;
Linux scripts are included but have not been run on a Linux machine. macOS is CPU only and slow.

**Windows**

```
powershell -ExecutionPolicy Bypass -File install\install.ps1
powershell -ExecutionPolicy Bypass -File install\add_to_nuke.ps1
```

**Linux / macOS**

```
install/install.sh
install/add_to_nuke.sh
```

`install` builds the private environment (about 2.5 GB of downloads for PyTorch), fetches the exact tested tapnet source, downloads
the model weights (about 1 GB for the two default models; add `-AllModels` / `--all-models` for all four, about 3 GB), and finishes
with a quick real test. It is safe to run again; it only repairs what is missing. Downloads go to the `checkpoints/` folder inside
this one; the weights are Apache-2.0 licensed and are never part of the repository.

`add_to_nuke` adds one clearly marked block to your Nuke `init.py` (`~/.nuke/init.py` unless you pass `-NukeDir`). It touches nothing
else, and `-Remove` takes it out again. Restart Nuke, then use **Nodes > TAPTrack > TAPTrack**.

**Check it any time:** `install\check_install.ps1 -SelfTest` (or `install/check_install.sh --selftest`) lists what is fine and what
to fix, in plain language.

---

## Your first track

1. Create the node (Nodes > TAPTrack) with your plate selected. The plate goes into the first input.
2. Go to the frame where the things you want to track are **clearly visible and not hidden**; this is the *reference frame*. Press
   **Current frame** next to *reference frame*. It can be in the middle of the shot: TAPTrack tracks forward and backward from it.
3. Choose where the points come from (**points from**):
   * **Tracker4**: place tracks in a Tracker4 the normal way, select that Tracker4, press **Pick selected**. Their positions on the
     reference frame are used (tracks that are disabled there are skipped).
   * **Grid in mask**: connect a Roto (or any mask) to the second input. A grid of points is placed wherever its alpha is above 50%
     on the reference frame. *grid spacing* and *max points* control the density.
   * **Full-frame grid**: the same grid over the whole frame.

   The grid is quick but blind: if something covers part of the surface on the reference frame (a thumb on a phone screen, a
   person in front of a sign), grid points land on it and follow it instead of the surface. For those shots use **Tracker4**:
   you only *place* the tracks (don't track them in the Tracker4), on features that belong to the surface and stay clear of the
   occluder. Six to twelve well-spread points work better than four: with exactly four, outlier rejection has nothing to compare.
4. Press **Track**. A progress bar shows the work, **Cancel** stops it. When it finishes, the *status* line shows the number of
   points, the percentage visible and the time.
5. Go to the **Export** tab, choose what you want and press **Export**. The new node appears below TAPTrack, connected to it.

The result is stored inside your script. Close and reopen it and **Export** still works without tracking again. If you change the plate,
frame range, points or settings, Track runs again; otherwise it reuses the stored result instantly.

### Export types

| Output | What you get | Good for |
|---|---|---|
| **Tracker4** | Every tracked point as a track. Frames where a point is hidden have its *enable* switched off. | Your own further work; picking points by hand. |
| **CornerPin2D** | Four corners that follow the plate. By default they come from a perspective fit to *all* points, so they survive a corner being hidden. | Screens, signs, posters, any flat surface. |
| **Transform - Matchmove** | One motion fitted to all points (translate, translate+rotate+scale, or affine), keyed on every frame. | Locking an element to the plate. |
| **Transform - Stabilize** | The same motion inverted. | Locking the plate to the reference frame. |

Other Export settings:

* **reject outliers (RANSAC)**: on by default. On each frame the fit uses the points that agree with each other and ignores the rest.
* **ignore re-acquired points when possible**: on by default. A point that was hidden and then found again can come back slightly
  off. This uses only points that stayed visible since the reference frame, unless there aren't enough of them.
* **smoothing / smooth (frames)**: optional Gaussian smoothing of the motion. The reference frame stays exact and straight camera
  moves are not flattened.
* **corner pin points**: four point numbers (the Tracker4 row numbers), or empty to let TAPTrack pick four well-spread, reliable points.
* **corner pin from all points**: untick to make the corners follow the four raw tracks instead.

If the motion could not be fitted reliably on some frames (usually because most of the tracked area was hidden), Export tells you which
frames to check; those frames are filled in from their neighbours.

---

## Which model and resolution?

| Model | Use it when |
|---|---|
| **BootsTAPNext** (default) | Almost always. Best accuracy in our tests, fast. Always works at 256x256. |
| **BootsTAPIR (offline)** | You want a second opinion, or a higher working resolution (512x512 by default). |
| **TAPNext** | Comparing with the bootstrapped version; it is measurably weaker. |
| **BootsTAPIR (online)** | Few points only. With many points it is very slow. |

Frames are squashed to a square for the network, whatever the plate's shape; positions are mapped back exactly, including anamorphic plates.
**working resolution**: *Auto* is each model's native size. TAPNext is fixed at 256; BootsTAPIR can use 256, 384, 512 or a custom
(multiple of 8) size.

The plate is converted from scene-linear to sRGB before tracking because the models were trained on ordinary video. The *OCIO in / OCIO out*
fields in **Settings** pick the conversion (they default to `scene_linear` and an sRGB output); change them if your colour setup differs.

---

## What to expect (please read)

* **Accuracy is limited by the working resolution, not the plate.** The models are accurate to about a quarter to a half of a
  *model* pixel. On a 640-pixel-wide plate that is about 0.3 px; on a 1920-pixel plate expect roughly 1-3 px with BootsTAPNext and 0.5-1.5 px
  with BootsTAPIR. That is excellent for matchmoves, blocking and stabilizing, and **not** a replacement for a sub-pixel planar tracker
  when you need 0.1 px.
* **Hidden and re-found points can come back offset.** The fits ignore such points whenever there are enough others. We have shown
  this works on generated tests (see `docs/DRIFT.md`) but have **not yet tested it on real footage with a real occlusion**; if you
  try it on a shot like that, please look at the result.
* **Frame ranges and reference frame:** the reference frame must be inside the frame range. Points are tracked forward and backward from it, so
  it should be a frame where they are all visible.
* Tested speed (RTX 3060 12 GB): 500 points over 200 frames takes about a minute with BootsTAPNext (256) or BootsTAPIR (512), using
  well under 6 GB of video memory.

---

## Troubleshooting

Every message TAPTrack shows has a **Details** line pointing at a log file in `~/.taptrack/logs/`. Send that file with any report.

| You see | What it means / what to do |
|---|---|
| *No module named 'taptrack'* (when pressing a button) | This Nuke was started without TAPTrack on its plugin path (common when reopening a saved script). Run `install\add_to_nuke.ps1` once and restart Nuke, or open the script with `examples\open_demo.ps1 -Script <name>.nk`. |
| *Model weights missing - run install/download_weights.* | The models were not downloaded. Run `install\download_weights.ps1`. |
| *The TAPTrack tracker environment is not installed.* | Run `install\install.ps1`, or set **worker Python** in the Settings tab if it lives elsewhere. |
| *No NVIDIA graphics card is available to the tracker* | Update the NVIDIA driver, or set **device** to `cpu` in Settings (much slower). |
| *The GPU ran out of memory.* | Lower the working resolution or the number of points, or close other programs using the GPU. |
| *The tracker's PyTorch installation will not start.* | Run `install\check_install.ps1`, then `install\install.ps1` to repair. |
| *The mask is empty on frame N* | No grid point falls inside the mask on the reference frame. Check the mask or lower *grid spacing*. |
| *Pick the Tracker4 to take points from* | Select a Tracker4 and press **Pick selected**. |
| *The motion could not be fitted reliably on frame(s) ...* | Most of the tracked area was hidden there. Check those frames, or track more points. |

Other things worth knowing:

* **Where results live:** in the script (a link to a cached result) and in the cache folder (*Settings > cache folder*; by default
  `$NUKE_TEMP_DIR\taptrack_cache` if that is set, otherwise `~/.taptrack/cache`). **Clear cache** deletes the frames and result for the
  current setup. The cache can be deleted at any time; Track simply runs again.
* **Backslashes in paths:** Nuke turns backslashes in text fields into escape codes when it saves a script, so TAPTrack converts the
  path fields to forward slashes automatically.
* **Windows:** TAPTrack starts the tracker through Windows' own process service. Starting it directly from Nuke makes PyTorch fail to
  load on at least one machine we tried (error 1114); this route avoids it.
* **Uninstall:** `install\add_to_nuke.ps1 -Remove`, then delete the TAPTrack folder. Nodes you exported keep working.

---

## For developers

* `docs/TAPNET_NOTES.md` - what the tapnet code actually does (query order, output order, pixel-centre conventions, resolutions) and measured speed/VRAM.
* `docs/DRIFT.md` - post-occlusion drift: handling, evidence, and what is not yet shown.
* `CLAUDE.md` - architecture, protocol and the hard-won facts (Nuke conventions, Tracker4 table format, Windows launch).
* Layout: `worker/` (PyTorch side, runs in the private environment), `nuke/taptrack/` (runs inside Nuke: stdlib only, no PyTorch),
  `install/`, `tests/`, `docs/`.
* Tests: `pytest` (fast, GPU accuracy and bidirectional tests; needs weights), `pytest -m nuke tests/test_nuke.py` (runs
  `tests/nuke_tests/` inside headless Nuke; set `TAPTRACK_NUKE` to choose the executable), `pytest -m slow tests/test_vram.py`.
* `CHANGELOG.md` lists what changed in each phase.
