"""TAPTrack menu entry (loaded by Nuke when <taptrack>/nuke is on the plugin path)."""
import nuke

nuke.menu("Nodes").addCommand("TAPTrack/TAPTrack", "import taptrack.node as _n; _n.create()")
