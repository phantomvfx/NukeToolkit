"""GUI smoke test for the threaded Track path:  Nuke16.1.exe tests/fixtures/gui_smoke.py

Needs TAPTRACK_TEST_DATA (json from tests/test_nuke.py). Writes tests/_out/gui_smoke.txt and exits.
"""
import json
import os
import sys
import threading
import time

ROOT = "L:/Nuke/tools/taptrack"
sys.path.insert(0, ROOT + "/nuke")
sys.path.insert(0, ROOT + "/tests/nuke_tests")
sys.path.insert(0, ROOT + "/tests/fixtures")
os.environ["TAPTRACK_TEST_DATA"] = ROOT + "/tests/_out/nuke_test_data.json"
import nuke

import test_pipeline as T
from taptrack import job, node

OUT = ROOT + "/tests/_out/gui_smoke.txt"
lines = ["gui=%s" % nuke.GUI]


def finish():
    open(OUT, "w").write("\n".join(lines))
    os._exit(0)


def check():
    g = nuke.toNode("TAPTrack")
    deadline = time.time() + 120
    while time.time() < deadline:
        status = nuke.executeInMainThreadWithResult(lambda: g["status"].value())
        if status not in ("tracking...",):
            break
        time.sleep(0.5)
    path = nuke.executeInMainThreadWithResult(lambda: g["result_path"].value())
    lines.append("status=%s" % status)
    lines.append("result_path=%s exists=%s" % (path, os.path.isfile(path) if path else None))
    if path and os.path.isfile(path):
        lines.append("points=%d" % len(job.load_result(path)["tracks"]))
    finish()


d = T._data()
g = T._group(d)
nuke.message = lambda *a, **k: lines.append("MESSAGE: %s" % (a,))     # never block on a modal in the smoke test
node.on_track(g)
lines.append("on_track returned; status=%s" % g["status"].value())
threading.Thread(target=check, daemon=True).start()
