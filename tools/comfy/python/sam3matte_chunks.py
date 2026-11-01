"""SAM3Matte: chunk planning, mask IoU matching and tracking size.

Pure leaf module: stdlib only, no nuke import, no sibling imports.
Target Nuke 16.1 (Python 3.11). Tested with pytest outside Nuke
(tools/comfy/tests/test_sam3matte_chunks.py); nothing here touches Nuke or ComfyUI.

Used by the SAM3Matte runner to
  - pick the PNG size sent to SAM3 (tracking_size),
  - split a shot into overlapping chunks tracked outward from a key frame (plan_chunks),
  - keep object slots stable from chunk to chunk by mask overlap (mask_iou, match_objects).
"""


def tracking_size(w, h, target=1024):
    """Size of the PNGs sent to SAM3: width capped at `target`, both sides made even."""
    tw = min(w, target)
    th = round(tw * h / w)
    tw += tw % 2
    th += th % 2
    return tw, th


def _split(new_frames, size, prefix):
    """Cut `new_frames` (tracking order) into chunk frame lists.

    `prefix` is a frame already known that the first chunk starts on, or None.
    The first chunk holds up to `size` new frames when there is no prefix; every
    other chunk starts on the previous chunk's last frame and holds up to
    `size - 1` new frames. Returns a list of (frames, skip_first).
    """
    out = []
    i = 0
    start = prefix
    while i < len(new_frames):
        if start is None:
            take = new_frames[i:i + size]
            frames, skip = list(take), False
        else:
            take = new_frames[i:i + size - 1]
            frames, skip = [start] + list(take), True
        out.append((frames, skip))
        i += len(take)
        start = frames[-1]
    return out


def plan_chunks(first, last, key, chunk_size):
    """Plan the tracking chunks for the frame range first..last.

    Each chunk is {"index", "direction": "fwd"|"bwd", "frames" (tracking order),
    "skip_first" (frames[0] is already known: do not save its output),
    "seed": "detect"|"chunk"|"text", "seed_chunk": int | None}.
    Range mode (key is None): one forward run seeded by the text prompt.
    Key mode: forward chunks key..last first, then backward chunks key-1..first
    whose first chunk starts on the key frame.
    """
    if first > last:
        raise ValueError("first (%s) is after last (%s)" % (first, last))
    if chunk_size < 2:
        raise ValueError("chunk_size must be at least 2, got %s" % chunk_size)
    if key is not None and not (first <= key <= last):
        raise ValueError("key %s is outside %s..%s" % (key, first, last))

    plan = []

    def add(direction, parts, first_seed):
        for n, (frames, skip) in enumerate(parts):
            if n == 0:
                seed, seed_chunk = first_seed, None
            else:
                seed, seed_chunk = "chunk", len(plan) - 1
            plan.append({"index": len(plan), "direction": direction, "frames": frames,
                         "skip_first": skip, "seed": seed, "seed_chunk": seed_chunk})

    if key is None:
        add("fwd", _split(list(range(first, last + 1)), chunk_size, None), "text")
        return plan

    if not (key == last and key > first):
        add("fwd", _split(list(range(key, last + 1)), chunk_size, None), "detect")
        if key > first:
            # the key frame is already known from forward chunk 0
            add("bwd", _split(list(range(key - 1, first - 1, -1)), chunk_size, key), "detect")
    else:
        # key on the last frame: nothing to go forward to, the key frame is new in the backward run
        add("bwd", _split(list(range(key, first - 1, -1)), chunk_size, None), "detect")
    return plan


def _binarize(data, threshold):
    """bytes -> int with one bit per byte position set where value >= threshold."""
    table = bytes(1 if v >= threshold else 0 for v in range(256))
    return int.from_bytes(data.translate(table), "big")


def _iou_bits(a, b):
    union = (a | b).bit_count()
    if union == 0:
        return 0.0
    return (a & b).bit_count() / union


def mask_iou(a, b, threshold=128):
    """Intersection over union of two equal-length 8-bit grey masks (>= threshold is inside)."""
    if len(a) != len(b):
        raise ValueError("masks differ in size: %d vs %d bytes" % (len(a), len(b)))
    return _iou_bits(_binarize(a, threshold), _binarize(b, threshold))


def match_objects(known, found, next_free, max_slots=8, threshold=0.5):
    """Assign each mask in `found` a slot number.

    `known` maps slot -> mask bytes from the previous frame/chunk. Greedy by
    descending IoU over all (found, slot) pairs with IoU >= threshold, each side
    used once. Unmatched found objects take next_free, next_free+1, ... while
    < max_slots, otherwise None. Returns (slots, new_next_free).
    """
    kbits = {slot: _binarize(m, 128) for slot, m in known.items()}
    fbits = [_binarize(m, 128) for m in found]
    for m in found:
        for k in known.values():
            if len(m) != len(k):
                raise ValueError("masks differ in size: %d vs %d bytes" % (len(m), len(k)))
    pairs = []
    for i, fb in enumerate(fbits):
        for slot, kb in kbits.items():
            iou = _iou_bits(fb, kb)
            if iou >= threshold:
                pairs.append((-iou, i, slot))
    pairs.sort()

    result = [None] * len(found)
    used_slots = set()
    for _neg, i, slot in pairs:
        if result[i] is None and slot not in used_slots:
            result[i] = slot
            used_slots.add(slot)

    for i in range(len(found)):
        if result[i] is None and next_free < max_slots:
            result[i] = next_free
            next_free += 1
    return result, next_free
