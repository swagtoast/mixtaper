"""Draws the app icon (a minimal cassette) without image libraries."""

from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

import numpy as np

BG = np.array([11, 11, 11]) / 255
FG = np.array([255, 122, 26]) / 255


def _rounded_box(x, y, cx, cy, hw, hh, r):
    qx = np.abs(x - cx) - (hw - r)
    qy = np.abs(y - cy) - (hh - r)
    return np.hypot(np.maximum(qx, 0), np.maximum(qy, 0)) + np.minimum(np.maximum(qx, qy), 0) - r


def render(size: int) -> np.ndarray:
    s = size
    ss = 4  # supersampling
    n = s * ss
    y, x = (np.mgrid[0:n, 0:n] + 0.5) / n
    alpha = (_rounded_box(x, y, 0.5, 0.5, 0.47, 0.47, 0.11) <= 0).astype(float)
    stroke = 0.035
    body = np.abs(_rounded_box(x, y, 0.5, 0.52, 0.34, 0.22, 0.05)) <= stroke / 2
    reel_l = np.abs(np.hypot(x - 0.36, y - 0.47) - 0.07) <= stroke / 2
    reel_r = np.abs(np.hypot(x - 0.64, y - 0.47) - 0.07) <= stroke / 2
    hub_l = np.hypot(x - 0.36, y - 0.47) <= 0.022
    hub_r = np.hypot(x - 0.64, y - 0.47) <= 0.022
    tape = (np.abs(y - 0.47) <= stroke / 4) & (x > 0.43) & (x < 0.57)
    window = np.abs(_rounded_box(x, y, 0.5, 0.665, 0.17, 0.045, 0.02)) <= stroke / 3
    ink = (body | reel_l | reel_r | hub_l | hub_r | tape | window).astype(float)
    rgb = BG[None, None, :] * (1 - ink[..., None]) + FG[None, None, :] * ink[..., None]
    rgba = np.concatenate([rgb, alpha[..., None]], axis=-1)
    rgba = rgba.reshape(s, ss, s, ss, 4).mean(axis=(1, 3))
    return (rgba * 255 + 0.5).astype(np.uint8)


def write_png(path: Path, img: np.ndarray) -> None:
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[row].tobytes() for row in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    path.write_bytes(png)


def make_iconset(folder: Path) -> None:
    """The .iconset layout macOS' iconutil turns into an .icns."""
    folder.mkdir(parents=True, exist_ok=True)
    for base in (16, 32, 128, 256, 512):
        write_png(folder / f"icon_{base}x{base}.png", render(base))
        write_png(folder / f"icon_{base}x{base}@2x.png", render(base * 2))


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "icon.png")
    write_png(out, render(512))
