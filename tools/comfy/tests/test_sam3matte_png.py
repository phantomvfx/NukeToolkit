import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

import struct
import time
import zlib

import pytest

import sam3matte_png as p


def _chunk(tag, body):
    return (struct.pack(">I", len(body)) + tag + body
            + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF))


def _raw_png(w, h, bit_depth, colour_type, interlace, raw):
    ihdr = struct.pack(">IIBBBBB", w, h, bit_depth, colour_type, 0, 0, interlace)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr)
            + _chunk(b"IDAT", zlib.compress(raw)) + _chunk(b"IEND", b""))


def _ramp(w, h):
    return bytes((x * 11 + y * 29) % 256 for y in range(h) for x in range(w))


def test_roundtrip_gray(tmp_path):
    path = str(tmp_path / "g.png")
    data = bytes(range(0, 35 * 7, 7))
    p.write_gray_png(path, 7, 5, data)
    assert p.read_mask(path) == (7, 5, data)


def test_all_filters_rgb(tmp_path):
    import random
    rnd = random.Random(1234)
    data = bytes(rnd.randrange(256) for _ in range(31 * 17))
    for ft in range(5):
        path = str(tmp_path / ("f%d.png" % ft))
        p.write_rgb_png(path, 31, 17, data, filter_type=ft)
        assert p.read_mask(path) == (31, 17, data), "filter %d" % ft


def test_filter_bytes_written(tmp_path):
    # the writer must really emit the requested filter type on each row
    data = _ramp(9, 6)
    for ft in range(5):
        path = str(tmp_path / ("w%d.png" % ft))
        p.write_rgb_png(path, 9, 6, data, filter_type=ft)
        blob = open(path, "rb").read()
        pos, idat = 8, b""
        while pos < len(blob):
            n, tag = struct.unpack(">I4s", blob[pos:pos + 8])
            if tag == b"IDAT":
                idat += blob[pos + 8:pos + 8 + n]
            pos += 12 + n
        raw = zlib.decompress(idat)
        stride = 1 + 9 * 3
        assert len(raw) == 6 * stride
        assert {raw[r * stride] for r in range(6)} == {ft}


def test_colour_type_6_rgba(tmp_path):
    w, h = 4, 3
    rows = b""
    expect = bytearray()
    for y in range(h):
        row = bytearray()
        for x in range(w):
            r = (x * 50 + y * 20) % 256
            row += bytes((r, 200, 100, 255))
            expect.append(r)
        rows += b"\x00" + bytes(row)
    path = str(tmp_path / "rgba.png")
    with open(path, "wb") as f:
        f.write(_raw_png(w, h, 8, 6, 0, rows))
    assert p.read_mask(path) == (w, h, bytes(expect))


def test_rejects_16bit_and_interlaced(tmp_path):
    bad16 = str(tmp_path / "16.png")
    with open(bad16, "wb") as f:
        f.write(_raw_png(2, 2, 16, 0, 0, (b"\x00" + b"\x00" * 4) * 2))
    with pytest.raises(ValueError):
        p.read_mask(bad16)
    with pytest.raises(ValueError):
        p.is_blank(bad16)
    interlaced = str(tmp_path / "adam7.png")
    with open(interlaced, "wb") as f:
        f.write(_raw_png(2, 2, 8, 0, 1, (b"\x00" + b"\x00" * 2) * 2))
    with pytest.raises(ValueError):
        p.read_mask(interlaced)
    notpng = str(tmp_path / "x.png")
    with open(notpng, "wb") as f:
        f.write(b"not a png at all")
    with pytest.raises(ValueError):
        p.read_mask(notpng)


def test_read_mask_downsample(tmp_path):
    w, h = 16, 8
    data = _ramp(w, h)
    path = str(tmp_path / "d.png")
    p.write_rgb_png(path, w, h, data, filter_type=4)
    ow, oh, out = p.read_mask(path, max_width=4)
    assert (ow, oh) == (4, 2) and len(out) == 8
    f = 4
    for by in range(oh):
        for bx in range(ow):
            vals = [data[(by * f + dy) * w + bx * f + dx] for dy in range(f) for dx in range(f)]
            assert out[by * ow + bx] == (sum(vals) + len(vals) // 2) // len(vals)
    # max_width at or above the width: untouched
    assert p.read_mask(path, max_width=16) == (w, h, data)
    assert p.read_mask(path, max_width=100) == (w, h, data)


def test_read_mask_downsample_uneven(tmp_path):
    w, h = 10, 7
    data = bytes([200]) * (w * h)
    path = str(tmp_path / "u.png")
    p.write_gray_png(path, w, h, data)
    ow, oh, out = p.read_mask(path, max_width=4)   # factor 3 -> 4 x 3, edge blocks partial
    assert (ow, oh) == (4, 3)
    assert out == bytes([200]) * 12


def test_is_blank(tmp_path):
    w, h = 12, 9
    black = str(tmp_path / "black.png")
    p.write_rgb_png(black, w, h, bytes(w * h), filter_type=4)
    assert p.is_blank(black) is True
    one = bytearray(w * h)
    one[5 * w + 3] = 1
    lit = str(tmp_path / "lit.png")
    p.write_rgb_png(lit, w, h, bytes(one), filter_type=4)
    assert p.is_blank(lit) is False
    # a pixel set only in green/blue (channel 0 black) is not blank
    rows = b""
    for y in range(h):
        row = bytearray(w * 3)
        if y == 2:
            row[3 * 4 + 2] = 9
        rows += b"\x00" + bytes(row)
    gb = str(tmp_path / "gb.png")
    with open(gb, "wb") as f:
        f.write(_raw_png(w, h, 8, 2, 0, rows))
    assert p.is_blank(gb) is False


def test_to_grid():
    data = bytes([0, 255, 0, 255, 0, 255, 0, 255])
    assert p.to_grid(4, 2, data, 2, 1) == [[0.5, 0.5]]
    grid = p.to_grid(4, 2, bytes([255] * 4 + [0] * 4), 1, 2)
    assert grid == [[1.0], [0.0]]          # row 0 is the top
    one = p.to_grid(3, 3, bytes([51] * 9), 1, 1)
    assert one == [[pytest.approx(0.2)]]


SPIKE = r"L:\ComfyUI\output\sam3_spike\run1"
needs_spike = pytest.mark.skipif(not os.path.isdir(SPIKE), reason="spike folder missing")


@needs_spike
def test_spike_masks():
    blank = os.path.join(SPIKE, "obj0_00001_.png")
    lit = os.path.join(SPIKE, "obj0_00100_.png")
    assert p.is_blank(blank) is True
    assert p.is_blank(lit) is False
    w, h, data = p.read_mask(lit, 512)
    assert (w, h) == (512, 270)
    assert len(data) == 138240
    assert max(data) > 0


@needs_spike
def test_decode_speed():
    lit = os.path.join(SPIKE, "obj0_00100_.png")
    t0 = time.perf_counter()
    w, h, data = p.read_mask(lit, 1024)
    dt = time.perf_counter() - t0
    print("read_mask(2048x1080 -> %dx%d) took %.3f s" % (w, h, dt))
    assert (w, h) == (1024, 540)
    assert dt < 1.5
