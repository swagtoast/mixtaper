"""Reading technical info about the input file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import ffmpeg
from .model import fmt_time

AUDIO_EXTS = {".mp3", ".flac", ".aiff", ".aif", ".aifc", ".m4a", ".mp4", ".wav"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif", ".tif", ".tiff", ".heic"}
OUTPUT_FORMATS = ("mp3", "flac", "aiff", "m4a")


@dataclass
class MediaInfo:
    path: str
    container: str      # mp3 | flac | aiff | m4a | wav
    codec: str          # mp3 | flac | pcm | aac | alac | ...
    sample_rate: int
    channels: int
    bits: int           # bit depth for lossless sources, 0 otherwise
    bitrate: int        # bits per second
    duration: float
    lossless: bool

    def describe(self) -> str:
        parts = [self.container.upper()]
        if self.codec not in (self.container, "pcm"):
            parts[0] += f" ({self.codec.upper()})"
        if self.lossless:
            parts.append(f"{self.bits or 16}-bit")
        elif self.bitrate:
            parts.append(f"{round(self.bitrate / 1000)} kbps")
        parts.append(f"{self.sample_rate / 1000:g} kHz")
        parts.append({1: "mono", 2: "stereo"}.get(self.channels, f"{self.channels} ch"))
        parts.append(fmt_time(self.duration))
        return " · ".join(parts)


def container_of(path: str) -> str:
    ext = Path(path).suffix.lower()
    return {".aif": "aiff", ".aifc": "aiff", ".mp4": "m4a"}.get(ext, ext.lstrip("."))


def probe(path: str) -> MediaInfo:
    import mutagen

    cont = container_of(path)
    try:
        f = mutagen.File(path)
    except Exception:
        f = None
    if f is not None and getattr(f, "info", None) is not None:
        info = f.info
        kind = type(f).__name__
        sr = int(getattr(info, "sample_rate", 0) or 0)
        ch = int(getattr(info, "channels", 2) or 2)
        bits = int(getattr(info, "bits_per_sample", 0) or 0)
        br = int(getattr(info, "bitrate", 0) or 0)
        dur = float(getattr(info, "length", 0.0) or 0.0)
        if kind in ("MP3", "EasyMP3"):
            return MediaInfo(path, "mp3", "mp3", sr, ch, 0, br, dur, False)
        if kind == "FLAC":
            return MediaInfo(path, "flac", "flac", sr, ch, bits or 16, br, dur, True)
        if kind == "AIFF":
            return MediaInfo(path, "aiff", "pcm", sr, ch, bits or 16, br, dur, True)
        if kind in ("WAVE",):
            return MediaInfo(path, "wav", "pcm", sr, ch, bits or 16, br, dur, True)
        if kind in ("MP4", "EasyMP4"):
            codec = (getattr(info, "codec", "") or "").lower()
            if codec.startswith("alac"):
                return MediaInfo(path, "m4a", "alac", sr, ch, bits or 16, br, dur, True)
            return MediaInfo(path, "m4a", "aac", sr, ch, 0, br, dur, False)
    return _probe_ffmpeg(path, cont)


def _probe_ffmpeg(path: str, cont: str) -> MediaInfo:
    err = ffmpeg.run(["-i", path], check=False).stderr.decode("utf-8", "replace")
    dur = 0.0
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", err)
    if m:
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    a = re.search(r"Audio:\s*([\w]+)[^,]*,\s*(\d+)\s*Hz,\s*([^,]+)", err)
    if not a:
        raise ValueError("no audio stream")
    codec, sr, layout = a.group(1).lower(), int(a.group(2)), a.group(3)
    ch = 1 if "mono" in layout else 2
    br = 0
    b = re.search(r"(\d+)\s*kb/s", err[a.start():])
    if b:
        br = int(b.group(1)) * 1000
    lossless = codec.startswith(("pcm", "flac", "alac", "wavpack", "ape"))
    bits = 24 if "24" in codec or "s32" in err[a.start(): a.start() + 120] else (16 if lossless else 0)
    return MediaInfo(path, cont, "pcm" if codec.startswith("pcm") else codec, sr, ch, bits, br, dur, lossless)
