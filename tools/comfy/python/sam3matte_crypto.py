"""SAM3Matte: Cryptomatte maths (pure Python, no nuke import).

Builds the pieces that turn eight per-object coverage channels (sam.m0..sam.m7)
into a Cryptomatte layer "CryptoSAM00" (id0, cov0, id1, cov1) that Nuke's own
Cryptomatte node can pick from:
  - MurmurHash3_32 of the object name -> float32 id (exponent fixed so it is
    never 0 or 255, as the Cryptomatte spec requires)
  - the manifest and the four exr/cryptomatte/<key>/... metadata lines
  - four Expression strings, one per channel (id0, cov0, id1, cov1)
  - reference(): the same maths in Python, for tests

Target Nuke 16.1 (Python 3.11). Tested: pure-Python unit tests only; the
expressions follow the spike verified in Nuke 16.1 headless, but this module's
output has not been run in Nuke yet.
"""
import json
import struct

LAYER = "CryptoSAM"
SAM_CHANNELS = ["sam.m%d" % i for i in range(8)]
_PLANES = ("red", "green", "blue", "alpha")


def mmh3_32(text, seed=0):
    data = bytearray(text.encode("utf-8"))
    c1, c2 = 0xCC9E2D51, 0x1B873593
    h = seed
    n = len(data) // 4
    for i in range(n):
        k = data[4 * i] | data[4 * i + 1] << 8 | data[4 * i + 2] << 16 | data[4 * i + 3] << 24
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    tail = data[4 * n:]
    k = 0
    if len(tail) >= 3:
        k ^= tail[2] << 16
    if len(tail) >= 2:
        k ^= tail[1] << 8
    if len(tail) >= 1:
        k ^= tail[0]
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        k = (k * c2) & 0xFFFFFFFF
        h ^= k
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    h ^= h >> 16
    return h


def name_to_id(name):
    """(uint32 with the exponent fix, float32 value) for an object name."""
    h = mmh3_32(name)
    if ((h >> 23) & 255) in (0, 255):
        h ^= 1 << 23
    return h, struct.unpack("<f", struct.pack("<I", h))[0]


def layer_key(layer=LAYER):
    return ("%08x" % mmh3_32(layer))[:7]


def manifest(names):
    return {n: "%08x" % name_to_id(n)[0] for n in names if n is not None}


def metadata_script(names):
    prefix = "exr/cryptomatte/%s/" % layer_key()
    man = json.dumps(json.dumps(manifest(names), separators=(",", ":")))
    return "\n".join([
        "{set %sname %s}" % (prefix, LAYER),
        "{set %shash MurmurHash3_32}" % prefix,
        "{set %sconversion uint32_to_float32}" % prefix,
        "{set %smanifest %s}" % (prefix, man),
    ])


def _slots(names, channels):
    return [(i, names[i], channels[i]) for i in range(min(len(names), len(channels)))
            if names[i] is not None]


def _rank_expr(pos, slots):
    """Additive Nuke expression for slot `pos`'s rank; ties go to the lower index."""
    i, _, mi = slots[pos]
    terms = []
    for q, (j, _, mj) in enumerate(slots):
        if q == pos:
            continue
        terms.append("(%s %s %s)" % (mj, ">=" if j < i else ">", mi))
    return " + ".join(terms) if terms else "0"


def expressions(names, channels=SAM_CHANNELS):
    slots = _slots(names, channels)
    if not slots:
        return {"expr0": "0", "expr1": "0", "expr2": "0", "expr3": "0"}
    ms = [m for _, _, m in slots]
    cov0 = ms[0] if len(ms) == 1 else "max(%s)" % ", ".join(ms)
    ranks = [_rank_expr(p, slots) for p in range(len(slots))]

    def pick_id(rank):
        e = "0"
        for p in range(len(slots) - 1, -1, -1):
            idv = "%.9g" % name_to_id(slots[p][1])[1]
            e = "((%s) == %d) ? %s : (%s)" % (ranks[p], rank, idv, e)
        return e

    def pick_cov1():
        terms = ["(((%s) == 1) ? %s : 0)" % (ranks[p], slots[p][2]) for p in range(len(slots))]
        return terms[0] if len(terms) == 1 else "max(%s)" % ", ".join(terms)

    if len(slots) == 1:
        return {
            "expr0": "(%s > 0) ? %s : 0" % (cov0, "%.9g" % name_to_id(slots[0][1])[1]),
            "expr1": cov0, "expr2": "0", "expr3": "0",
        }
    cov1 = pick_cov1()
    return {
        "expr0": "(%s > 0) ? (%s) : 0" % (cov0, pick_id(0)),
        "expr1": cov0,
        "expr2": "(%s > 0) ? (%s) : 0" % (cov1, pick_id(1)),
        "expr3": cov1,
    }


def reference(values, names):
    """What the expressions compute for slot coverages `values`: (id0, cov0, id1, cov1)."""
    slots = [(i, names[i]) for i in range(min(len(names), len(values))) if names[i] is not None]
    if not slots:
        return (0.0, 0.0, 0.0, 0.0)
    order = sorted(slots, key=lambda s: (-values[s[0]], s[0]))   # ties: lower index first
    out = []
    for r in range(2):
        if r < len(order) and values[order[r][0]] > 0:
            out += [name_to_id(order[r][1])[1], values[order[r][0]]]
        else:
            out += [0.0, 0.0]
    cov0 = max(values[i] for i, _ in slots)
    if cov0 <= 0:
        return (0.0, 0.0, 0.0, 0.0)
    return tuple(out)


def apply_to_nodes(expr_node, meta_node, names):
    """Set the Expression and ModifyMetaData nodes (duck-typed: node[knob].setValue / fromScript)."""
    for k, plane in enumerate(_PLANES):
        expr_node["channel%d" % k].setValue("%s00.%s" % (LAYER, plane))
    for key, expr in expressions(names).items():
        expr_node[key].setValue(expr)
    meta_node["metadata"].fromScript(metadata_script(names))
