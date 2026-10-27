"""Editor previews and start-time editing."""
import asyncio

from mixtaper.config import Settings
from mixtaper.model import Album, Session, Track
from mixtaper.tui.app import MixtaperApp
from mixtaper.tui.screens import EditorScreen


class FakePlayer:
    def __init__(self):
        self.calls = []

    def play(self, src, start, length, tick_at=None):
        self.calls.append((start, length, tick_at))

    def stop(self):
        pass

    def close(self):
        pass


def test_preview_and_start_edit():
    async def run():
        app = MixtaperApp(Settings())
        app.autosave = lambda *a, **k: None
        app.player = FakePlayer()
        app.session = Session(source="/tmp/mix.wav", duration=600, album=Album(artist="DJ", title="Mix"),
                              tracks=[Track(0, "A", "a"), Track(248.907, "B", "b"), Track(400, "C", "c")])
        async with app.run_test(size=(120, 36)) as pilot:
            await app.push_screen(EditorScreen())
            await pilot.pause()
            await pilot.press("down", "space")
            await app.workers.wait_for_complete()
            assert app.player.calls[-1] == (248.907, 10.0, None)          # space: from the cut
            await pilot.press("p")
            await app.workers.wait_for_complete()
            assert app.player.calls[-1] == (248.907 - 6.0, 10.0, 248.907)  # p: across the cut
            # type a new start and leave the field with Escape, without Enter
            app.screen.query_one("#f_start").focus()
            await pilot.pause()
            app.screen.query_one("#f_start").value = ""
            await pilot.press(*"4:10.5")
            await pilot.press("escape")
            await pilot.pause()
            assert app.session.tracks[1].start == 250.5
            await pilot.press("ctrl+z")
            assert app.session.tracks[1].start == 248.907

    asyncio.run(run())
