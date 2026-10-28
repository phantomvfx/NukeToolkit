"""Rebuild tracker4_reference.nk in headless Nuke 16:  Nuke16.1.exe -t make_tracker4_reference.py

Data comes from tracker4_expected.py. Rows are created by loading a blank 3-row table with
fromScript (headless 'add_track' adds nothing), values are keyed with setValueAt(value, frame, row*31+col).
"""
import os
import sys

import nuke

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
sys.path.insert(0, os.path.join(here, "..", "..", "nuke"))
import tracker4_expected as E  # noqa: E402
from taptrack.export import TrackData, write_tracker4_table  # noqa: E402

nuke.root()["first_frame"].setValue(1)
nuke.root()["last_frame"].setValue(15)
nuke.root()["format"].setValue("HD_1080")
t = nuke.nodes.Tracker4(name="Tracker4_ref")
tracks = []
for r, (name, xs, ys) in enumerate(E.TRACKS):
    tracks.append(TrackData(name, dict(zip(E.KEY_FRAMES, xs)), dict(zip(E.KEY_FRAMES, ys)), dict(E.ENABLE_KEYS[r])))
write_tracker4_table(t["tracks"], tracks)
nuke.scriptSaveAs(os.path.join(here, "tracker4_reference.nk"), overwrite=1)
print("saved")
