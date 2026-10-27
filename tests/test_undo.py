"""Editor undo: grouped steps, ctrl+z / super+z, five-step limit."""
import asyncio

from mixtaper.config import Settings
from mixtaper.model import Album, Session, Track
from mixtaper.tui.app import MixtaperApp
from mixtaper.tui.screens import EditorScreen


def test_undo():
    async def run():
        app = MixtaperApp(Settings())
        app.autosave = lambda *a, **k: None
        app.session = Session(source="/tmp/mix.mp3", duration=600, album=Album(artist="DJ", title="Mix"),
                              tracks=[Track(0, "A", "a"), Track(200, "B", "b"), Track(400, "C", "c")])
        async with app.run_test(size=(120, 36)) as pilot:
            await app.push_screen(EditorScreen())
            await pilot.pause()
            ed = app.screen
            await pilot.press("down")
            await pilot.press("full_stop", "full_stop", "greater_than_sign")
            assert app.session.tracks[1].start == 202.5
            await pilot.press("ctrl+z")                       # three nudges = one step
            assert app.session.tracks[1].start == 200
            await pilot.press("enter", "x", "y")              # typing in one field = one step
            assert app.session.tracks[1].artist == "xy"     # the field selects all on focus
            await pilot.press("super+z")
            assert app.session.tracks[1].artist == "B"
            await pilot.press("escape")
            for _ in range(6):                                # 6 operations, only 5 undoable
                await pilot.press("i")
                await pilot.press("down") if ed.sel < 2 else await pilot.press("up")
            for _ in range(6):
                await pilot.press("ctrl+z")
            assert len(ed.history) == 0
            assert sum(t.is_id for t in app.session.tracks) == 1

    asyncio.run(run())
