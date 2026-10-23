"""Locating and driving ffmpeg (bundled binary first, then system, then imageio-ffmpeg)."""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Callable

_EXE = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_cached: str | None = None
_encoders: set[str] | None = None


class FFmpegError(RuntimeError):
    pass


def ffmpeg_exe() -> str:
    global _cached
    if _cached:
        return _cached
    candidates: list[str] = []
    if os.environ.get("MIXTAPER_FFMPEG"):
        candidates.append(os.environ["MIXTAPER_FFMPEG"])
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidates.append(os.path.join(bundle, "bin", _EXE))
    candidates.append(os.path.join(os.path.dirname(sys.executable), "bin", _EXE))
    found = shutil.which(_EXE)
    if found:
        candidates.append(found)
    # Apps launched from Finder get a minimal PATH.
    candidates += ["/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg"]
    try:
        import imageio_ffmpeg

        candidates.append(imageio_ffmpeg.get_ffmpeg_exe())
    except Exception:
        pass
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            _cached = c
            return c
    raise FFmpegError("ffmpeg not found")


def run(args: list[str], check: bool = True, **kw) -> subprocess.CompletedProcess:
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", *args]
    kw.setdefault("capture_output", True)
    proc = subprocess.run(cmd, creationflags=_NO_WINDOW, **kw)
    if check and proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace") if isinstance(proc.stderr, bytes) else str(proc.stderr)
        raise FFmpegError(err.strip().splitlines()[-1] if err.strip() else f"ffmpeg exited {proc.returncode}")
    return proc


def has_encoder(name: str) -> bool:
    global _encoders
    if _encoders is None:
        out = run(["-encoders"], check=False).stdout.decode("utf-8", "replace")
        _encoders = {line.split()[1] for line in out.splitlines() if len(line.split()) > 1 and line.startswith(" ")}
    return name in _encoders


async def run_async(
    args: list[str],
    duration: float = 0.0,
    progress: Callable[[float], None] | None = None,
) -> None:
    """Run ffmpeg, reporting progress (0..1) from its -progress output."""
    cmd = [ffmpeg_exe(), "-hide_banner", "-nostdin", "-v", "error", "-progress", "pipe:1", "-nostats", *args]
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, creationflags=_NO_WINDOW
    )
    err_chunks: list[bytes] = []

    async def pump_err() -> None:
        assert proc.stderr
        while chunk := await proc.stderr.read(4096):
            err_chunks.append(chunk)

    err_task = asyncio.create_task(pump_err())
    assert proc.stdout
    try:
        while line := await proc.stdout.readline():
            if progress and duration > 0 and line.startswith(b"out_time_us="):
                try:
                    us = int(line.split(b"=", 1)[1])
                except ValueError:
                    continue
                progress(min(1.0, max(0.0, us / 1e6 / duration)))
        rc = await proc.wait()
    except asyncio.CancelledError:
        proc.kill()
        raise
    await err_task
    if rc != 0:
        msg = b"".join(err_chunks).decode("utf-8", "replace").strip()
        raise FFmpegError(msg.splitlines()[-1] if msg else f"ffmpeg exited {rc}")


async def decode_mono16k(src: str, dst: Path, duration: float, progress: Callable[[float], None] | None = None) -> None:
    """Decode the first audio stream to raw 16 kHz mono s16le (what Shazam signatures need)."""
    tmp = dst.with_suffix(".part")
    await run_async(
        ["-y", "-i", src, "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-f", "s16le", str(tmp)],
        duration=duration,
        progress=progress,
    )
    os.replace(tmp, dst)


def image_to_jpeg(src: str, dst: str, max_side: int = 3000) -> None:
    run([
        "-y", "-v", "error", "-i", src,
        "-vf", f"scale='min({max_side},iw)':'min({max_side},ih)':force_original_aspect_ratio=decrease",
        "-frames:v", "1", "-q:v", "2", dst,
    ])


def reveal(path: str | Path) -> None:
    """Open a folder in the system file manager."""
    path = str(path)
    try:
        if sys.platform == "darwin":
            subprocess.Popen(["open", path])
        elif os.name == "nt":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


def file_size(path: str | Path) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0
