"""Put TAPTrack on Nuke's plugin path by adding one clearly marked block to <nuke dir>/init.py.

    python install/add_to_nuke.py [--nuke-dir DIR] [--remove] [--dry-run]

The block sits between '# >>> TAPTrack' and '# <<< TAPTrack' markers, so it can be found, updated or removed. Nothing
else in init.py is touched. The default Nuke folder is ~/.nuke (or NUKE_PATH's first entry when set).
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NL = chr(10)
BEGIN, END ="# >>> TAPTrack (managed by install/add_to_nuke.py)", "# <<< TAPTrack"


def nuke_dir(arg: str | None) -> Path:
    if arg:
        return Path(arg)
    first = os.environ.get("NUKE_PATH", "").split(os.pathsep)[0].strip()
    return Path(first) if first else Path.home() / ".nuke"


def block() -> str:
    path = (ROOT / "nuke").as_posix()
    return "%s\nimport nuke\nnuke.pluginAddPath(%r)\n%s\n" % (BEGIN, path, END)


def apply(init: Path, remove: bool, dry: bool) -> str:
    text = init.read_text(encoding="utf8") if init.exists() else ""
    has = BEGIN in text and END in text
    if has:
        a, b = text.index(BEGIN), text.index(END) + len(END)
        base = (text[:a] + text[b:]).strip()
    else:
        base = text.strip()
    if remove:
        new = (base + NL) if base else ""
        msg = "removed the TAPTrack block from %s" % init if has else "nothing to remove in %s" % init
    else:
        new = (base + NL + NL if base else "") + block()
        msg = ("updated" if has else "added") + " the TAPTrack block in %s" % init
    if new == text or (not remove and has and new.strip() == text.strip()):
        return "already up to date: %s" % init
    if not dry:
        init.parent.mkdir(parents=True, exist_ok=True)
        init.write_text(new, encoding="utf8")
    return ("(dry run) would have " if dry else "") + msg


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--nuke-dir")
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    init = nuke_dir(a.nuke_dir) / "init.py"
    print(apply(init, a.remove, a.dry_run))
    if not a.remove:
        print("Restart Nuke; TAPTrack appears under Nodes > TAPTrack.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
