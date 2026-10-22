import struct

import pytest

from psd_backup.psd_check import PSDError, validate


def make_psd(path, width=4, height=3, channels=3, fill=0, version=1):
    data = struct.pack(">4sH6sHIIHH", b"8BPS", version, b"\0" * 6, channels, height, width, 8, 3)
    data += struct.pack(">I", 0)  # color mode data
    data += struct.pack(">I", 0)  # image resources
    data += struct.pack(">I" if version == 1 else ">Q", 0)  # layer and mask info
    data += struct.pack(">H", 0)  # raw image data
    data += bytes([fill]) * (channels * width * height)
    path.write_bytes(data)
    return path


def test_valid_psd_and_psb(tmp_path):
    validate(make_psd(tmp_path / "a.psd"))
    validate(make_psd(tmp_path / "a.psb", version=2))


def test_wrong_signature(tmp_path):
    p = make_psd(tmp_path / "a.psd")
    p.write_bytes(b"XXXX" + p.read_bytes()[4:])
    with pytest.raises(PSDError, match="signature"):
        validate(p)


def test_truncated(tmp_path):
    p = make_psd(tmp_path / "a.psd")
    p.write_bytes(p.read_bytes()[:30])
    with pytest.raises(PSDError):
        validate(p)


def test_section_past_end(tmp_path):
    p = make_psd(tmp_path / "a.psd")
    raw = bytearray(p.read_bytes())
    raw[30:34] = struct.pack(">I", 10_000)  # image resources length
    p.write_bytes(bytes(raw))
    with pytest.raises(PSDError, match="past end"):
        validate(p)
