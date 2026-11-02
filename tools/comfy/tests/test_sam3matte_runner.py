import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import re
import socket
import subprocess
import threading

import pytest

import fake_comfy as fc
import sam3matte_chunks as ch
import sam3matte_graph as g
import sam3matte_runner as r
import sam3matte_versions as sv

W, H = 28, 8                 # tracking size used by the scenes (room for 9 object bars)
JOB = "SAM3Matte1_20261101_100000"


# ----------------------------------------------------------------------------- the scene the fake "SAM3" sees

def val(frame):
    return 200 + frame % 50


def band(k, value, w=W, h=H):
    """Object k = a 2 px wide vertical bar at x = 3k, rows 1..h-2, grey `value`."""
    data = bytearray(w * h)
    for y in range(1, h - 1):
        for x in (3 * k, 3 * k + 1):
            data[y * w + x] = value
    return bytes(data)


def png(data, w=W, h=H):
    return fc.rgb_png_bytes(w, h, data)


class Scene:
    """Objects k visible on frames vis[k] = (a, b). Every (object, frame) has its own bytes (value depends on frame)."""

    def __init__(self, first, vis):
        self.first, self.vis = first, vis

    def visible(self, k, frame):
        a, b = self.vis[k]
        return a <= frame <= b

    def mask(self, k, frame):
        return band(k, val(frame)) if self.visible(k, frame) else bytes(W * H)

    def png(self, k, frame):
        return png(self.mask(k, frame))


def band_of(png_bytes):
    """Which object a seed/mask PNG shows (index of its bar)."""
    w, h, data = fc.decode_grey(png_bytes)
    xs = [i % w for i, v in enumerate(data) if v]
    return min(xs) // 3


class SceneResponder:
    """Plays SAM3 for a Scene. Detect -> the key-frame masks (value 255) of `detect_objs`. Track -> per save slot j the
    seeded object j, then (text given) the unseeded visible objects in `order(prompt_no)`. The first time an
    (object, frame) is returned the bytes are right; any later time (an overlap frame) they are poisoned (value 150, still
    inside the mask), so a runner that rewrites an overlap frame fails the byte comparison."""

    def __init__(self, scene, detect_objs=(), order=None, on_prompt=None):
        self.scene, self.detect_objs = scene, list(detect_objs)
        self.order = order or (lambda n: sorted(scene.vis))
        self.on_prompt = on_prompt
        self.seen = set()
        self.fake = None

    def __call__(self, wf, n):
        if self.on_prompt:
            self.on_prompt(wf, n)
        if fc.nodes_of(wf, "SAM3_Detect"):
            return {"detect_save": [png(band(k, 255)) for k in self.detect_objs]}
        info = fc.track_info(wf)
        sub = info["directory"]
        idx = sorted(int(re.match(r"f_(\d+)\.png", name).group(1)) for name in self.fake.uploaded_names(sub))
        frames = [self.scene.first + i for i in idx]
        if info["reverse"]:
            frames = frames[::-1]
        seed_objs = []
        for value in info["seeds"]:
            s_sub, _, s_name = value.rpartition("/")
            seed_objs.append(band_of(self.fake.upload_data[(s_sub, s_name)]))
        extras = []
        if info["has_text"]:
            extras = [k for k in self.order(n) if k not in seed_objs
                      and any(self.scene.visible(k, f) for f in frames)]
        out = {}
        for j in range(info["n_save"]):
            if j < len(seed_objs):
                k = seed_objs[j]
            elif j - len(seed_objs) < len(extras):
                k = extras[j - len(seed_objs)]
            else:
                k = None
            images = []
            for f in frames:
                if k is None or not self.scene.visible(k, f):
                    images.append(png(bytes(W * H)))
                elif (k, f) in self.seen:
                    images.append(png(band(k, 150)))
                else:
                    self.seen.add((k, f))
                    images.append(self.scene.png(k, f))
            out["save%d" % j] = images
        return out


def static_responder(detect_pngs, w=W, h=H):
    """Detect -> the given PNGs; Track -> each seed returned unchanged for every frame, extras blank."""
    holder = {}

    def responder(wf, n):
        if fc.nodes_of(wf, "SAM3_Detect"):
            return {"detect_save": list(detect_pngs)}
        info = fc.track_info(wf)
        nframes = len(holder["fake"].uploaded_names(info["directory"]))
        out = {}
        for j in range(info["n_save"]):
            if j < len(info["seeds"]):
                s_sub, _, s_name = info["seeds"][j].rpartition("/")
                blob = holder["fake"].upload_data[(s_sub, s_name)]
            else:
                blob = png(bytes(w * h), w, h)
            out["save%d" % j] = [blob] * nframes
        return out

    responder.holder = holder
    return responder


# -------------------------------------------------------------------------------------------------- job plumbing

def make_job(tmp_path, mode="key", first=1001, last=1010, key=1005, text="person", positive=None, negative=(),
             stencil=None, max_objects=4, chunk_size=4, name=JOB):
    folder = str(tmp_path / name).replace("\\", "/")
    job = {"job": name, "folder": folder, "frames_dir": folder + "/_plate",
           "key_png": folder + "/_plate/key.png" if mode == "key" else None,
           "mode": mode, "first": first, "last": last, "key": key if mode == "key" else None,
           "text": text, "positive": [{"x": 1, "y": 1}] if (positive is None and mode == "key") else list(positive or []),
           "negative": list(negative), "stencil": stencil, "max_objects": max_objects, "chunk_size": chunk_size,
           "track_w": W, "track_h": H}
    make_plate(job)
    return job


def make_plate(job):
    os.makedirs(job["frames_dir"], exist_ok=True)
    blob = fc.rgb_png_bytes(4, 3, bytes(12))
    for f in range(job["first"], job["last"] + 1):
        with open("%s/f_%05d.png" % (job["frames_dir"], f - job["first"]), "wb") as fh:
            fh.write(blob)
    if job["key_png"]:
        with open(job["key_png"], "wb") as fh:
            fh.write(blob)


def start(responder):
    fake = fc.FakeComfy(responder).start()
    if hasattr(responder, "fake"):
        responder.fake = fake
    if hasattr(responder, "holder"):
        responder.holder["fake"] = fake
    return fake


def run(job, fake, resume=None, report=None, cancel=None):
    msgs = []

    def say(m):
        msgs.append(m)
        if report:
            report(m)

    result = r.run_job(job, say, cancel or threading.Event(), client=r.ComfyClient(fake.url), resume=resume,
                       poll_s=0.01)
    return result, msgs


def mask_files(job):
    return sorted(n for n in os.listdir(job["folder"]) if re.match(r"obj\d\.\d+\.png$", n))


def read_file(job, slot, frame):
    with open("%s/obj%d.%04d.png" % (job["folder"], slot, frame), "rb") as fh:
        return fh.read()


@pytest.fixture
def fakes():
    started = []

    def make(responder):
        f = start(responder)
        started.append(f)
        return f

    yield make
    for f in started:
        f.stop()


def key_scene(first=1001, last=1010):
    return Scene(first, {0: (first, last), 1: (first, last)})


# ------------------------------------------------------------------------------------------------------ key mode

def test_key_mode_two_directions_naming(tmp_path, fakes):
    scene = key_scene()
    job = make_job(tmp_path)
    fake = fakes(SceneResponder(scene, detect_objs=[0, 1]))
    res, _ = run(job, fake)
    assert res["error"] is None and not res["cancelled"] and not res["partial"]
    plan = ch.plan_chunks(1001, 1010, 1005, 4)
    assert res["n_chunks"] == len(plan) == 4
    assert res["completed_chunks"] == [0, 1, 2, 3]
    expected = sorted("obj%d.%04d.png" % (k, f) for k in (0, 1) for f in range(1001, 1011))
    assert mask_files(job) == expected                      # every file exactly once, nothing else
    for k in (0, 1):
        for f in range(1001, 1011):
            assert read_file(job, k, f) == scene.png(k, f), (k, f)   # correct frame for every output position
    assert res["hints"] == {"select": True, "stencil": False, "text": True}
    assert res["names"] == {"0": "select", "1": "person_1"}
    assert res["next_free"] == 2 and res["dropped"] == 0
    assert [(o["index"], o["name"], o["first"], o["last"]) for o in res["objects"]] == [
        (0, "select", 1001, 1010), (1, "person_1", 1001, 1010)]
    assert (res["folder"], res["first"], res["last"], res["key"], res["mode"], res["text"]) == (
        job["folder"], 1001, 1010, 1005, "key", "person")
    assert (res["width"], res["height"]) == (W, H)


def test_key_mode_graph_shapes(tmp_path, fakes):
    scene = key_scene()
    job = make_job(tmp_path)
    fake = fakes(SceneResponder(scene, detect_objs=[0, 1]))
    res, _ = run(job, fake)
    assert res["error"] is None
    plan = ch.plan_chunks(1001, 1010, 1005, 4)
    assert len(fake.prompts) == 1 + len(plan)
    detect = fake.prompts[0]
    assert fc.nodes_of(detect, "SAM3_Detect")
    assert detect["detect_save"]["inputs"]["images"] is not None
    assert detect[fc.nodes_of(detect, "LoadImage")[0]]["inputs"]["image"] == "nukecomfy/%s_key.png" % JOB
    assert detect["detect_save"]["inputs"]["filename_prefix"] == "NukeComfy/%s/detect/obj" % JOB
    for c, wf in enumerate(fake.prompts[1:]):
        tracks = fc.nodes_of(wf, "SAM3_VideoTrack")
        assert len(tracks) == 1 and "initial_mask" in wf[tracks[0]]["inputs"]
        assert "conditioning" not in wf[tracks[0]]["inputs"]
        assert bool(fc.nodes_of(wf, "ReverseImageBatch")) == (plan[c]["direction"] == "bwd")
        info = fc.track_info(wf)
        assert info["directory"] == "nukecomfy/%s_c%d" % (JOB, c)
        assert info["seeds"] == ["nukecomfy/%s_seed%d_%d.png" % (JOB, c, j) for j in range(2)]
        assert info["n_save"] == 2
        assert wf["save0"]["inputs"]["filename_prefix"] == "NukeComfy/%s/c%d/obj0" % (JOB, c)
        # frames uploaded ascending, named by offset from first, into the chunk subfolder
        want = ["f_%05d.png" % (f - 1001) for f in sorted(plan[c]["frames"])]
        assert fake.uploaded_names(info["directory"]) == want
    assert ("nukecomfy", "%s_key.png" % JOB) in fake.uploads
    # seeds of a "chunk" chunk are the previous chunk's last frame as written to the version folder
    assert fake.upload_data[("nukecomfy", "%s_seed1_0.png" % JOB)] == read_file(job, 0, plan[0]["frames"][-1])
    assert fake.upload_data[("nukecomfy", "%s_seed3_1.png" % JOB)] == read_file(job, 1, plan[2]["frames"][-1])
    # the first backward chunk is seeded from Detect again (full-size masks), not from the forward result
    assert fake.upload_data[("nukecomfy", "%s_seed2_0.png" % JOB)] == png(band(0, 255))


def test_upload_not_nested_layout(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(g, "UPLOAD_NESTED", False)
    job = make_job(tmp_path, first=1001, last=1004, key=1001, chunk_size=250)
    fake = fakes(SceneResponder(key_scene(1001, 1004), detect_objs=[0]))
    res, _ = run(job, fake)
    assert res["error"] is None
    assert fake.uploaded_names("nukecomfy_%s_c0" % JOB) == ["f_%05d.png" % i for i in range(4)]
    assert fc.track_info(fake.prompts[1])["directory"] == "nukecomfy_%s_c0" % JOB


def test_report_mentions_chunk_progress(tmp_path, fakes):
    job = make_job(tmp_path)
    fake = fakes(SceneResponder(key_scene(), detect_objs=[0, 1]))
    res, msgs = run(job, fake)
    low = [m.lower() for m in msgs]
    assert any("chunk 2/" in m for m in low)
    assert "Detecting..." in msgs
    assert any(re.match(r"Uploading chunk 2/4 \(\d+/\d+\)$", m) for m in msgs)
    assert "Chunk 2/4: tracking..." in msgs


def test_nothing_found_creates_no_version(tmp_path, fakes):
    def boom(wf, n):
        raise fc.FakeError("SaveImage", "index 0 is out of bounds for dimension 0 with size 0",
                           node_id="detect_save", exception_type="IndexError")

    job = make_job(tmp_path, positive=[])
    fake = fakes(boom)
    with pytest.raises(r.NothingFound):
        run(job, fake)
    assert mask_files(job) == []
    assert not os.path.exists(job["folder"] + "/_plate")
    assert issubclass(r.NothingFound, r.JobError)


def test_nothing_found_when_detect_returns_no_images(tmp_path, fakes):
    job = make_job(tmp_path, positive=[])
    fake = fakes(lambda wf, n: {"detect_save": []})
    with pytest.raises(r.NothingFound):
        run(job, fake)
    assert mask_files(job) == []


def test_other_detect_error_is_not_nothing_found(tmp_path, fakes):
    def boom(wf, n):
        raise fc.FakeError("SAM3_Detect", "CUDA out of memory", node_id="2")

    job = make_job(tmp_path)
    res, _ = run(job, fakes(boom))
    assert "CUDA out of memory" in res["error"] and res["completed_chunks"] == []


def _stencil_detect():
    w, h = 20, 4
    a = bytearray(w * h)   # select object: row 2, x 0..3 (100% inside the stencil)
    for x in range(4):
        a[2 * w + x] = 255
    b = bytearray(w * h)   # text object 1: row 0, x 0..4 -> 4 of 5 inside = 80%
    for x in range(5):
        b[0 * w + x] = 255
    c = bytearray(w * h)   # text object 2: row 1, x 0..3 -> 1 of 4 inside = 25%
    for x in range(4):
        c[1 * w + x] = 255
    values = [[0.0] * w for _ in range(h)]
    for x in range(4):
        values[0][x] = 1.0
        values[2][x] = 1.0
    values[1][0] = 1.0
    stencil = {"cols": w, "rows": h, "values": values}
    return [png(bytes(m), w, h) for m in (a, b, c)], stencil


def test_stencil_drop_text_objects(tmp_path, fakes):
    pngs, stencil = _stencil_detect()
    job = make_job(tmp_path, stencil=stencil, chunk_size=250, first=1001, last=1003, key=1001)
    resp = static_responder(pngs, 20, 4)
    fake = fakes(resp)
    resp.holder["fake"] = fake
    res, _ = run(job, fake)
    assert res["error"] is None
    assert res["hints"]["stencil"] is True
    assert sorted(res["names"]) == ["0", "1"] and res["names"]["0"] == "select"
    assert res["dropped"] == 0                                     # the stencil is the artist's choice, not a limit
    assert fake.upload_data[("nukecomfy", "%s_seed0_0.png" % JOB)] == pngs[0]   # select kept although inside
    assert fake.upload_data[("nukecomfy", "%s_seed0_1.png" % JOB)] == pngs[2]   # 25% object kept, 80% one dropped
    assert ("nukecomfy", "%s_seed0_2.png" % JOB) not in fake.uploads


def test_stencil_does_not_drop_without_select_when_below_half(tmp_path, fakes):
    pngs, stencil = _stencil_detect()
    job = make_job(tmp_path, stencil=stencil, positive=[], chunk_size=250, first=1001, last=1003, key=1001)
    resp = static_responder(pngs, 20, 4)
    fake = fakes(resp)
    resp.holder["fake"] = fake
    res, _ = run(job, fake)
    # no points object: all three are text objects; the 100% and 80% ones are dropped, only the 25% one stays
    assert list(res["names"]) == ["0"] and res["names"]["0"] == "person_1"
    assert fake.upload_data[("nukecomfy", "%s_seed0_0.png" % JOB)] == pngs[2]


def test_max_objects_caps_detect(tmp_path, fakes):
    scene = Scene(1001, {k: (1001, 1006) for k in range(5)})
    job = make_job(tmp_path, positive=[], max_objects=3, first=1001, last=1006, key=1003, chunk_size=250)
    fake = fakes(SceneResponder(scene, detect_objs=[0, 1, 2, 3, 4]))
    res, _ = run(job, fake)
    assert res["error"] is None
    assert sorted(res["names"]) == ["0", "1", "2"] and res["next_free"] == 3
    assert res["dropped"] == 2
    assert len(res["objects"]) == 3
    assert not any(n.startswith("obj3") or n.startswith("obj4") for n in mask_files(job))
    assert ("nukecomfy", "%s_seed0_3.png" % JOB) not in fake.uploads


# ----------------------------------------------------------------------------------------------------- range mode

def range_job(tmp_path, first=1001, last=1010, **kw):
    return make_job(tmp_path, mode="range", first=first, last=last, chunk_size=4, **kw)


def test_range_mode_seed_plus_text(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(g, "WHOLE_RANGE_SEED_WITH_TEXT", True)
    scene = Scene(1001, {0: (1001, 1010), 1: (1001, 1010), 2: (1008, 1010)})
    job = range_job(tmp_path)
    fake = fakes(SceneResponder(scene))
    res, _ = run(job, fake)
    assert res["error"] is None and res["n_chunks"] == 3 and res["completed_chunks"] == [0, 1, 2]
    infos = [fc.track_info(wf) for wf in fake.prompts]
    assert len(fake.prompts) == 3                                  # no Detect prompt in range mode
    assert infos[0]["seeds"] == [] and infos[0]["has_text"] and infos[0]["n_save"] == 4
    assert [len(i["seeds"]) for i in infos[1:]] == [2, 2] and all(i["has_text"] for i in infos[1:])
    assert all(not i["reverse"] for i in infos)
    assert res["hints"] == {"select": False, "stencil": False, "text": True}
    assert res["names"] == {"0": "person_1", "1": "person_2", "2": "person_3"}
    assert res["next_free"] == 3
    for k, (a, b) in ((0, (1001, 1010)), (1, (1001, 1010)), (2, (1008, 1010))):
        for f in range(a, b + 1):
            assert read_file(job, k, f) == scene.png(k, f), (k, f)
    third = {o["index"]: (o["first"], o["last"]) for o in res["objects"]}
    assert third == {0: (1001, 1010), 1: (1001, 1010), 2: (1008, 1010)}
    # the blank slots the first chunk returned (2 and 3 before C showed up) left no files behind except real ones
    assert not [n for n in mask_files(job) if n.startswith("obj3")]
    # chunk 0 saved Max objects slots with pass "text"
    assert fake.prompts[0]["save3"] and "save4" not in fake.prompts[0]


def test_range_mode_iou_strategy(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(g, "WHOLE_RANGE_SEED_WITH_TEXT", False)
    scene = Scene(1001, {0: (1001, 1010), 1: (1001, 1010), 2: (1008, 1010)})

    def order(n):
        # prompt 0 and 1: A, B ; prompt 2 (chunk 2): C, B, A  -> the same objects come back in another order
        return [2, 1, 0] if n == 2 else [0, 1, 2]

    job = range_job(tmp_path)
    fake = fakes(SceneResponder(scene, order=order))
    res, _ = run(job, fake)
    assert res["error"] is None and res["completed_chunks"] == [0, 1, 2]
    for wf in fake.prompts[1:]:
        i = fc.track_info(wf)
        assert i["seeds"] == [] and i["has_text"]                  # text only, no initial_mask
    for k, (a, b) in ((0, (1001, 1010)), (1, (1001, 1010)), (2, (1008, 1010))):
        for f in range(a, b + 1):
            assert read_file(job, k, f) == scene.png(k, f), (k, f)   # same slot for the same object
    assert res["next_free"] == 3 and sorted(res["names"]) == ["0", "1", "2"]
    assert [n for n in mask_files(job) if n.startswith("obj2.")] == ["obj2.%04d.png" % f for f in (1008, 1009, 1010)]


def test_range_mode_slot_overflow_counts_dropped(tmp_path, fakes, monkeypatch):
    monkeypatch.setattr(g, "WHOLE_RANGE_SEED_WITH_TEXT", True)
    # 8 objects fill every slot in chunk 0; object 7 is gone by the end of it, so chunk 1 has room for one new
    # object (a ninth), which has no slot left: dropped and counted
    vis = {k: (1001, 1007) for k in range(7)}
    vis[7] = (1001, 1003)
    vis[8] = (1005, 1007)
    job = range_job(tmp_path, last=1007, max_objects=8)
    fake = fakes(SceneResponder(Scene(1001, vis)))
    res, _ = run(job, fake)
    assert res["error"] is None
    assert len(res["objects"]) == 8 and res["next_free"] == 8
    assert res["dropped"] == 1
    assert not [n for n in mask_files(job) if n.startswith("obj8")]


# ---------------------------------------------------------------------------------------- empty slots, first / last

def test_empty_slot_dropped_and_first_last(tmp_path, fakes):
    scene = Scene(1001, {0: (1004, 1006), 1: (1, 0)})   # object 1: never visible
    job = make_job(tmp_path)
    fake = fakes(SceneResponder(scene, detect_objs=[0, 1]))
    res, _ = run(job, fake)
    assert res["error"] is None
    assert [(o["index"], o["first"], o["last"]) for o in res["objects"]] == [(0, 1004, 1006)]
    assert "1" not in res["names"]
    assert not [n for n in mask_files(job) if n.startswith("obj1")]
    assert res["completed_chunks"] == [0, 1, 2, 3]       # chunks with nothing left to seed still count as done


def test_range_mode_always_black_slots_absent(tmp_path, fakes):
    scene = Scene(1001, {0: (1003, 1008)})
    job = range_job(tmp_path, max_objects=3)
    fake = fakes(SceneResponder(scene))
    res, _ = run(job, fake)
    assert [(o["index"], o["first"], o["last"]) for o in res["objects"]] == [(0, 1003, 1008)]
    assert list(res["names"]) == ["0"]
    assert {n.split(".")[0] for n in mask_files(job)} == {"obj0"}


@pytest.mark.parametrize("mode", ["key", "range"])
def test_first_frame_zero(tmp_path, fakes, mode):
    scene = Scene(0, {0: (0, 5)})
    job = make_job(tmp_path, mode=mode, first=0, last=5, key=2, chunk_size=250)
    fake = fakes(SceneResponder(scene, detect_objs=[0]))
    res, _ = run(job, fake)
    assert res["error"] is None
    assert mask_files(job) == ["obj0.%04d.png" % f for f in range(6)]
    assert "f_00000.png" in [n for sub, n in fake.uploads if sub.startswith("nukecomfy/")]


@pytest.mark.parametrize("mode", ["key", "range"])
def test_single_frame_range(tmp_path, fakes, mode):
    scene = Scene(1001, {0: (1001, 1001)})
    job = make_job(tmp_path, mode=mode, first=1001, last=1001, key=1001, chunk_size=250)
    fake = fakes(SceneResponder(scene, detect_objs=[0]))
    res, _ = run(job, fake)
    assert res["error"] is None and res["n_chunks"] == 1 and res["completed_chunks"] == [0]
    assert mask_files(job) == ["obj0.1001.png"]
    assert read_file(job, 0, 1001) == scene.png(0, 1001)
    assert (res["objects"][0]["first"], res["objects"][0]["last"]) == (1001, 1001)


# ---------------------------------------------------------------------------------- errors, cancel, resume, cleanup

def _oom_at(n_fail):
    def hook(wf, n):
        if n == n_fail:
            raise fc.FakeError("SAM3_VideoTrack", "out of memory")
    return hook


def test_execution_error_keeps_partial(tmp_path, fakes):
    scene = key_scene()
    job = make_job(tmp_path)
    fake = fakes(SceneResponder(scene, detect_objs=[0, 1], on_prompt=_oom_at(4)))
    res, _ = run(job, fake)
    assert res["partial"] is True and res["cancelled"] is False
    assert "out of memory" in res["error"] and res["error"].startswith("Error in SAM3_VideoTrack")
    assert res["completed_chunks"] == [0, 1, 2] and res["n_chunks"] == 4
    names = mask_files(job)
    for f in range(1002, 1011):
        assert "obj0.%04d.png" % f in names
    assert "obj0.1001.png" not in names                    # the failed chunk left nothing behind
    assert [(o["index"], o["first"], o["last"]) for o in res["objects"]] == [(0, 1002, 1010), (1, 1002, 1010)]
    assert not os.path.exists(job["folder"] + "/_plate")


def test_server_down_in_chunk0(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    job = make_job(tmp_path)
    res = r.run_job(job, lambda m: None, threading.Event(), client=r.ComfyClient("http://127.0.0.1:%d" % port),
                    poll_s=0.01)
    assert res["error"] and res["completed_chunks"] == [] and res["partial"] is False
    assert res["objects"] == []
    assert not os.path.exists(job["folder"] + "/_plate")


def test_cancel_during_chunk_interrupts(tmp_path, fakes):
    cancel = threading.Event()
    job = make_job(tmp_path)
    resp = SceneResponder(key_scene(), detect_objs=[0, 1])

    def hook(wf, n):
        if n == 2:                                   # chunk 1 is queued and running when the artist cancels
            resp.fake.hold = "running"
            cancel.set()

    resp.on_prompt = hook
    fake = fakes(resp)
    res, _ = run(job, fake, cancel=cancel)
    assert res["cancelled"] is True and res["partial"] is True and res["error"] is None
    assert res["completed_chunks"] == [0]
    assert fake.deleted == ["fake-0002"]
    assert fake.interrupt_bodies == [{"prompt_id": "fake-0002"}]     # targeted, never a global interrupt


def test_cancel_between_chunks_stops_before_queueing(tmp_path, fakes):
    cancel = threading.Event()
    job = make_job(tmp_path)
    fake = fakes(SceneResponder(key_scene(), detect_objs=[0, 1]))
    res, _ = run(job, fake, cancel=cancel, report=lambda m: cancel.set() if m.startswith("Uploading chunk 2/") else None)
    assert res["cancelled"] is True and res["partial"] is True
    assert res["completed_chunks"] == [0]
    assert len(fake.prompts) == 2                       # Detect and chunk 0 only
    assert not fake.interrupts
    assert len(mask_files(job)) == 2 * 4                # chunk 0 wrote frames 1005..1008 for both objects


def test_cancel_before_start(tmp_path, fakes):
    cancel = threading.Event()
    cancel.set()
    job = make_job(tmp_path)
    fake = fakes(SceneResponder(key_scene(), detect_objs=[0, 1]))
    res, _ = run(job, fake, cancel=cancel)
    assert res["cancelled"] is True and res["completed_chunks"] == [] and res["partial"] is False
    assert fake.uploads == [] and fake.prompts == []


def test_resume_skips_completed_chunks(tmp_path, fakes):
    scene = key_scene()
    resp = SceneResponder(scene, detect_objs=[0, 1], on_prompt=_oom_at(4))
    job = make_job(tmp_path)
    fake1 = fakes(resp)
    res1, _ = run(job, fake1)
    assert res1["partial"] is True and res1["completed_chunks"] == [0, 1, 2]
    version = sv.new_version(res1, job, "2026-11-01 10:00:00")
    assert sv.version_matches(version, job)

    resp.on_prompt = None
    fake2 = fakes(resp)
    make_plate(job)                                       # the Nuke side re-renders the plate scratch
    res2, msgs = run(job, fake2, resume=version)
    assert res2["error"] is None and res2["partial"] is False
    assert res2["completed_chunks"] == [0, 1, 2, 3]
    assert len(fake2.prompts) == 1                        # only chunk 3: no Detect, no finished chunk again
    info = fc.track_info(fake2.prompts[0])
    assert info["directory"] == "nukecomfy/%s_c3" % JOB and info["reverse"]
    assert res2["names"] == res1["names"] == {"0": "select", "1": "person_1"}
    assert res2["next_free"] == res1["next_free"]
    assert res2["folder"] == res1["folder"]
    for k in (0, 1):
        for f in range(1001, 1011):
            assert read_file(job, k, f) == scene.png(k, f), (k, f)
    assert [(o["index"], o["first"], o["last"]) for o in res2["objects"]] == [(0, 1001, 1010), (1, 1001, 1010)]
    assert not any("Detecting" in m for m in msgs)


def test_range_mode_text_matching_nothing_is_nothing_found(tmp_path, fakes):
    # measured live: a prompt that matches nothing is a success with all-black images, not an execution_error
    job = range_job(tmp_path, text="giraffe")
    fake = fakes(SceneResponder(Scene(1001, {})))
    with pytest.raises(r.NothingFound):
        run(job, fake)
    assert len(fake.prompts) == 3                       # every chunk ran, all black
    assert mask_files(job) == []
    assert not os.path.exists(job["folder"] + "/_plate")


@pytest.mark.parametrize("bad", [
    {},                                                                  # missing keys
    {"chunk_size": 1},                                                   # plan_chunks rejects it
    {"first": 1010, "last": 1001},                                       # first after last
    {"key": 3000},                                                       # key outside the range
])
def test_run_job_never_raises_on_a_bad_job(tmp_path, bad):
    if bad == {}:
        job = {}
    else:
        job = make_job(tmp_path)
        job.update(bad)
    res = r.run_job(job, lambda m: None, threading.Event(), client=r.ComfyClient("http://127.0.0.1:9"), poll_s=0.01)
    assert res["error"] and res["completed_chunks"] == [] and res["partial"] is False and res["objects"] == []
    assert set(res) >= {"folder", "objects", "names", "error", "hints", "message"}


def test_run_job_bad_resume_is_an_error_result(tmp_path):
    job = make_job(tmp_path)
    res = r.run_job(job, lambda m: None, threading.Event(), client=r.ComfyClient("http://127.0.0.1:9"),
                    resume={"completed_chunks": [0]}, poll_s=0.01)      # no "folder"
    assert res["error"]


def test_corrupt_mask_file_in_folder_does_not_escape(tmp_path):
    job = make_job(tmp_path)
    with open(job["folder"] + "/obj0.1001.png", "wb") as f:       # a crash leftover
        f.write(b"not a png")
    res = r.run_job(job, lambda m: None, threading.Event(), client=r.ComfyClient("http://127.0.0.1:9"), poll_s=0.01)
    assert res["error"] and res["completed_chunks"] == []
    assert "unreadable mask file" in res["message"]
    assert not os.path.exists(job["folder"] + "/_plate")


def test_corrupt_leftover_in_a_good_run_is_reported_not_raised(tmp_path, fakes):
    job = make_job(tmp_path)
    with open(job["folder"] + "/obj5.1001.png", "wb") as f:
        f.write(b"not a png")
    fake = fakes(SceneResponder(key_scene(), detect_objs=[0, 1]))
    res, _ = run(job, fake)
    assert res["error"] is None and "unreadable mask file" in res["message"]


def test_undeletable_empty_slot_file_does_not_escape(tmp_path, fakes, monkeypatch):
    job = make_job(tmp_path)
    fake = fakes(SceneResponder(Scene(1001, {0: (1001, 1010), 1: (1, 0)}), detect_objs=[0, 1]))
    real_remove = os.remove

    def locked(path):
        if os.path.basename(path).startswith("obj1."):
            raise PermissionError("in use")
        return real_remove(path)

    monkeypatch.setattr(r.os, "remove", locked)
    res, _ = run(job, fake)
    assert res["error"] is None and "could not delete" in res["message"]


def test_finish_failure_becomes_an_error_result(tmp_path, fakes, monkeypatch):
    def boom(self, error, cancelled):
        raise RuntimeError("finish exploded")

    monkeypatch.setattr(r._Run, "finish", boom)
    job = make_job(tmp_path)
    res, _ = run(job, fakes(SceneResponder(key_scene(), detect_objs=[0])))
    assert "finish exploded" in res["error"] and res["objects"] == []


def test_plate_scratch_removed(tmp_path, fakes):
    job = make_job(tmp_path)
    fake = fakes(SceneResponder(key_scene(), detect_objs=[0, 1]))
    res, _ = run(job, fake)
    assert res["error"] is None
    assert not os.path.exists(job["folder"] + "/_plate")


def test_result_has_every_c3_key(tmp_path, fakes):
    job = make_job(tmp_path)
    fake = fakes(SceneResponder(key_scene(), detect_objs=[0]))
    res, _ = run(job, fake)
    assert set(res) == {"folder", "first", "last", "key", "mode", "text", "width", "height", "objects", "names",
                        "next_free", "completed_chunks", "n_chunks", "partial", "cancelled", "error", "dropped",
                        "hints", "message"}
    assert isinstance(res["message"], str) and res["message"]


def test_no_nuke_import():
    assert "nuke" not in sys.modules
    code = ("import sys; sys.path.insert(0, %r); import sam3matte_runner; "
            "assert 'nuke' not in sys.modules and 'nukescripts' not in sys.modules")
    pydir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python")
    subprocess.run([sys.executable, "-c", code % pydir], check=True)


# ----------------------------------------------------------------------------------------------- ComfyClient

def test_client_queue_400_gives_node_errors_text(fakes):
    def reject(wf, n):
        raise fc.FakeReject("Prompt outputs failed validation", {"5": {"class_type": "SAM3_VideoTrack", "errors": [
            {"type": "value_not_in_list", "message": "Value not in list: directory", "details": "directory: 'x'"}]}})

    fake = fakes(reject)
    client = r.ComfyClient(fake.url)
    with pytest.raises(r.JobError) as e:
        client.queue({"1": {"class_type": "X", "inputs": {}}})
    assert "Value not in list" in str(e.value) and "SAM3_VideoTrack" in str(e.value)


def test_client_wait_polls_until_done_and_returns_outputs(fakes, tmp_path):
    fake = fakes(lambda wf, n: {"save0": [fc.rgb_png_bytes(2, 2, bytes(4))]})
    fake.pending_polls = 3
    client = r.ComfyClient(fake.url)
    pid = client.queue({"save0": {"class_type": "SaveImage", "inputs": {"filename_prefix": "NukeComfy/j/c0/obj0"}}})
    outputs = client.wait(pid, threading.Event(), poll_s=0.01)
    img = outputs["save0"]["images"][0]
    assert img["subfolder"] == "NukeComfy/j/c0"
    dest = str(tmp_path / "dl.png")
    client.download(img, dest)
    assert open(dest, "rb").read() == fc.rgb_png_bytes(2, 2, bytes(4))


def test_client_wait_execution_error_message(fakes):
    def boom(wf, n):
        raise fc.FakeError("SAM3_VideoTrack", "out of memory", node_id="7", exception_type="OutOfMemoryError")

    fake = fakes(boom)
    client = r.ComfyClient(fake.url)
    pid = client.queue({"1": {"class_type": "X", "inputs": {}}})
    with pytest.raises(r.JobError) as e:
        client.wait(pid, threading.Event(), poll_s=0.01)
    assert str(e.value) == "Error in SAM3_VideoTrack: out of memory"


def test_client_wait_cancel_interrupts_only_our_running_prompt(fakes):
    fake = fakes(lambda wf, n: {})
    fake.hold = "running"
    client = r.ComfyClient(fake.url)
    pid = client.queue({"1": {"class_type": "X", "inputs": {}}})
    cancel = threading.Event()
    threading.Timer(0.1, cancel.set).start()
    with pytest.raises(r.JobCancelled):
        client.wait(pid, cancel, poll_s=0.01)
    assert fake.deleted == [pid]
    assert fake.interrupt_bodies == [{"prompt_id": pid}]


def test_client_cancel_of_queued_prompt_dequeues_and_never_interrupts(fakes):
    fake = fakes(lambda wf, n: {})
    fake.hold = "pending"
    fake.foreign_running = ["someone-elses-job"]       # a shared server busy with another client's job
    client = r.ComfyClient(fake.url)
    pid = client.queue({"1": {"class_type": "X", "inputs": {}}})
    assert client.queue_state(pid) == "pending"
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(r.JobCancelled):
        client.wait(pid, cancel, poll_s=0.01)
    assert fake.deleted == [pid]
    assert pid not in fake.queue_pending
    assert fake.interrupts == [] and fake.interrupt_bodies == []     # the other job was not touched


def test_client_wait_reports_a_lost_job(fakes):
    fake = fakes(lambda wf, n: {})
    fake.hold = "lost"                                  # accepted, then the server restarted: in no queue, no history
    client = r.ComfyClient(fake.url)
    pid = client.queue({"1": {"class_type": "X", "inputs": {}}})
    with pytest.raises(r.JobError) as e:
        client.wait(pid, threading.Event(), poll_s=0.01)
    assert "lost the job" in str(e.value)


def test_client_wait_keeps_waiting_while_the_job_is_queued(fakes):
    fake = fakes(lambda wf, n: {"save0": [fc.rgb_png_bytes(2, 2, bytes(4))]})
    fake.hold = "pending"
    fake.foreign_running = ["someone-elses-job"]
    client = r.ComfyClient(fake.url)
    pid = client.queue({"save0": {"class_type": "SaveImage", "inputs": {"filename_prefix": "NukeComfy/j/c0/obj0"}}})
    threading.Timer(0.3, fake.release).start()          # many polls (and queue checks) happen before this
    outputs = client.wait(pid, threading.Event(), poll_s=0.01)
    assert len(outputs["save0"]["images"]) == 1


def test_run_job_lost_job_is_an_error_result(tmp_path, fakes):
    job = make_job(tmp_path)
    resp = SceneResponder(key_scene(), detect_objs=[0, 1], on_prompt=lambda wf, n: setattr(resp.fake, "hold", "lost") if n == 1 else None)
    fake = fakes(resp)
    res, _ = run(job, fake)
    assert "lost the job" in res["error"] and res["completed_chunks"] == []


def test_client_upload_failure_raises_joberror(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    f = tmp_path / "a.png"
    f.write_bytes(fc.rgb_png_bytes(2, 2, bytes(4)))
    with pytest.raises(r.JobError):
        r.ComfyClient("http://127.0.0.1:%d" % port).upload(str(f), "nukecomfy")


def test_client_upload_returns_loadimage_value(fakes, tmp_path):
    fake = fakes(lambda wf, n: {})
    f = tmp_path / "a.png"
    f.write_bytes(fc.rgb_png_bytes(2, 2, bytes(4)))
    assert r.ComfyClient(fake.url).upload(str(f), "nukecomfy/j_c0") == "nukecomfy/j_c0/a.png"
