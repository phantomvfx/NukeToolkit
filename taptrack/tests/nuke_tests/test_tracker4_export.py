"""Tracker4 export, proven by what Nuke itself evaluates and against tests/fixtures/tracker4_reference.nk."""
import os
import time

import nuke

import tracker4_expected as E
from taptrack import export

FIXTURE = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "fixtures", "tracker4_reference.nk"))


def _load_fixture():
    nuke.scriptClear()
    nuke.nodePaste(FIXTURE)
    return nuke.toNode("Tracker4_ref")


def _expected_tracks():
    return [export.TrackData(n, dict(zip(E.KEY_FRAMES, xs)), dict(zip(E.KEY_FRAMES, ys)), dict(E.ENABLE_KEYS[r]))
            for r, (n, xs, ys) in enumerate(E.TRACKS)]


def _check_values(node):
    assert export.table_columns(node["tracks"]) == E.NCOLS == 31
    assert export.table_rows(node["tracks"]) == 3
    for r, (name, xs, ys) in enumerate(E.TRACKS):
        for f, x, y in zip(E.KEY_FRAMES, xs, ys):
            assert abs(export.evaluate(node, r, "track_x", f) - x) < 1e-6, (r, f)
            assert abs(export.evaluate(node, r, "track_y", f) - y) < 1e-6, (r, f)
        mid = export.evaluate(node, r, "track_x", 3)           # between the keys at 1 and 5
        assert min(xs[0], xs[1]) <= mid <= max(xs[0], xs[1])
    # track 2 (row 1) disabled on 6-9 and stepped (no fractional enable); the others always enabled
    assert [export.evaluate(node, 1, "enable", f) for f in (1, 5, 6, 7, 8, 9, 10, 15)] == [1, 1, 0, 0, 0, 0, 1, 1]
    assert all(export.evaluate(node, r, "enable", f) == 1 for r in (0, 2) for f in range(1, 16))


def test_fixture_evaluates_to_documented_values():
    _check_values(_load_fixture())


def test_export_evaluates_to_same_values():
    _load_fixture()
    n = nuke.nodes.Tracker4(name="rt")
    export.write_tracker4_table(n["tracks"], _expected_tracks())
    _check_values(n)


def test_export_roundtrips_fixture_text_exactly():
    ref = _load_fixture()["tracks"].toScript()
    n = nuke.nodes.Tracker4(name="rt2")
    export.write_tracker4_table(n["tracks"], _expected_tracks())
    assert n["tracks"].toScript() == ref


def test_write_replaces_rows():
    n = nuke.nodes.Tracker4(name="rep")
    export.write_tracker4_table(n["tracks"], _expected_tracks())
    export.write_tracker4_table(n["tracks"], _expected_tracks()[:1])
    assert export.table_rows(n["tracks"]) == 1


def test_dense_export_500_points_200_frames():
    res = {"first": 1001, "last": 1200,
           "tracks": [[[100.0 + q + f * 0.5, 200.0 + q * 0.25 + f] for f in range(200)] for q in range(500)],
           "visible": [[1 if (f + q) % 17 else 0 for f in range(200)] for q in range(500)]}
    n = nuke.nodes.Tracker4(name="dense")
    t0 = time.time()
    export.write_tracker4_table(n["tracks"], export.tracks_from_result(res))
    print("  500 x 200 write: %.1fs" % (time.time() - t0))
    assert export.table_rows(n["tracks"]) == 500
    assert abs(export.evaluate(n, 499, "track_x", 1100) - (100.0 + 499 + 99 * 0.5)) < 1e-6
    for q, f in ((0, 1017), (3, 1014), (10, 1040)):
        want = 1.0 if (f - 1001 + q) % 17 else 0.0
        assert export.evaluate(n, q, "enable", f) == want, (q, f)
