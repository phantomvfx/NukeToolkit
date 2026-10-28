"""
plate_to_aces_exr.py  --  Convert a display-referred Rec.709 plate (MOV/MP4) to an
ACES EXR image sequence using OCIO.

Target: Nuke 16 (OCIO color management, built-in ACES 1.3 config). Not tested
here: Nuke is not available in the authoring environment. Check the first frames.

What it does
  1. Sets the project to OCIO + the built-in ACES 1.3 CG config (working space ACEScg).
  2. Reads the clip RAW (no input transform).
  3. OCIODisplay, INVERTED: display "Rec.1886 Rec.709 - Display",
     view "ACES 1.0 - SDR Video"  ->  ACEScg (the working space).
     The Write then outputs ACEScg (default) or ACES2065-1.
     ActionVFX 2K REC709 clips are tagged "Rec.709 (ACES Output - Rec.709)", i.e. they
     were rendered through this exact output transform, so inverting it recovers
     scene-linear. Highlights clipped in the 709 file stay clipped; use the
     4K LOG version when you need real highlight range.
  4. Writes 16-bit half EXR, ZIP compression, one file per frame.
  5. Saves the conversion as a .nk next to the EXRs for reference.

Usage (Nuke terminal mode)
  "C:\\Program Files\\Nuke16.0vX\\Nuke16.0.exe" -t plate_to_aces_exr.py <input.mov> <output_dir> [ACEScg|ACES2065-1]

Example (SH010)
  Nuke16.0.exe -t L:/NukeDevSandbox/python/plate_to_aces_exr.py ^
      L:/NukeAiReel/02_shots/SH010_set_extension/01_plate/PF0018-01_Clip-1_REC709_2K.mov ^
      L:/NukeAiReel/02_shots/SH010_set_extension/01_plate/exr_acescg

Output name: <input stem>_<target>.####.exr  (e.g. PF0018-01_Clip-1_REC709_2K_ACEScg.1001.exr)
"""
import os
import sys

import nuke

DISPLAY = "Rec.1886 Rec.709 - Display"
VIEW = "ACES 1.0 - SDR Video"
START_FRAME = 1001  # VFX-standard first frame


def log(msg):
    print("[plate_to_aces_exr] " + msg)
    sys.stdout.flush()


def set_knob(node, knob, value):
    """Set a knob, failing loudly with the valid options if the value isn't accepted."""
    k = node[knob]
    try:
        k.setValue(value)
    except Exception as exc:
        opts = k.values() if hasattr(k, "values") else []
        raise RuntimeError("%s.%s = %r failed (%s). Valid: %s" % (node.name(), knob, value, exc, opts))
    if isinstance(value, str) and k.value() != value and not k.value().endswith("/" + value):
        log("WARNING %s.%s set to %r but reads back %r" % (node.name(), knob, value, k.value()))


def setup_project():
    root = nuke.root()
    set_knob(root, "colorManagement", "OCIO")
    # Menu entries look like "name\tMenu/Path"; setValue wants the bare name.
    configs = list(dict.fromkeys(v.split("\t")[0] for v in root["OCIO_config"].values()))
    # Prefer the built-in ACES 1.3 CG config, fall back to any ACES 1.3 config.
    pick = [c for c in configs if "aces" in c.lower() and "1.3" in c and "cg" in c.lower()] or \
           [c for c in configs if "aces" in c.lower() and "1.3" in c]
    if not pick:
        raise RuntimeError("No ACES 1.3 OCIO config found. Available: %s" % configs)
    set_knob(root, "OCIO_config", pick[0])
    log("OCIO config: " + pick[0])
    return root


def main(argv):
    if len(argv) < 3:
        log(__doc__)
        sys.exit(1)
    src = argv[1].replace("\\", "/")
    out_dir = argv[2].replace("\\", "/")
    target = argv[3] if len(argv) > 3 else "ACEScg"
    if target not in ("ACEScg", "ACES2065-1"):
        raise RuntimeError("target must be ACEScg or ACES2065-1")
    if not os.path.isfile(src):
        raise RuntimeError("input not found: " + src)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    root = setup_project()

    read = nuke.nodes.Read(file=src)
    read["raw"].setValue(True)  # no input transform: we invert the display transform ourselves
    first, last = int(read["first"].value()), int(read["last"].value())
    # Renumber to start at 1001.
    read["frame_mode"].setValue("start at")
    read["frame"].setValue(str(START_FRAME))
    out_first, out_last = START_FRAME, START_FRAME + (last - first)
    root["first_frame"].setValue(out_first)
    root["last_frame"].setValue(out_last)
    root["format"].setValue(read["format"].value())
    log("source %s frames %d-%d -> %d-%d, %dx%d" % (
        src, first, last, out_first, out_last, read.width(), read.height()))

    inv = nuke.nodes.OCIODisplay()
    inv.setInput(0, read)
    set_knob(inv, "colorspace", "ACEScg")  # inverse display -> working space (ACEScg)
    set_knob(inv, "display", DISPLAY)
    set_knob(inv, "view", VIEW)
    inv["invert"].setValue(True)

    stem = os.path.splitext(os.path.basename(src))[0]
    out_path = "%s/%s_%s.####.exr" % (out_dir, stem, target)

    write = nuke.nodes.Write(file=out_path)
    write.setInput(0, inv)
    set_knob(write, "file_type", "exr")
    set_knob(write, "colorspace", target)  # ACEScg: no-op; ACES2065-1: AP1 -> AP0 on write
    set_knob(write, "datatype", "16 bit half")
    try:
        set_knob(write, "compression", "Zip (1 scanline)")
    except RuntimeError as exc:
        log("compression left at default: %s" % exc)

    nk_path = "%s/%s_%s_conversion.nk" % (out_dir, stem, target)
    nuke.scriptSaveAs(nk_path, overwrite=1)
    log("saved script: " + nk_path)

    log("rendering -> " + out_path)
    nuke.execute(write, out_first, out_last, 1)
    log("done: %d frames" % (out_last - out_first + 1))


if __name__ == "__main__":
    main(sys.argv)
