import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
import sam3matte_graph as g  # noqa: E402

POS = [{"x": 962, "y": 538}, {"x": 983, "y": 744}]
NEG = [{"x": 300, "y": 200}]


def by_class(wf, cls):
    return [n for n in wf.values() if n["class_type"] == cls]


def detect_params(**kw):
    p = {"key_image": "nukecomfy/j_key.png", "text": "", "positive": [], "negative": [], "threshold": 0.5,
         "prefix": "NukeComfy/j/detect/obj"}
    p.update(kw)
    return p


def track_params(**kw):
    p = {"directory": "nukecomfy/j_c0", "reverse": False, "track_w": 1024, "track_h": 540, "seeds": [],
         "text": "person", "max_objects": 4, "n_save": 4, "prefix": "NukeComfy/j/c0/obj", "pass_name": "text"}
    p.update(kw)
    return p


def test_detect_text_only():
    wf, out = g.build_detect(detect_params(text="person:2, ball:1", negative=[{"x": 1, "y": 2}]))
    assert out == {"detect_save": {"obj": None, "pass": "key"}}
    det = by_class(wf, "SAM3_Detect")[0]
    assert det["inputs"]["individual_masks"] is True
    assert "positive_coords" not in det["inputs"] and "negative_coords" not in det["inputs"]
    assert by_class(wf, "CLIPTextEncode")[0]["inputs"]["text"] == "person:2, ball:1"
    assert "conditioning" in det["inputs"]
    assert det["inputs"]["refine_iterations"] == 2 and det["inputs"]["threshold"] == 0.5


def test_detect_points_only_no_text_node():
    wf, out = g.build_detect(detect_params(positive=POS, negative=NEG))
    det = by_class(wf, "SAM3_Detect")[0]
    assert not by_class(wf, "CLIPTextEncode") and "conditioning" not in det["inputs"]
    assert json.loads(wf[det["inputs"]["positive_coords"][0]]["inputs"]["value"]) == POS
    assert json.loads(wf[det["inputs"]["negative_coords"][0]]["inputs"]["value"]) == NEG
    assert wf[det["inputs"]["positive_coords"][0]]["class_type"] == "PrimitiveStringMultiline"


def test_detect_negative_only_is_dropped():
    wf, out = g.build_detect(detect_params(text="person", negative=NEG))
    det = by_class(wf, "SAM3_Detect")[0]
    assert "negative_coords" not in det["inputs"] and "positive_coords" not in det["inputs"]


def test_detect_text_and_points():
    wf, out = g.build_detect(detect_params(text="person", positive=POS, negative=NEG))
    dets = by_class(wf, "SAM3_Detect")
    assert len(dets) == 1
    assert "conditioning" in dets[0]["inputs"] and "positive_coords" in dets[0]["inputs"]
    assert "negative_coords" in dets[0]["inputs"]
    assert "detect_save" in wf and wf["detect_save"]["class_type"] == "SaveImage"
    assert wf["detect_save"]["inputs"]["filename_prefix"] == "NukeComfy/j/detect/obj"


def test_detect_needs_text_or_points():
    with pytest.raises(ValueError):
        g.build_detect(detect_params())
    with pytest.raises(ValueError):
        g.build_detect(detect_params(negative=NEG))


def test_detect_split_mode(monkeypatch):
    monkeypatch.setattr(g, "SPLIT_DETECT", True)
    wf, out = g.build_detect(detect_params(text="person", positive=POS, negative=NEG))
    dets = by_class(wf, "SAM3_Detect")
    assert len(dets) == 2
    text_det = [d for d in dets if "conditioning" in d["inputs"]]
    pts_det = [d for d in dets if "positive_coords" in d["inputs"]]
    assert len(text_det) == 1 and len(pts_det) == 1 and text_det[0] is not pts_det[0]
    assert "positive_coords" not in text_det[0]["inputs"] and "conditioning" not in pts_det[0]["inputs"]
    assert out == {"detect_save": {"obj": None, "pass": "key"}}
    batch = by_class(wf, "ImageBatch")[0]
    first = wf[batch["inputs"]["image1"][0]]
    assert first["class_type"] == "MaskToImage"
    assert wf[first["inputs"]["mask"][0]] is pts_det[0]  # points object first
    assert len(by_class(wf, "ImageToMask")) == 1 and len(by_class(wf, "MaskToImage")) == 3
    assert wf["detect_save"]["class_type"] == "SaveImage"


def test_track_text_only():
    wf, out = g.build_track(track_params())
    assert sorted(out) == ["save0", "save1", "save2", "save3"]
    assert out["save2"] == {"obj": 2, "pass": "text"}
    vt = by_class(wf, "SAM3_VideoTrack")[0]
    assert not by_class(wf, "ReverseImageBatch") and "initial_mask" not in vt["inputs"]
    assert "conditioning" in vt["inputs"]
    assert vt["inputs"]["max_objects"] == 4 and vt["inputs"]["detection_threshold"] == 0.5
    assert vt["inputs"]["detect_interval"] == 1
    ckpt_id = vt["inputs"]["model"][0]
    assert wf[ckpt_id]["class_type"] == "CheckpointLoaderSimple" and wf[ckpt_id]["inputs"]["ckpt_name"] == g.CKPT
    load = by_class(wf, "VHS_LoadImages")[0]["inputs"]
    assert load["directory"] == "nukecomfy/j_c0" and load["image_load_cap"] == 0
    ttm = wf[wf[wf["save2"]["inputs"]["images"][0]]["inputs"]["mask"][0]]
    assert ttm["class_type"] == "SAM3_TrackToMask" and ttm["inputs"]["object_indices"] == "2"


def test_track_seeds_reverse_no_text():
    wf, out = g.build_track(track_params(seeds=["nukecomfy/a.png", "nukecomfy/b.png"], reverse=True, text="",
                                         max_objects=1, pass_name="bwd"))
    vt = by_class(wf, "SAM3_VideoTrack")[0]
    assert len(by_class(wf, "LoadImage")) == 2 and len(by_class(wf, "ImageBatch")) == 1
    assert len(by_class(wf, "ReverseImageBatch")) == 1
    sc = by_class(wf, "ImageScale")[0]["inputs"]
    assert (sc["width"], sc["height"], sc["crop"]) == (1024, 540, "disabled")
    assert sc["upscale_method"] == "bilinear"
    assert "conditioning" not in vt["inputs"] and not by_class(wf, "CLIPTextEncode")
    assert vt["inputs"]["max_objects"] == 2   # max(max_objects=1, len(seeds))
    assert out["save0"] == {"obj": 0, "pass": "bwd"}
    rev = wf[vt["inputs"]["images"][0]]
    assert rev["class_type"] == "ReverseImageBatch"
    assert wf[rev["inputs"]["images"][0]]["class_type"] == "VHS_LoadImages"


def test_track_seed_chain_ends_in_threshold_mask():
    wf, _ = g.build_track(track_params(seeds=["a.png", "b.png", "c.png"], text=""))
    vt = by_class(wf, "SAM3_VideoTrack")[0]
    th = wf[vt["inputs"]["initial_mask"][0]]
    assert th["class_type"] == "ThresholdMask" and th["inputs"]["value"] == 0.5
    i2m = wf[th["inputs"]["mask"][0]]
    assert i2m["class_type"] == "ImageToMask" and i2m["inputs"]["channel"] == "red"
    sc = wf[i2m["inputs"]["image"][0]]
    assert sc["class_type"] == "ImageScale"
    b2 = wf[sc["inputs"]["image"][0]]
    assert b2["class_type"] == "ImageBatch"
    b1 = wf[b2["inputs"]["image1"][0]]
    assert b1["class_type"] == "ImageBatch"
    assert wf[b2["inputs"]["image2"][0]]["class_type"] == "LoadImage"
    assert wf[b1["inputs"]["image1"][0]]["inputs"]["image"] == "a.png"
    assert wf[b1["inputs"]["image2"][0]]["inputs"]["image"] == "b.png"
    assert wf[b2["inputs"]["image2"][0]]["inputs"]["image"] == "c.png"


def test_track_seeds_and_text():
    wf, _ = g.build_track(track_params(seeds=["a.png"], text="person"))
    vt = by_class(wf, "SAM3_VideoTrack")[0]
    assert "initial_mask" in vt["inputs"] and "conditioning" in vt["inputs"]
    assert not by_class(wf, "ImageBatch")


def test_track_needs_seeds_or_text():
    with pytest.raises(ValueError):
        g.build_track(track_params(text="", seeds=[]))


def test_track_max_objects_capped_64():
    wf, _ = g.build_track(track_params(max_objects=200))
    assert by_class(wf, "SAM3_VideoTrack")[0]["inputs"]["max_objects"] == 64


def test_track_save_prefix_unique():
    wf, out = g.build_track(track_params(prefix="NukeComfy/j/c1/obj", n_save=8))
    assert len(out) == 8
    prefixes = [wf[f"save{j}"]["inputs"]["filename_prefix"] for j in range(8)]
    assert prefixes == [f"NukeComfy/j/c1/obj{j}" for j in range(8)]


def test_workflow_is_json_serialisable_and_links_resolve(monkeypatch):
    cases = [
        g.build_detect(detect_params(text="person", positive=POS, negative=NEG)),
        g.build_detect(detect_params(positive=POS)),
        g.build_track(track_params()),
        g.build_track(track_params(seeds=["a.png", "b.png"], reverse=True, text="x")),
    ]
    monkeypatch.setattr(g, "SPLIT_DETECT", True)
    cases.append(g.build_detect(detect_params(text="person", positive=POS)))
    for wf, out in cases:
        json.dumps(wf)
        assert all(isinstance(k, str) for k in wf)
        for node in wf.values():
            for v in node["inputs"].values():
                if isinstance(v, list):
                    assert len(v) == 2 and v[0] in wf and isinstance(v[1], int)
        for nid in out:
            assert wf[nid]["class_type"] == "SaveImage"
