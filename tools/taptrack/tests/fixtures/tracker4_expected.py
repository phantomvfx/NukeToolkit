"""The data baked into tracker4_reference.nk (shared by the generator and the tests)."""

NCOLS = 31
COL = {"enable": 0, "name": 1, "track_x": 2, "track_y": 3, "offset_x": 4, "offset_y": 5, "T": 6, "R": 7, "S": 8}
KEY_FRAMES = [1, 5, 10, 15]

# name -> (x per key frame, y per key frame), Nuke pixels, origin bottom-left
TRACKS = [
    ("track 1", [100.0, 150.0, 220.25, 300.0], [200.0, 210.5, 230.0, 260.75]),
    ("track 2", [500.5, 480.0, 450.0, 400.0], [400.0, 420.0, 450.5, 500.0]),
    ("track 3", [960.0, 940.0, 900.0, 860.0], [540.0, 560.0, 580.25, 600.0]),
]
# Track 2 is disabled on frames 6-9: enable keys (frame -> value)
ENABLE_KEYS = {
    0: {1: 1.0, 5: 1.0, 10: 1.0, 15: 1.0},
    1: {1: 1.0, 5: 1.0, 6: 0.0, 9: 0.0, 10: 1.0, 15: 1.0},
    2: {1: 1.0, 5: 1.0, 10: 1.0, 15: 1.0},
}
