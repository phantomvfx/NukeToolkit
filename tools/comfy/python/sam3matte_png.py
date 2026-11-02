"""SAM3Matte: stdlib-only PNG mask reader and writer.

Reads the mask PNGs that ComfyUI returns (8-bit, non-interlaced, colour type
0 grey, 2 RGB or 6 RGBA) with zlib and struct only, so the Nuke side needs no
PIL or numpy. Only channel 0 (red) is decoded: the PNG filters act per channel,
so the other channels are never unfiltered.

    read_mask(path, max_width=0) -> (w, h, bytes)   channel 0, row-major, w*h bytes
    is_blank(path)               -> bool            every pixel of every channel is 0
    to_grid(w, h, data, cols, rows) -> [[float]]    area average 0..1, row 0 = top
    write_gray_png / write_rgb_png                  fixtures and tests

Pure leaf module (imports no sibling). Python 3.11 syntax.
Tested: pytest only (tools/comfy/tests/test_sam3matte_png.py); not run inside Nuke.
"""
import struct
import zlib
from itertools import accumulate

_SIG = b"\x89PNG\r\n\x1a\n"
_CHANNELS = {0: 1, 2: 3, 6: 4}      # supported colour types -> channels per pixel


# ---------------------------------------------------------------- parsing

def _load(path):
    """Return (w, h, channels, raw) where raw is the inflated scanline data."""
    with open(path, "rb") as f:
        blob = f.read()
    if blob[:8] != _SIG:
        raise ValueError("not a PNG file: %s" % path)
    pos, n = 8, len(blob)
    ihdr = None
    idat = []
    while pos + 8 <= n:
        length, tag = struct.unpack(">I4s", blob[pos:pos + 8])
        body = blob[pos + 8:pos + 8 + length]
        if len(body) < length:
            raise ValueError("truncated PNG chunk in %s" % path)
        if tag == b"IHDR":
            ihdr = body
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
        pos += 12 + length
    if ihdr is None or len(ihdr) != 13:
        raise ValueError("missing or bad IHDR in %s" % path)
    w, h, depth, ctype, comp, filt, interlace = struct.unpack(">IIBBBBB", ihdr)
    if depth != 8:
        raise ValueError("only 8-bit PNGs are supported (bit depth %d)" % depth)
    if ctype not in _CHANNELS:
        raise ValueError("unsupported PNG colour type %d" % ctype)
    if interlace != 0:
        raise ValueError("interlaced PNGs are not supported")
    if comp != 0 or filt != 0:
        raise ValueError("unknown PNG compression/filter method")
    if w < 1 or h < 1:
        raise ValueError("empty PNG")
    if not idat:
        raise ValueError("PNG has no image data")
    nch = _CHANNELS[ctype]
    expected = h * (1 + w * nch)
    try:
        raw = zlib.decompressobj().decompress(b"".join(idat), expected)
    except zlib.error as e:
        raise ValueError("corrupt PNG data: %s" % e)
    if len(raw) < expected:
        raise ValueError("truncated PNG image data")
    return w, h, nch, raw


# ------------------------------------------------------------ unfiltering

def _unfilter(ft, cur, prev):
    """Undo PNG filter ft on one channel's bytes (bpp 1). cur, prev: bytes of length w."""
    if ft == 0:
        return cur
    if ft == 1:                                    # Sub
        return bytes([v & 255 for v in accumulate(cur)])
    if ft == 2:                                    # Up
        return bytes([(a + b) & 255 for a, b in zip(cur, prev)])
    out = bytearray(len(cur))
    if ft == 3:                                    # Average
        left = 0
        for i, (x, up) in enumerate(zip(cur, prev)):
            left = out[i] = (x + ((left + up) >> 1)) & 255
        return bytes(out)
    if ft == 4:                                    # Paeth
        left = ul = 0
        for i, (x, up) in enumerate(zip(cur, prev)):
            pa = up - ul                           # |p - left|, p = left + up - ul
            pb = left - ul                         # |p - up|
            pc = pa + pb                           # |p - ul|
            pa = -pa if pa < 0 else pa
            pb = -pb if pb < 0 else pb
            pc = -pc if pc < 0 else pc
            if pa <= pb and pa <= pc:
                pred = left
            elif pb <= pc:
                pred = up
            else:
                pred = ul
            left = out[i] = (x + pred) & 255
            ul = up
        return bytes(out)
    raise ValueError("bad PNG filter type %d" % ft)


def _shrink(acc, w, f, nrows):
    """Reduce a vertical sum row (sum of nrows rows) horizontally by f -> mean bytes."""
    nfull = w // f
    sums = list(map(sum, zip(*[acc[k:nfull * f:f] for k in range(f)])))
    counts = [f * nrows] * nfull
    if w % f:
        sums.append(sum(acc[nfull * f:]))
        counts.append((w - nfull * f) * nrows)
    return bytes([(s + (c >> 1)) // c for s, c in zip(sums, counts)])


def read_mask(path, max_width=0):
    """Channel 0 of an 8-bit non-interlaced PNG (colour type 0, 2 or 6).

    Returns (w, h, data), data row-major, w*h bytes. With max_width > 0 and
    width > max_width the image is shrunk by the integer factor
    ceil(width / max_width) (block average, partial edge blocks average what
    they have); the returned w and h are the shrunk sizes. Anything unsupported
    raises ValueError.
    """
    w, h, nch, raw = _load(path)
    stride = w * nch
    f = 1
    if max_width and max_width > 0 and w > max_width:
        f = -(-w // max_width)
    prev = bytes(w)
    rows = []
    acc = None
    nacc = 0
    for y in range(h):
        off = y * (stride + 1)
        ft = raw[off]
        if ft > 4:
            raise ValueError("bad PNG filter type %d" % ft)
        cur = _unfilter(ft, raw[off + 1:off + 1 + stride:nch], prev)
        prev = cur
        if f == 1:
            rows.append(cur)
            continue
        acc = list(cur) if acc is None else [a + b for a, b in zip(acc, cur)]
        nacc += 1
        if nacc == f or y == h - 1:
            rows.append(_shrink(acc, w, f, nacc))
            acc, nacc = None, 0
    ow = w if f == 1 else -(-w // f)
    return ow, len(rows), b"".join(rows)


def is_blank(path):
    """True when every pixel of every channel is 0 (inflate only, no unfiltering).

    A zero filtered byte decodes to a zero pixel under every filter and a
    non-zero one to a non-zero pixel, so the filter byte of each row is skipped
    and the rest checked as is.
    """
    w, h, nch, raw = _load(path)
    stride = w * nch
    for y in range(h):
        off = y * (stride + 1)
        if raw[off + 1:off + 1 + stride].count(0) != stride:
            return False
    return True


# ------------------------------------------------------------------- grid

def to_grid(w, h, data, cols, rows):
    """Area-average data (w*h bytes) onto a cols x rows grid of 0..1 floats, row 0 = top."""
    if len(data) != w * h:
        raise ValueError("data length %d != %d*%d" % (len(data), w, h))
    if cols < 1 or rows < 1:
        raise ValueError("grid must be at least 1x1")
    xs = [(c * w // cols, max(c * w // cols + 1, (c + 1) * w // cols)) for c in range(cols)]
    grid = []
    for r in range(rows):
        y0 = r * h // rows
        y1 = max(y0 + 1, (r + 1) * h // rows)
        sums = [0] * cols
        for y in range(y0, y1):
            base = y * w
            for c, (x0, x1) in enumerate(xs):
                sums[c] += sum(data[base + x0:base + x1])
        grid.append([sums[c] / (255.0 * (y1 - y0) * (xs[c][1] - xs[c][0])) for c in range(cols)])
    return grid


# ----------------------------------------------------------------- writing

def _chunk(tag, body):
    return (struct.pack(">I", len(body)) + tag + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))


def _write_png(path, w, h, ctype, scanlines):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, ctype, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(_SIG + _chunk(b"IHDR", ihdr)
                + _chunk(b"IDAT", zlib.compress(scanlines)) + _chunk(b"IEND", b""))


def write_gray_png(path, w, h, data):
    """8-bit grey PNG (colour type 0), filter 0 on every row."""
    if len(data) != w * h:
        raise ValueError("data length %d != %d*%d" % (len(data), w, h))
    rows = b"".join(b"\x00" + bytes(data[y * w:(y + 1) * w]) for y in range(h))
    _write_png(path, w, h, 0, rows)


def _filter_row(ft, cur, prev, bpp):
    out = bytearray(len(cur))
    for i in range(len(cur)):
        a = cur[i - bpp] if i >= bpp else 0
        b = prev[i]
        c = prev[i - bpp] if i >= bpp else 0
        if ft == 0:
            pred = 0
        elif ft == 1:
            pred = a
        elif ft == 2:
            pred = b
        elif ft == 3:
            pred = (a + b) >> 1
        else:
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
        out[i] = (cur[i] - pred) & 255
    return bytes(out)


def write_rgb_png(path, w, h, data, filter_type=0):
    """8-bit RGB PNG (colour type 2); data = w*h grey bytes replicated into R, G, B.

    filter_type 0..4 is applied to every row (for testing the unfilterer).
    """
    if len(data) != w * h:
        raise ValueError("data length %d != %d*%d" % (len(data), w, h))
    if filter_type not in (0, 1, 2, 3, 4):
        raise ValueError("filter_type must be 0..4")
    prev = bytes(w * 3)
    out = []
    for y in range(h):
        cur = bytes(v for g in data[y * w:(y + 1) * w] for v in (g, g, g))
        out.append(bytes([filter_type]) + _filter_row(filter_type, cur, prev, 3))
        prev = cur
    _write_png(path, w, h, 2, b"".join(out))
