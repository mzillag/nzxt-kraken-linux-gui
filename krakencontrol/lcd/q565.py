"""q565 frame codec used by the Kraken Elite "fast memory" image mode (0x08).

q565 is a QOI-style lossless codec over RGB565 pixels.  Stream layout:

    'q' '5' '6' '5'  width u16 LE  height u16 LE  <opcodes...>  0xFF

Opcodes (first byte):
    0x00..0x3F  INDEX        pixel from the 64-entry hash table
    0x40..0x7F  DIFF         per channel deltas in -2..1 (bias 2)
    0x80..0x9F  LUMA         + 1 byte: green delta -16..15, red/blue relative to green
    0xA0..0xBF  DIFF_INDEXED + 1 byte: diff relative to an indexed pixel
    0xC0..0xFD  RUN          repeat previous pixel 1..62 times
    0xFE        RGB565       + 2 bytes: literal pixel, little endian
    0xFF        END

The production encoder is the Rust `q565` crate exposed through
`native/q565` (loaded with ctypes).  A numpy fallback that only emits RUN and
literal opcodes is provided so the application still works without the native
library, at the cost of larger frames.  A reference decoder is included for
tests and for the on-screen preview path.
"""

from __future__ import annotations

import ctypes
import glob
import os
import struct
from typing import Optional

import numpy as np

MAGIC = b"q565"
OP_INDEX = 0x00
OP_DIFF = 0x40
OP_LUMA = 0x80
OP_DIFF_INDEXED = 0xA0
OP_RUN = 0xC0
OP_RGB565 = 0xFE
OP_END = 0xFF
MAX_RUN = 62

_NATIVE_CANDIDATES = (
    os.path.join(os.path.dirname(__file__), "..", "..", "native", "q565", "target", "release", "libkraken_q565.so"),
    os.path.join(os.path.dirname(__file__), "libkraken_q565.so"),
    "/usr/local/lib/libkraken_q565.so",
    "/usr/lib/libkraken_q565.so",
)


class _NativeEncoder:
    def __init__(self, path: str) -> None:
        self._lib = ctypes.CDLL(path)
        self._lib.kraken_q565_encode_rgb888.restype = ctypes.c_size_t
        self._lib.kraken_q565_encode_rgb888.argtypes = [
            ctypes.c_char_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_size_t,
        ]
        self.path = path

    def encode(self, rgb: bytes, width: int, height: int) -> bytes:
        cap = 9 + width * height * 3 + 64
        out = ctypes.create_string_buffer(cap)
        written = self._lib.kraken_q565_encode_rgb888(rgb, width, height, out, cap)
        if written == 0:
            raise RuntimeError("native q565 encoder failed")
        return out.raw[:written]


def _load_native() -> Optional[_NativeEncoder]:
    env = os.environ.get("KRAKEN_Q565_LIB")
    candidates = ([env] if env else []) + [os.path.abspath(p) for p in _NATIVE_CANDIDATES]
    for path in candidates:
        if path and os.path.exists(path):
            try:
                return _NativeEncoder(path)
            except OSError:
                continue
    return None


_native = _load_native()


def native_available() -> bool:
    return _native is not None


def rgb888_to_rgb565(rgb: np.ndarray) -> np.ndarray:
    """Vectorised RGB888 -> RGB565 using the same rounding as the native encoder."""
    r = (rgb[..., 0].astype(np.uint32) * 249 + 1014) >> 11
    g = (rgb[..., 1].astype(np.uint32) * 253 + 505) >> 10
    b = (rgb[..., 2].astype(np.uint32) * 249 + 1014) >> 11
    return ((r << 11) | (g << 5) | b).astype(np.uint16)


def encode_fallback(rgb: bytes, width: int, height: int) -> bytes:
    """RUN + literal only encoder (numpy).  Larger output but no native code."""
    pixels = np.frombuffer(rgb, dtype=np.uint8).reshape(width * height, 3)
    px565 = rgb888_to_rgb565(pixels)
    out = bytearray(MAGIC + struct.pack("<HH", width, height))
    n = len(px565)
    if n == 0:
        out.append(OP_END)
        return bytes(out)
    # Find run boundaries: index i starts a new run when px[i] != px[i-1]
    change = np.empty(n, dtype=bool)
    change[0] = True
    np.not_equal(px565[1:], px565[:-1], out=change[1:])
    starts = np.flatnonzero(change)
    lengths = np.diff(np.append(starts, n))
    values = px565[starts]
    lo = (values & 0xFF).astype(np.uint8)
    hi = (values >> 8).astype(np.uint8)
    for value_lo, value_hi, length in zip(lo.tolist(), hi.tolist(), lengths.tolist()):
        out.append(OP_RGB565)
        out.append(value_lo)
        out.append(value_hi)
        remaining = length - 1
        while remaining > 0:
            chunk = min(remaining, MAX_RUN)
            out.append(OP_RUN | (chunk - 1))
            remaining -= chunk
    out.append(OP_END)
    return bytes(out)


def encode(rgb: bytes, width: int, height: int) -> bytes:
    """Encode a packed RGB888 buffer into a q565 stream."""
    if len(rgb) != width * height * 3:
        raise ValueError("rgb buffer size does not match dimensions")
    if _native is not None:
        return _native.encode(rgb, width, height)
    return encode_fallback(rgb, width, height)


def decode(data: bytes) -> tuple[int, int, np.ndarray]:
    """Reference decoder mirroring the `q565` crate.  Returns (width, height, rgb565 pixels)."""
    if data[:4] != MAGIC:
        raise ValueError("not a q565 stream")
    width, height = struct.unpack_from("<HH", data, 4)
    total = width * height
    out = np.zeros(total, dtype=np.uint16)
    table = [0] * 64
    prev = 0
    pos = 8
    i = 0
    length = len(data)
    while i < total and pos < length:
        op = data[pos]
        pos += 1
        tag = op >> 6
        if tag == 0b00:  # INDEX: not re-added to the colour table
            prev = table[op]
        elif tag == 0b01:  # DIFF: not added to the colour table
            prev = _apply_diff(prev, ((op >> 4) & 3) - 2, ((op >> 2) & 3) - 2, (op & 3) - 2)
        elif tag == 0b10:
            arg = data[pos]
            pos += 1
            if op & 0x20 == 0:  # LUMA
                dg = (op & 0x1F) - 16
                prev = _apply_diff(prev, (arg >> 4) - 8 + dg, dg, (arg & 0x0F) - 8 + dg)
            else:  # DIFF_INDEXED
                dg = ((op & 0x1C) >> 2) - 4
                dr = (op & 0x03) - 2
                db = (arg >> 6) - 2
                prev = _apply_diff(table[arg & 0x3F], dr, dg, db)
            table[((prev & 0xFF) + (prev >> 8)) & 0x3F] = prev
        else:
            if op == OP_END:
                break
            if op == OP_RGB565:
                prev = data[pos] | (data[pos + 1] << 8)
                pos += 2
                table[((prev & 0xFF) + (prev >> 8)) & 0x3F] = prev
            else:  # RUN
                run = (op & 0x3F) + 1
                out[i : i + run] = prev
                i += run
                continue
        out[i] = prev
        i += 1
    return width, height, out


def _apply_diff(px: int, dr: int, dg: int, db: int) -> int:
    r = ((px >> 11) + dr) & 0x1F
    g = ((px >> 5) + dg) & 0x3F
    b = (px + db) & 0x1F
    return (r << 11) | (g << 5) | b
