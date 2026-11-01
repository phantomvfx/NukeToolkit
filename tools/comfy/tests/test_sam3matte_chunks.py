import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

import pytest
import sam3matte_chunks as c


def _frames(chunks):
    return [ch["frames"] for ch in chunks]


def test_tracking_size():
    assert c.tracking_size(2048, 1080) == (1024, 540)
    assert c.tracking_size(4096, 2160) == (1024, 540)
    assert c.tracking_size(1999, 1080) == (1024, 554)  # 553 made even


def test_tracking_size_small_plate():
    assert c.tracking_size(640, 360) == (640, 360)


def test_plan_range_mode():
    p = c.plan_chunks(1001, 1200, None, 60)
    assert _frames(p) == [list(range(1001, 1061)), list(range(1060, 1120)),
                          list(range(1119, 1179)), list(range(1178, 1201))]
    assert [x["skip_first"] for x in p] == [False, True, True, True]
    assert [x["seed"] for x in p] == ["text", "chunk", "chunk", "chunk"]
    assert [x["seed_chunk"] for x in p] == [None, 0, 1, 2]
    assert [x["direction"] for x in p] == ["fwd"] * 4
    assert [x["index"] for x in p] == [0, 1, 2, 3]


def test_plan_key_mode():
    p = c.plan_chunks(1001, 1200, 1120, 60)
    assert _frames(p) == [list(range(1120, 1180)), list(range(1179, 1201)),
                          [1120] + list(range(1119, 1060, -1)),
                          list(range(1061, 1001, -1)),
                          [1002, 1001]]
    assert [x["skip_first"] for x in p] == [False, True, True, True, True]
    assert [x["seed"] for x in p] == ["detect", "chunk", "detect", "chunk", "chunk"]
    assert [x["seed_chunk"] for x in p] == [None, 0, None, 2, 3]
    assert [x["direction"] for x in p] == ["fwd", "fwd", "bwd", "bwd", "bwd"]
    assert [x["index"] for x in p] == [0, 1, 2, 3, 4]


def test_plan_key_at_last():
    p = c.plan_chunks(1, 10, 10, 60)
    assert len(p) == 1
    assert p[0]["direction"] == "bwd"
    assert p[0]["frames"] == list(range(10, 0, -1))
    assert p[0]["skip_first"] is False
    assert p[0]["seed"] == "detect"
    assert p[0]["seed_chunk"] is None
    assert p[0]["index"] == 0


def test_plan_key_at_first():
    p = c.plan_chunks(1, 10, 1, 60)
    assert len(p) == 1
    assert p[0]["direction"] == "fwd"
    assert p[0]["frames"] == list(range(1, 11))
    assert p[0]["skip_first"] is False
    assert p[0]["seed"] == "detect"


def test_plan_single_frame():
    for key in (5, None):
        p = c.plan_chunks(5, 5, key, 60)
        assert len(p) == 1
        assert p[0]["frames"] == [5]
        assert p[0]["skip_first"] is False


def test_plan_first_frame_zero():
    p = c.plan_chunks(0, 119, None, 60)
    assert p[0]["frames"][0] == 0
    covered = sorted({f for ch in p for f in ch["frames"]})
    assert covered == list(range(0, 120))


def test_plan_bad_args():
    with pytest.raises(ValueError):
        c.plan_chunks(1, 10, 11, 60)
    with pytest.raises(ValueError):
        c.plan_chunks(1, 10, 0, 60)
    with pytest.raises(ValueError):
        c.plan_chunks(10, 1, None, 60)
    with pytest.raises(ValueError):
        c.plan_chunks(1, 10, None, 1)


@pytest.mark.parametrize("first,last,key,size", [
    (1001, 1200, None, 60), (1001, 1200, 1120, 60), (1, 10, 10, 60), (1, 10, 1, 60),
    (5, 5, 5, 60), (5, 5, None, 2), (0, 119, 60, 2), (0, 119, None, 2),
    (1, 500, 250, 250), (1, 500, 500, 7), (1, 500, 1, 7), (1, 101, 51, 51),
    (1, 3, 2, 2),
])
def test_plan_invariants(first, last, key, size):
    p = c.plan_chunks(first, last, key, size)
    seen = set()
    for ch in p:
        fr = ch["frames"]
        assert 1 <= len(fr) <= size
        new = fr[1:] if ch["skip_first"] else fr
        if ch["skip_first"]:
            assert fr[0] in seen  # known from an earlier chunk
        for f in new:
            assert f not in seen  # covered exactly once
            seen.add(f)
        assert ch["index"] == p.index(ch)
        if ch["seed"] == "chunk":
            assert ch["seed_chunk"] is not None and ch["seed_chunk"] < ch["index"]
            prev = p[ch["seed_chunk"]]
            assert prev["direction"] == ch["direction"]
            assert prev["frames"][-1] == fr[0]
    assert seen == set(range(first, last + 1))


def test_mask_iou():
    assert c.mask_iou(bytes([255] * 4), bytes([255] * 4)) == 1.0
    assert c.mask_iou(bytes([255] * 4 + [0] * 4), bytes([0] * 4 + [255] * 4)) == 0.0
    assert c.mask_iou(bytes(8), bytes(8)) == 0.0
    a = bytes([255] * 4 + [0] * 4)
    b = bytes([0] * 2 + [255] * 4 + [0] * 2)
    assert c.mask_iou(a, b) == pytest.approx(1 / 3)
    # threshold: 127 counts as 0, 128 as 1
    assert c.mask_iou(bytes([127] * 4), bytes([255] * 4)) == 0.0
    assert c.mask_iou(bytes([128] * 4), bytes([255] * 4)) == 1.0
    assert c.mask_iou(bytes([127] * 4), bytes([255] * 4), threshold=100) == 1.0


def test_mask_iou_length_mismatch():
    with pytest.raises(ValueError):
        c.mask_iou(bytes(4), bytes(5))


def _m(on, n=16):
    """n-byte mask, 255 at the indices in `on`."""
    return bytes(255 if i in on else 0 for i in range(n))


def test_match_objects():
    A = _m(range(0, 6))
    B = _m(range(8, 14))
    A2 = _m(range(0, 5))      # overlaps A
    B2 = _m(range(9, 14))     # overlaps B
    C = _m([6, 7])            # overlaps neither
    got, nxt = c.match_objects({0: A, 1: B}, [B2, A2, C], 2)
    assert got == [1, 0, 2]
    assert nxt == 3


def test_match_objects_full():
    known = {0: _m([0, 1]), 1: _m([4, 5]), 2: _m([8, 9])}
    new = _m([12, 13])
    got, nxt = c.match_objects(known, [new], 3, max_slots=3)
    assert got == [None]
    assert nxt == 3


def test_match_objects_one_to_one():
    K = _m(range(0, 8))
    good = _m(range(0, 8))        # IoU 1.0
    weak = _m(range(0, 5))        # IoU 5/8 = 0.625, still above threshold
    got, nxt = c.match_objects({0: K}, [weak, good], 1)
    assert got[1] == 0           # best IoU wins the slot
    assert got[0] == 1           # the other becomes a new object
    assert nxt == 2


def test_match_objects_below_threshold_is_new():
    K = _m(range(0, 8))
    far = _m([7, 8, 9, 10, 11])   # IoU 1/12
    got, nxt = c.match_objects({0: K}, [far], 1)
    assert got == [1]
    assert nxt == 2
