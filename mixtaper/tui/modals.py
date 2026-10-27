"""Dialogs: file picker, album details, tracklist import, settings, prompts."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label, Select, Static, Switch, TextArea

from ..config import Settings
from ..i18n import t
from ..media import IMAGE_EXTS
from ..model import Album
from .common import clean_path, start_dir


class _Modal(ModalScreen):
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def action_cancel(self) -> None:
        self.dismiss(None)


class FilteredTree(DirectoryTree):
    def __init__(self, path: str, exts: set[str] | None, dirs_only: bool, **kw) -> None:
        super().__init__(path, **kw)
        self.exts = exts
        self.dirs_only = dirs_only

    def filter_paths(self, paths: Iterable[Path]) -> Iterable[Path]:
        out = []
        for p in paths:
            if p.name.startswith("."):
                continue
            try:
                if p.is_dir():
                    out.append(p)
                elif not self.dirs_only and (self.exts is None or p.suffix.lower() in self.exts):
                    out.append(p)
            except OSError:
                continue
        return out


class FilePicker(_Modal):
    """Pick a file with the given extensions, or a folder when dirs_only."""

    def __init__(self, title: str, exts: set[str] | None, start: str, dirs_only: bool = False) -> None:
        super().__init__()
        self.title_text = title
        self.exts = exts
        self.start = start_dir(start)
        self.dirs_only = dirs_only
        self.current = self.start

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Static(self.title_text, classes="dialog-title")
            yield Input(self.start, id="path")
            yield FilteredTree(self.start, self.exts, self.dirs_only, id="tree")
            with Horizontal(classes="buttons"):
                if self.dirs_only:
                    yield Button(t("b.choose_dir"), id="choose", variant="primary")
                yield Button(t("b.cancel"), id="cancel")

    def on_mount(self) -> None:
        self.query_one("#tree").focus()

    @on(DirectoryTree.FileSelected)
    def _file(self, event: DirectoryTree.FileSelected) -> None:
        if not self.dirs_only:
            self.dismiss(str(event.path))

    @on(DirectoryTree.DirectorySelected)
    def _dir(self, event: DirectoryTree.DirectorySelected) -> None:
        self.current = str(event.path)
        self.query_one("#path", Input).value = self.current

    @on(Input.Submitted, "#path")
    def _typed(self, event: Input.Submitted) -> None:
        p = Path(clean_path(event.value))
        if p.is_dir():
            self.current = str(p)
            self.query_one("#tree", FilteredTree).path = p
        elif p.is_file() and not self.dirs_only:
            self.dismiss(str(p))

    @on(Button.Pressed, "#choose")
    def _choose(self) -> None:
        self.dismiss(self.current)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class AlbumModal(_Modal):
    def __init__(self, album: Album) -> None:
        super().__init__()
        self.album = album

    def compose(self) -> ComposeResult:
        a = self.album
        with Vertical(classes="dialog"):
            yield Static(t("album.title"), classes="dialog-title")
            for key, wid, value, ph in (
                ("f.album_artist", "artist", a.artist, "f.album_artist.ph"),
                ("f.album", "title", a.title, "f.album.ph"),
                ("f.year", "year", a.year, ""),
                ("f.genre", "genre", a.genre, ""),
            ):
                with Horizontal(classes="row"):
                    yield Label(t(key), classes="lbl")
                    yield Input(value, placeholder=t(ph) if ph else "", id=wid)
            with Horizontal(classes="row"):
                yield Label(t("f.cover"), classes="lbl")
                yield Input(a.cover, placeholder=t("f.cover.ph"), id="cover")
                yield Button(t("b.browse"), id="browse", classes="small")
            with Horizontal(classes="row"):
                yield Label(t("f.compilation"), classes="lbl")
                yield Switch(a.compilation, id="compilation")
            with Horizontal(classes="buttons"):
                yield Button(t("b.save"), id="save", variant="primary")
                yield Button(t("b.cancel"), id="cancel")

    def on_mount(self) -> None:
        self.query_one("#artist").focus()

    @on(Button.Pressed, "#browse")
    def _browse(self) -> None:
        cur = self.query_one("#cover", Input).value

        def done(path: str | None) -> None:
            if path:
                self.query_one("#cover", Input).value = path

        self.app.push_screen(FilePicker(t("f.cover"), IMAGE_EXTS, cur or self.app.settings.last_dir), done)

    @on(Input.Changed, "#cover")
    def _clean(self, event: Input.Changed) -> None:
        cleaned = clean_path(event.value)
        if cleaned != event.value and Path(cleaned).exists():
            event.input.value = cleaned

    @on(Input.Submitted)
    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        v = {w: self.query_one(f"#{w}", Input).value.strip() for w in ("artist", "title", "year", "genre", "cover")}
        self.dismiss(Album(artist=v["artist"], title=v["title"], year=v["year"], genre=v["genre"],
                           cover=clean_path(v["cover"]), compilation=self.query_one("#compilation", Switch).value))

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class ImportModal(_Modal):
    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Static(t("import.title"), classes="dialog-title")
            yield Static(t("import.help"), classes="muted")
            yield TextArea(id="text")
            with Horizontal(classes="buttons"):
                yield Button(t("b.apply"), id="apply", variant="primary")
                yield Button(t("b.cancel"), id="cancel")

    def on_mount(self) -> None:
        self.query_one("#text").focus()

    @on(Button.Pressed, "#apply")
    def _apply(self) -> None:
        text = self.query_one("#text", TextArea).text.strip()
        maybe = Path(clean_path(text)) if "\n" not in text else None
        if maybe and maybe.is_file():
            text = maybe.read_text("utf-8", "replace")
        self.dismiss(text or None)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class SettingsModal(_Modal):
    def __init__(self, settings: Settings) -> None:
        super().__init__()
        self.s = settings

    def compose(self) -> ComposeResult:
        s = self.s
        with Vertical(classes="dialog"):
            yield Static(t("settings.title"), classes="dialog-title")
            with Horizontal(classes="row"):
                yield Label(t("settings.lang"), classes="lbl")
                yield Select([(t("settings.lang.auto"), ""), ("Italiano", "it"), ("English", "en")],
                             value=s.lang, allow_blank=False, id="lang")
            with Horizontal(classes="row"):
                yield Label(t("settings.cut"), classes="lbl")
                yield Select([(t("settings.cut.start"), "start"), (t("settings.cut.blend"), "blend")],
                             value=s.cut_mode if s.cut_mode in ("start", "blend") else "start",
                             allow_blank=False, id="cut")
            with Horizontal(classes="row"):
                yield Label(t("f.compilation"), classes="lbl")
                yield Switch(s.compilation, id="compilation")
            yield Static(t("settings.acr"), classes="muted")
            for wid, label, value in (("acr_host", "Host", s.acr_host), ("acr_key", "Access key", s.acr_key),
                                      ("acr_secret", "Secret", s.acr_secret)):
                with Horizontal(classes="row"):
                    yield Label(label, classes="lbl")
                    yield Input(value, id=wid, password=wid == "acr_secret")
            with Horizontal(classes="buttons"):
                yield Button(t("b.save"), id="save", variant="primary")
                yield Button(t("b.cancel"), id="cancel")

    @on(Button.Pressed, "#save")
    def _save(self) -> None:
        s = self.s
        s.lang = self.query_one("#lang", Select).value or ""
        s.cut_mode = self.query_one("#cut", Select).value or "start"
        s.compilation = self.query_one("#compilation", Switch).value
        for wid in ("acr_host", "acr_key", "acr_secret"):
            setattr(s, wid, self.query_one(f"#{wid}", Input).value.strip())
        s.save()
        self.dismiss(s)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class PromptModal(_Modal):
    def __init__(self, question: str, default: str = "") -> None:
        super().__init__()
        self.question = question
        self.default = default

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog small"):
            yield Static(self.question, classes="dialog-title")
            yield Input(self.default, id="answer")
            with Horizontal(classes="buttons"):
                yield Button(t("b.ok"), id="ok", variant="primary")
                yield Button(t("b.cancel"), id="cancel")

    def on_mount(self) -> None:
        self.query_one("#answer").focus()

    @on(Input.Submitted)
    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(self.query_one("#answer", Input).value)

    @on(Button.Pressed, "#cancel")
    def _cancel(self) -> None:
        self.dismiss(None)


class ConfirmModal(_Modal):
    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog small"):
            yield Static(self.question, classes="dialog-title")
            with Horizontal(classes="buttons"):
                yield Button(t("confirm.yes"), id="yes", variant="primary")
                yield Button(t("confirm.no"), id="no")

    def on_mount(self) -> None:
        self.query_one("#yes").focus()

    @on(Button.Pressed, "#yes")
    def _yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def _no(self) -> None:
        self.dismiss(False)


HELP = [
    ("↑ ↓", "k.edit", "enter"),
    ("space", "k.play", ""),
    ("p", "k.transition", "−6 s … tic … +4 s"),
    (", .  < >", "k.nudge", "±0.25s  ±2s"),
    ("s", "k.split", ""),
    ("m", "k.merge", ""),
    ("del", "k.delete", ""),
    ("r", "k.rescan", ""),
    ("i", "k.id", ""),
    ("a", "k.album", ""),
    ("t", "k.import", ""),
    ("e", "k.export", ""),
    ("ctrl+z / ⌘z", "k.undo", "max 5"),
    ("ctrl+s", "k.save", ""),
    ("f2", "k.settings", ""),
    ("ctrl+l", "k.lang", ""),
    ("q", "k.quit", ""),
]


class HelpModal(_Modal):
    BINDINGS = [Binding("escape,question_mark,q", "cancel", show=False)]

    def compose(self) -> ComposeResult:
        lines = "\n".join(f"[b reverse] {k} [/]  {t(d)} [dim]{extra}[/dim]" for k, d, extra in HELP)
        with Vertical(classes="dialog"):
            yield Static(t("help.title"), classes="dialog-title")
            yield Static(lines)
            with Horizontal(classes="buttons"):
                yield Button(t("b.ok"), id="ok", variant="primary")

    @on(Button.Pressed, "#ok")
    def _ok(self) -> None:
        self.dismiss(None)
