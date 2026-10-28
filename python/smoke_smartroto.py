# Smoke test: verify the SmartRoto plugin (RotoNet) is discoverable and loadable in Nuke 17.
#
# SmartRoto is a vendor-protected Nuke plugin (Foundry licensing) wrapping a
# PyTorch-based AI rotoscoping engine. Its .cat files (FnRotoNet.cat,
# FnRotoNetMultiFrame.cat) are trained model weights loaded by the plugin's
# DLLs — they are not loaded directly by the user.
#
# The plugin folder is added to Nuke 17's plugin path in ~/.nuke/init.py:
#     if nuke.NUKE_VERSION_MAJOR == 17:
#         nuke.pluginAddPath("L:/Apps/Nuke17/plugins/SmartRoto")
#
# Run: Nuke17.0.exe -t python/smoke_smartroto.py
import nuke
import os

print("Nuke version:", nuke.NUKE_VERSION_STRING)

# 1. Is the SmartRoto folder on the plugin path?
smartroto_on_path = any("SmartRoto" in p for p in nuke.pluginPath())
print("SmartRoto on plugin path:", smartroto_on_path)

# 2. Do the plugin DLLs load? (nuke.load resolves them from the plugin path)
for dll in ("RotoPlugin", "RotoNet"):
    try:
        nuke.load(dll)
        print("nuke.load OK:", dll)
    except Exception as e:
        print("nuke.load FAILED:", dll, "->", e)

# 3. Are the model files present next to the DLLs? (the plugin needs them)
smart_dir = next((p for p in nuke.pluginPath() if "SmartRoto" in p), None)
if smart_dir:
    for f in ("FnRotoNet.cat", "FnRotoNetMultiFrame.cat"):
        p = os.path.join(smart_dir, f)
        print(f, "present:", os.path.exists(p),
              f"({os.path.getsize(p) / 1e6:.0f} MB)" if os.path.exists(p) else "")
