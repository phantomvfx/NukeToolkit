"""TAPTrack worker CLI: reads job.json, writes result.json.

    python taptrack_worker.py job.json [--result result.json]

stdout carries ONLY JSON lines: ``{"progress": 0.42, "msg": "..."}`` and, on failure,
``{"error": {"code", "message", "hint", "log"}}`` followed by a non-zero exit code.
Everything else (tracebacks, library noise) goes to the log file in ~/.taptrack/logs/.

Job format (version 1), plate coordinates in Nuke convention (origin bottom-left):

    {"version": 1, "model": "bootstapnext", "frames_dir": "...", "frame_pattern": "f.%04d.png",
     "first": 1001, "last": 1100, "ref_frame": 1040,
     "plate_w": 1920, "plate_h": 1080, "pixel_aspect": 1.0,
     "work_w": 512, "work_h": 512,
     "queries": [[1040, 812.5, 410.0]],        # [frame, x, y]; [x, y] uses ref_frame
     "device": "cuda", "precision": "fp16", "chunk": 16, "window": 64, "points_per_pass": 1000}
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coords  # noqa: E402
import io_frames  # noqa: E402
import models  # noqa: E402

LOG_DIR = Path(os.environ.get("TAPTRACK_LOG_DIR", Path.home() / ".taptrack" / "logs"))
_log_file = None


def log(msg: str) -> None:
    if _log_file:
        _log_file.write(msg + "\n")
        _log_file.flush()


def emit(**obj) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


class Progress:
    """Maps frames-processed counts onto 0..1 over the whole job."""

    def __init__(self, total_frames: int):
        self.total = max(1, total_frames)
        self.done = 0
        self.last_emit = 0.0

    def add(self, n: int, msg: str = "") -> None:
        self.done += n
        now = time.time()
        if now - self.last_emit > 0.25 or self.done >= self.total:
            self.last_emit = now
            emit(progress=round(min(1.0, self.done / self.total), 4), msg=msg)


def normalise_queries(job: dict) -> np.ndarray:
    """-> float64 [Q, 3] of (frame, x, y)."""
    rows = []
    for q in job["queries"]:
        if len(q) == 2:
            rows.append([job["ref_frame"], q[0], q[1]])
        elif len(q) == 3:
            rows.append(list(q))
        else:
            raise models.WorkerError("BAD_JOB", f"Bad query {q}: expected [frame, x, y].")
    arr = np.asarray(rows, dtype=np.float64)
    if arr.size == 0:
        raise models.WorkerError("BAD_JOB", "The job has no points to track.",
                                 "Add tracks to the Tracker or draw a mask for the grid.")
    out_of_range = (arr[:, 0] < job["first"]) | (arr[:, 0] > job["last"])
    if out_of_range.any():
        raise models.WorkerError("BAD_JOB", "A query point lies outside the frame range.")
    return arr


def track_group(tracker, frames_u8, first, frame_q, q_xy_model, progress, label):
    """Bidirectional track of queries that all live on ``frame_q``. Returns arrays over ALL frames.

    Forward pass: frames frame_q..last. Backward pass: frame_q..first, order reversed.
    The query frame itself is pinned to the query position afterwards.
    """
    T = len(frames_u8)
    i0 = frame_q - first
    Q = len(q_xy_model)
    tracks = np.zeros((Q, T, 2), np.float32)
    vis = np.zeros((Q, T), bool)
    conf = np.zeros((Q, T), np.float32)

    tf, vf, cf = tracker.track(frames_u8[i0:], q_xy_model, lambda n: progress.add(n, f"{label} forward"))
    tracks[:, i0:], vis[:, i0:], conf[:, i0:] = tf, vf, cf
    if i0 > 0:
        tb, vb, cb = tracker.track(np.ascontiguousarray(frames_u8[i0::-1]), q_xy_model, lambda n: progress.add(n, f"{label} backward"))
        # tb index k is frame i0-k ; k=0 is the query frame (already set by forward)
        tracks[:, :i0] = tb[:, :0:-1]
        vis[:, :i0] = vb[:, :0:-1]
        conf[:, :i0] = cb[:, :0:-1]
    # Pin the query frame exactly to the query.
    tracks[:, i0] = q_xy_model
    vis[:, i0] = True
    conf[:, i0] = 1.0
    return tracks, vis, conf


def run(job: dict, result_path: Path) -> None:
    t_start = time.time()
    if job.get("version") != 1:
        raise models.WorkerError("BAD_JOB", f"Unsupported job version {job.get('version')}.")
    first, last = int(job["first"]), int(job["last"])
    T = last - first + 1
    plate_w, plate_h = float(job["plate_w"]), float(job["plate_h"])
    work_w, work_h = int(job["work_w"]), int(job["work_h"])
    device = models.resolve_device(job.get("device", "auto"))
    precision = job.get("precision", "fp16")
    spec = models.REGISTRY.get(job["model"])
    if spec is None:
        raise models.WorkerError("BAD_JOB", f"Unknown model '{job['model']}'.")
    if spec.fixed_res:
        model_w, model_h = spec.fixed_res
    elif spec.square:
        side = max(work_w, work_h) // 8 * 8
        model_w = model_h = side
    else:
        model_w, model_h = work_w, work_h
    queries = normalise_queries(job)

    emit(progress=0.0, msg=f"Loading {spec.label}")
    tracker = models.load_tracker(job["model"], device, precision, int(job.get("chunk", 16)),
                                  int(job.get("window", 64)))[1]
    ckpt = tracker.checkpoint_path
    t_loaded = time.time()

    emit(progress=0.0, msg="Reading frames")
    try:
        frames = io_frames.load_frames(job["frames_dir"], job["frame_pattern"], first, last, (model_w, model_h))
    except io_frames.FrameError as exc:
        raise models.WorkerError("FRAMES_MISSING", str(exc), "Re-run Track so the plate is rendered again.") from exc

    t_frames = time.time()
    # plate -> work image -> model space (all continuous, centres at +0.5)
    work_xy = coords.plate_to_work(queries[:, 1:3], plate_w, plate_h, work_w, work_h)
    model_xy = coords.scale_image(work_xy, work_w, work_h, model_w, model_h)

    frames_per_group = {}
    for fq in np.unique(queries[:, 0]).astype(int):
        frames_per_group[fq] = (last - fq + 1) + (fq - first + 1 if fq > first else 0)
    pts_per_pass = int(job.get("points_per_pass", 1000))
    n_passes = {fq: -(-int((queries[:, 0] == fq).sum()) // pts_per_pass) for fq in frames_per_group}
    progress = Progress(sum(frames_per_group[fq] * n_passes[fq] for fq in frames_per_group))

    Q = len(queries)
    tracks_m = np.zeros((Q, T, 2), np.float32)
    vis = np.zeros((Q, T), bool)
    conf = np.zeros((Q, T), np.float32)
    torch = models._import_torch()
    if device == "cuda":
        torch.cuda.reset_peak_memory_stats()
    for fq in sorted(frames_per_group):
        idx = np.where(queries[:, 0] == fq)[0]
        for s in range(0, len(idx), pts_per_pass):
            sel = idx[s:s + pts_per_pass]
            label = f"frame {fq}, points {s + 1}-{s + len(sel)}"
            try:
                tr, vi, co = track_group(tracker, frames, first, int(fq), model_xy[sel], progress, label)
            except torch.cuda.OutOfMemoryError as exc:
                raise models.classify_exception(exc) from exc
            tracks_m[sel], vis[sel], conf[sel] = tr, vi, co

    t_tracked = time.time()
    # model -> work -> plate
    tracks_w = coords.scale_image(tracks_m, model_w, model_h, work_w, work_h)
    tracks_p = coords.work_to_plate(tracks_w, plate_w, plate_h, work_w, work_h)
    # Pin the query frame to the original plate-space query exactly (no round-trip drift).
    for qi in range(Q):
        tracks_p[qi, int(queries[qi, 0]) - first] = queries[qi, 1:3]

    elapsed = time.time() - t_start
    peak = {}
    if device == "cuda":
        peak = {"peak_vram_allocated_mb": round(torch.cuda.max_memory_allocated() / 2**20, 1),
                "peak_vram_reserved_mb": round(torch.cuda.max_memory_reserved() / 2**20, 1),
                "gpu": torch.cuda.get_device_name(0)}
    result = {
        "version": 1,
        "first": first, "last": last,
        "query_frames": queries[:, 0].astype(int).tolist(),
        "tracks": np.round(tracks_p, 3).tolist(),            # [Q][T][x, y] plate px, Nuke convention
        "visible": vis.astype(int).tolist(),                  # [Q][T]
        "confidence": np.round(conf, 4).tolist(),             # [Q][T]
        "meta": {
            "model": job["model"], "checkpoint": ckpt.name, "checkpoint_sha256": models.checkpoint_hash(ckpt),
            "tapnet_commit": models.tapnet_commit(), "elapsed_s": round(elapsed, 2),
            "load_s": round(t_loaded - t_start, 2), "frames_s": round(t_frames - t_loaded, 2),
            "track_s": round(t_tracked - t_frames, 2),
            "frame_passes": int(sum(frames_per_group[fq] * n_passes[fq] for fq in frames_per_group)),
            "device": device, "precision": precision, "work_w": work_w, "work_h": work_h,
            "model_w": model_w, "model_h": model_h, "plate_w": plate_w, "plate_h": plate_h,
            "pixel_aspect": job.get("pixel_aspect", 1.0), "points": Q, "frames": T,
            "percent_visible": round(float(vis.mean()) * 100, 1), **peak,
        },
    }
    tmp = result_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(result))
    os.replace(tmp, result_path)
    emit(progress=1.0, msg=f"Done: {Q} points x {T} frames in {elapsed:.1f}s")


def main(argv=None) -> int:
    global _log_file
    ap = argparse.ArgumentParser()
    ap.add_argument("job")
    ap.add_argument("--result", default=None)
    args = ap.parse_args(argv)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / f"worker-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.log"
    _log_file = open(log_path, "w", encoding="utf8")
    sys.stderr = _log_file  # keep library noise off the protocol stream
    try:
        job_path = Path(args.job)
        job = json.loads(job_path.read_text())
        result_path = Path(args.result) if args.result else job_path.with_name("result.json")
        log(f"job: {job_path}")
        run(job, result_path)
        return 0
    except models.WorkerError as exc:
        log(traceback.format_exc())
        emit(error={"code": exc.code, "message": exc.message, "hint": exc.hint, "log": str(log_path)})
    except Exception as exc:  # noqa: BLE001
        log(traceback.format_exc())
        err = models.classify_exception(exc)
        emit(error={"code": err.code, "message": err.message, "hint": err.hint, "log": str(log_path)})
    return 1


if __name__ == "__main__":
    sys.exit(main())
