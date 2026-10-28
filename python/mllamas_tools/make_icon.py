# Generates icons/rv.png (32x32, "RV" on a dark tile). Pure stdlib; run with any python3.
import struct, zlib
glyph = {
 "R": ["1110","1001","1001","1110","1010","1001","1001"],
 "V": ["1001","1001","1001","1001","1001","0110","0110"],
}
W = H = 32
bg, fg = (38, 38, 38, 255), (255, 170, 0, 255)
px = [[bg] * W for _ in range(H)]
s = 3  # pixel scale
x0, y0 = (W - (4 * s * 2 + s)) // 2, (H - 7 * s) // 2
for gi, ch in enumerate("RV"):
    for r, row in enumerate(glyph[ch]):
        for c, v in enumerate(row):
            if v == "1":
                for dy in range(s):
                    for dx in range(s):
                        px[y0 + r * s + dy][x0 + gi * (4 * s + s) + c * s + dx] = fg
raw = b"".join(b"\x00" + b"".join(bytes(p) for p in row) for row in px)
def chunk(t, d):
    c = struct.pack(">I", len(d)) + t + d
    return c + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0)) \
      + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
open("icons/rv.png", "wb").write(png)
