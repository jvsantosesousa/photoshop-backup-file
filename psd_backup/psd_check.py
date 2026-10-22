"""Lightweight structural validation of PSD/PSB files (no third-party deps).

Walks the top-level file sections and checks that every declared length fits
inside the file. This catches truncated or corrupted saves without decoding
any image data.

Layout (all big-endian):
  header (26 bytes) | color mode data | image resources | layer & mask info | image data
"""
from __future__ import annotations

import os
import struct
from pathlib import Path

VALID_DEPTHS = {1, 8, 16, 32}
VALID_COLOR_MODES = {0, 1, 2, 3, 4, 7, 8, 9}
VALID_COMPRESSION = {0, 1, 2, 3}


class PSDError(Exception):
    pass


def validate(path: Path | str) -> None:
    """Raise PSDError if the file is not a structurally sound PSD/PSB."""
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        header = f.read(26)
        if len(header) < 26:
            raise PSDError("file too small for a PSD header")
        sig, version, _reserved, channels, height, width, depth, mode = struct.unpack(
            ">4sH6sHIIHH", header
        )
        if sig != b"8BPS":
            raise PSDError("missing 8BPS signature")
        if version not in (1, 2):
            raise PSDError(f"unknown version {version}")
        max_dim = 30000 if version == 1 else 300000
        if not 1 <= channels <= 56:
            raise PSDError(f"invalid channel count {channels}")
        if not (1 <= height <= max_dim and 1 <= width <= max_dim):
            raise PSDError(f"invalid dimensions {width}x{height}")
        if depth not in VALID_DEPTHS:
            raise PSDError(f"invalid bit depth {depth}")
        if mode not in VALID_COLOR_MODES:
            raise PSDError(f"invalid color mode {mode}")

        pos = 26
        for name, len_size in (
            ("color mode data", 4),
            ("image resources", 4),
            ("layer and mask info", 4 if version == 1 else 8),
        ):
            raw = f.read(len_size)
            if len(raw) < len_size:
                raise PSDError(f"truncated before {name} length")
            length = struct.unpack(">I" if len_size == 4 else ">Q", raw)[0]
            pos += len_size + length
            if pos > size:
                raise PSDError(f"{name} section runs past end of file")
            f.seek(pos)

        raw = f.read(2)
        if len(raw) < 2:
            raise PSDError("truncated before image data")
        compression = struct.unpack(">H", raw)[0]
        if compression not in VALID_COMPRESSION:
            raise PSDError(f"invalid image data compression {compression}")
