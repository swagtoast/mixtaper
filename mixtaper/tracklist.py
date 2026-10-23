"""Tracklist text, CUE sheet and playlist output; tracklist import (text / CUE)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .model import Session, fmt_time, parse_time
from .titles import MIX_WORDS, split_remix


def tracklist_text(s: Session) -> str:
    head = " — ".join(x for x in (s.album.artist, s.album.title) if x) or Path(s.source).stem
    lines = [head, ""]
    for i, t in enumerate(s.tracks):
        line = f"{i + 1:02d}. [{fmt_time(t.start)}] {t.artist} - {t.display_title}"
        if t.label:
            line += f" [{t.label}]"
        lines.append(line)
    return "\n".join(lines) + "\n"


def _q(text: str) -> str:
    return '"' + (text or "").replace('"', "'") + '"'


def cue_text(s: Session) -> str:
    src = Path(s.source)
    kind = "MP3" if src.suffix.lower() == ".mp3" else ("AIFF" if src.suffix.lower() in (".aif", ".aiff") else "WAVE")
    out = []
    if s.album.genre:
        out.append(f"REM GENRE {_q(s.album.genre)}")
    if s.album.year:
        out.append(f"REM DATE {s.album.year}")
    out += [f"PERFORMER {_q(s.album.artist)}", f"TITLE {_q(s.album.title or src.stem)}", f"FILE {_q(src.name)} {kind}"]
    for i, t in enumerate(s.tracks):
        frames = int(round(t.start * 75))
        mm, rem = divmod(frames, 75 * 60)
        ss, ff = divmod(rem, 75)
        out += [
            f"  TRACK {i + 1:02d} AUDIO",
            f"    TITLE {_q(t.display_title)}",
            f"    PERFORMER {_q(t.artist)}",
            f"    INDEX 01 {mm:02d}:{ss:02d}:{ff:02d}",
        ]
    return "\n".join(out) + "\n"


def m3u_text(s: Session, files: list[str]) -> str:
    out = ["#EXTM3U"]
    for i, (t, f) in enumerate(zip(s.tracks, files)):
        out.append(f"#EXTINF:{int(round(s.length_of(i)))},{t.artist} - {t.display_title}")
        out.append(f)
    return "\n".join(out) + "\n"


# -- import ---------------------------------------------------------------------------

@dataclass
class Entry:
    start: float | None
    artist: str
    title: str
    remixer: str = ""
    label: str = ""


_TS = r"(\d{1,2}:\d{2}(?::\d{2})?(?:[.,]\d+)?)"
_LINE = re.compile(
    r"^\s*(?:\d{1,3}[.)\-:]?\s+)?"          # track number
    r"(?:[\[(]?" + _TS + r"[\])]?\s*[-–|.]?\s*)?"   # timestamp
    r"(?P<rest>.+?)\s*$"
)
_LABEL = re.compile(r"\s*\[([^\[\]]+)\]\s*$")


def parse_tracklist(text: str) -> list[Entry]:
    if re.search(r"^\s*INDEX\s+01", text, re.M):
        return _parse_cue(text)
    out: list[Entry] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        ts, rest = m.group(1), m.group("rest")
        if not re.search(r"\s[-–]\s", rest):
            continue
        label = ""
        lm = _LABEL.search(rest)
        if lm and not re.search(r"\b(" + MIX_WORDS + r")\b", lm.group(1), re.I):
            label, rest = lm.group(1).strip(), rest[: lm.start()]
        artist, title = re.split(r"\s+[-–]\s+", rest, maxsplit=1)
        title, remixer = split_remix(title.strip())
        out.append(Entry(parse_time(ts) if ts else None, artist.strip(), title, remixer, label))
    return out


def _parse_cue(text: str) -> list[Entry]:
    out: list[Entry] = []
    cur: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("TRACK "):
            if cur:
                out.append(_entry(cur))
            cur = {}
        elif cur is not None:
            m = re.match(r'(TITLE|PERFORMER)\s+"?(.*?)"?$', line)
            if m:
                cur[m.group(1)] = m.group(2)
            m = re.match(r"INDEX\s+01\s+(\d+):(\d+):(\d+)", line)
            if m:
                cur["start"] = int(m.group(1)) * 60 + int(m.group(2)) + int(m.group(3)) / 75
    if cur:
        out.append(_entry(cur))
    return out


def _entry(d: dict) -> Entry:
    title, remixer = split_remix(d.get("TITLE", "ID"))
    return Entry(d.get("start"), d.get("PERFORMER", "ID"), title, remixer)


def apply_entries(s: Session, entries: list[Entry]) -> None:
    """Timed entries replace the cut points; untimed ones fill the existing tracks in order."""
    from .model import Track

    if entries and all(e.start is not None for e in entries):
        s.tracks = [
            Track(start=float(e.start or 0.0), artist=e.artist or "ID", title=e.title or "ID",
                  remixer=e.remixer, label=e.label, source="import")
            for e in entries
        ]
        s.normalize()
        return
    for t, e in zip(s.tracks, entries):
        t.artist, t.title, t.remixer = e.artist or "ID", e.title or "ID", e.remixer
        t.label = e.label or t.label
        t.source = "import"
