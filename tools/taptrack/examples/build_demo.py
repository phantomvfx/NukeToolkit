"""Build examples/taptrack_demo.nk by running the real pipeline (headless Nuke):

    Nuke16.1.exe -t examples/build_demo.py

Plate: tests/footage/kling25_wall_handheld.mp4 (generated with Kling 2.5; 241 frames, 24 fps, 1920x1080).
The script it saves contains a tracked TAPTrack node plus every export type, wired up so the result can be judged by eye.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "nuke"))
import nuke

from taptrack import export, job, node

VIDEO = os.path.join(ROOT, "tests", "footage", "kling25_wall_handheld.mp4").replace(chr(92), "/")
OUT = os.path.join(ROOT, "examples", "taptrack_demo.nk")
REF = 75                                    # Nuke frame 75 = the clean frame before the person walks in

nuke.root()["first_frame"].setValue(1)
nuke.root()["last_frame"].setValue(241)
nuke.root()["fps"].setValue(24)
nuke.root()["format"].setValue("HD_1080")

plate = nuke.nodes.Read(name="Plate", file=VIDEO)
plate["first"].setValue(1)
plate["last"].setValue(241)
if "sRGB" in [v.split(chr(9))[0] for v in plate["colorspace"].values()]:
    plate["colorspace"].setValue("sRGB")

mask = nuke.nodes.Rectangle(name="TrackMask")          # where the grid of points goes: the poster wall
mask["area"].setValue([300, 280, 1850, 760])

g = node.create()
g["name"].setValue("TAPTrack1")
g.setInput(0, plate)
g.setInput(1, mask)
g["query_source"].setValue("Grid in mask")
g["grid_spacing"].setValue(90)
g["max_points"].setValue(300)
g["first"].setValue(1)
g["last"].setValue(241)
g["ref_frame"].setValue(REF)
print("tracking ...", flush=True)
path = node.track_blocking(g)
res = job.load_result(path)
print("status:", g["status"].value(), flush=True)

# exports (the same code the Export button runs)
def fit(out_type, model="Translate+Rotate+Scale", **kw):
    g["out_type"].setValue(out_type)
    g["fit_model"].setValue(model)
    return job.run_fit(g["worker_python"].value(), __import__("pathlib").Path(path).parent, node.fit_options(g, path))

tracker = export.export_tracker4(res, source=g, ref_frame=REF)
tracker["name"].setValue("TAPTrack_Tracker4")

red = nuke.nodes.Rectangle(name="RedSticker")           # sits on the big red poster at the reference frame
red["area"].setValue([846, 338, 972, 486])
red["color"].setValue([1, 0.1, 0.1, 1])
matchmove = export.export_transform(fit("Transform - Matchmove", "Affine"), stabilize=False, source=red, name="Matchmove_Affine")

blue = nuke.nodes.Rectangle(name="BlueSticker")         # sits on a dark poster on the right
blue["area"].setValue([1542, 390, 1692, 558])
blue["color"].setValue([0.1, 0.3, 1, 1])
pin = export.export_cornerpin(fit("CornerPin2D"), source=blue, name="CornerPin_Blue")

m1 = nuke.nodes.Merge2(name="Merge_Red", operation="over", mix=0.7)
m1.setInput(0, plate)
m1.setInput(1, matchmove)
m2 = nuke.nodes.Merge2(name="Merge_Blue", operation="over", mix=0.7)
m2.setInput(0, m1)
m2.setInput(1, pin)

stab = export.export_transform(fit("Transform - Stabilize", "Translate+Rotate+Scale"), stabilize=True, source=plate, name="Stabilize_Sim")

note = nuke.nodes.StickyNote(name="README_Note")
note["label"].setValue(chr(10).join([
    "TAPTRACK DEMO  (plate: Kling 2.5 generated wall, ref frame 75)",
    "Viewer 1 = Merge_Blue: a RED tint (Transform, affine matchmove) and a BLUE tint (CornerPin2D) stuck to two posters.",
    "Play frames 1-100: they should stay on their posters. After ~frame 105 the AI-generated wall itself stops",
    "being a consistent surface (see docs/DRIFT.md), so expect them to wander late in the clip.",
    "Select TAPTrack_Tracker4 to see all tracks. Stabilize_Sim = the plate locked to frame 75 (view it with key 2).",
    "Change something in the TAPTrack node and press Track to re-run; Export builds new nodes below it."]))

# tidy layout
plate.setXYpos(0, -200)
mask.setXYpos(250, -200)
g.setXYpos(0, -80)
tracker.setXYpos(-250, 40)
red.setXYpos(250, 0)
matchmove.setXYpos(250, 80)
blue.setXYpos(500, 0)
pin.setXYpos(500, 80)
m1.setXYpos(0, 160)
m2.setXYpos(0, 240)
stab.setXYpos(-250, 160)
note.setXYpos(-500, -200)

viewer = nuke.nodes.Viewer(name="Viewer1")
viewer.setInput(0, m2)
viewer.setInput(1, stab)
nuke.scriptSaveAs(OUT, overwrite=1)
print("saved", OUT, flush=True)
