"""Errors an artist can hit must come out as plain language + a log path, never a stack trace."""
import json
import os
import subprocess

import pytest

import models
import synth


def test_classify_exception_covers_the_common_failures():
    c = models.classify_exception
    assert c(RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")).code == "OUT_OF_MEMORY"
    assert c(OSError("[WinError 1114] A dynamic link library (DLL) initialization routine failed.")).code == "TORCH_BROKEN"
    assert c(RuntimeError("CUDA error: an illegal memory access was encountered")).code == "GPU_ERROR"
    assert c(RuntimeError("cuDNN error: CUDNN_STATUS_NOT_INITIALIZED")).code == "GPU_ERROR"
    assert c(MemoryError()).code == "OUT_OF_MEMORY"
    assert c(KeyError("plate_w")).code == "BAD_JOB"
    other = c(RuntimeError("something else entirely"))
    assert other.code == "UNEXPECTED" and "something else" in other.message
    for e in (RuntimeError("CUDA out of memory"), OSError("DLL load failed"), RuntimeError("CUDA error"), KeyError("x")):
        err = c(e)
        assert err.message and err.hint and "Traceback" not in err.message and "File \"" not in err.message


def run_cli(job: dict, tmp_path, env=None):
    jp = tmp_path / "job.json"
    jp.write_text(json.dumps(job) if isinstance(job, dict) else job)
    p = subprocess.run([str(synth.VENV_PY), str(synth.WORKER), str(jp), "--result", str(tmp_path / "result.json")],
                       capture_output=True, text=True, env=dict(os.environ, TAPTRACK_LOG_DIR=str(tmp_path / "logs"), **(env or {})))
    lines = [json.loads(l) for l in p.stdout.splitlines() if l.strip()]
    return p, lines


BASE = {"version": 1, "model": "bootstapnext", "frames_dir": "x", "frame_pattern": "f.%04d.png", "first": 1, "last": 2, "ref_frame": 1,
        "plate_w": 640, "plate_h": 360, "work_w": 256, "work_h": 256, "queries": [[1, 10.0, 10.0]]}

pytestmark = pytest.mark.gpu


def check(p, lines, code, tmp_path):
    assert p.returncode != 0
    assert all("Traceback" not in l for l in p.stdout.splitlines())          # protocol stream is clean
    err = lines[-1]["error"]
    assert err["code"] == code, err
    assert err["message"] and "Traceback" not in err["message"]
    assert err["log"] and os.path.isfile(err["log"])                         # the details point at a real log file
    return err


def test_weights_missing_message(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    p, lines = run_cli(BASE, tmp_path, {"TAPTRACK_CHECKPOINTS": str(empty)})
    err = check(p, lines, "WEIGHTS_MISSING", tmp_path)
    assert "download_weights" in err["hint"] and "bootstapnext_ckpt.npz" in err["message"]


def test_no_points_message(tmp_path):
    p, lines = run_cli(dict(BASE, queries=[]), tmp_path)
    err = check(p, lines, "BAD_JOB", tmp_path)
    assert "no points" in err["message"].lower() and "Tracker" in err["hint"]


def test_damaged_job_message(tmp_path):
    p, lines = run_cli({"version": 1, "model": "bootstapnext"}, tmp_path)
    check(p, lines, "BAD_JOB", tmp_path)
    p, lines = run_cli("{ this is not json", tmp_path)
    check(p, lines, "BAD_JOB", tmp_path)


def test_unknown_model_message(tmp_path):
    p, lines = run_cli(dict(BASE, model="nope"), tmp_path)
    check(p, lines, "BAD_JOB", tmp_path)
