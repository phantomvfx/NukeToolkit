"""Reopen taptrack_demo.nk headless and render the composite on a few frames:  Nuke16.1.exe -t examples/render_check.py"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "nuke"))
import nuke

nuke.scriptOpen(os.path.join(ROOT, "examples", "taptrack_demo.nk"))
g = nuke.toNode("TAPTrack1")
print("status after reopen:", g["status"].value())
print("nodes:", sorted(n.name() for n in nuke.allNodes() if n.Class() not in ("Viewer",)))
out = os.path.join(ROOT, "tests", "_out", "demo_render")
os.makedirs(out, exist_ok=True)
w = nuke.nodes.Write(file=(out + "/comp.%04d.png").replace(chr(92), "/"), file_type="png")
w.setInput(0, nuke.toNode("Merge_Blue"))
for f in (30, 75, 95, 140, 200, 235):
    nuke.execute(w, f, f)
print("rendered")
