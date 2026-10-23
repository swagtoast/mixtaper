"""Audio recognition back-ends."""

from __future__ import annotations

import io
import wave
from dataclasses import asdict, dataclass, fields

import numpy as np

SR = 16000          # all back-ends receive 16 kHz mono s16 windows
WINDOW = 12.0       # seconds of audio per recognition query


class RecognitionUnavailable(RuntimeError):
    """The service is unreachable or keeps rate-limiting us."""


@dataclass
class Match:
    key: str
    title: str
    artist: str
    offset: float               # position inside the track at the start of the query window (s)
    skew: float = 0.0           # playback rate - 1 (the DJ's pitch fader)
    album: str = ""
    label: str = ""
    year: str = ""
    genre: str = ""
    isrc: str = ""
    apple_id: str = ""
    duration: float | None = None   # full length of the original track (s), when known
    source: str = "shazam"

    @property
    def rate(self) -> float:
        return min(1.25, max(0.8, 1.0 + self.skew))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Match":
        names = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in names})


def wav_bytes(pcm: np.ndarray) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(np.ascontiguousarray(pcm, dtype="<i2").tobytes())
    return buf.getvalue()
