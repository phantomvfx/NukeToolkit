"""Mini test runner for ``Nuke -t`` (Nuke's Python has no pytest).

    Nuke16.1.exe -t tests/nuke_tests/run.py [name_filter]

Runs every ``test_*`` function in ``tests/nuke_tests/test_*.py`` and prints ``ALL PASSED``
(exit 0) or the failures (exit 1).
"""
import glob
import importlib.util
import os
import sys
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (os.path.join(ROOT, "nuke"), os.path.join(ROOT, "tests", "fixtures"), HERE):
    sys.path.insert(0, p)

flt = sys.argv[1] if len(sys.argv) > 1 else ""
failures, passed = [], 0
for path in sorted(glob.glob(os.path.join(HERE, "test_*.py"))):
    spec = importlib.util.spec_from_file_location(os.path.basename(path)[:-3], path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in sorted(dir(mod)):
        if name.startswith("test_") and flt in name:
            t0 = time.time()
            try:
                getattr(mod, name)()
                passed += 1
                print("PASS %s.%s (%.1fs)" % (mod.__name__, name, time.time() - t0), flush=True)
            except Exception:  # noqa: BLE001
                failures.append("%s.%s" % (mod.__name__, name))
                print("FAIL %s.%s\n%s" % (mod.__name__, name, traceback.format_exc()), flush=True)

print("ALL PASSED (%d)" % passed if not failures else "FAILED: %s" % ", ".join(failures), flush=True)
sys.exit(1 if failures else 0)
