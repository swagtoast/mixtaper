"""Command line entry point: the TUI by default, or a headless scan/export."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from . import __version__, i18n
from .config import Settings
from .i18n import t


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="mixtaper", description="Split a DJ mix into tagged tracks.")
    ap.add_argument("file", nargs="?", help="mix audio file (mp3/flac/aiff/m4a/wav) or a mixtaper-session.json")
    ap.add_argument("--artist", help="album artist: who made the mix")
    ap.add_argument("--album", help="album title: the mix title")
    ap.add_argument("--year")
    ap.add_argument("--genre")
    ap.add_argument("--cover", help="cover image")
    ap.add_argument("--format", choices=["same", "mp3", "flac", "aiff", "m4a"], help="output format")
    ap.add_argument("--out", help="output root folder (default: next to the mix)")
    ap.add_argument("--mode", choices=["fast", "balanced", "accurate"], help="scan mode")
    ap.add_argument("--tracklist", help="import a tracklist (.txt/.cue) instead of / on top of scanning")
    ap.add_argument("--compilation", action="store_true", help="flag the album as a compilation")
    ap.add_argument("--headless", action="store_true", help="no UI: scan, then export")
    ap.add_argument("--scan-only", action="store_true", help="no UI: scan and print the tracklist")
    ap.add_argument("--lang", choices=["it", "en"])
    ap.add_argument("--selftest", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--version", action="version", version=f"mixtaper {__version__}")
    return ap


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):   # e.g. a cp1252 Windows pipe cannot print "█"
            stream.reconfigure(errors="replace")
    settings = Settings.load()
    i18n.set_lang(args.lang or settings.lang or i18n.detect())
    if args.mode:
        settings.scan_mode = args.mode
    if args.format:
        settings.out_format = args.format
    if args.selftest:
        sys.exit(asyncio.run(_selftest()))
    if args.headless or args.scan_only:
        if not args.file:
            sys.exit("mixtaper: a file is required in headless mode")
        sys.exit(asyncio.run(_headless(args, settings)))
    from .tui.app import run

    run(settings, args)


async def _selftest() -> int:
    """Start the UI headless and check ffmpeg / recognition engine are usable (for packaged builds)."""
    from shazamio_core import Recognizer  # noqa: F401  (native extension present)

    from . import ffmpeg
    from .tui.app import MixtaperApp

    exe = ffmpeg.ffmpeg_exe()
    app = MixtaperApp(Settings())
    async with app.run_test(size=(120, 36)) as pilot:
        await pilot.pause(0.3)
        screen = type(app.screen).__name__
    ok = screen == "SetupScreen" and ffmpeg.has_encoder("libmp3lame") and ffmpeg.has_encoder("flac")
    print(f"ffmpeg: {exe}\nscreen: {screen}\n{'OK' if ok else 'FAIL'}")
    return 0 if ok else 1


def _session_for(args: argparse.Namespace, settings: Settings):
    from .media import probe
    from .model import Album, Session

    src = os.path.abspath(args.file)
    if src.endswith(".json"):
        session = Session.load(src)
        return session, probe(session.source)
    info = probe(src)
    album = Album(artist=args.artist or "", title=args.album or "", year=args.year or "",
                  genre=args.genre or "", cover=os.path.abspath(args.cover) if args.cover else "",
                  compilation=args.compilation or settings.compilation)
    return Session(source=src, duration=info.duration, album=album), info


async def _headless(args: argparse.Namespace, settings: Settings) -> int:
    from .analysis import Analyzer
    from .export import export
    from .model import fmt_time
    from .recognize import RecognitionUnavailable
    from .tracklist import apply_entries, parse_tracklist

    session, info = _session_for(args, settings)
    print(f"{Path(session.source).name}  ·  {info.describe()}")

    if not session.tracks:
        def emit(kind: str, **kw) -> None:
            if kind == "phase":
                print(f"  · {t('phase.' + kw['name'])}", flush=True)
            elif kind == "probe":
                m = kw["match"]
                print(f"    {fmt_time(kw['t']):>8}  {(m.artist + ' - ' + m.title) if m else '—'}", flush=True)

        analyzer = Analyzer(info, settings, emit)
        try:
            session.tracks = await analyzer.run()
        except RecognitionUnavailable:
            print("  ! " + t("err.shazam"))
            session.tracks = analyzer.build_tracks()
        session.duration = analyzer.duration
        session.analyzed = True
    if args.tracklist:
        apply_entries(session, parse_tracklist(Path(args.tracklist).read_text("utf-8", "replace")))

    print()
    for i, tr in enumerate(session.tracks):
        print(f"{i + 1:02d}  {fmt_time(tr.start):>8}  {tr.artist} - {tr.display_title}")
    if args.scan_only:
        return 0

    root = Path(args.out or settings.out_dir or Path(session.source).parent)

    def progress(f: float, name: str) -> None:
        bar = "█" * int(f * 30)
        print(f"\r  [{bar:<30}] {f * 100:5.1f}%", end="", flush=True)

    res = await export(session, info, settings.out_format, root, progress)
    print(f"\n\n{t('export.done', n=len(res.files), size=_mb(res.bytes), src=_mb(res.input_bytes))}")
    print(res.folder)
    return 0


def _mb(n: int) -> str:
    return f"{n / 1e6:.1f} MB"
