"""Write a copy of a classic (non-Big) TIFF that keeps only the first IFD.

Every tag of IFD0 is copied byte for byte and the compressed tile/strip data
is copied verbatim, so the full-resolution image is not re-encoded. Only the
offset tags are rewritten to point at the new positions.
"""
import struct
import sys

TYPE_SIZE = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 6: 1, 7: 1, 8: 2, 9: 4, 10: 8, 11: 4, 12: 8, 16: 8}
OFFSET_TAGS = {324: 325, 273: 279}  # TileOffsets->TileByteCounts, StripOffsets->StripByteCounts


def strip_overviews(src_path, dst_path):
    data = open(src_path, "rb").read()
    bo = {b"II": "<", b"MM": ">"}[data[:2]]
    if struct.unpack(bo + "H", data[2:4])[0] != 42:
        raise ValueError("not a classic TIFF (BigTIFF not supported)")
    ifd = struct.unpack(bo + "I", data[4:8])[0]
    n = struct.unpack(bo + "H", data[ifd:ifd + 2])[0]
    entries = []
    for i in range(n):
        e = ifd + 2 + 12 * i
        tag, typ, count = struct.unpack(bo + "HHI", data[e:e + 8])
        size = TYPE_SIZE[typ] * count
        if size <= 4:
            raw = data[e + 8:e + 8 + size]
        else:
            off = struct.unpack(bo + "I", data[e + 8:e + 12])[0]
            raw = data[off:off + size]
        entries.append([tag, typ, count, raw])
    tags = {t[0]: t for t in entries}

    def values(tag):
        t = tags[tag]
        fmt = {3: "H", 4: "I"}[t[1]]
        return list(struct.unpack(bo + fmt * t[2], t[3]))

    off_tag = next(t for t in OFFSET_TAGS if t in tags)
    offsets, counts = values(off_tag), values(OFFSET_TAGS[off_tag])

    out = bytearray(b"II" if bo == "<" else b"MM")
    out += struct.pack(bo + "HI", 42, 0)  # IFD offset patched below
    new_offsets = []
    for o, c in zip(offsets, counts):
        new_offsets.append(len(out))
        out += data[o:o + c]
        if len(out) % 2:
            out += b"\0"
    t = tags[off_tag]
    t[1] = 4
    t[3] = struct.pack(bo + "I" * len(new_offsets), *new_offsets)

    # out-of-line values, then the IFD itself
    entries.sort(key=lambda t: t[0])
    value_pos = {}
    for t in entries:
        if len(t[3]) > 4:
            value_pos[t[0]] = len(out)
            out += t[3]
            if len(out) % 2:
                out += b"\0"
    ifd_pos = len(out)
    out += struct.pack(bo + "H", len(entries))
    for tag, typ, count, raw in entries:
        out += struct.pack(bo + "HHI", tag, typ, count)
        if len(raw) > 4:
            out += struct.pack(bo + "I", value_pos[tag])
        else:
            out += raw.ljust(4, b"\0")
    out += struct.pack(bo + "I", 0)  # no next IFD
    out[4:8] = struct.pack(bo + "I", ifd_pos)
    open(dst_path, "wb").write(out)


if __name__ == "__main__":
    strip_overviews(sys.argv[1], sys.argv[2])
