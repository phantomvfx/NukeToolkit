# mllamas_tools - the "mllamas tools" menu for Nuke (target: Nuke 16).
#
# Loaded from ~/.nuke/menu.py:   import mllamas_tools; mllamas_tools.install()
# Tools are .nk Groups in L:\Nuke\gizmos, pasted into the script on click
# (no toolbar registration - see CLAUDE.md "Nuke tool file format").
#
#   BulletHitDebris -> gizmos/BulletHitDebris.nk
#   CamProjectSnap  -> gizmos/CamProjectSnap.nk
#   CopyCat Setup   -> gizmos/copycatsetup.nk
#   Send to RV      -> sends the selected Read node(s) to RV (shortcut Ctrl+Alt+R)

import os
import re
import subprocess

import nuke

RV_EXE = r"L:\Apps\OpenRV\bin\rv.exe"
RV_SHORTCUT = "ctrl+alt+r"

_HERE = os.path.dirname(os.path.abspath(__file__))
_GIZMOS = os.path.normpath(os.path.join(_HERE, "..", "..", "gizmos"))
_ICON = os.path.join(_HERE, "icons", "rv.png")
_MENU_ICON = os.path.normpath(os.path.join(_HERE, "..", "..", "icons", "mllamas.png"))


def paste_tool(filename):
    path = os.path.join(_GIZMOS, filename)
    if not os.path.isfile(path):
        nuke.message("Tool file not found:\n%s" % path)
        return
    nuke.nodePaste(path.replace("\\", "/"))


def _rv_path(read, f):
    """Turn a Nuke path with %0Nd / #### padding into RV sequence syntax (name.FIRST-LAST@@@@.ext)."""
    m = re.search(r"%0?(\d*)d|(#+)", f)
    if not m:
        return f  # single file / movie
    pad = int(m.group(1) or 1) if m.group(1) is not None else len(m.group(2))
    first, last = int(read["first"].value()), int(read["last"].value())
    return f[:m.start()] + "%d-%d" % (first, last) + "@" * pad + f[m.end():]


def send_to_rv():
    reads = [n for n in nuke.selectedNodes() if n.Class() == "Read"]
    if not reads:
        nuke.message("Select a Read node first.")
        return
    if not os.path.isfile(RV_EXE):
        nuke.message("RV not found:\n%s" % RV_EXE)
        return
    files = []
    for n in reads:
        # value() keeps %04d; evaluate() would substitute the current frame and load one frame
        f = n["file"].value()
        if f:
            files.append(_rv_path(n, f))
    if not files:
        nuke.message("The selected Read node has no file path.")
        return
    subprocess.Popen([RV_EXE] + files)


def install():
    menu = nuke.menu("Nodes").addMenu("mllamas tools", icon=_MENU_ICON)
    menu.addCommand("BulletHitDebris", lambda: paste_tool("BulletHitDebris.nk"))
    menu.addCommand("CamProjectSnap", lambda: paste_tool("CamProjectSnap.nk"))
    menu.addCommand("CopyCat Setup", lambda: paste_tool("copycatsetup.nk"))
    menu.addCommand("Send to RV", send_to_rv, RV_SHORTCUT, icon=_ICON)
