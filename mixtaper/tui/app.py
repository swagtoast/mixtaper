"""The Textual application."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from textual.app import App
from textual.theme import Theme

from .. import i18n
from ..analysis import Analyzer, file_key
from ..config import Settings, cache_dir
from ..i18n import t
from ..media import MediaInfo, probe
from ..model import Session
from ..preview import Player
from .modals import SettingsModal
from .screens import EditorScreen, SetupScreen

THEME = Theme(
    name="mixtaper",
    primary="#ff7a1a",
    secondary="#9a9a9a",
    accent="#ff7a1a",
    foreground="#e4e4e4",
    background="#0b0b0b",
    surface="#111111",
    panel="#1b1b1b",
    success="#7bd88f",
    warning="#ffcb6b",
    error="#ff5f5f",
    dark=True,
)


class MixtaperApp(App):
    CSS_PATH = "app.tcss"
    TITLE = "mixtaper"

    def __init__(self, settings: Settings, args: argparse.Namespace | None = None) -> None:
        super().__init__()
        self.settings = settings
        self.session: Session | None = None
        self.info: MediaInfo | None = None
        self.analyzer: Analyzer | None = None
        self.player = Player()
        self._autosave_timer = None
        a = args or argparse.Namespace()
        self.draft = {
            "src": os.path.abspath(a.file) if getattr(a, "file", None) else "",
            "artist": getattr(a, "artist", None) or "",
            "album": getattr(a, "album", None) or "",
            "year": getattr(a, "year", None) or "",
            "genre": getattr(a, "genre", None) or "",
            "cover": os.path.abspath(a.cover) if getattr(a, "cover", None) else "",
            "format": getattr(a, "format", None) or settings.out_format or "same",
            "mode": getattr(a, "mode", None) or settings.scan_mode or "balanced",
            "out": getattr(a, "out", None) or settings.out_dir or "",
        }

    def on_mount(self) -> None:
        self.register_theme(THEME)
        self.theme = "mixtaper"
        self.push_screen(SetupScreen())

    def on_unmount(self) -> None:
        self.player.close()

    # -- session persistence -------------------------------------------------------
    @staticmethod
    def saved_session_path(src: str) -> Path:
        folder = cache_dir() / "sessions"
        folder.mkdir(parents=True, exist_ok=True)
        try:
            return folder / f"{file_key(src)}.json"
        except OSError:
            return folder / "unknown.json"

    def autosave(self, delay: bool = False) -> Path | None:
        if self.session is None:
            return None
        if delay:
            if self._autosave_timer is not None:
                self._autosave_timer.stop()
            self._autosave_timer = self.set_timer(1.0, lambda: self.autosave())
            return None
        path = self.saved_session_path(self.session.source)
        try:
            self.session.save(path)
        except OSError:
            return None
        return path

    def get_analyzer(self) -> Analyzer:
        if self.analyzer is None or self.analyzer.info.path != self.session.source:
            self.analyzer = Analyzer(self.info or probe(self.session.source), self.settings)
        return self.analyzer

    # -- global actions --------------------------------------------------------------
    def action_settings(self) -> None:
        def done(s: Settings | None) -> None:
            if s is None:
                return
            self.notify(t("settings.saved"))
            new = s.lang or i18n.detect()
            if new != i18n.get_lang():
                i18n.set_lang(new)
                self.call_later(self._relang)

        self.push_screen(SettingsModal(self.settings), done)

    def action_toggle_lang(self) -> None:
        i18n.set_lang("en" if i18n.get_lang() == "it" else "it")
        self.settings.lang = i18n.get_lang()
        self.settings.save()
        self.call_later(self._relang)

    async def _relang(self) -> None:
        screen = self.screen
        await screen.recompose()
        if isinstance(screen, EditorScreen):
            screen.populate()
        elif isinstance(screen, SetupScreen):
            screen.on_mount()


def _fit_terminal() -> None:
    """Ask a too-small terminal window to grow (Terminal.app, iTerm2, conhost)."""
    try:
        cols, rows = os.get_terminal_size()
    except OSError:
        return
    if cols >= 110 and rows >= 32:
        return
    if os.name == "nt":
        os.system("mode con: cols=120 lines=36 >nul 2>&1")
    elif sys.platform == "darwin":
        sys.stdout.write("\x1b[8;36;120t")
        sys.stdout.flush()


def run(settings: Settings, args: argparse.Namespace | None = None) -> None:
    _fit_terminal()
    app = MixtaperApp(settings, args)
    if args is not None and getattr(args, "file", None) and str(args.file).endswith(".json"):
        app.draft["src"] = os.path.abspath(args.file)
    app.run()
