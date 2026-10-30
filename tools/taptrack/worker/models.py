"""Model registry and one-direction trackers (torch lives here, never in Nuke).

Every tracker implements::

    track(frames_u8, queries_xy, progress) -> (tracks, visible, confidence)

* ``frames_u8``   uint8 RGB ``[T, H, W, 3]``; frame 0 is the query frame (the caller
                  reverses the sequence for the backward pass).
* ``queries_xy``  float ``[Q, 2]``, continuous model-space pixels (centres at +0.5, y down)
                  at frame 0.
* returns         tracks ``[Q, T, 2]`` (x, y) in the same continuous convention,
                  visible ``[Q, T]`` bool, confidence ``[Q, T]`` float in [0, 1].

All calls below are copied from the pinned tapnet sources / demo notebooks, see
docs/TAPNET_NOTES.md.
"""
from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import os
import sys
from pathlib import Path
from typing import Callable, Optional

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
Progress = Callable[[int], None]  # called with number of frames just completed


class WorkerError(Exception):
    """Artist-facing failure. ``code`` is machine readable, ``hint`` is the fix."""

    def __init__(self, code: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


@dataclasses.dataclass(frozen=True)
class ModelSpec:
    key: str
    kind: str                       # "tapnext" | "tapir" | "tapir_online"
    checkpoint: str                 # file name inside checkpoints/
    fixed_res: Optional[tuple]      # (w, h) the net requires, else None
    square: bool                    # frames must be squashed to a square (non-square inputs break the torch TAPIR port)
    default_work: int               # default long side of the work frame
    pixel_centre: str               # "half" (centres at +0.5) | "integer"
    label: str


REGISTRY = {
    "bootstapnext": ModelSpec("bootstapnext", "tapnext", "bootstapnext_ckpt.npz", (256, 256), True, 256, "half", "BootsTAPNext"),
    "tapnext": ModelSpec("tapnext", "tapnext", "tapnext_ckpt.npz", (256, 256), True, 256, "half", "TAPNext"),
    "bootstapir": ModelSpec("bootstapir", "tapir", "bootstapir_checkpoint_v2.pt", None, True, 512, "integer", "BootsTAPIR (offline)"),
    "bootstapir_online": ModelSpec("bootstapir_online", "tapir_online", "causal_bootstapir_checkpoint.pt", None, True, 512, "integer", "BootsTAPIR (online)"),
}


def tapnet_root() -> Path:
    return Path(os.environ.get("TAPTRACK_TAPNET", ROOT / "vendor" / "tapnet"))


def checkpoint_dir() -> Path:
    return Path(os.environ.get("TAPTRACK_CHECKPOINTS", ROOT / "checkpoints"))


def tapnet_commit() -> str:
    f = ROOT / "install" / "tapnet_commit.txt"
    return f.read_text().strip() if f.exists() else "unknown"


def checkpoint_hash(path: Path) -> str:
    """sha256 of the checkpoint, cached in a sidecar keyed by size+mtime."""
    side = path.with_suffix(path.suffix + ".sha256")
    key = f"{path.stat().st_size}:{int(path.stat().st_mtime)}"
    if side.exists():
        k, _, h = side.read_text().partition(" ")
        if k == key:
            return h.strip()
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    try:
        side.write_text(f"{key} {h.hexdigest()}")
    except OSError:
        pass
    return h.hexdigest()


def _import_torch():
    try:
        import torch
    except ImportError as exc:  # pragma: no cover
        raise WorkerError("TORCH_MISSING", "PyTorch is not installed in the TAPTrack environment.",
                          "Run install/install (see the README) to set it up.") from exc
    except OSError as exc:  # e.g. WinError 1114 when a DLL cannot start
        raise classify_exception(exc) from exc
    return torch


def classify_exception(exc: BaseException) -> WorkerError:
    """Turn a low-level exception into a message an artist can act on (the traceback goes to the log)."""
    text = str(exc)
    low = text.lower()
    if isinstance(exc, (KeyError, ValueError)) and not isinstance(exc, UnicodeError) and "cuda" not in low:
        return WorkerError("BAD_JOB", "The tracking job description is incomplete or damaged (%s)." % text[:80],
                           "Press Track again so TAPTrack rebuilds it.")
    if "out of memory" in low:
        return WorkerError("OUT_OF_MEMORY", "The GPU ran out of memory.",
                           "Lower the working resolution, the number of points, or close other GPU programs.")
    if isinstance(exc, OSError) and ("dll" in low or "winerror" in low or "shared object" in low or "libtorch" in low):
        return WorkerError("TORCH_BROKEN", "The tracker's PyTorch installation will not start.",
                           "Run install/check_install to diagnose it, then install/install to repair it.")
    if "cuda" in low or "cudnn" in low or "cublas" in low or "nvidia" in low:
        return WorkerError("GPU_ERROR", "The graphics card reported an error while tracking.",
                           "Update the NVIDIA driver, restart Nuke, or set Device to cpu in the Settings tab (much slower).")
    if isinstance(exc, MemoryError):
        return WorkerError("OUT_OF_MEMORY", "The computer ran out of memory.", "Track fewer frames or fewer points at a time.")
    return WorkerError("UNEXPECTED", "The tracker stopped unexpectedly: %s" % text[:200],
                       "Send the log file to whoever supports this tool.")


def _import_tapnet():
    root = tapnet_root()
    if not (root / "tapnet").is_dir():
        raise WorkerError("TAPNET_MISSING", f"The tapnet source was not found at {root}.",
                          "Run install/setup_venv to fetch the pinned copy.")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))


def resolve_device(requested: str) -> str:
    torch = _import_torch()
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        raise WorkerError("NO_CUDA", "No NVIDIA GPU is available to the tracker.",
                          "Update the NVIDIA driver, or set device to CPU (much slower).")
    return requested


def _autocast(torch, device: str, precision: str):
    if device != "cuda" or precision == "fp32":
        return contextlib.nullcontext()
    dtype = {"fp16": torch.float16, "bf16": torch.bfloat16}[precision]
    return torch.autocast("cuda", dtype=dtype)


class TapNextTracker:
    """TAPNext / BootsTAPNext: causal, frame-recurrent. Net is fixed at 256x256."""

    def __init__(self, spec: ModelSpec, device: str, precision: str, chunk: int):
        torch = _import_torch()
        _import_tapnet()
        from tapnet.tapnext.tapnext_torch import TAPNext
        from tapnet.tapnext.tapnext_torch_utils import restore_model_from_jax_checkpoint, tracker_certainty
        self.torch, self.spec, self.device, self.precision = torch, spec, device, precision
        self.chunk = max(1, chunk)
        self._certainty = tracker_certainty
        path = checkpoint_dir() / spec.checkpoint
        if not path.exists():
            raise WorkerError("WEIGHTS_MISSING", f"Model weights missing: {spec.checkpoint}.",
                              "Run install/download_weights.")
        model = TAPNext(image_size=spec.fixed_res[::-1])
        model = restore_model_from_jax_checkpoint(model, str(path))
        self.model = model.to(device).eval()
        self.checkpoint_path = path

    def track(self, frames_u8: np.ndarray, queries_xy: np.ndarray, progress: Optional[Progress] = None):
        torch = self.torch
        T, Q = len(frames_u8), len(queries_xy)
        # TAPNext query format is (t, y, x) in 256-space, centres at +0.5 (grid_sample align_corners=False)
        q = np.concatenate([np.zeros((Q, 1)), queries_xy[:, ::-1]], axis=1).astype(np.float32)
        qp = torch.from_numpy(q)[None].to(self.device)
        tracks = np.empty((Q, T, 2), np.float32)
        vis = np.empty((Q, T), bool)
        conf = np.empty((Q, T), np.float32)
        state = None
        with torch.inference_mode():
            for s in range(0, T, self.chunk):
                clip = torch.from_numpy(frames_u8[s:s + self.chunk]).to(self.device)[None]
                clip = clip.float() / 255.0 * 2.0 - 1.0            # [1, t, H, W, 3] in [-1, 1]
                with _autocast(torch, self.device, self.precision):
                    if state is None:
                        xy, logits, vlog, state = self.model(video=clip, query_points=qp)
                    else:
                        xy, logits, vlog, state = self.model(video=clip, state=state)
                xy, logits, vlog = xy.float(), logits.float(), vlog.float()   # [1,t,Q,2], [1,t,Q,512], [1,t,Q,1]
                cert = self._certainty(xy, logits, 8)                         # [1,t,Q,1]
                p_vis = torch.sigmoid(vlog)
                e = s + xy.shape[1]
                # Despite the variable names in tapnext_torch.py, the output order is (y, x),
                # the same as the query order (verified empirically); demos flip with [..., ::-1].
                tracks[:, s:e] = xy[0].permute(1, 0, 2).flip(-1).cpu().numpy()
                vis[:, s:e] = (vlog[0, ..., 0] > 0).T.cpu().numpy()
                conf[:, s:e] = (p_vis * cert)[0, ..., 0].T.cpu().numpy()
                if progress:
                    progress(e - s)
        return tracks, vis, conf


class TapirTracker:
    """BootsTAPIR offline (whole sequence at once) or online/causal (frame by frame)."""

    def __init__(self, spec: ModelSpec, device: str, precision: str, chunk: int, window: int = 64):
        torch = _import_torch()
        _import_tapnet()
        from tapnet.torch import tapir_model
        self.torch, self.spec, self.device, self.precision = torch, spec, device, precision
        self.online = spec.kind == "tapir_online"
        self.window = window
        self.chunk = max(1, chunk)
        path = checkpoint_dir() / spec.checkpoint
        if not path.exists():
            raise WorkerError("WEIGHTS_MISSING", f"Model weights missing: {spec.checkpoint}.",
                              "Run install/download_weights.")
        model = tapir_model.TAPIR(pyramid_level=1, use_casual_conv=self.online)
        model.load_state_dict(torch.load(str(path), map_location="cpu"))
        self.model = model.to(device).eval()
        self.checkpoint_path = path

    @staticmethod
    def _visible(torch, occ, dist):
        p = (1 - torch.sigmoid(occ)) * (1 - torch.sigmoid(dist))
        return p > 0.5, p

    def track(self, frames_u8: np.ndarray, queries_xy: np.ndarray, progress: Optional[Progress] = None):
        torch = self.torch
        T, Q = len(frames_u8), len(queries_xy)
        # TAPIR query format is (t, y, x) in video pixels, centres at INTEGER positions
        qi = queries_xy - 0.5
        q = np.concatenate([np.zeros((Q, 1)), qi[:, ::-1]], axis=1).astype(np.float32)
        qp = torch.from_numpy(q).to(self.device)
        with torch.inference_mode():
            if self.online:
                tracks, vis, conf = self._run_online(frames_u8, qp, progress)
            else:
                tracks, vis, conf = self._run_offline(frames_u8, qp, progress)
        return tracks + 0.5, vis, conf

    def _prep(self, u8):
        return (u8.float() / 255.0 * 2.0 - 1.0)

    def _run_offline(self, frames_u8, qp, progress):
        """Offline model, run in temporal windows so VRAM stays bounded (~43 MB/frame at 512^2).

        Windows overlap by one frame; each window after the first is queried at the position
        the previous window ended on (re-query, like chaining), so very long clips can drift slightly.
        """
        torch = self.torch
        T = len(frames_u8)
        win = max(2, self.window)
        tr, vi, co = [], [], []
        start, q = 0, qp
        while True:
            end = min(T, start + win)
            video = self._prep(torch.from_numpy(frames_u8[start:end]).to(self.device))[None]
            with _autocast(torch, self.device, self.precision):
                out = self.model(video, q[None], query_chunk_size=64)
            vis, conf = self._visible(torch, out["occlusion"].float(), out["expected_dist"].float())
            t_ = out["tracks"][0].float()                      # [Q, w, 2] (x, y), integer-centred
            keep = slice(0, None) if start == 0 else slice(1, None)
            tr.append(t_[:, keep].cpu().numpy())
            vi.append(vis[0][:, keep].cpu().numpy())
            co.append(conf[0][:, keep].cpu().numpy())
            if progress:
                progress(end - start - (0 if start == 0 else 1))
            del video, out
            if end >= T:
                break
            last = t_[:, -1]                                   # (x, y) at the window's last frame
            h, w = frames_u8.shape[1:3]
            last = torch.stack([last[:, 0].clamp(0, w - 1), last[:, 1].clamp(0, h - 1)], -1)
            q = torch.cat([torch.zeros_like(last[:, :1]), last.flip(-1)], -1)   # (t=0, y, x)
            start = end - 1
        return np.concatenate(tr, 1), np.concatenate(vi, 1), np.concatenate(co, 1)

    def _run_online(self, frames_u8, qp, progress):
        torch = self.torch
        m = self.model
        T, Q = len(frames_u8), qp.shape[0]
        frame0 = self._prep(torch.from_numpy(frames_u8[:1]).to(self.device))[None]
        with _autocast(torch, self.device, self.precision):
            fg = m.get_feature_grids(frame0, is_training=False)
            qf = m.get_query_features(frame0, is_training=False, query_points=qp[None], feature_grids=fg)
        state = m.construct_initial_causal_state(Q, len(qf.resolutions) - 1)
        for i in range(len(state)):
            for k, v in state[i].items():
                state[i][k] = v.to(self.device)
        tr, vi, co = [], [], []
        for t in range(T):
            frame = self._prep(torch.from_numpy(frames_u8[t:t + 1]).to(self.device))[None]
            with _autocast(torch, self.device, self.precision):
                fg = m.get_feature_grids(frame, is_training=False)
                traj = m.estimate_trajectories(frame.shape[-3:-1], is_training=False, feature_grids=fg,
                                               query_features=qf, query_points_in_video=None,
                                               query_chunk_size=64, causal_context=state,
                                               get_causal_context=True)
            state = traj["causal_context"]
            v, p = self._visible(torch, traj["occlusion"][-1].float(), traj["expected_dist"][-1].float())
            tr.append(traj["tracks"][-1][0].float().cpu().numpy())   # [Q,1,2]
            vi.append(v[0].cpu().numpy())
            co.append(p[0].cpu().numpy())
            if progress:
                progress(1)
        return np.concatenate(tr, 1), np.concatenate(vi, 1), np.concatenate(co, 1)


def load_tracker(model_key: str, device: str, precision: str, chunk: int, window: int = 64):
    if model_key not in REGISTRY:
        raise WorkerError("BAD_MODEL", f"Unknown model '{model_key}'.", "Choose one of: " + ", ".join(REGISTRY))
    spec = REGISTRY[model_key]
    if spec.kind == "tapnext":
        return spec, TapNextTracker(spec, device, precision, chunk)
    return spec, TapirTracker(spec, device, precision, chunk, window)
