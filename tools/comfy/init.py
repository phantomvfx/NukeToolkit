import nuke
import os

# The path to the NukeComfy directory relative to this init.py
# Assuming this init.py is inside the NukeComfy folder
plugin_path = os.path.dirname(__file__)

# Add the main plugin path
nuke.pluginAddPath(plugin_path)

# Recursively add subfolders for gizmos, python, etc.
for folder in ['gizmos', 'python', 'json', 'icons']:
    sub_path = os.path.join(plugin_path, folder)
    if os.path.exists(sub_path):
        nuke.pluginAddPath(sub_path)

print(f"NukeComfy: Plugin paths initialized from {plugin_path}")
