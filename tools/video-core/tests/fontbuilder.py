"""Build tiny synthetic sfnt blobs that contain only a cmap (enough for coverage tests).
They are NOT usable fonts for rendering; they exist to exercise the cmap reader."""

import struct


def _fmt4(ranges):
    segs = sorted(ranges) + [(0xFFFF, 0xFFFF)]
    n = len(segs)
    ends = b"".join(struct.pack(">H", e) for _s, e in segs)
    starts = b"".join(struct.pack(">H", s) for s, _e in segs)
    deltas = b"".join(struct.pack(">H", (1 - s) & 0xFFFF) for s, _e in segs)  # gid = cp + delta = 1 at range start
    offs = b"\x00\x00" * n
    body = struct.pack(">HHHH", n * 2, 0, 0, 0) + ends + b"\x00\x00" + starts + deltas + offs
    return struct.pack(">HHH", 4, 6 + len(body), 0) + body


def _fmt12(groups):
    body = b"".join(struct.pack(">III", s, e, g) for s, e, g in groups)
    return struct.pack(">HHIII", 12, 0, 16 + len(body), 0, len(groups)) + body


def make_font(fmt4=(), fmt12=(), platform=3, tag=b"\x00\x01\x00\x00"):
    subs = []
    if fmt4:
        subs.append((platform, 1, _fmt4(fmt4)))
    if fmt12:
        subs.append((3, 10, _fmt12(fmt12)))
    header_len = 4 + 8 * len(subs)
    offset, records, blobs = header_len, b"", b""
    for p, e, blob in subs:
        records += struct.pack(">HHI", p, e, offset)
        blobs += blob
        offset += len(blob)
    cmap = struct.pack(">HH", 0, len(subs)) + records + blobs
    table_dir = struct.pack(">4sIII", b"cmap", 0, 12 + 16, len(cmap))
    return tag + struct.pack(">HHHH", 1, 16, 0, 0) + table_dir + cmap
