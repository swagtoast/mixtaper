"""Session data: the mix, its album metadata and the tracklist (a list of cut points)."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .titles import full_title

ID = "ID"


@dataclass
class Track:
    start: float = 0.0          # seconds from the start of the mix
    artist: str = ID
    title: str = ID
    remixer: str = ""
    label: str = ""
    year: str = ""
    genre: str = ""
    isrc: str = ""
    release: str = ""           # the original release (album/EP) the track comes from
    source: str = "id"          # shazam | acrcloud | manual | import | id
    hits: int = 0               # how many recognition probes agreed on this track
    key: str = ""               # Shazam track key
    apple_id: str = ""

    @property
    def is_id(self) -> bool:
        return self.artist.strip().upper() == ID and self.title.strip().upper() == ID

    @property
    def display_title(self) -> str:
        return full_title(self.title, self.remixer)

    def set_unknown(self) -> None:
        for f in fields(self):
            if f.name != "start":
                setattr(self, f.name, f.default)


@dataclass
class Album:
    artist: str = ""            # album artist: whoever made the mix
    title: str = ""             # album: the mix title
    year: str = ""
    genre: str = ""
    cover: str = ""             # path to the cover image
    compilation: bool = False


@dataclass
class Session:
    source: str = ""
    duration: float = 0.0
    album: Album = field(default_factory=Album)
    tracks: list[Track] = field(default_factory=list)
    analyzed: bool = False
    version: int = 1

    def end_of(self, i: int) -> float:
        return self.tracks[i + 1].start if i + 1 < len(self.tracks) else self.duration

    def length_of(self, i: int) -> float:
        return self.end_of(i) - self.tracks[i].start

    def normalize(self) -> None:
        """Keep tracks sorted, contiguous and starting at 0."""
        self.tracks.sort(key=lambda t: t.start)
        clean: list[Track] = []
        for t in self.tracks:
            t.start = max(0.0, min(t.start, max(0.0, self.duration - 1.0)))
            if clean and t.start - clean[-1].start < 1.0:
                continue
            clean.append(t)
        if clean:
            clean[0].start = 0.0
        else:
            clean = [Track()]
        self.tracks = clean

    def index_at(self, t: float) -> int:
        for i in range(len(self.tracks) - 1, -1, -1):
            if self.tracks[i].start <= t:
                return i
        return 0

    # -- persistence -------------------------------------------------------
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Session":
        tk = {f.name for f in fields(Track)}
        ak = {f.name for f in fields(Album)}
        s = cls(
            source=d.get("source", ""),
            duration=float(d.get("duration", 0.0)),
            album=Album(**{k: v for k, v in (d.get("album") or {}).items() if k in ak}),
            tracks=[Track(**{k: v for k, v in t.items() if k in tk}) for t in d.get("tracks") or []],
            analyzed=bool(d.get("analyzed", False)),
        )
        return s

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=1, ensure_ascii=False), "utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Session":
        return cls.from_dict(json.loads(Path(path).read_text("utf-8")))


# -- time helpers -------------------------------------------------------------

def fmt_time(sec: float, decimals: int = 0) -> str:
    """1:02:13 / 4:05, optionally with fractional seconds (4:05.25)."""
    sec = max(0.0, float(sec))
    if decimals:
        scale = 10 ** decimals
        total = round(sec * scale)
        whole, frac = divmod(total, scale)
    else:
        whole, frac = int(round(sec)), 0
    h, rem = divmod(whole, 3600)
    m, s = divmod(rem, 60)
    out = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    if decimals:
        out += f".{frac:0{decimals}d}"
    return out


_TIME = re.compile(r"^\s*(?:(\d+):)?(?:(\d+):)?(\d+(?:[.,]\d+)?)\s*$")


def parse_time(text: str) -> float | None:
    """Parse h:mm:ss(.ff), mm:ss(.ff) or plain seconds."""
    m = _TIME.match(text or "")
    if not m:
        return None
    a, b, c = m.groups()
    secs = float(c.replace(",", "."))
    if a is not None and b is not None:
        return int(a) * 3600 + int(b) * 60 + secs
    if a is not None:
        return int(a) * 60 + secs
    return secs
