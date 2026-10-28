# Changelog

## Unreleased

### Phase 1 — research + worker CLI
- Pinned tapnet at `730cda1`; `docs/TAPNET_NOTES.md` documents the real API (query order, output order, pixel-centre
  conventions, resolutions, visibility/uncertainty outputs).
- Worker CLI (`worker/taptrack_worker.py`) with four models: BootsTAPNext, TAPNext, BootsTAPIR (offline), BootsTAPIR (online).
- Bidirectional tracking from any reference frame (forward + reversed backward pass, ref frame pinned), queries on multiple frames, point batching.
- JSON-line progress/error protocol, result.json with checkpoint hash, tapnet commit, timings, peak VRAM.
- `install/` scripts (venv + pinned tapnet clone, weights downloader).
- Offline BootsTAPIR runs in 64-frame windows (bounded VRAM).
- Tests: coordinate round-trips, synthetic accuracy, bidirectional correctness, VRAM benchmark.

### Phase 2 - Nuke round trip
- `nuke/taptrack/`: `coords` (stdlib mirror of the worker math), `queries` (Tracker4 / grid in mask / full-frame grid), `job` (plan, cache key, render, launch, progress, cancel), `export` (Tracker4), `node` (TAPTrack Group + knobs + callbacks), `menu.py`.
- Hidden render branch Reformat (distort, square) -> OCIOColorSpace (scene-linear -> sRGB) -> Write (8-bit PNG), rendered with `nuke.execute`.
- Worker launched via WMI on Windows (direct child of Nuke cannot load torch), progress tailed from a file, cancel kills the process tree.
- Tracker4 export reverse-engineered with headless Nuke 16.1 and proven by evaluation (`nuke.expression`), text round trip, and a 500x200 write (0.6 s).
- Result cached under a key of plate/range/size/model/queries; stored in a hidden knob, restored on script reopen; stable across save/reopen.
- Tests: 15 in-Nuke tests, coords parity, GUI smoke script.

### Phase 3 - matchmove exports
- `worker/fit.py`: per-frame RANSAC fit (translate / similarity / affine / homography) in square pixels, exact Nuke Transform decomposition (R @ skewX @ S about the centre, desqueezed for pixel aspect), continuity preference for re-acquired points, gap interpolation, Gaussian smoothing (odd reflection, reference frame pinned), automatic corner-pin anchors, per-frame quality and weak-frame reporting.
- Nuke exports: Transform (matchmove and stabilize via `invert_matrix`), CornerPin2D (robust homography corners or raw tracks, user or automatic anchors); Export tab knobs for model, RANSAC, smoothing, corner points.
- Nuke conventions measured with a coordinate image (Nuke 16.1): rotation CCW y-up, linear part acts in desqueezed space on anamorphic formats, translate in plate pixels.
- Exports verified against Nuke's own renderer (0.2 px mean, 0.55 px max) including an anamorphic plate; cache restore on reopen (Phase 2 test) still passing.
- `docs/DRIFT.md` records the drift handling, the evidence, and a correction: the Kling test clip cannot measure tracker drift.

### Phase 4 - polish
- Installers: `install.ps1`/`install.sh` (environment + pinned tapnet + weights + self-test, safe to re-run), `download_weights.*`, `check_install.*` (plain-language diagnosis, optional real self-test), `add_to_nuke.*` (one marked block in `init.py`, idempotent, removable). Native command failures stop with a plain message; Python version, Git and GPU are checked up front.
- Errors: one worker classifier (`models.classify_exception`) turns out-of-memory, broken-DLL, CUDA and damaged-job failures into plain-language messages with a hint; unexpected exceptions in Nuke button callbacks are logged with their traceback and shown as "Something went wrong inside TAPTrack" with the log path; every dialog has a Details line with a real log path; automatic callbacks (script open, knob edits) never pop up dialogs.
- Artist README with install, first-track walkthrough, export types, model guide, honest limits and a troubleshooting table.
- Tests: error messages (worker CLI + in-Nuke), classifier, installer scripts (syntax, `add_to_nuke` idempotence, `check_install` failure messages, a full clean install into a temporary venv).

### Fixes from the live GUI test (monitors.mp4, Nuke 16.1)
- **Export created its node inside the TAPTrack Group**, wired to TAPTrack itself: Nuke runs a Group's button callbacks with that Group as the current context. Nothing appeared in the graph (the earlier "Tracker4 export did nothing"), and the GUI then hung at 100% CPU. Export now works in the Group that holds TAPTrack (root, or an enclosing Group).
- **Pick selected** looked for the selected Tracker4 inside TAPTrack and always said none was selected; it now looks next to TAPTrack. The Tracker4 is resolved by full name from the root.
- **A plate that failed to render** showed "Something went wrong" instead of "Nuke could not render the plate frames" (the error path called `log()` without a message). Test: a Read of missing frames.
- Tests: Export (all four types, top level and nested in a Group) and Pick selected + plan, each called the way the button calls them (`with group:`). The headless tests had called the functions from the root, which is why they missed this.
