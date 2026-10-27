"""Short audio previews around a cut point, through the system player (no extra dependencies)."""

from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import sys
import tempfile
import threading

from . import ffmpeg


class Player:
    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()       # previews are requested from worker threads
        self._counter = itertools.count()
        self._tmp = ""
        self._dir = tempfile.gettempdir()

    def play(self, src: str, start: float, length: float, tick_at: float | None = None) -> None:
        """Play [start, start+length]; an optional tick marks the cut point."""
        with self._lock:
            self._play(src, start, length, tick_at)

    def _play(self, src: str, start: float, length: float, tick_at: float | None) -> None:
        self.stop()
        self._remove_tmp()
        # a fresh file per preview: never rewrite a file another player may still be reading
        self._tmp = os.path.join(self._dir, f"mixtaper-preview-{os.getpid()}-{next(self._counter)}.wav")
        start = max(0.0, start)
        args = ["-y", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", src]
        if tick_at is not None and 0 <= tick_at - start < length:
            ms = int((tick_at - start) * 1000)
            args += [
                "-filter_complex",
                f"[0:a:0]aformat=sample_rates=44100:channel_layouts=stereo[a];"
                f"sine=f=1760:d=0.06,volume=0.5,adelay={ms}|{ms},aformat=channel_layouts=stereo[t];"
                f"[a][t]amix=inputs=2:duration=first:normalize=0[o]",
                "-map", "[o]",
            ]
        else:
            args += ["-map", "0:a:0", "-ac", "2", "-ar", "44100"]
        args += ["-c:a", "pcm_s16le", self._tmp]
        ffmpeg.run(args)
        if sys.platform == "darwin":
            self._proc = subprocess.Popen(["afplay", self._tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif os.name == "nt":
            import winsound

            winsound.PlaySound(self._tmp, winsound.SND_FILENAME | winsound.SND_ASYNC)
        else:
            for cmd in (["paplay"], ["aplay", "-q"], ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]):
                if shutil.which(cmd[0]):
                    self._proc = subprocess.Popen(cmd + [self._tmp], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    break

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._proc = None
        if os.name == "nt":
            try:
                import winsound

                winsound.PlaySound(None, 0)
            except Exception:
                pass

    def _remove_tmp(self) -> None:
        if self._tmp:
            try:
                os.remove(self._tmp)
            except OSError:
                pass

    def close(self) -> None:
        with self._lock:
            self.stop()
            self._remove_tmp()
