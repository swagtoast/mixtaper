"""Offline tests: titles, tracklists, time parsing, MP3 splitting round trip."""

import os
import subprocess

import numpy as np
import pytest

from mixtaper import ffmpeg, mp3cut
from mixtaper.model import Session, Track, fmt_time, parse_time
from mixtaper.titles import full_title, safe_filename, song_identity, split_remix
from mixtaper.tracklist import apply_entries, cue_text, parse_tracklist


@pytest.mark.parametrize("raw,base,remixer", [
    ("Summertime (feat. Noel McKoy & Opolopo) [Opolopo Remix]", "Summertime (feat. Noel McKoy & Opolopo)", "Opolopo"),
    ("Sick House (Rich Lane Chug Dub)", "Sick House", "Rich Lane Chug Dub"),
    ("Song - Floating Points Remix", "Song", "Floating Points"),
    ("Track (Extended Mix)", "Track (Extended Mix)", ""),
    ("Track (Original Mix)", "Track", ""),
    ("A Mother (Mixed)", "A Mother", ""),
    ("Livin' Free (Soundtrack Mix)", "Livin' Free (Soundtrack Mix)", ""),
    ("Song (Masters At Work Mix)", "Song", "Masters At Work Mix"),
])
def test_split_remix(raw, base, remixer):
    assert split_remix(raw) == (base, remixer)


def test_full_title_roundtrip():
    assert full_title("Song", "Opolopo") == "Song (Opolopo Remix)"
    assert full_title("Song (feat. X)", "Y") == "Song (feat. X) [Y Remix]"
    assert full_title("Song", "Y Edit") == "Song (Y Edit)"
    assert full_title("Summertime (feat. Noel McKoy & Opolopo)", "Opolopo").endswith("[Opolopo Remix]")


def test_song_identity_ignores_version():
    assert song_identity("Tata Vega", 'Get It Up For Love (12" Mix)') == \
        song_identity("Tata Vega", "Get It Up For Love (John Morales M+M CD Mix)")


def test_safe_filename():
    assert safe_filename("DJ-Kicks: Kruder & Dorfmeister") == "DJ-Kicks - Kruder & Dorfmeister"
    assert safe_filename('a/b\\c*?"<>|') == "a_b_c"
    assert safe_filename("CON") == "_CON"


def test_time_parsing():
    assert parse_time("1:02:13.5") == pytest.approx(3733.5)
    assert parse_time("4:05") == 245
    assert parse_time("12,5") == 12.5
    assert parse_time("abc") is None
    assert fmt_time(3733.5, 1) == "1:02:13.5"


def test_parse_tracklist_and_apply():
    text = """
    01. [00:00] Herbaliser - A Mother [Ninja Tune]
    02. [05:16] Small World - Livin' Free (Soundtrack Mix)
    03 10:26 Tango - Spellbound (Floating Points Remix)
    """
    entries = parse_tracklist(text)
    assert [e.start for e in entries] == [0, 316, 626]
    assert entries[0].label == "Ninja Tune"
    assert entries[2].remixer == "Floating Points"
    s = Session(source="/x/mix.mp3", duration=900, tracks=[Track()])
    apply_entries(s, entries)
    assert [t.start for t in s.tracks] == [0, 316, 626]
    cue = cue_text(s)
    assert "INDEX 01 05:16:00" in cue and 'PERFORMER "Tango"' in cue
    assert [e.title for e in parse_tracklist(cue)] == ["A Mother", "Livin' Free (Soundtrack Mix)", "Spellbound"]


def test_crc16_arc():
    assert mp3cut.crc16(b"123456789") == 0xBB3D


@pytest.fixture(scope="module")
def mp3(tmp_path_factory):
    path = tmp_path_factory.mktemp("mp3") / "tone.mp3"
    ffmpeg.run(["-y", "-v", "error", "-f", "lavfi", "-i", "sine=f=440:d=30", "-f", "lavfi", "-i",
                "anoisesrc=d=30:a=0.05", "-filter_complex", "amix=inputs=2,aformat=channel_layouts=stereo",
                "-c:a", "libmp3lame", "-b:a", "192k", str(path)])
    return str(path)


def _decode(path):
    out = subprocess.run([ffmpeg.ffmpeg_exe(), "-v", "error", "-i", path, "-f", "s16le", "-ac", "2", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype="<i2").reshape(-1, 2).astype(np.int32)


def test_mp3_split_is_gapless(mp3, tmp_path):
    s = mp3cut.scan(mp3)
    total = s.output_samples
    s.close()
    cuts = [123457, 600001, 1000003]
    bounds = list(zip([0] + cuts, cuts + [None]))
    dests = [str(tmp_path / f"{i}.mp3") for i in range(len(bounds))]
    mp3cut.split(mp3, bounds, dests)
    ref = _decode(mp3)
    parts = [_decode(d) for d in dests]
    assert len(ref) == total
    assert [len(p) for p in parts] == [b - a for a, b in zip([0] + cuts, cuts + [total])]
    assert np.abs(np.concatenate(parts) - ref).max() <= 1
    assert sum(os.path.getsize(d) for d in dests) < os.path.getsize(mp3) * 1.02
