"""Create native nodes from a worker result. Phase 2: Tracker4.

Tracker4 ``tracks`` knob format (reverse-engineered with headless Nuke 16.1, see
``tests/fixtures/`` and ``tests/nuke_tests/test_tracker4_export.py``):

* The knob is a table: ``{ 1 <ncols> <nrows> }``, a column header block (31 columns in Nuke 16),
  then one ``{ ... }`` row per track with one cell per column.
* Columns: 0 enable, 1 name, 2 track_x, 3 track_y, 4/5 offset_x/y, 6/7/8 T/R/S use-flags,
  9 error, 10-19 pattern/search boxes (plain numbers), 20-30 key_* (curves).
* Animated cells must be written as ``{curve x<frame> <value> ...}``; ``enable`` is a stepped
  curve ``{curve K x<frame> <0|1> ...}`` (``K`` = constant interpolation).
* DON'T build the table with ``Knob.setValueAt``: it serialises as ``{0 x1 100 ...}`` (no ``curve``)
  and Nuke then evaluates every track to 0 at every frame. Verified: ``nuke.expression(
  "Tracker4.tracks.1.track_x")`` and Tracker4's own match-move ignore such cells.
* ``Knob.getValueAt`` reads only static values here; to read a track use :func:`evaluate`
  (``nuke.expression("<node>.tracks.<row+1>.<column>")`` at the wanted frame).
* Rows are 1-based in expressions; an occluded point stays in the table with its position keyed
  and its ``enable`` curve at 0.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence

NL = "\n"
COL = {"enable": 0, "name": 1, "track_x": 2, "track_y": 3, "offset_x": 4, "offset_y": 5, "T": 6, "R": 7, "S": 8}


@dataclass
class TrackData:
    """One Tracker4 row: sparse curves keyed by frame (plate pixels, Nuke convention)."""

    name: str
    x: Dict[int, float] = field(default_factory=dict)
    y: Dict[int, float] = field(default_factory=dict)
    enable: Dict[int, float] = field(default_factory=dict)


def _table_header(knob):
    m = re.match(r"\s*\{\s*(\d+)\s+(\d+)\s+(\d+)\s*\}", knob.toScript())
    if not m:
        raise ValueError("Unrecognised Tracker4 tracks table.")
    return int(m.group(2)), int(m.group(3))


def table_columns(knob) -> int:
    """Number of columns in a Tracker4 ``tracks`` knob (31 in Nuke 16)."""
    return _table_header(knob)[0]


def table_rows(knob) -> int:
    """Number of tracks (rows) currently in the table."""
    return _table_header(knob)[1]


def _curve(values: Dict[int, float], flag: str = "") -> str:
    if not values:
        return "{curve %s0}" % flag
    keys = " ".join("x%d %r" % (int(f), float(v)) for f, v in sorted(values.items()))
    return "{curve %s%s}" % (flag, keys)


def _row_text(tr: TrackData, n_cols: int) -> str:
    name = tr.name.replace('"', "'").replace(chr(92), "/")
    cells = [_curve(tr.enable or {0: 1.0}, "K "), '"%s"' % name, _curve(tr.x), _curve(tr.y),
             "{curve 0}", "{curve 0}", "1", "1", "1", "{curve 0}"]
    cells += ["0"] * 10 + ["{curve 0}"] * 11
    if len(cells) != n_cols:
        raise ValueError("Unexpected Tracker4 layout: %d columns, expected %d." % (n_cols, len(cells)))
    return "{ " + " ".join(cells) + " }"


def write_tracker4_table(knob, tracks: Sequence[TrackData]) -> None:
    """Replace the contents of a Tracker4 ``tracks`` knob with ``tracks``."""
    n_cols = table_columns(knob)
    lines = knob.toScript().split(NL)
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "}")
    header = NL.join(lines[1:end + 1])        # the column block, as this Nuke defines it
    rows = NL.join(_row_text(t, n_cols) for t in tracks)
    knob.fromScript("{ 1 %d %d }%s%s%s{%s%s%s}%s" % (n_cols, len(tracks), NL, header, NL, NL, rows, NL, NL))


def evaluate(node, row: int, column: str, frame: int) -> float:
    """Value of Tracker4 ``column`` (e.g. ``track_x``) of 0-based track ``row`` at ``frame``."""
    import nuke

    cur = nuke.frame()
    try:
        nuke.frame(int(frame))
        return float(nuke.expression("%s.tracks.%d.%s" % (node.fullName(), row + 1, column)))
    finally:
        nuke.frame(cur)


def tracks_from_result(result: dict, indices: Optional[Iterable[int]] = None,
                       names: Optional[Sequence[str]] = None) -> List[TrackData]:
    """Dense per-frame ``TrackData`` from a worker result (all points, or the chosen ``indices``)."""
    first, last = int(result["first"]), int(result["last"])
    idx = list(range(len(result["tracks"]))) if indices is None else list(indices)
    out = []
    for n, q in enumerate(idx):
        td = TrackData(name=(names[q] if names and q < len(names) else "TAPTrack %03d" % (n + 1)))
        for k, f in enumerate(range(first, last + 1)):
            px, py = result["tracks"][q][k]
            td.x[f], td.y[f] = px, py
            td.enable[f] = 1.0 if result["visible"][q][k] else 0.0
        out.append(td)
    return out


def export_tracker4(result: dict, source=None, name: str = "TAPTrack_Tracker4", ref_frame: Optional[int] = None):
    """Create a Tracker4 holding every tracked point. Returns the node.

    ``source``: node to connect to input 0 (and to position the new node below).
    """
    import nuke  # local: this module is also importable without Nuke (pure helpers above)

    node = nuke.nodes.Tracker4(name=name)
    write_tracker4_table(node["tracks"], tracks_from_result(result))
    if ref_frame is not None and "reference_frame" in node.knobs():
        node["reference_frame"].setValue(int(ref_frame))
    if source is not None:
        node.setInput(0, source)
        node.setXYpos(int(source.xpos()), int(source.ypos()) + 80)
    return node


# --------------------------------------------------------------------------- matchmove / stabilize exports
TRANSFORM_KEYS = ("tx", "ty", "rotate", "sx", "sy", "skewX")


def frame_ranges(frames: Sequence[int]) -> str:
    """``[1100, 1101, 1102, 1120]`` -> ``'1100-1102, 1120'`` (for messages to the artist)."""
    out, run = [], []
    for f in sorted(frames):
        if run and f == run[-1] + 1:
            run.append(f)
        else:
            if run:
                out.append(run)
            run = [f]
    if run:
        out.append(run)
    return ", ".join("%d-%d" % (r[0], r[-1]) if len(r) > 1 else str(r[0]) for r in out)


def _animate(knob, index: int, values: Dict[int, float]) -> None:
    knob.setAnimated(index)
    for f, v in values.items():
        knob.setValueAt(float(v), int(f), index)


def _summary(fit: dict, what: str) -> str:
    used = [u for u in fit["used"]]
    inl = [i for i, v in zip(fit["inliers"], fit["valid"]) if v]
    rms = [r for r, v in zip(fit["rms"], fit["valid"]) if v and r > 0]
    med = lambda a: sorted(a)[len(a) // 2] if a else 0  # noqa: E731
    text = "TAPTrack %s (%s)%s%d points, median %d inliers/frame, rms %.2f px" % (
        what, fit["model"], chr(10), fit.get("points_used", 0), med(inl), med(rms))
    if fit.get("weak_frames"):
        text += chr(10) + "check frames: " + frame_ranges(fit["weak_frames"]) + " (fit not reliable; the element is held there)"
    return text


def export_transform(fit: dict, stabilize: bool, source=None, name: Optional[str] = None):
    """Create a Transform node carrying the fitted motion (keyed on every frame). Returns the node.

    Matchmove: the Transform applies the motion reference -> frame (an element locked to the plate).
    Stabilize: the same curves with ``invert_matrix`` on (Nuke evaluates the exact inverse).
    Knob values follow the convention measured in Nuke 16.1 (see ``worker/fit.py``).
    """
    import nuke

    first, last = int(fit["first"]), int(fit["last"])
    frames = list(range(first, last + 1))
    node = nuke.nodes.Transform(name=name or ("TAPTrack_Stabilize" if stabilize else "TAPTrack_Matchmove"))
    node["center"].setValue(fit["center"][0], 0)
    node["center"].setValue(fit["center"][1], 1)
    p = fit["params"]
    _animate(node["translate"], 0, dict(zip(frames, p["tx"])))
    _animate(node["translate"], 1, dict(zip(frames, p["ty"])))
    if fit["model"] in ("similarity", "affine"):
        _animate(node["rotate"], 0, dict(zip(frames, p["rotate"])))
        _animate(node["scale"], 0, dict(zip(frames, p["sx"])))
        _animate(node["scale"], 1, dict(zip(frames, p["sy"])))
    if fit["model"] == "affine":
        _animate(node["skewX"], 0, dict(zip(frames, p["skewX"])))
    if stabilize:
        node["invert_matrix"].setValue(True)
    node["label"].setValue(_summary(fit, "stabilize" if stabilize else "matchmove"))
    if source is not None:
        node.setInput(0, source)
        node.setXYpos(int(source.xpos()), int(source.ypos()) + 80)
    export_validity(fit, node.name() + "_valid", node)
    return node


def export_cornerpin(fit: dict, source=None, name: str = "TAPTrack_CornerPin"):
    """Create a CornerPin2D: ``from`` = the four anchors on the reference frame, ``to`` keyed per frame."""
    import nuke

    first, last = int(fit["first"]), int(fit["last"])
    frames = list(range(first, last + 1))
    node = nuke.nodes.CornerPin2D(name=name)
    for i in range(4):
        fx, fy = fit["corners_from"][i]
        node["from%d" % (i + 1)].setValue(fx, 0)
        node["from%d" % (i + 1)].setValue(fy, 1)
        _animate(node["to%d" % (i + 1)], 0, {f: fit["corners"][k][i][0] for k, f in enumerate(frames)})
        _animate(node["to%d" % (i + 1)], 1, {f: fit["corners"][k][i][1] for k, f in enumerate(frames)})
    node["label"].setValue(_summary(fit, "corner pin") + chr(10) + "anchors: " +
                           ", ".join(str(i + 1) for i in fit["anchor_indices"]))
    if source is not None:
        node.setInput(0, source)
        node.setXYpos(int(source.xpos()), int(source.ypos()) + 80)
    export_validity(fit, node.name() + "_valid", node)
    return node


def export_validity(fit: dict, name: str, source=None):
    """A NoOp with a keyed ``valid`` knob: 1 where the fit is reliable, 0 on frames flagged weak.

    The Transform/CornerPin exports hold their last good value on frames with no usable fit (the tracked surface is
    out of frame or hidden), which would leave a frozen element on screen. Link a Merge's ``mix`` (or ``disable``) to
    ``<name>.valid`` to switch the element off there.
    """
    import nuke

    first, last = int(fit["first"]), int(fit["last"])
    weak = set(int(f) for f in fit.get("weak_frames", []))
    node = nuke.nodes.NoOp(name=name)
    knob = nuke.Double_Knob("valid", "valid")
    node.addKnob(knob)
    _animate(knob, 0, {f: (0.0 if f in weak else 1.0) for f in range(first, last + 1)})
    node["label"].setValue("valid: 1 = reliable fit, 0 = check this frame" + chr(10) +
                           "link a Merge mix to this: mix = value * " + name + ".valid")
    if source is not None:
        node.setXYpos(int(source.xpos()) + 160, int(source.ypos()))
    return node
