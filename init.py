# L:\Nuke loader - add to ~/.nuke/init.py:   nuke.pluginAddPath("L:/Nuke")
#
# Puts every tool in this repo on Nuke's plugin path. Nuke then runs each
# folder's own init.py / menu.py:
#   tools/comfy          NukeComfy menu (ComfyUI bridge)
#   tools/mcp            NukeMCP listener (Claude <-> live Nuke, port 54321)
#   tools/taptrack/nuke  TAPTrack node (DeepMind point tracking)
#   python/              mllamas_tools (started from this folder's menu.py)

import importlib.util
import os
import sys

import nuke

ROOT = os.path.dirname(os.path.abspath(__file__)).replace("\\", "/")

for sub in ("python", "tools/comfy", "tools/mcp", "tools/taptrack/nuke"):
    path = ROOT + "/" + sub
    if os.path.isdir(path):
        nuke.pluginAddPath(path)


def _alias_nukecomfy():
    """Make tools/comfy importable as the `NukeComfy` package.

    NukeComfy's modules and the Generate buttons embedded in saved .nk scripts
    run `import NukeComfy.python.bridge`, so the package name must survive the
    move from a folder called NukeComfy to tools/comfy.
    """
    if "NukeComfy" in sys.modules:
        return
    pkg = ROOT + "/tools/comfy"
    spec = importlib.util.spec_from_file_location(
        "NukeComfy", pkg + "/__init__.py", submodule_search_locations=[pkg]
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["NukeComfy"] = module
    spec.loader.exec_module(module)


_alias_nukecomfy()
