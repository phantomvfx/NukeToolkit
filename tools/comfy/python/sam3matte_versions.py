"""SAM3Matte versions, object names and the Objects knob parser.

Pure leaf module (no nuke, no sibling imports), testable from plain Python.
A version is the result dict of one Generate plus "created" and "params";
the list of versions lives in the hidden JSON knob `versions_data`.
"""
import copy

PARAM_KEYS = ("mode", "key", "first", "last", "text", "positive", "negative",
              "max_objects", "chunk_size", "track_w", "track_h")


def parse_prompt(prompt):
    """'person:2, ball' -> [('person', 2), ('ball', None)]. Blank items skipped."""
    out = []
    for item in (prompt or "").split(","):
        item = item.strip()
        if not item:
            continue
        subject, sep, count = item.rpartition(":")
        if sep and subject.strip() and count.strip().isdigit():
            out.append((subject.strip(), int(count)))
        else:
            out.append((item, None))
    return out


def _unique(name, used):
    candidate, n = name, 2
    while candidate in used:
        candidate = "%s_%d" % (name, n)
        n += 1
    used.add(candidate)
    return candidate


def object_names(prompt, kinds):
    """Names for slots 0..len(kinds)-1. kinds[k] is "select" or "text"."""
    subjects = parse_prompt(prompt)
    used = set()
    names = []
    text_n = 0
    for k, kind in enumerate(kinds):
        if kind == "select":
            base = "select"
        else:
            text_n += 1
            base = "%s_%d" % (subjects[0][0], text_n) if len(subjects) == 1 else "obj%d" % k
        names.append(_unique(base, used))
    return names


def new_version(result, job, created):
    v = copy.deepcopy(result)
    v["created"] = created
    v["params"] = {k: copy.deepcopy(job.get(k)) for k in PARAM_KEYS}
    return v


def version_matches(v, job):
    """True when `v` is a partial version produced by the same parameters as `job`."""
    if not v.get("partial"):
        return False
    return all(job.get(k) == val for k, val in v.get("params", {}).items())


def version_label(index, v):
    """index is the 0-based position in the versions list; shown as v<index+1>."""
    prompt = (v.get("text") or "").strip()[:28].replace("/", "-") or "select"
    if v.get("mode") == "range":
        where = "range %s-%s" % (v.get("first"), v.get("last"))
    else:
        where = "key %s" % v.get("key")
    n = len(v.get("objects", []))
    label = "v%d  %s  %s  %d object%s" % (index + 1, prompt, where, n, "" if n == 1 else "s")
    if v.get("partial"):
        label += "  partial"
    return label


def parse_objects(text, objects):
    """Slots (sorted) selected by the Objects knob: 'all', '', '0,2' or names."""
    slots = sorted(o["index"] for o in objects)
    text = (text or "").strip()
    if not text:
        return slots
    by_name = {str(o.get("name", "")).lower(): o["index"] for o in objects}
    chosen = set()
    for tok in text.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok.lower() == "all":
            return slots
        if tok.isdigit():
            if int(tok) in slots:
                chosen.add(int(tok))
        elif tok.lower() in by_name:
            chosen.add(by_name[tok.lower()])
    return sorted(chosen)
