"""Job handling for the TAPTrack node: plan, render frames, run the worker, cache.

Flow (all ``nuke.*`` calls on the main thread; only the subprocess pipe is read off-thread):

1. :func:`prepare`   collect queries, compute the cache key, build ``job.json``  (main thread)
2. :func:`render_frames`  run the hidden Reformat -> OCIO -> Write branch with ``nuke.execute``
3. :func:`run_worker`     launch the venv python, parse JSON progress lines  (thread or blocking)
4. the caller stores ``result_path`` in the node's hidden knob so the result survives a reopen.

No state lives in this module: everything is in the node's knobs or the arguments.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from . import coords, queries

ROOT = Path(__file__).resolve().parents[2]            # <taptrack>/
JOB_VERSION = 1

# label shown in the UI -> worker model key
MODELS: List[Tuple[str, str]] = [
    ("BootsTAPNext", "bootstapnext"),
    ("TAPNext", "tapnext"),
    ("BootsTAPIR (offline)", "bootstapir"),
    ("BootsTAPIR (online)", "bootstapir_online"),
]
# model key -> (native square side the net needs / works best at, fixed?)
NATIVE_SIDE = {"bootstapnext": (256, True), "tapnext": (256, True),
               "bootstapir": (512, False), "bootstapir_online": (512, False)}
WORK_CHOICES = ["Auto", "256", "384", "512", "Custom"]


class TapTrackError(Exception):
    """Error shown to the artist: plain-language ``message`` plus ``details`` (usually a log path)."""

    def __init__(self, message: str, details: str = ""):
        super().__init__(message)
        self.message, self.details = message, details

    def text(self) -> str:
        return self.message + ("\n\nDetails: " + self.details if self.details else "")


# --------------------------------------------------------------------------- paths / logging
def log_dir() -> Path:
    d = Path(os.environ.get("TAPTRACK_LOG_DIR", Path.home() / ".taptrack" / "logs"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def log(msg: str) -> str:
    """Append a line to today's Nuke-side log; returns the log path."""
    path = log_dir() / ("nuke-%s.log" % time.strftime("%Y%m%d"))
    with open(path, "a", encoding="utf8") as f:
        f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
    return str(path)


def default_python() -> str:
    env = os.environ.get("TAPTRACK_PYTHON")
    if env:
        return env
    sub = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return str(ROOT / ".venv" / sub)


def default_cache_dir() -> str:
    env = os.environ.get("TAPTRACK_CACHE")
    if env:
        return env
    tmp = os.environ.get("NUKE_TEMP_DIR")
    if tmp:
        return os.path.join(tmp, "taptrack_cache")
    return str(Path.home() / ".taptrack" / "cache")


def knob_path(path) -> str:
    """Path text safe to store in a string knob: forward slashes only (Nuke treats backslashes in knob
    values as escapes, so ``C:\temp\new`` would come back from a saved script as ``C:<TAB>emp<NL>ew``)."""
    return str(path).replace(chr(92), "/")


def worker_script() -> str:
    return str(ROOT / "worker" / "taptrack_worker.py")


# --------------------------------------------------------------------------- planning
def work_side(model_key: str, choice: str, custom: int) -> int:
    """Square side of the frames handed to the worker (multiple of 8). Fixed-size nets ignore the choice."""
    native, fixed = NATIVE_SIDE[model_key]
    if fixed or choice == "Auto":
        return native
    side = custom if choice == "Custom" else int(choice)
    return max(64, int(round(side / 8.0)) * 8)


@dataclass
class Plan:
    """Everything needed to run (or restore) one tracking job."""

    job: Dict
    key: str
    job_dir: Path
    frames_dir: Path
    result_path: Path
    cached: bool
    side: int
    first: int
    last: int
    ref: int
    names: List[str] = field(default_factory=list)


def plate_info(group) -> Dict:
    """Format (never the bbox), pixel aspect and frame range of the plate on input 0."""
    src = group.input(0)
    if src is None:
        raise TapTrackError("Connect the plate (a Read or any image) to the TAPTrack input.")
    fmt = src.format()
    return {"plate_w": int(fmt.width()), "plate_h": int(fmt.height()), "pixel_aspect": float(fmt.pixelAspect())}


def input_range(group) -> Tuple[int, int]:
    """First/last frame of the plate on input 0 (falls back to the script range)."""
    import nuke

    src = group.input(0)
    for node in ([src] if src is not None else []):
        try:
            a, b = int(node.firstFrame()), int(node.lastFrame())
            if b >= a:
                return a, b
        except Exception:  # noqa: BLE001
            pass
    return int(nuke.root().firstFrame()), int(nuke.root().lastFrame())


_READ_KNOBS = ("file", "first", "last", "origfirst", "origlast", "colorspace", "premultiplied", "raw",
               "frame_mode", "frame", "before", "after", "on_error")
_VOLATILE = re.compile(r"^\s*(xpos|ypos|selected|hide_input|note_font\w*|tile_color|gl_color|label)\b")


def plate_signature(group) -> str:
    """Hash of everything upstream of input 0 (parameters + file paths), ignoring node-graph layout."""
    import nuke

    seen, stack, parts = set(), [group.input(0)], []
    while stack:
        n = stack.pop()
        if n is None or n.fullName() in seen:
            continue
        seen.add(n.fullName())
        if n.Class() == "Read":     # Read gains internal OCIO mirror knobs after a reopen: hash what defines pixels
            script = "|".join("%s=%s" % (k, n[k].value()) for k in _READ_KNOBS if k in n.knobs())
            script += "|size=%dx%d" % (n.width(), n.height())
        else:
            script = "\n".join(l for l in n.writeKnobs(nuke.TO_SCRIPT | nuke.TO_VALUE).splitlines()
                               if not _VOLATILE.match(l))
        parts.append("%s|%s|%s" % (n.Class(), n.name(), script))
        stack.extend(n.dependencies())
    return hashlib.sha1("\n".join(sorted(parts)).encode("utf8")).hexdigest()


def cache_key(model: str, first: int, last: int, side: int, precision: str, plate_sig: str,
              queries_: List[List[float]]) -> str:
    """Key over (plate, frame range, working res, model, queries) as specified."""
    q = ";".join("%d,%.3f,%.3f" % (int(f), x, y) for f, x, y in queries_)
    raw = "|".join([plate_sig, model, "%d-%d" % (first, last), str(side), precision, q])
    return hashlib.sha1(raw.encode("utf8")).hexdigest()[:20]


def collect_queries(group, source: str, ref: int, info: Dict) -> Tuple[List[Tuple[float, float]], List[str]]:
    """Points for the chosen query source, in plate pixels."""
    import nuke

    spacing = float(group["grid_spacing"].value())
    cap = int(group["max_points"].value())
    if source == "Tracker4":
        name = group["tracker_node"].value().strip()
        with nuke.root():      # a full name; Track runs inside the TAPTrack Group, where toNode would look in there
            node = nuke.toNode(name) if name else None
        if node is None:
            raise queries.QueryError("Pick the Tracker4 to take points from (select it and press 'Pick selected').")
        return queries.tracker4_points(node, ref)
    if source == "Grid in mask":
        mask = group.input(1)
        if mask is None:
            raise queries.QueryError("Connect a Roto (or any mask) to the mask input of the TAPTrack node.")
        pts = queries.mask_grid(mask, ref, info["plate_w"], info["plate_h"], spacing, cap)
    else:
        pts = queries.full_grid(info["plate_w"], info["plate_h"], spacing, cap)
    return pts, ["point %d" % (i + 1) for i in range(len(pts))]


def prepare(group) -> Plan:
    """Build the job for ``group`` (main thread). Detects a cache hit."""
    model_key = dict(MODELS)[group["model"].value()]
    info = plate_info(group)
    first, last, ref = int(group["first"].value()), int(group["last"].value()), int(group["ref_frame"].value())
    if not first <= ref <= last:
        raise TapTrackError("The reference frame (%d) must lie inside the frame range (%d-%d)." % (ref, first, last))
    try:
        pts, names = collect_queries(group, group["query_source"].value(), ref, info)
    except queries.QueryError as exc:
        raise TapTrackError(str(exc)) from exc
    side = work_side(model_key, group["work_res"].value(), int(group["work_custom"].value()))
    precision = group["precision"].value()
    q = [[ref, round(x, 3), round(y, 3)] for x, y in pts]
    key = cache_key(model_key, first, last, side, precision, plate_signature(group), q)
    job_dir = Path(knob_path(group["cache_dir"].value() or default_cache_dir())) / key
    result_path = job_dir / "result.json"
    job = {
        "version": JOB_VERSION, "model": model_key,
        "frames_dir": knob_path(job_dir / "frames"), "frame_pattern": "f.%04d.png",
        "first": first, "last": last, "ref_frame": ref,
        "plate_w": info["plate_w"], "plate_h": info["plate_h"], "pixel_aspect": info["pixel_aspect"],
        "work_w": side, "work_h": side, "queries": q,
        "device": group["device"].value(), "precision": precision, "chunk": 16, "window": 64,
    }
    return Plan(job, key, job_dir, job_dir / "frames", result_path, result_path.is_file(), side, first, last, ref, names)


# --------------------------------------------------------------------------- frame render
def pick(values: List[str], wanted: List[str]) -> Optional[str]:
    """First of ``wanted`` present in a Nuke enumeration's ``values()`` (entries may be 'name<TAB>label')."""
    bare = {v.split("\t")[0]: v for v in values}
    for w in wanted:
        if w in bare:
            return bare[w].split("\t")[0]
    return None


def render_frames(group, plan: Plan) -> None:
    """Render ``first..last`` through the hidden branch into ``plan.frames_dir`` (blocking, main thread)."""
    import nuke

    plan.frames_dir.mkdir(parents=True, exist_ok=True)
    base = group.fullName()
    reformat, cs, write = (nuke.toNode(base + "." + n) for n in ("tt_reformat", "tt_colorspace", "tt_write"))
    if not (reformat and cs and write):
        raise TapTrackError("This TAPTrack node is damaged (its hidden render branch is missing). "
                            "Delete it and create a new one.")
    reformat["box_width"].setValue(plan.side)
    reformat["box_height"].setValue(plan.side)
    for knob, name in ((cs["in_colorspace"], "ocio_in"), (cs["out_colorspace"], "ocio_out")):
        want = group[name].value().strip()
        if want:
            knob.setValue(want)
    ident = pick(write["colorspace"].values(), ["raw", "Raw", "linear", "data", "scene_linear"])
    if ident:
        write["colorspace"].setValue(ident)
    write["file"].setValue((plan.frames_dir / "f.%04d.png").as_posix())
    write["file_type"].setValue("png")
    write["datatype"].setValue("8 bit")
    log("render %s %d-%d side=%d -> %s" % (base, plan.first, plan.last, plan.side, plan.frames_dir))
    try:
        nuke.execute(write, plan.first, plan.last)
    except RuntimeError as exc:
        raise TapTrackError("Nuke could not render the plate frames for tracking: %s" % exc,
                            log("render failed for %s: %s" % (group.fullName(), exc))) from exc


# --------------------------------------------------------------------------- worker
def parse_line(line: str) -> Optional[Dict]:
    """One stdout line of the worker protocol -> dict (None if it isn't JSON)."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        return json.loads(line)
    except ValueError:
        return None


def check_environment(python: str, model_key: str) -> None:
    """Fail early, in plain language, if the worker environment or weights are missing."""
    if not os.path.isfile(python):
        raise TapTrackError("The TAPTrack tracker environment is not installed.\n"
                            "Run install/setup_venv (see the README), or set 'Worker Python' in the Settings tab.",
                            "Looked for: " + python)
    ck = os.environ.get("TAPTRACK_CHECKPOINTS", str(ROOT / "checkpoints"))
    spec = {"bootstapnext": "bootstapnext_ckpt.npz", "tapnext": "tapnext_ckpt.npz",
            "bootstapir": "bootstapir_checkpoint_v2.pt", "bootstapir_online": "causal_bootstapir_checkpoint.pt"}[model_key]
    if not os.path.isfile(os.path.join(ck, spec)):
        raise TapTrackError("Model weights missing - run install/download_weights.", os.path.join(ck, spec))


@dataclass
class Launched:
    """A running worker: its pid (the whole tree is killed on cancel) and how to tell it has finished."""

    pid: int
    progress_log: Path
    done_file: Path
    result_file: Path
    proc: Optional[subprocess.Popen] = None


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def worker_files(job_dir: Path, kind: str) -> Dict[str, str]:
    """Script and file names for a worker run: ``track`` (taptrack_worker.py) or ``fit`` (fit.py)."""
    if kind == "fit":
        return {"script": str(ROOT / "worker" / "fit.py"), "job": str(job_dir / "fit_job.json"),
                "result": str(job_dir / "fit_result.json"), "progress": str(job_dir / "fit_progress.log"),
                "done": str(job_dir / "fit_done.txt"), "cmd": str(job_dir / "fit_run.cmd")}
    return {"script": worker_script(), "job": str(job_dir / "job.json"), "result": str(job_dir / "result.json"),
            "progress": str(job_dir / "progress.log"), "done": str(job_dir / "done.txt"), "cmd": str(job_dir / "run.cmd")}


def launch_worker(python: str, job_dir: Path, kind: str = "track") -> Launched:
    """Start the worker detached from this process; its stdout goes to ``progress.log``.

    On Windows the worker is created through WMI (``Win32_Process.Create``) instead of ``Popen``: a direct
    child of Nuke cannot load torch there (``c10.dll`` fails with WinError 1114 - reproduced in both GUI and
    terminal Nuke 16.1, while the identical command started by any other parent works). A tiny ``run.cmd``
    avoids quoting problems and records the exit code in ``done.txt``. Elsewhere a normal Popen is used.
    """
    names = worker_files(job_dir, kind)
    progress, done, result = Path(names["progress"]), Path(names["done"]), Path(names["result"])
    for f in (progress, done):
        if f.exists():
            f.unlink()
    args = [python, names["script"], names["job"], "--result", names["result"]]
    if os.name == "nt":
        cmd_file = Path(names["cmd"])
        crlf = chr(13) + chr(10)
        body = crlf.join(['@echo off',
                          '"%s" "%s" "%s" --result "%s" > "%s" 2>&1' % (args[0], args[1], args[2], args[4], progress),
                          'echo %%ERRORLEVEL%% > "%s"' % done]) + crlf
        cmd_file.write_text(body, encoding="utf8")
        line = 'cmd.exe /c ""%s""' % cmd_file
        ps = ("$si = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow=[uint16]0}; "
              "$r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments "
              "@{CommandLine=%s; CurrentDirectory=%s; ProcessStartupInformation=$si}; "
              "if ($r.ReturnValue -ne 0) { exit 1 }; $r.ProcessId" % (_ps_quote(line), _ps_quote(str(job_dir))))
        out = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                             text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if out.returncode != 0 or not out.stdout.strip().isdigit():
            raise TapTrackError("Could not start the tracker process.", log("CIM launch failed: %s %s" % (out.stdout, out.stderr)))
        return Launched(int(out.stdout.strip()), progress, done, result)
    f = open(progress, "w")
    proc = subprocess.Popen(args, stdout=f, stderr=subprocess.STDOUT, start_new_session=True, env=worker_env())
    return Launched(proc.pid, progress, done, result, proc)


def _finished(l: Launched) -> Optional[int]:
    """Exit code once the worker has finished, else None."""
    if l.proc is not None:
        return l.proc.poll()
    if l.done_file.is_file():
        txt = l.done_file.read_text().strip()
        if txt.lstrip("-").isdigit():
            return int(txt)
        if time.time() - l.done_file.stat().st_mtime > 2.0:      # marker exists but is unreadable: trust the result file
            return 0 if l.result_file.is_file() else 1
    return None


def run_worker(python: str, job_dir: Path, on_progress: Callable[[float, str], None],
               on_started: Optional[Callable[[int], None]] = None,
               should_cancel: Optional[Callable[[], bool]] = None, poll: float = 0.2, kind: str = "track") -> Dict:
    """Run the worker for ``job_dir/job.json``; blocks. Returns ``{'ok', 'cancelled', 'error', 'returncode'}``.

    ``on_progress(fraction, message)`` is called for each progress line; ``should_cancel`` is polled every
    ``poll`` seconds and, when it returns True, the whole process tree is killed.
    """
    launched = launch_worker(python, job_dir, kind)
    if on_started:
        on_started(launched.pid)
    error, cancelled, offset, rc = None, False, 0, None

    def drain() -> None:
        nonlocal error, offset
        try:
            with open(launched.progress_log, "rb") as f:
                f.seek(offset)
                chunk = f.read()
        except OSError:
            return
        end = chunk.rfind(bytes([10])) + 1       # only whole lines
        offset += end
        for raw in chunk[:end].decode("utf8", "replace").splitlines():
            msg = parse_line(raw)
            if msg is None:
                if raw.strip():
                    log("worker: " + raw.strip())
                continue
            if "progress" in msg:
                on_progress(float(msg["progress"]), str(msg.get("msg", "")))
            if "error" in msg:
                error = msg["error"]

    while True:
        drain()
        rc = _finished(launched)
        if rc is not None:
            drain()
            break
        if should_cancel and should_cancel():
            cancelled = True
            kill_process(launched.pid)
            break
        time.sleep(poll)
    return {"ok": rc == 0 and not cancelled and error is None, "cancelled": cancelled, "error": error, "returncode": rc}


def worker_env() -> Dict[str, str]:
    """Environment for the worker: Nuke's own Python/DLL settings must not leak into the venv python.

    Nuke sets PYTHONHOME/PYTHONPATH and puts its install folder (with its own CUDA/OpenMP/VC runtime DLLs)
    first on PATH; torch then fails with 'DLL initialization routine failed'. Keep the rest of the user's env.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith(("PYTHON", "QT_", "FOUNDRY_", "NUKE_PATH"))}
    nuke_dir = os.path.dirname(os.path.abspath(sys.executable)).lower()
    parts = [p for p in env.get("PATH", "").split(os.pathsep)
             if p and os.path.abspath(p).lower() != nuke_dir and "nuke" not in p.lower()]
    env["PATH"] = os.pathsep.join(parts)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def kill_process(pid: int) -> None:
    """Kill the worker and anything it spawned."""
    if not pid:
        return
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            import signal
            os.killpg(os.getpgid(pid), signal.SIGTERM)
    except Exception as exc:  # noqa: BLE001
        log("kill %s failed: %s" % (pid, exc))


def error_from_worker(err: Dict) -> TapTrackError:
    """Worker error object -> artist-facing error."""
    msg = err.get("message", "The tracker stopped unexpectedly.")
    if err.get("hint"):
        msg += "\n" + err["hint"]
    return TapTrackError(msg, err.get("log", ""))


def load_result(path: str) -> Dict:
    try:
        with open(path, encoding="utf8") as f:
            return json.load(f)
    except (OSError, ValueError) as exc:
        raise TapTrackError("The tracking result could not be read. Press Track to run it again.", "%s (%s)" % (path, exc)) from exc


def status_text(result: Dict) -> str:
    m = result.get("meta", {})
    return "%d points | %s%% visible | %.1f s | %s" % (m.get("points", 0), m.get("percent_visible", "?"),
                                                       m.get("elapsed_s", 0.0), m.get("model", "?"))


def clear_cache(job_dir: Path) -> None:
    """Delete a job folder (frames + result)."""
    import shutil

    if job_dir.is_dir():
        shutil.rmtree(job_dir, ignore_errors=True)


def run_fit(python: str, job_dir: Path, fit_job: Dict) -> Dict:
    """Run ``worker/fit.py`` for ``fit_job`` (blocks, a few seconds). Returns the parsed ``fit_result.json``."""
    job_dir.mkdir(parents=True, exist_ok=True)
    names = worker_files(job_dir, "fit")
    Path(names["job"]).write_text(json.dumps(fit_job), encoding="utf8")
    out = run_worker(python, job_dir, lambda f, m: None, kind="fit")
    if not out["ok"]:
        err = out["error"] or {"message": "The transform fit stopped unexpectedly.", "log": log("fit exit %s" % out["returncode"])}
        raise error_from_worker(err)
    with open(names["result"], encoding="utf8") as f:
        return json.load(f)
