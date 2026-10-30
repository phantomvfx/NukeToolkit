"""What an artist sees when something is wrong: a plain message, a Details line with a real log path, no tracebacks."""
import os
import tempfile

import nuke

from taptrack import job, node


class Capture:
    """Replace nuke.message for the duration of a test."""

    def __enter__(self):
        self.msgs, self._old = [], nuke.message
        nuke.message = lambda m, *a, **k: self.msgs.append(m)
        return self

    def __exit__(self, *a):
        nuke.message = self._old


def _group_without_plate():
    nuke.scriptClear()
    return node.create()


def _details_log(msg):
    assert "Details:" in msg, msg
    path = msg.split("Details:")[1].strip().splitlines()[0].strip()
    assert os.path.isfile(path), "Details must point at a real file: %r" % path
    return path


def test_no_plate_connected():
    g = _group_without_plate()
    with Capture() as c:
        node.on_track(g)
    assert len(c.msgs) == 1 and "Connect the plate" in c.msgs[0]
    _details_log(c.msgs[0])
    assert "Traceback" not in c.msgs[0] and 'File "' not in c.msgs[0]


def test_plate_that_cannot_render():
    """Regression: the render-failure path called log() with no message, so the artist got 'Something went wrong'."""
    nuke.scriptClear()
    nuke.root()["first_frame"].setValue(1)
    nuke.root()["last_frame"].setValue(3)
    bad = nuke.nodes.Read(file=os.path.join(tempfile.mkdtemp(), "missing.####.png").replace(chr(92), "/"),
                          first=1, last=3, on_error="error")
    bad["format"].setValue("HD_1080")
    g = node.create()
    g.setInput(0, bad)
    g["first"].setValue(1)
    g["last"].setValue(3)
    g["ref_frame"].setValue(1)
    g["query_source"].setValue("Full-frame grid")
    g["cache_dir"].setValue(tempfile.mkdtemp().replace(chr(92), "/"))
    plan = job.prepare(g)
    try:
        job.render_frames(g, plan)
    except job.TapTrackError as exc:
        assert "could not render the plate" in exc.message, exc.message
        assert exc.details and os.path.isfile(exc.details), exc.details
    else:
        raise AssertionError("a plate that cannot be read must raise TapTrackError")


def test_export_before_tracking():
    g = _group_without_plate()
    with Capture() as c:
        node.on_export(g)
    assert "press Track first" in c.msgs[0]
    _details_log(c.msgs[0])


def test_missing_weights_message_points_at_the_installer():
    nuke.scriptClear()
    nuke.root()["first_frame"].setValue(1001)
    nuke.root()["last_frame"].setValue(1002)
    g = node.create()
    g.setInput(0, nuke.nodes.Constant())
    g["query_source"].setValue("Full-frame grid")
    g["first"].setValue(1001)
    g["last"].setValue(1002)
    g["ref_frame"].setValue(1001)
    g["cache_dir"].setValue(job.knob_path(tempfile.mkdtemp(prefix="tt_err_")))
    old = os.environ.get("TAPTRACK_CHECKPOINTS")
    os.environ["TAPTRACK_CHECKPOINTS"] = tempfile.mkdtemp(prefix="tt_nockpt_")
    try:
        with Capture() as c:
            node.on_track(g)
    finally:
        if old is None:
            os.environ.pop("TAPTRACK_CHECKPOINTS", None)
        else:
            os.environ["TAPTRACK_CHECKPOINTS"] = old
    assert "Model weights missing - run install/download_weights" in c.msgs[0]
    assert "bootstapnext_ckpt.npz" in c.msgs[0]


def test_unexpected_exception_is_logged_not_shown_as_a_traceback():
    g = _group_without_plate()
    real = job.prepare

    def boom(_g):
        raise RuntimeError("kaboom-42")

    job.prepare = boom
    try:
        with Capture() as c:
            node.on_track(g)
    finally:
        job.prepare = real
    msg = c.msgs[0]
    assert "Something went wrong inside TAPTrack" in msg and "kaboom" not in msg and "Traceback" not in msg
    log_text = open(_details_log(msg), encoding="utf8").read()
    assert "kaboom-42" in log_text and "Traceback" in log_text           # the developer detail is in the log


def test_automatic_callbacks_never_pop_up_dialogs():
    g = _group_without_plate()
    g["result_path"].setValue("L:/does/not/exist/result.json")
    with Capture() as c:
        node.refresh_status(g)
        g["result_path"].setValue("")
        node.on_knob_changed(g, None)
        node.on_knob_changed(object(), object())          # garbage must be swallowed (and logged)
    assert c.msgs == []
    assert "gone" in g["status"].value() or g["status"].value()


def test_cancel_with_nothing_running_is_polite():
    g = _group_without_plate()
    with Capture() as c:
        node.on_cancel(g)
    assert "Nothing is tracking" in c.msgs[0]


def test_bad_pin_points_message():
    g = _group_without_plate()
    g.setInput(0, nuke.nodes.Constant())
    g["out_type"].setValue("CornerPin2D")
    g["pin_points"].setValue("1 2")
    try:
        node.fit_options(g, "x")
        raise AssertionError("expected an error")
    except job.TapTrackError as e:
        assert "exactly four" in e.message
