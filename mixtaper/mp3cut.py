"""Sample-exact, gapless MP3 splitting without re-encoding.

Every piece is a byte-for-byte copy of the original MPEG frames. To begin exactly on the
wanted sample, a few frames before it are kept as decoder warm-up (they also hold the
bit-reservoir data the first real frame needs), and a LAME/Xing "Info" header tells
gapless-aware players (Music/iTunes, foobar2000, ffmpeg, ...) how many samples to drop at
the start and at the end. Decoded back to back, the pieces reproduce the original mix.

Sample positions are expressed like ffmpeg's decoder output: sample 0 is the first audible
sample after the original file's own encoder delay has been removed.
"""

from __future__ import annotations

import mmap
from dataclasses import dataclass

DECODER_DELAY = 529          # LAME convention (528 + 1) for the synthesis filterbank delay
_LAME_IDS = (b"LAME", b"Lavf", b"Lavc")

_BITRATES = {
    1: [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],   # MPEG-1 Layer III
    2: [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],       # MPEG-2/2.5 Layer III
}
_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


class Mp3CutError(RuntimeError):
    pass


@dataclass(frozen=True)
class Header:
    raw: int
    version: int        # 3 = MPEG-1, 2 = MPEG-2, 0 = MPEG-2.5
    br_index: int
    sr_index: int
    padding: int
    mono: bool
    crc: bool

    @property
    def mpeg1(self) -> bool:
        return self.version == 3

    @property
    def bitrate(self) -> int:
        return _BITRATES[1 if self.mpeg1 else 2][self.br_index]

    @property
    def sample_rate(self) -> int:
        return _RATES[self.version][self.sr_index]

    @property
    def spf(self) -> int:
        return 1152 if self.mpeg1 else 576

    @property
    def size(self) -> int:
        return (144 if self.mpeg1 else 72) * self.bitrate * 1000 // self.sample_rate + self.padding

    @property
    def side_info(self) -> int:
        if self.mpeg1:
            return 17 if self.mono else 32
        return 9 if self.mono else 17


def parse_header(b: bytes) -> Header | None:
    if len(b) < 4:
        return None
    h = int.from_bytes(b[:4], "big")
    if (h >> 21) & 0x7FF != 0x7FF:
        return None
    version = (h >> 19) & 3
    layer = (h >> 17) & 3
    br = (h >> 12) & 0xF
    sr = (h >> 10) & 3
    if version == 1 or layer != 1 or br in (0, 15) or sr == 3:
        return None
    return Header(h, version, br, sr, (h >> 9) & 1, ((h >> 6) & 3) == 3, not ((h >> 16) & 1))


def _syncsafe(b: bytes) -> int:
    return (b[0] << 21) | (b[1] << 14) | (b[2] << 7) | b[3]


@dataclass
class Stream:
    mm: mmap.mmap
    offsets: list[int]
    sizes: list[int]
    head: Header                # first audio frame
    enc_delay: int | None       # from the original LAME tag
    enc_padding: int | None
    lame: bytes | None          # original 36-byte LAME extension, reused for the pieces
    cbr: bool

    @property
    def spf(self) -> int:
        return self.head.spf

    @property
    def sample_rate(self) -> int:
        return self.head.sample_rate

    @property
    def start_skip(self) -> int:
        """Decoded samples ffmpeg drops at the start (stream -> output coordinates)."""
        return self.enc_delay + DECODER_DELAY if self.enc_delay is not None else 0

    @property
    def stream_end(self) -> int:
        """End of the audible output, in stream (decoded-frame) coordinates."""
        total = len(self.offsets) * self.spf
        if self.enc_padding is not None:
            return min(total, total - self.enc_padding + DECODER_DELAY)
        return total

    @property
    def output_samples(self) -> int:
        return self.stream_end - self.start_skip

    def close(self) -> None:
        self.mm.close()


def _next_sync(mm: mmap.mmap, pos: int, end: int) -> int | None:
    while True:
        pos = mm.find(b"\xff", pos, end - 3)
        if pos < 0:
            return None
        h = parse_header(mm[pos: pos + 4])
        if h is not None:
            nxt = pos + h.size
            if nxt >= end:
                return pos
            h2 = parse_header(mm[nxt: nxt + 4])
            if h2 is not None and h2.version == h.version and h2.sr_index == h.sr_index:
                return pos
        pos += 1


def scan(path: str) -> Stream:
    f = open(path, "rb")
    try:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    finally:
        f.close()
    n = len(mm)
    pos = 0
    while mm[pos: pos + 3] == b"ID3" and pos + 10 <= n:
        pos += 10 + _syncsafe(mm[pos + 6: pos + 10]) + (10 if mm[pos + 5] & 0x10 else 0)
    end = n
    if end >= 128 and mm[end - 128: end - 125] == b"TAG":
        end -= 128
    if end >= 32 and mm[end - 32: end - 24] == b"APETAGEX":
        size = int.from_bytes(mm[end - 20: end - 16], "little")
        flags = int.from_bytes(mm[end - 12: end - 8], "little")
        end -= size + (32 if flags & 0x80000000 else 0)
    start = _next_sync(mm, pos, end)
    if start is None:
        mm.close()
        raise Mp3CutError("no MPEG audio frames found")
    head = parse_header(mm[start: start + 4])
    assert head is not None

    enc_delay = enc_padding = None
    lame = None
    info_frame = False
    x = start + 4 + (2 if head.crc else 0) + head.side_info
    tag = mm[x: x + 4]
    if tag in (b"Xing", b"Info"):
        info_frame = True
        flags = int.from_bytes(mm[x + 4: x + 8], "big")
        p = x + 8 + (4 if flags & 1 else 0) + (4 if flags & 2 else 0) + (100 if flags & 4 else 0) + (4 if flags & 8 else 0)
        ext = mm[p: p + 36]
        if ext[:4] in _LAME_IDS:
            v = int.from_bytes(ext[21:24], "big")
            enc_delay, enc_padding, lame = v >> 12, v & 0xFFF, bytes(ext)
    elif mm[start + 36: start + 40] == b"VBRI":
        info_frame = True

    offsets: list[int] = []
    sizes: list[int] = []
    rates: set[int] = set()
    pos = start + head.size if info_frame else start
    while pos + 4 <= end:
        h = parse_header(mm[pos: pos + 4])
        if h is None or h.version != head.version or h.sr_index != head.sr_index:
            nxt = _next_sync(mm, pos + 1, end)
            if nxt is None:
                break
            pos = nxt
            continue
        if pos + h.size > end:
            break
        offsets.append(pos)
        sizes.append(h.size)
        rates.add(h.br_index)
        pos += h.size
    if not offsets:
        mm.close()
        raise Mp3CutError("no audio frames")
    first = parse_header(mm[offsets[0]: offsets[0] + 4])
    assert first is not None
    return Stream(mm, offsets, sizes, first, enc_delay, enc_padding, lame, len(rates) == 1)


# -- LAME tag ------------------------------------------------------------------------

def _crc16_table() -> list[int]:
    table = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
        table.append(c)
    return table


_CRC = _crc16_table()


def crc16(data: bytes, crc: int = 0) -> int:
    """CRC-16/ARC, as used for the LAME tag checksum."""
    for b in data:
        crc = (crc >> 8) ^ _CRC[(crc ^ b) & 0xFF]
    return crc


def _info_frame(s: Stream, nframes: int, audio_bytes: int, toc_pos: list[int], delay: int, padding: int) -> bytes:
    h = s.head
    need = 4 + h.side_info + 120 + 36
    table = _BITRATES[1 if h.mpeg1 else 2]
    order = ([h.br_index] if s.cbr else []) + list(range(1, 15))
    for bri in order:
        size = (144 if h.mpeg1 else 72) * table[bri] * 1000 // h.sample_rate
        if size >= need:
            break
    else:  # pragma: no cover - every MPEG layout fits at 320/160 kbps
        raise Mp3CutError("cannot fit the info tag")
    raw = (0x7FF << 21) | (h.version << 19) | (1 << 17) | (1 << 16) | (bri << 12) | (h.sr_index << 10) | (h.raw & 0x1FF)
    buf = bytearray(size)
    buf[0:4] = raw.to_bytes(4, "big")
    x = 4 + h.side_info
    total = size + audio_bytes
    buf[x: x + 4] = b"Info" if s.cbr else b"Xing"
    buf[x + 4: x + 8] = (0x0F).to_bytes(4, "big")
    buf[x + 8: x + 12] = nframes.to_bytes(4, "big")
    buf[x + 12: x + 16] = total.to_bytes(4, "big")
    for i in range(100):
        buf[x + 16 + i] = min(255, (size + toc_pos[i]) * 256 // total)
    # quality (x+116..x+120) left at 0
    L = x + 120
    src = s.lame or b""
    buf[L: L + 9] = src[:9] if src[:4] == b"LAME" else b"LAME3.100"
    buf[L + 9] = src[9] if src else (0x01 if s.cbr else 0x03)
    buf[L + 10] = src[10] if src else 0
    # L+11..L+18 replay gain: belongs to the whole mix, so left empty
    buf[L + 19] = src[19] if src else 0
    buf[L + 20] = src[20] if src else min(255, h.bitrate)
    buf[L + 21: L + 24] = ((delay << 12) | padding).to_bytes(3, "big")
    buf[L + 24] = src[24] if src else 0
    buf[L + 26: L + 28] = src[26:28] if src else b"\x00\x00"
    buf[L + 28: L + 32] = total.to_bytes(4, "big")
    # L+32..L+34 music CRC: optional, left at 0
    buf[L + 34: L + 36] = crc16(bytes(buf[: L + 34])).to_bytes(2, "big")
    return bytes(buf)


def write_piece(s: Stream, s0: int, s1: int | None, dest: str) -> None:
    """Write output samples [s0, s1) (s1=None: to the end) to dest."""
    spf = s.spf
    n = len(s.offsets)
    p0 = s0 + s.start_skip
    p1 = s.stream_end if s1 is None else min(s.stream_end, s1 + s.start_skip)
    if p1 <= p0:
        raise Mp3CutError("empty piece")
    avg = sum(s.sizes[: min(n, 200)]) / min(n, 200)
    warmup = (2 if avg > 520 else 3) if s.head.mpeg1 else 4
    fa = max(0, p0 // spf - warmup)
    fb = min(n - 1, (p1 - 1) // spf)
    delay = max(0, p0 - fa * spf - DECODER_DELAY)
    padding = max(0, (fb + 1) * spf + DECODER_DELAY - p1)
    if delay > 4095 or padding > 4095:
        raise Mp3CutError("gapless delay out of range")
    nframes = fb - fa + 1
    base = s.offsets[fa]
    audio_bytes = s.offsets[fb] + s.sizes[fb] - base
    toc = [s.offsets[fa + min(nframes - 1, i * nframes // 100)] - base for i in range(100)]
    with open(dest, "wb") as out:
        out.write(_info_frame(s, nframes, audio_bytes, toc, delay, padding))
        out.write(s.mm[base: base + audio_bytes])


def split(src: str, pieces: list[tuple[int, int | None]], dests: list[str], progress=None) -> None:
    s = scan(src)
    try:
        for i, ((a, b), d) in enumerate(zip(pieces, dests)):
            write_piece(s, a, b, d)
            if progress:
                progress((i + 1) / len(pieces))
    finally:
        s.close()
