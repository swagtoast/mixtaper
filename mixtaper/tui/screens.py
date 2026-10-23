"""The four screens: setup → analysis → tracklist editor → export."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, DataTable, Input, Label, ProgressBar, RichLog, Select, Static

from .. import ffmpeg
from ..analysis import Analyzer
from ..export import export, make_plan
from ..i18n import t
from ..media import AUDIO_EXTS, IMAGE_EXTS, probe
from ..model import Album, Session, Track, fmt_time, parse_time
from ..recognize import RecognitionUnavailable
from ..titles import identity, split_remix
from ..tracklist import apply_entries, parse_tracklist
from .common import LOGO, PALETTE, clean_path, key_bar, start_dir
from .modals import (AlbumModal, ConfirmModal, FilePicker, HelpModal, ImportModal, PromptModal,
                     SettingsModal)

FORMATS = ["same", "mp3", "flac", "aiff", "m4a"]
MODES = ["fast", "balanced", "accurate"]


def _format_options(container: str | None = None) -> list[tuple[str, str]]:
    same = t("fmt.same") + (f" ({container.upper()})" if container else "")
    return [(same, "same")] + [(f.upper(), f) for f in FORMATS[1:]]


# ───────────────────────────────────────────────────────────────────────── setup ──

class SetupScreen(Screen):
    BINDINGS = [
        Binding("ctrl+o", "browse_src", show=False),
        Binding("ctrl+l", "app.toggle_lang", show=False),
        Binding("f2", "app.settings", show=False),
        Binding("ctrl+q", "app.quit", show=False),
    ]

    def compose(self) -> ComposeResult:
        d = self.app.draft
        yield Static(LOGO.format(tagline=t("tagline")), id="logo")
        with VerticalScroll(id="setup"):
            yield Static(t("sec.mix"), classes="section")
            with Horizontal(classes="row"):
                yield Label(t("f.source"), classes="lbl")
                yield Input(d["src"], placeholder=t("f.source.ph"), id="src")
                yield Button(t("b.browse"), id="browse_src", classes="small")
            yield Static("", id="src_info", classes="info")
            yield Static(t("sec.album"), classes="section")
            with Horizontal(classes="row"):
                yield Label(t("f.album_artist"), classes="lbl")
                yield Input(d["artist"], placeholder=t("f.album_artist.ph"), id="artist")
            with Horizontal(classes="row"):
                yield Label(t("f.album"), classes="lbl")
                yield Input(d["album"], placeholder=t("f.album.ph"), id="album")
            with Horizontal(classes="row"):
                yield Label(t("f.year"), classes="lbl")
                yield Input(d["year"], id="year", classes="short")
                yield Label(t("f.genre"), classes="lbl2")
                yield Input(d["genre"], id="genre")
            with Horizontal(classes="row"):
                yield Label(t("f.cover"), classes="lbl")
                yield Input(d["cover"], placeholder=t("f.cover.ph"), id="cover")
                yield Button(t("b.browse"), id="browse_cover", classes="small")
            yield Static(t("sec.output"), classes="section")
            with Horizontal(classes="row"):
                yield Label(t("f.format"), classes="lbl")
                yield Select(_format_options(), value=d["format"], allow_blank=False, id="format")
                yield Label(t("f.mode"), classes="lbl2")
                yield Select([(t(f"mode.{m}"), m) for m in MODES], value=d["mode"], allow_blank=False, id="mode")
            with Horizontal(classes="row"):
                yield Label(t("f.out"), classes="lbl")
                yield Input(d["out"], placeholder=t("f.out.ph"), id="out")
                yield Button(t("b.browse"), id="browse_out", classes="small")
            with Horizontal(id="actions"):
                yield Button(t("b.analyze"), id="go", variant="primary")
                yield Button(t("b.resume"), id="resume", classes="hidden")
                yield Button(t("b.import"), id="import")
                yield Button(t("b.settings"), id="settings")
            yield Static(t("hint.setup"), classes="hint")

    def on_mount(self) -> None:
        self._update_source(self.app.draft["src"])
        self.query_one("#src" if not self.app.draft["src"] else "#go").focus()

    # drag & drop: terminals paste the dropped path
    def on_paste(self, event) -> None:
        path = clean_path(event.text)
        p = Path(path)
        target = None
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS | {".json"}:
            target = "#src"
        elif p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            target = "#cover"
        elif p.is_dir():
            target = "#out"
        if target:
            event.stop()
            event.prevent_default()
            self.query_one(target, Input).value = path

    @on(Input.Changed)
    def _changed(self, event: Input.Changed) -> None:
        wid = event.input.id or ""
        if wid in ("src", "cover", "out"):
            cleaned = clean_path(event.value)
            if cleaned != event.value and Path(cleaned).exists():
                event.input.value = cleaned
                return
        key = {"src": "src", "artist": "artist", "album": "album", "year": "year", "genre": "genre",
               "cover": "cover", "out": "out"}.get(wid)
        if key:
            self.app.draft[key] = event.value
        if wid == "src":
            self._update_source(event.value)

    @on(Select.Changed)
    def _select(self, event: Select.Changed) -> None:
        if event.select.id in ("format", "mode") and event.value is not Select.BLANK:
            self.app.draft[event.select.id] = event.value

    def _update_source(self, value: str) -> None:
        info_w = self.query_one("#src_info", Static)
        resume = self.query_one("#resume", Button)
        resume.add_class("hidden")
        path = clean_path(value)
        if not path:
            info_w.update("")
            return
        p = Path(path)
        if not p.is_file():
            info_w.update("")
            return
        if p.suffix.lower() == ".json":
            try:
                s = Session.load(p)
                info_w.update(f"[b]{Path(s.source).name}[/b] · {len(s.tracks)} tracks")
                resume.remove_class("hidden")
            except Exception as e:  # noqa: BLE001
                info_w.update(f"[red]{t('err.bad_file', e=e)}[/red]")
            return
        try:
            info = probe(path)
        except Exception as e:  # noqa: BLE001
            info_w.update(f"[red]{t('err.bad_file', e=e)}[/red]")
            return
        self.app.info = info
        text = f"[b]{info.describe()}[/b]"
        if self.app.saved_session_path(path).exists():
            text += f"\n[dim]{t('info.session_found')}[/dim]"
            resume.remove_class("hidden")
        info_w.update(text)
        fmt = self.query_one("#format", Select)
        cur = fmt.value
        fmt.set_options(_format_options(info.container))
        fmt.value = cur if cur in FORMATS else "same"

    def _album(self) -> Album:
        d = self.app.draft
        return Album(artist=d["artist"].strip(), title=d["album"].strip(), year=d["year"].strip(),
                     genre=d["genre"].strip(), cover=clean_path(d["cover"]),
                     compilation=self.app.settings.compilation)

    def _source(self) -> str | None:
        path = clean_path(self.app.draft["src"])
        if not path or not Path(path).is_file():
            self.app.notify(t("err.no_file"), severity="warning")
            self.query_one("#src").focus()
            return None
        return str(Path(path).resolve())

    @on(Button.Pressed, "#go")
    def _go(self) -> None:
        src = self._source()
        if not src:
            return
        if src.endswith(".json"):
            self._resume()
            return
        try:
            info = probe(src)
        except Exception as e:  # noqa: BLE001
            self.app.notify(t("err.bad_file", e=e), severity="error")
            return
        self.app.settings.scan_mode = self.app.draft["mode"]
        self.app.settings.out_format = self.app.draft["format"]
        self.app.settings.last_dir = str(Path(src).parent)
        self.app.settings.save()
        self.app.info = info
        self.app.session = Session(source=src, duration=info.duration, album=self._album())
        self.app.push_screen(ScanScreen())

    @on(Button.Pressed, "#resume")
    def _resume(self) -> None:
        src = self._source()
        if not src:
            return
        session = Session.load(src) if src.endswith(".json") else Session.load(self.app.saved_session_path(src))
        album = self._album()
        for k in ("artist", "title", "year", "genre", "cover"):      # fields typed now win
            if getattr(album, k):
                setattr(session.album, k, getattr(album, k))
        try:
            self.app.info = probe(session.source)
        except Exception as e:  # noqa: BLE001
            self.app.notify(t("err.bad_file", e=e), severity="error")
            return
        self.app.session = session
        self.app.push_screen(EditorScreen())

    @on(Button.Pressed, "#import")
    def _import(self) -> None:
        src = self._source()
        if not src:
            return

        def done(text: str | None) -> None:
            if not text:
                return
            entries = parse_tracklist(text)
            if not entries:
                self.app.notify(t("import.empty"), severity="warning")
                return
            info = probe(src)
            self.app.info = info
            session = Session(source=src, duration=info.duration, album=self._album(), tracks=[Track()])
            apply_entries(session, entries)
            self.app.session = session
            self.app.notify(t("import.done", n=len(entries)))
            self.app.push_screen(EditorScreen())

        self.app.push_screen(ImportModal(), done)

    @on(Button.Pressed, "#settings")
    def _settings(self) -> None:
        self.app.action_settings()

    @on(Button.Pressed, "#browse_src")
    def action_browse_src(self) -> None:
        self._pick("#src", t("picker.title"), AUDIO_EXTS | {".json"})

    @on(Button.Pressed, "#browse_cover")
    def _browse_cover(self) -> None:
        self._pick("#cover", t("f.cover"), IMAGE_EXTS)

    @on(Button.Pressed, "#browse_out")
    def _browse_out(self) -> None:
        self._pick("#out", t("picker.dir_title"), None, dirs_only=True)

    def _pick(self, target: str, title: str, exts: set[str] | None, dirs_only: bool = False) -> None:
        cur = self.query_one(target, Input).value
        start = start_dir(clean_path(cur), self.app.draft["src"], self.app.settings.last_dir)

        def done(path: str | None) -> None:
            if path:
                self.query_one(target, Input).value = path

        self.app.push_screen(FilePicker(title, exts, start, dirs_only), done)


# ────────────────────────────────────────────────────────────────────── analysis ──

class ScanScreen(Screen):
    BINDINGS = [Binding("escape", "stop", show=False)]

    def __init__(self) -> None:
        super().__init__()
        self.phase = "decode"
        self.pos = 0.0
        self._marks: dict[float, str | None] = {}
        self._colors: dict[str, str] = {}
        self.analyzer: Analyzer | None = None

    def compose(self) -> ComposeResult:
        s = self.app.session
        yield Static(f"[b]{t('scan.title')}[/b] · {Path(s.source).name} · [dim]{self.app.info.describe()}[/dim]",
                     classes="header")
        yield ProgressBar(total=1000, show_eta=False, id="bar")
        yield Static("", id="status")
        yield Static("", id="timeline")
        yield RichLog(id="log", markup=True, wrap=False)
        with Horizontal(classes="buttons"):
            yield Button(t("b.stop"), id="stop")

    def on_mount(self) -> None:
        self.run_analysis()

    @work(exclusive=True, group="scan")
    async def run_analysis(self) -> None:
        app = self.app
        self.analyzer = app.analyzer = Analyzer(app.info, app.settings, self._on_analysis)
        try:
            tracks = await self.analyzer.run()
        except RecognitionUnavailable:
            app.notify(t("err.shazam"), severity="warning", timeout=10)
            tracks = await asyncio.to_thread(self.analyzer.build_tracks)
        except ffmpeg.FFmpegError as e:
            app.notify(str(e), severity="error", timeout=10)
            app.pop_screen()
            return
        self._finish(tracks)

    def _finish(self, tracks: list[Track]) -> None:
        app = self.app
        app.session.tracks = tracks
        app.session.duration = self.analyzer.duration if self.analyzer else app.session.duration
        app.session.analyzed = True
        app.session.normalize()
        app.autosave()
        app.switch_screen(EditorScreen())

    def action_stop(self) -> None:
        self.workers.cancel_group(self, "scan")
        an = self.analyzer
        if an is not None and an.pcm is not None and an.probes:
            self._finish(an.build_tracks())
        else:
            self.app.pop_screen()

    @on(Button.Pressed, "#stop")
    def _stop(self) -> None:
        self.action_stop()

    def _on_analysis(self, kind: str, **kw) -> None:
        dur = max(1.0, self.analyzer.duration if self.analyzer else self.app.session.duration)
        bar = self.query_one("#bar", ProgressBar)
        log = self.query_one("#log", RichLog)
        if kind == "phase":
            self.phase = kw["name"]
            if self.phase not in ("decode", "scan"):
                bar.update(progress=1000)
        elif kind == "decode":
            bar.update(progress=kw["frac"] * 1000)
        elif kind == "position":
            self.pos = kw["t"]
            if self.phase == "scan":
                bar.update(progress=min(1.0, self.pos / dur) * 1000)
        elif kind == "probe":
            m = kw["match"]
            when = f"[dim]{fmt_time(kw['t']):>8}[/dim]"
            if m:
                ident = identity(m.artist, m.title)
                color = self._colors.setdefault(ident, PALETTE[len(self._colors) % len(PALETTE)])
                self._marks[kw["t"]] = color
                log.write(f"{when}  [{color}]■[/]  {m.artist} — {m.title}")
            else:
                self._marks[kw["t"]] = None
                log.write(f"{when}  [red]·[/]  [dim]{t('scan.none')}[/dim]")
            self._draw_timeline(dur)
        elif kind == "throttle":
            log.write(f"[dim]{' ' * 10}{t('scan.throttle', s=kw['wait'])}[/dim]")
        q = self.analyzer.queries if self.analyzer else 0
        self.query_one("#status", Static).update(t(
            "scan.status", phase=t(f"phase.{self.phase}"), pos=fmt_time(self.pos), dur=fmt_time(dur),
            found=len(self._colors), q=q))

    def _draw_timeline(self, dur: float) -> None:
        w = max(20, self.query_one("#timeline").size.width - 2)
        cells: list[tuple[str, str]] = [("─", "dim")] * w
        for tt, color in sorted(self._marks.items()):
            x = min(w - 1, int(tt / dur * w))
            cells[x] = ("█", color) if color else ("·", "red")
        text = Text()
        for ch, style in cells:
            text.append(ch, style=style)
        self.query_one("#timeline", Static).update(text)


# ──────────────────────────────────────────────────────────────────────── editor ──

SOURCE_LABEL = {"shazam": "src.shazam", "acrcloud": "src.acrcloud", "manual": "src.manual",
                "import": "src.import", "id": "src.id"}
FIELDS = ("artist", "title", "remixer", "label")


class EditorScreen(Screen):
    BINDINGS = [
        Binding("enter", "edit", show=False),
        Binding("space", "play", show=False),
        Binding("s", "split", show=False),
        Binding("m", "merge", show=False),
        Binding("delete,backspace", "delete", show=False),
        Binding("comma", "nudge(-0.25)", show=False),
        Binding("full_stop", "nudge(0.25)", show=False),
        Binding("less_than_sign", "nudge(-2)", show=False),
        Binding("greater_than_sign", "nudge(2)", show=False),
        Binding("r", "rescan", show=False),
        Binding("i", "mark_id", show=False),
        Binding("a", "album", show=False),
        Binding("t", "import", show=False),
        Binding("e", "export", show=False),
        Binding("ctrl+s", "save", show=False),
        Binding("escape", "focus_table", show=False),
        Binding("question_mark", "help", show=False),
        Binding("f2", "app.settings", show=False),
        Binding("ctrl+l", "app.toggle_lang", show=False),
        Binding("q", "quit", show=False),
    ]

    def __init__(self) -> None:
        super().__init__()
        self.sel = 0
        self._loading = False
        self._asked_album = False

    @property
    def session(self) -> Session:
        return self.app.session

    def compose(self) -> ComposeResult:
        yield Static("", id="ed_header", classes="header")
        yield DataTable(id="table", cursor_type="row", zebra_stripes=True)
        with Horizontal(id="form"):
            with Vertical(classes="fcol fstart"):
                yield Label(t("f.start"))
                yield Input(id="f_start")
            for f, cls in (("artist", "fwide"), ("title", "fwide"), ("remixer", "fmid"), ("label", "fmid")):
                with Vertical(classes=f"fcol {cls}"):
                    yield Label(t(f"f.{f}"))
                    yield Input(id=f"f_{f}")
        yield Static(t("ed.form_hint"), classes="hint", id="form_hint")
        yield Static(key_bar([("↵", "k.edit"), ("space", "k.play"), (",.<>", "k.nudge"), ("s", "k.split"),
                              ("m", "k.merge"), ("del", "k.delete"), ("r", "k.rescan"), ("a", "k.album"),
                              ("t", "k.import"), ("e", "k.export"), ("?", "k.help")]), id="keys")

    def on_mount(self) -> None:
        self.populate()

    def _widths(self) -> list[tuple[str, int]]:
        fixed = [("n", 3), ("start", 9), ("len", 6), ("remixer", 16), ("label", 14), ("source", 10)]
        avail = max(30, self.app.size.width - 6 - sum(w for _, w in fixed) - 2 * 8 - 2)
        artist = max(12, int(avail * 0.42))
        title = max(12, avail - artist)
        w = dict(fixed, artist=artist, title=title)
        return [(k, w[k]) for k in ("n", "start", "len", "artist", "title", "remixer", "label", "source")]

    def on_resize(self) -> None:
        if self.query("#table") and self._widths() != getattr(self, "_last_widths", None):
            self.populate(ask=False)

    def populate(self, ask: bool = True) -> None:
        table = self.query_one("#table", DataTable)
        table.clear(columns=True)
        self._last_widths = self._widths()
        for key, width in self._last_widths:
            table.add_column(t(f"col.{key}"), key=key, width=width)
        self.refresh_table()
        table.focus()
        if ask and not self._asked_album and not (self.session.album.artist and self.session.album.title):
            self._asked_album = True
            self.call_after_refresh(self.action_album)

    def _header(self) -> None:
        s = self.session
        name = " — ".join(x for x in (s.album.artist, s.album.title) if x) or Path(s.source).name
        ids = sum(1 for tr in s.tracks if tr.is_id)
        summary = t("ed.summary", n=len(s.tracks), ids=ids, dur=fmt_time(s.duration))
        cover = "  [dim]▣ cover[/dim]" if s.album.cover and Path(s.album.cover).is_file() else ""
        self.query_one("#ed_header", Static).update(f"[b]{t('ed.title')}[/b] · {name} · [dim]{summary}[/dim]{cover}")

    def _row(self, i: int) -> list:
        s = self.session
        tr = s.tracks[i]
        idstyle = "bold #ff5f5f" if tr.is_id else ""
        src = t(SOURCE_LABEL.get(tr.source, "src.id"))
        if tr.source in ("shazam", "acrcloud") and tr.hits:
            src += f" ×{tr.hits}"
        return [
            Text(f"{i + 1:02d}", style="dim"),
            fmt_time(tr.start, 1),
            Text(fmt_time(s.length_of(i)), style="dim"),
            Text(tr.artist, style=idstyle),
            Text(tr.title, style=idstyle),
            tr.remixer,
            Text(tr.label, style="dim"),
            Text(src, style="dim"),
        ]

    def refresh_table(self, select: int | None = None) -> None:
        table = self.query_one("#table", DataTable)
        table.clear()
        for i in range(len(self.session.tracks)):
            table.add_row(*self._row(i), key=str(i))
        self.sel = min(max(0, self.sel if select is None else select), len(self.session.tracks) - 1)
        table.move_cursor(row=self.sel)
        self._header()
        self.load_form()

    def _update_row(self, i: int) -> None:
        table = self.query_one("#table", DataTable)
        for (col, value) in zip(("n", "start", "len", "artist", "title", "remixer", "label", "source"), self._row(i)):
            table.update_cell(str(i), col, value)

    def load_form(self) -> None:
        tr = self.session.tracks[self.sel]
        self._loading = True
        try:
            self.query_one("#f_start", Input).value = fmt_time(tr.start, 2)
            for f in FIELDS:
                self.query_one(f"#f_{f}", Input).value = getattr(tr, f)
        finally:
            self._loading = False

    @on(DataTable.RowSelected)
    def _row_selected(self, event: DataTable.RowSelected) -> None:
        self.action_edit()

    @on(DataTable.RowHighlighted)
    def _row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.sel = event.cursor_row
        self.load_form()

    @on(Input.Changed)
    def _field_changed(self, event: Input.Changed) -> None:
        if self._loading or not event.input.id or not event.input.id.startswith("f_"):
            return
        field = event.input.id[2:]
        if field not in FIELDS:
            return
        tr = self.session.tracks[self.sel]
        setattr(tr, field, event.value)
        if tr.source == "id" and not tr.is_id:
            tr.source = "manual"
        self._update_row(self.sel)
        self._header()
        self.app.autosave(delay=True)

    @on(Input.Submitted)
    def _submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "f_start":
            self._set_start(event.value)
            return
        order = ["f_artist", "f_title", "f_remixer", "f_label"]
        if event.input.id in order[:-1]:
            self.query_one(f"#{order[order.index(event.input.id) + 1]}").focus()
        else:
            self.action_focus_table()

    def _set_start(self, text: str) -> None:
        if self.sel == 0:
            self.app.notify(t("ed.first_cut"), severity="warning")
            self.load_form()
            return
        value = parse_time(text)
        s = self.session
        lo = s.tracks[self.sel - 1].start + 1
        hi = s.end_of(self.sel) - 1
        if value is None or not lo <= value <= hi:
            self.app.notify(t("ed.bad_time"), severity="warning")
            self.load_form()
            return
        s.tracks[self.sel].start = value
        self._after_move()
        self.action_focus_table()

    def _after_move(self) -> None:
        for i in (self.sel - 1, self.sel):
            if 0 <= i < len(self.session.tracks):
                self._update_row(i)
        self.load_form()
        self.app.autosave()

    def action_focus_table(self) -> None:
        self.query_one("#table").focus()

    def action_edit(self) -> None:
        self.query_one("#f_artist").focus()

    def action_play(self) -> None:
        tr = self.session.tracks[self.sel]
        start = max(0.0, tr.start - 6.0)
        self._play(start, 12.0 if self.sel else 10.0, tr.start if self.sel else None)

    @work(thread=True, exclusive=True, group="play")
    def _play(self, start: float, length: float, tick: float | None) -> None:
        try:
            self.app.player.play(self.session.source, start, length, tick)
        except Exception as e:  # noqa: BLE001
            self.app.call_from_thread(self.app.notify, str(e), severity="error")

    def action_nudge(self, delta: float) -> None:
        if self.sel == 0:
            return
        s = self.session
        tr = s.tracks[self.sel]
        new = min(s.end_of(self.sel) - 1, max(s.tracks[self.sel - 1].start + 1, tr.start + delta))
        tr.start = round(new, 3)
        self._after_move()

    def action_split(self) -> None:
        s = self.session
        mid = (s.tracks[self.sel].start + s.end_of(self.sel)) / 2

        def done(text: str | None) -> None:
            if text is None:
                return
            value = parse_time(text)
            if value is None or not 1 <= value <= s.duration - 1:
                self.app.notify(t("ed.bad_time"), severity="warning")
                return
            s.tracks.append(Track(start=value))
            s.normalize()
            self.refresh_table(select=s.index_at(value))
            self.app.autosave()

        self.app.push_screen(PromptModal(t("ed.split_prompt"), fmt_time(mid, 1)), done)

    def action_merge(self) -> None:
        s = self.session
        if self.sel + 1 < len(s.tracks):
            del s.tracks[self.sel + 1]
            self.refresh_table()
            self.app.autosave()

    def action_delete(self) -> None:
        if self.sel == 0:
            self.app.notify(t("ed.first_cut"), severity="warning")
            return
        del self.session.tracks[self.sel]
        self.refresh_table(select=self.sel - 1)
        self.app.autosave()

    def action_mark_id(self) -> None:
        self.session.tracks[self.sel].set_unknown()
        self._update_row(self.sel)
        self.load_form()
        self._header()
        self.app.autosave()

    def action_rescan(self) -> None:
        self._rescan(self.sel)

    @work(exclusive=True, group="rescan")
    async def _rescan(self, i: int) -> None:
        s = self.session
        lo, hi = s.tracks[i].start, s.end_of(i)
        self.app.notify(t("ed.rescanning", a=fmt_time(lo), b=fmt_time(hi)))
        try:
            m = await self.app.get_analyzer().identify_range(lo, hi)
        except RecognitionUnavailable:
            self.app.notify(t("err.shazam"), severity="warning")
            return
        if not m:
            self.app.notify(t("ed.rescan_none"))
            return
        tr = s.tracks[i]
        tr.title, tr.remixer = split_remix(m.title)
        tr.artist, tr.label, tr.isrc, tr.release = m.artist, m.label, m.isrc, m.album
        tr.genre = m.genre
        tr.year = "".join(ch for ch in m.year if ch.isdigit())[:4]
        tr.source, tr.key, tr.apple_id = m.source, m.key, m.apple_id
        tr.hits = max(1, tr.hits)
        self._update_row(i)
        if i == self.sel:
            self.load_form()
        self._header()
        self.app.autosave()
        self.app.notify(t("ed.rescan_ok", x=f"{m.artist} — {m.title}"))

    def action_album(self) -> None:
        def done(album: Album | None) -> None:
            if album:
                self.session.album = album
                self._header()
                self.app.autosave()

        self.app.push_screen(AlbumModal(self.session.album), done)

    def action_import(self) -> None:
        def done(text: str | None) -> None:
            if not text:
                return
            entries = parse_tracklist(text)
            if not entries:
                self.app.notify(t("import.empty"), severity="warning")
                return
            apply_entries(self.session, entries)
            self.refresh_table(select=0)
            self.app.autosave()
            self.app.notify(t("import.done", n=len(entries)))

        self.app.push_screen(ImportModal(), done)

    def action_export(self) -> None:
        if not (self.session.album.artist and self.session.album.title):
            def done(album: Album | None) -> None:
                if album:
                    self.session.album = album
                    self._header()
                    self.app.autosave()
                self.app.push_screen(ExportScreen())

            self.app.push_screen(AlbumModal(self.session.album), done)
            return
        self.app.push_screen(ExportScreen())

    def action_save(self) -> None:
        path = self.app.autosave()
        self.app.notify(t("ed.saved", p=path))

    def action_help(self) -> None:
        self.app.push_screen(HelpModal())

    def action_quit(self) -> None:
        def done(yes: bool | None) -> None:
            if yes:
                self.app.autosave()
                self.app.exit()

        self.app.push_screen(ConfirmModal(t("ed.quit_confirm")), done)


# ──────────────────────────────────────────────────────────────────────── export ──

class ExportScreen(Screen):
    BINDINGS = [Binding("escape", "back", show=False)]

    def compose(self) -> ComposeResult:
        app = self.app
        s = app.session
        out = app.draft.get("out") or app.settings.out_dir or str(Path(s.source).parent)
        with Vertical(id="export"):
            yield Static(f"[b]{t('export.title')}[/b] · {s.album.artist or '—'} — {s.album.title or Path(s.source).stem}",
                         classes="header")
            with Horizontal(classes="row"):
                yield Label(t("f.format"), classes="lbl")
                yield Select(_format_options(app.info.container), value=app.draft.get("format", "same"),
                             allow_blank=False, id="fmt")
            with Horizontal(classes="row"):
                yield Label(t("f.out"), classes="lbl")
                yield Input(out, id="out")
                yield Button(t("b.browse"), id="browse", classes="small")
            yield Static("", id="quality", classes="info")
            with Horizontal(classes="buttons", id="go_row"):
                yield Button(t("b.export"), id="go", variant="primary")
                yield Button(t("b.cancel"), id="back")
            yield ProgressBar(total=1000, show_eta=False, id="bar", classes="hidden")
            yield Static("", id="current", classes="muted")
            yield Static("", id="summary", classes="info")
            with Horizontal(classes="buttons hidden", id="done_row"):
                yield Button(t("b.open_folder"), id="open", variant="primary")
                yield Button(t("b.back_editor"), id="editor")
                yield Button(t("b.quit"), id="quit")

    def on_mount(self) -> None:
        self._quality()
        self.query_one("#go").focus()
        self.folder: Path | None = None

    @on(Select.Changed, "#fmt")
    def _quality(self, event: Select.Changed | None = None) -> None:
        fmt = self.query_one("#fmt", Select).value
        plan = make_plan(self.app.info, fmt if isinstance(fmt, str) else "same")
        self.query_one("#quality", Static).update(f"{plan.fmt.upper()} · {t(plan.quality)}")

    @on(Button.Pressed, "#browse")
    def _browse(self) -> None:
        def done(path: str | None) -> None:
            if path:
                self.query_one("#out", Input).value = path

        self.app.push_screen(FilePicker(t("picker.dir_title"), None, self.query_one("#out", Input).value, True), done)

    @on(Button.Pressed, "#back")
    def action_back(self) -> None:
        if not self.workers:
            self.app.pop_screen()

    @on(Button.Pressed, "#go")
    def _go(self) -> None:
        self.query_one("#go_row").add_class("hidden")
        self.query_one("#bar").remove_class("hidden")
        self.run_export()

    @work(exclusive=True, group="export")
    async def run_export(self) -> None:
        app = self.app
        fmt = self.query_one("#fmt", Select).value
        fmt = fmt if isinstance(fmt, str) else "same"
        root = Path(clean_path(self.query_one("#out", Input).value) or Path(app.session.source).parent)
        app.draft["format"] = fmt
        try:
            res = await export(app.session, app.info, fmt, root, self._progress)
        except Exception as e:  # noqa: BLE001
            app.notify(str(e), severity="error", timeout=12)
            self.query_one("#go_row").remove_class("hidden")
            return
        self.folder = res.folder
        app.autosave()
        self.query_one("#current", Static).update("")
        self.query_one("#summary", Static).update(
            f"[b]{t('export.done', n=len(res.files), size=f'{res.bytes / 1e6:.1f} MB', src=f'{res.input_bytes / 1e6:.1f} MB')}[/b]"
            f"\n[dim]{res.folder}[/dim]")
        self.query_one("#done_row").remove_class("hidden")
        self.query_one("#open").focus()

    def _progress(self, frac: float, name: str) -> None:
        if threading.current_thread() is not threading.main_thread():
            self.app.call_from_thread(self._progress, frac, name)
            return
        self.query_one("#bar", ProgressBar).update(progress=frac * 1000)
        if name:
            self.query_one("#current", Static).update(name)

    @on(Button.Pressed, "#open")
    def _open(self) -> None:
        if self.folder:
            ffmpeg.reveal(self.folder)

    @on(Button.Pressed, "#editor")
    def _editor(self) -> None:
        self.app.pop_screen()

    @on(Button.Pressed, "#quit")
    def _quit(self) -> None:
        self.app.exit()
