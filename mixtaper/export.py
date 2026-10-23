"""Cutting the mix into tagged files.

Strategies, picked automatically from the input codec and the requested format:
  mp3copy  MP3 -> MP3: frame copy with gapless LAME headers (mp3cut.py), no re-encoding
  aaccopy  AAC -> M4A: packet copy via ffmpeg, cuts aligned to the 1024-sample AAC grid
  encode   anything else: one decode pass, sample-exact split with ffmpeg's asegment
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import ffmpeg, mp3cut
from .media import MediaInfo, container_of
from .model import Session
from .tagging import TagSet, write_tags
from .titles import safe_filename
from .tracklist import cue_text, m3u_text, tracklist_text

SESSION_FILE = "mixtaper-session.json"
Progress = Callable[[float, str], None]


@dataclass
class Plan:
    fmt: str            # mp3 | flac | aiff | m4a | wav
    method: str         # mp3copy | aaccopy | encode
    quality: str        # i18n key describing what happens to the audio


@dataclass
class Result:
    folder: Path
    files: list[Path]
    bytes: int
    input_bytes: int
    plan: Plan


def make_plan(info: MediaInfo, out_fmt: str) -> Plan:
    fmt = info.container if out_fmt in ("", "same") else out_fmt
    if fmt not in ("mp3", "flac", "aiff", "m4a", "wav"):
        fmt = "flac" if info.lossless else "mp3"
    if fmt == "mp3" and info.codec == "mp3":
        return Plan(fmt, "mp3copy", "q.copy")
    if fmt == "m4a" and info.codec == "aac":
        return Plan(fmt, "aaccopy", "q.copy")
    out_lossless = fmt in ("flac", "aiff", "wav") or (fmt == "m4a" and info.lossless)
    if out_lossless:
        return Plan(fmt, "encode", "q.lossless" if info.lossless else "q.lossy_to_lossless")
    return Plan(fmt, "encode", "q.to_lossy" if info.lossless else "q.lossy_to_lossy")


def _codec_args(fmt: str, info: MediaInfo) -> list[str]:
    hi_res = info.lossless and info.bits > 16
    if fmt == "flac":
        args = ["-c:a", "flac", "-compression_level", "8"]
        return args + (["-sample_fmt", "s32", "-bits_per_raw_sample", "24"] if hi_res else ["-sample_fmt", "s16"])
    if fmt == "aiff":
        return ["-c:a", "pcm_s24be" if hi_res else "pcm_s16be"]
    if fmt == "wav":
        return ["-c:a", "pcm_s24le" if hi_res else "pcm_s16le"]
    if fmt == "m4a":
        if info.lossless:
            return ["-c:a", "alac", "-sample_fmt", "s32p" if hi_res else "s16p"]
        # ffmpeg's own AAC encoder records priming/padding exactly (gapless); AudioToolbox does not
        return ["-c:a", "aac", "-b:a", _lossy_rate(info, (128, 160, 192, 256), 1.25)]
    # mp3
    rate = "320k" if info.lossless else _lossy_rate(info, (192, 224, 256, 320), 1.25)
    args = ["-c:a", "libmp3lame", "-b:a", rate]
    if info.sample_rate > 48000:
        args += ["-ar", "48000" if info.sample_rate % 48000 == 0 else "44100"]
    return args


def _lossy_rate(info: MediaInfo, ladder: tuple[int, ...], factor: float) -> str:
    """Lossless sources get the top rate; lossy ones a rate at least as good as the source."""
    if info.lossless or not info.bitrate:
        return f"{ladder[-1]}k"
    want = info.bitrate / 1000 * factor
    return f"{next((r for r in ladder if r >= want), ladder[-1])}k"


def track_names(s: Session, ext: str) -> list[str]:
    width = 3 if len(s.tracks) > 99 else 2
    names = []
    for i, t in enumerate(s.tracks):
        base = safe_filename(f"{i + 1:0{width}d} {t.artist} - {t.display_title}")
        names.append(f"{base}.{ext}")
    return names


def output_folder(s: Session, root: Path) -> Path:
    name = " - ".join(x for x in (s.album.artist, s.album.title) if x) or Path(s.source).stem
    base = root / safe_filename(name)
    folder = base
    n = 2
    while folder.exists() and any(folder.iterdir()):
        manifest = folder / SESSION_FILE
        try:
            prev = json.loads(manifest.read_text("utf-8"))
        except (OSError, ValueError):
            prev = None
        if prev and prev.get("source") == s.source:
            for f in prev.get("files") or []:      # re-export: replace our previous output
                try:
                    (folder / f).unlink()
                except OSError:
                    pass
            return folder
        folder = base.with_name(f"{base.name} ({n})")
        n += 1
    return folder


def _cover(src: str, folder: Path) -> tuple[bytes, str, str] | None:
    if not src or not os.path.isfile(src):
        return None
    ext = Path(src).suffix.lower()
    if ext in (".jpg", ".jpeg", ".png") and os.path.getsize(src) <= 8 * 1024 * 1024:
        name = "cover.png" if ext == ".png" else "cover.jpg"
        dst = folder / name
        if Path(src).resolve() != dst.resolve():
            shutil.copyfile(src, dst)
    else:
        name = "cover.jpg"
        dst = folder / name
        ffmpeg.image_to_jpeg(src, str(dst))
    return dst.read_bytes(), ("image/png" if name.endswith(".png") else "image/jpeg"), name


async def _aac_grid(src: str, sr: int) -> list[int]:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg.ffmpeg_exe(), "-v", "error", "-i", src, "-map", "0:a:0", "-c", "copy", "-f", "framecrc", "-",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    out, _ = await proc.communicate()
    tb_num, tb_den = 1, sr
    grid = []
    for line in out.decode("ascii", "replace").splitlines():
        if line.startswith("#tb"):
            num, den = line.split(":", 1)[1].strip().split("/")
            tb_num, tb_den = int(num), int(den)
        elif line and not line.startswith("#"):
            pts = int(line.split(",")[2])
            grid.append(round(pts * tb_num * sr / tb_den))
    return [g for g in grid if g > 0]


async def _cut_aac(src: str, sr: int, starts: list[int], dests: list[Path], progress: Progress) -> None:
    grid = await _aac_grid(src, sr)
    snapped = [0]
    for s in starts[1:]:
        g = min(grid, key=lambda p: abs(p - s)) if grid else s
        snapped.append(max(g, snapped[-1] + 1024))
    for i, dest in enumerate(dests):
        a = snapped[i]
        b = snapped[i + 1] if i + 1 < len(snapped) else None
        args = ["-y"]
        if a:
            # one sample before the packet boundary makes ffmpeg keep the AAC pre-roll packet
            # (clean decoding, exact start via an edit list); costs a single duplicated sample
            args += ["-ss", f"{(a - 1) / sr:.9f}"]
        args += ["-i", src]
        if b is not None:
            args += ["-t", f"{(b - a + (1 if a else 0)) / sr:.9f}"]
        args += ["-map", "0:a:0", "-c", "copy", "-map_metadata", "-1", "-movflags", "+faststart", str(dest)]
        await ffmpeg.run_async(args)
        progress((i + 1) / len(dests), dest.name)


async def _encode(src: str, info: MediaInfo, fmt: str, starts: list[int], dests: list[Path], dur: float,
                  progress: Progress) -> None:
    n = len(dests)
    if n > 1:
        segs = "".join(f"[s{i}]" for i in range(n))
        graph = f"[0:a:0]asegment=samples={'|'.join(str(x) for x in starts[1:])}{segs}"
        # every piece must start at timestamp 0, or the MP4 muxer mis-handles the AAC priming
        graph += "".join(f";[s{i}]asetpts=PTS-STARTPTS[o{i}]" for i in range(n))
    else:
        graph = "[0:a:0]anull[o0]"
    args = ["-y", "-i", src, "-filter_complex", graph]
    codec = _codec_args(fmt, info)
    for i, d in enumerate(dests):
        args += ["-map", f"[o{i}]", *codec, "-map_metadata", "-1", str(d)]
    await ffmpeg.run_async(args, duration=dur, progress=lambda f: progress(f, ""))


async def export(s: Session, info: MediaInfo, out_fmt: str, root: Path, progress: Progress | None = None) -> Result:
    progress = progress or (lambda f, name: None)
    plan = make_plan(info, out_fmt)
    s.normalize()
    root.mkdir(parents=True, exist_ok=True)
    folder = output_folder(s, root)
    folder.mkdir(parents=True, exist_ok=True)
    names = track_names(s, plan.fmt)
    dests = [folder / n for n in names]
    sr = info.sample_rate
    starts = [int(round(t.start * sr)) for t in s.tracks]
    starts[0] = 0

    if plan.method == "mp3copy":
        pieces = list(zip(starts, starts[1:] + [None]))
        try:
            await asyncio.to_thread(
                mp3cut.split, s.source, pieces, [str(d) for d in dests],
                lambda f: progress(f * 0.9, ""),
            )
        except mp3cut.Mp3CutError:
            plan = Plan(plan.fmt, "encode", "q.lossy_to_lossy")
            await _encode(s.source, info, plan.fmt, starts, dests, s.duration, lambda f, n: progress(f * 0.9, n))
    elif plan.method == "aaccopy":
        await _cut_aac(s.source, sr, starts, dests, lambda f, n: progress(f * 0.9, n))
    else:
        await _encode(s.source, info, plan.fmt, starts, dests, s.duration, lambda f, n: progress(f * 0.9, n))

    cover = await asyncio.to_thread(_cover, s.album.cover, folder)
    total = len(s.tracks)
    for i, (t, d) in enumerate(zip(s.tracks, dests)):
        tags = TagSet(
            title=t.display_title,
            artist=t.artist,
            album=s.album.title or Path(s.source).stem,
            album_artist=s.album.artist,
            track=i + 1,
            total=total,
            year=s.album.year or t.year,
            genre=s.album.genre or t.genre,
            compilation=s.album.compilation,
            remixer=t.remixer,
            label=t.label,
            isrc=t.isrc,
        )
        await asyncio.to_thread(write_tags, str(d), plan.fmt, tags, *(cover[:2] if cover else (None, "image/jpeg")))
        progress(0.9 + 0.1 * (i + 1) / total, d.name)

    stem = safe_filename(s.album.title or Path(s.source).stem)
    (folder / "tracklist.txt").write_text(tracklist_text(s), "utf-8")
    (folder / f"{stem}.cue").write_text(cue_text(s), "utf-8")
    (folder / f"{stem}.m3u8").write_text(m3u_text(s, names), "utf-8")
    manifest = s.to_dict()
    manifest["files"] = names + ["tracklist.txt", f"{stem}.cue", f"{stem}.m3u8"] + ([cover[2]] if cover else [])
    (folder / SESSION_FILE).write_text(json.dumps(manifest, indent=1, ensure_ascii=False), "utf-8")
    progress(1.0, "")
    return Result(folder, dests, sum(ffmpeg.file_size(d) for d in dests), ffmpeg.file_size(s.source), plan)


__all__ = ["Plan", "Result", "SESSION_FILE", "container_of", "export", "make_plan", "track_names"]
