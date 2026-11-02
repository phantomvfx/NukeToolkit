import json
import os
import struct
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

import sam3matte_crypto as c


def test_mmh3_known_vectors():
    assert c.mmh3_32("") == 0
    assert c.mmh3_32("hello") == 0x248BFA47
    assert c.mmh3_32("The quick brown fox jumps over the lazy dog") == 0x2E4FF723


def test_id_exponent_never_0_or_255():
    flipped = 0
    for i in range(5000):
        name = "n%d" % i
        h, _ = c.name_to_id(name)
        assert ((h >> 23) & 255) not in (0, 255)
        if ((c.mmh3_32(name) >> 23) & 255) in (0, 255):
            flipped += 1
    assert flipped > 0


def test_id_float_round_trips():
    for name in ("obj0", "obj1", "person_1", "select", "n17"):
        h, v = c.name_to_id(name)
        assert struct.unpack("<f", struct.pack("<I", h))[0] == v
        # %.9g is lossless for float32 (not for the double that holds it): round the parsed text back to float32
        parsed = float("%.9g" % v)
        assert struct.unpack("<f", struct.pack("<f", parsed))[0] == v


def test_layer_key():
    assert c.layer_key() == ("%08x" % c.mmh3_32("CryptoSAM"))[:7]
    assert c.layer_key("Other") == ("%08x" % c.mmh3_32("Other"))[:7]


def test_manifest_skips_none():
    expected = {
        "a": "%08x" % c.name_to_id("a")[0],
        "b": "%08x" % c.name_to_id("b")[0],
    }
    assert c.manifest(["a", None, "b"]) == expected


def test_metadata_script_roundtrip():
    names = ["obj0", None, "obj2"]
    lines = c.metadata_script(names).split("\n")
    assert len(lines) == 4
    prefix = "exr/cryptomatte/%s/" % c.layer_key()
    assert "{set %shash MurmurHash3_32}" % prefix in lines
    assert "{set %sconversion uint32_to_float32}" % prefix in lines
    assert "{set %sname CryptoSAM}" % prefix in lines
    line = [l for l in lines if "manifest" in l][0]
    assert line.startswith("{set %smanifest " % prefix) and line.endswith("}")
    payload = line[len("{set %smanifest " % prefix):-1]
    assert json.loads(json.loads(payload)) == c.manifest(names)


def test_expressions_one_channel_each():
    e = c.expressions(["a", "b", None, "d"])
    assert sorted(e) == ["expr0", "expr1", "expr2", "expr3"]
    assert e["expr2"] != "0" and e["expr3"] != "0"
    e = c.expressions([None] * 8)
    assert e == {"expr0": "0", "expr1": "0", "expr2": "0", "expr3": "0"}


def test_expressions_embed_ids():
    e = c.expressions(["obj1", None, "obj3"])
    idv = "%.9g" % c.name_to_id("obj1")[1]
    assert idv in e["expr0"]
    assert idv in e["expr2"]
    for k in ("expr0", "expr1", "expr2", "expr3"):
        assert "sam.m1" not in e[k]
    assert "sam.m2" in e["expr1"]
    assert "sam.m2" not in c.expressions(["a", None, None])["expr1"]
    assert "sam.m0" in c.expressions(["a", None, "c"])["expr1"]


def test_reference_ranks():
    names = ["a", "b", "c"]
    id_b = c.name_to_id("b")[1]
    id_c = c.name_to_id("c")[1]
    assert c.reference([0.2, 0.9, 0.5], names) == (id_b, 0.9, id_c, 0.5)
    assert c.reference([0, 0, 0], names) == (0, 0, 0, 0)
    id_a = c.name_to_id("a")[1]
    assert c.reference([0.4, 0, 0], names) == (id_a, 0.4, 0, 0)
    # tie: lower index is rank 0
    assert c.reference([0.5, 0.5], ["a", "b"]) == (id_a, 0.5, c.name_to_id("b")[1], 0.5)
    # empty slots do not take part
    assert c.reference([0.9, 0.3, 0.1], [None, "b", "c"]) == (
        c.name_to_id("b")[1], 0.3, c.name_to_id("c")[1], 0.1)


class FakeKnob:
    def __init__(self):
        self.value = None

    def setValue(self, v):
        self.value = v

    def fromScript(self, s):
        self.value = s


class FakeNode:
    def __init__(self):
        self.knobs = {}

    def __getitem__(self, k):
        return self.knobs.setdefault(k, FakeKnob())


def test_apply_to_nodes():
    names = ["a", None, "c"]
    ex, md = FakeNode(), FakeNode()
    c.apply_to_nodes(ex, md, names)
    for k, ch in enumerate(("red", "green", "blue", "alpha")):
        assert ex["channel%d" % k].value == "CryptoSAM00." + ch
    for k, expr in c.expressions(names).items():
        assert ex[k].value == expr
    assert md["metadata"].value == c.metadata_script(names)
