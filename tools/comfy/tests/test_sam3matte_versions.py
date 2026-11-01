import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

import sam3matte_versions as sv

PARAM_KEYS = {"mode", "key", "first", "last", "text", "positive", "negative",
              "max_objects", "chunk_size", "track_w", "track_h"}


def _job(**over):
    job = {"job": "SAM3Matte1_20261101_100000", "folder": "T/SAM3Matte1_20261101_100000",
           "frames_dir": "T/x/_plate", "key_png": "T/x/_plate/key.png",
           "mode": "key", "first": 1001, "last": 1200, "key": 1120,
           "text": "person:2", "positive": [{"x": 10, "y": 20}], "negative": [],
           "stencil": None, "max_objects": 4, "chunk_size": 250,
           "track_w": 1024, "track_h": 576}
    job.update(over)
    return job


def _result(**over):
    res = {"folder": "T/x", "first": 1001, "last": 1200, "key": 1120, "mode": "key",
           "text": "person:2", "width": 1024, "height": 576,
           "objects": [{"index": 0, "name": "select", "first": 1001, "last": 1200},
                       {"index": 1, "name": "person_1", "first": 1001, "last": 1200}],
           "names": {"0": "select", "1": "person_1"}, "next_free": 2,
           "completed_chunks": [0], "n_chunks": 1,
           "partial": False, "cancelled": False, "error": None, "dropped": 0,
           "hints": {"select": True, "stencil": False, "text": True}, "message": "ok"}
    res.update(over)
    return res


def test_parse_prompt():
    assert sv.parse_prompt("person:2, ball:1") == [("person", 2), ("ball", 1)]
    assert sv.parse_prompt(" person ,, ") == [("person", None)]
    assert sv.parse_prompt("") == []


def test_object_names_single_subject():
    assert sv.object_names("person:2", ["select", "text", "text"]) == ["select", "person_1", "person_2"]


def test_object_names_multi_subject():
    assert sv.object_names("person, ball", ["text", "text"]) == ["obj0", "obj1"]


def test_object_names_unique():
    names = sv.object_names("a", ["text"] * 3)
    assert len(set(names)) == 3
    names = sv.object_names("", ["select", "select"])
    assert len(set(names)) == 2


def test_new_version_and_matches():
    job = _job()
    v = sv.new_version(_result(partial=True), job, "2026-11-01 10:00:00")
    assert set(v["params"]) == PARAM_KEYS
    assert v["created"] == "2026-11-01 10:00:00"
    assert v["objects"][0]["name"] == "select"
    assert sv.version_matches(v, job)
    assert not sv.version_matches(v, _job(text="ball"))
    assert not sv.version_matches(v, _job(positive=[{"x": 11, "y": 20}]))
    done = sv.new_version(_result(partial=False), job, "2026-11-01 10:00:00")
    assert not sv.version_matches(done, job)


def test_label_key_mode():
    v = sv.new_version(_result(), _job(), "2026-11-01 10:00:00")
    assert sv.version_label(2, v) == "v3  person:2  key 1120  2 objects"


def test_label_range_partial_long_prompt():
    long_text = "a/b" + "x" * 40
    res = _result(mode="range", key=None, text=long_text,
                  objects=[{"index": 0, "name": "obj0", "first": 1001, "last": 1200}], partial=True)
    v = sv.new_version(res, _job(mode="range", key=None, text=long_text), "2026-11-01 10:00:00")
    label = sv.version_label(0, v)
    assert label == "v1  " + ("a-b" + "x" * 40)[:28] + "  range 1001-1200  1 object  partial"
    assert "/" not in label
    res = _result(mode="range", key=None, text="")
    v = sv.new_version(res, _job(mode="range", key=None, text=""), "2026-11-01 10:00:00")
    assert sv.version_label(0, v).startswith("v1  select  range 1001-1200")


OBJECTS = [{"index": 0, "name": "select"}, {"index": 2, "name": "person_1"}]


def test_parse_objects_all():
    assert sv.parse_objects("all", OBJECTS) == [0, 2]
    assert sv.parse_objects("", OBJECTS) == [0, 2]
    assert sv.parse_objects("ALL ", OBJECTS) == [0, 2]


def test_parse_objects_indices_and_names():
    assert sv.parse_objects("2", OBJECTS) == [2]
    assert sv.parse_objects("person_1, 0", OBJECTS) == [0, 2]


def test_parse_objects_garbage():
    assert sv.parse_objects("7", OBJECTS) == []
    assert sv.parse_objects("person_9", OBJECTS) == []
    assert sv.parse_objects(",,", OBJECTS) == []
    assert sv.parse_objects("0, nonsense", OBJECTS) == [0]
