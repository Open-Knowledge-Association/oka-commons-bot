"""Check that a stripped TIFF has one page, identical tags and identical pixels."""
import sys
import numpy as np
import tifffile


def verify(src, dst, decode_source=True):
    """decode_source=False decodes only the copy: its tags and compressed tiles were compared byte for byte
    above, so equal pixels follow, and one decode of a large scan (17,000 px RGB: 860 MB) fits in memory."""
    with tifffile.TiffFile(src) as a, tifffile.TiffFile(dst) as b:
        assert len(b.pages) == 1, "more than one page"
        pa, pb = a.pages[0], b.pages[0]
        for code, tag in pa.tags.items():
            if code in (324, 273):
                continue
            assert code in pb.tags, f"tag {code} missing"
            assert np.array_equal(np.asarray(tag.value, dtype=object), np.asarray(pb.tags[code].value, dtype=object)) if not isinstance(tag.value, bytes) else tag.value == pb.tags[code].value, f"tag {code} differs"
        fa, fb = a.filehandle, b.filehandle
        for (oa, ca), (ob, cb) in zip(zip(pa.dataoffsets, pa.databytecounts), zip(pb.dataoffsets, pb.databytecounts)):
            fa.seek(oa); fb.seek(ob)
            assert ca == cb and fa.read(ca) == fb.read(cb), "compressed tile differs"
        if decode_source:
            assert np.array_equal(pa.asarray(), pb.asarray()), "decoded pixels differ"
        else:
            assert pb.asarray().shape == pa.shape, "decoded copy has another size"
    return True


if __name__ == "__main__":
    print(verify(sys.argv[1], sys.argv[2]))
