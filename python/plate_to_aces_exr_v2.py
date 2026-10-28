"""
plate_to_aces_exr.py  --  Convert a display-referred Rec.709 plate (MOV/MP4) to an
ACES EXR image sequence using OCIO.

Target: Nuke 16 (menu names checked against Nuke 16.1v4 and its built-in config
"fn-nuke_cg-config-v2.2.0_aces-v1.3_ocio-v2.3"). Check the first frames.

What it does
  1. Sets the project to OCIO + the built-in ACES 1.3 CG config (working space ACEScg).
  2. Reads the clip RAW (no input transform) and detects its full frame range.
  3. OCIODisplay, INVERTED: display "Rec.1886 Rec.709 - Display",
     view "ACES 1.0 - SDR Video"  ->  ACEScg (the working space).
     ActionVFX 2K REC709 clips are tagged "Rec.709 (ACES Output - Rec.709)", i.e. they
     were rendered through this exact output transform, so inverting it recovers
     scene-linear. Highlights clipped in the 709 file stay clipped; use the
     4K LOG version when you need real highlight range.
  4. Writes 16-bit half EXR, ZIP compression, frames from 1001, as ACEScg (default)
     or ACES2065-1.
  5. Saves the conversion as a .nk next to the EXRs for reference.

Usage (Nuke terminal mode)
  L:\\Apps\\Nuke16.1v4\\Nuke16.1.exe -t plate_to_aces_exr.py <input.mov> <output_dir> [ACEScg|ACES2065-1]

Output name: <input stem>_<target>.####.exr
"""
import os
import sys

import nuke

START_FRAME = 1001  # VFX-standard first frame


def log(msg):
    print("[plate_to_aces_exr] " + msg)
    sys.stdout.flush()


def menu_names(knob):
    """Enumeration values as bare names. Nuke lists them as 'name<TAB>Menu/Path'."""
    names = []
    for v in knob.values():
        n = v.split("\t")[0].strip()
        if n and n not in names:
            names.append(n)
    return names


def pick_value(node, knob, wanted, fallback_terms=()):
    """Set an enumeration knob to an exact name, or the first name containing all fallback terms."""
    k = node[knob]
    names = menu_names(k)
    choice = None
    for w in wanted:
        if w in names:
            choice = w
            break
    if choice is None and fallback_terms:
        hits = [n for n in names if all(t.lower() in n.lower() for t in fallback_terms)]
        choice = hits[0] if hits else None
    if choice is None:
        raise RuntimeError("%s.%s: none of %s found. Available: %s" % (node.name(), knob, list(wanted), names))
    k.setValue(choice)
    log("%s.%s = %s" % (node.name(), knob, choice))
    return choice


def setup_project():
    root = nuke.root()
    root["colorManagement"].setValue("OCIO")
    configs = menu_names(root["OCIO_config"])
    pick = [c for c in configs if "aces" in c.lower() and "1.3" in c and "cg-config" in c.lower()] or \
           [c for c in configs if "aces" in c.lower() and "1.3" in c]
    if not pick:
        raise RuntimeError("No ACES 1.3 OCIO config found. Available: %s" % configs)
    root["OCIO_config"].setValue(pick[0])
    log("OCIO config: %s (reads back %r)" % (pick[0], root["OCIO_config"].value()))
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

    read = nuke.nodes.Read()
    # fromUserText runs the same logic as typing the path in the UI: it detects
    # the movie's frame range (nuke.nodes.Read(file=...) does not in -t mode).
    read["file"].fromUserText(src)
    read["raw"].setValue(True)  # no input transform: we invert the display transform ourselves
    first, last = int(read["first"].value()), int(read["last"].value())
    if last <= first:
        fc = read.metadata("input/frame_count") or read.metadata("quicktime/frame_count")
        if fc:
            first, last = 1, int(fc)
            for kn, val in (("first", first), ("last", last), ("origfirst", first), ("origlast", last)):
                read[kn].setValue(val)
    if last <= first:
        raise RuntimeError("Could not detect the frame range of %s (got %d-%d)" % (src, first, last))

    # Renumber to start at 1001.
    read["frame_mode"].setValue("start at")
    read["frame"].setValue(str(START_FRAME))
    out_first, out_last = START_FRAME, START_FRAME + (last - first)
    root["first_frame"].setValue(out_first)
    root["last_frame"].setValue(out_last)
    root["format"].setValue(read["format"].value())
    log("source frames %d-%d -> %d-%d, %dx%d" % (first, last, out_first, out_last, read.width(), read.height()))

    inv = nuke.nodes.OCIODisplay()
    inv.setInput(0, read)
    pick_value(inv, "colorspace", ["ACEScg", "ACES - ACEScg", "lin_ap1"], ("acescg",))
    pick_value(inv, "display", ["Rec.1886 Rec.709 - Display"], ("rec.1886",))
    pick_value(inv, "view", ["ACES 1.0 - SDR Video"], ("sdr video",))
    inv["invert"].setValue(True)

    stem = os.path.splitext(os.path.basename(src))[0]
    out_path = "%s/%s_%s.####.exr" % (out_dir, stem, target)

    write = nuke.nodes.Write(file=out_path)
    write.setInput(0, inv)
    write["file_type"].setValue("exr")
    if target == "ACEScg":
        pick_value(write, "colorspace", ["ACEScg", "ACES - ACEScg", "lin_ap1"], ("acescg",))
    else:
        pick_value(write, "colorspace", ["ACES2065-1", "ACES - ACES2065-1", "lin_ap0"], ("2065",))
    write["datatype"].setValue("16 bit half")
    try:
        pick_value(write, "compression", ["Zip (1 scanline)"], ("zip",))
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
