"""Run the analyzer on a mix and compare the cuts with a *.truth.json (testing aid)."""
import asyncio
import json
import os
import sys
import time

from mixtaper.analysis import Analyzer
from mixtaper.config import Settings
from mixtaper.media import probe
from mixtaper.model import fmt_time


def gold_truth(folder: str) -> list[dict]:
    """Cut points of an already split release: cumulative track lengths."""
    import glob
    import mutagen
    out, pos = [], 0.0
    for f in sorted(glob.glob(os.path.join(folder, "*"))):
        m = mutagen.File(f)
        if m is None or not getattr(m, "info", None):
            continue
        tags = m.tags or {}
        artist = str((tags.get("\xa9ART") or tags.get("TPE1") or [""])[0])
        title = str((tags.get("\xa9nam") or tags.get("TIT2") or [""])[0])
        out.append({"name": f"{artist} - {title}", "fade_start": pos, "fade_end": pos})
        pos += m.info.length
    return out


def main(path: str, mode: str = "balanced", gold: str = "") -> None:
    info = probe(path)
    t_start = time.time()

    def emit(kind, **kw):
        el = time.time() - t_start
        if kind == "probe":
            m = kw["match"]
            what = f"{m.artist} - {m.title}  off={m.offset:.1f} rate={m.rate:.3f} dur={m.duration}" if m else "—"
            print(f"[{el:6.1f}s] probe {fmt_time(kw['t'], 1):>9} {'(cache) ' if kw['cached'] else ''}{what}", flush=True)
        elif kind == "phase":
            print(f"[{el:6.1f}s] == {kw['name']}", flush=True)
        elif kind == "throttle":
            print(f"[{el:6.1f}s]    throttled {kw['wait']:.1f}s", flush=True)

    a = Analyzer(info, Settings(scan_mode=mode), emit)
    tracks = asyncio.run(a.run())
    print(f"\n{len(tracks)} tracks, {a.queries} queries, {time.time() - t_start:.0f}s\n")
    for i, t in enumerate(tracks):
        print(f"{i + 1:02d} {fmt_time(t.start, 2):>10}  {t.artist} - {t.display_title}  [{t.source} x{t.hits}]")
    if gold:
        truth = gold_truth(gold)
    else:
        try:
            truth = json.load(open(path.rsplit(".", 1)[0] + ".truth.json"))
        except OSError:
            return
    print("\ntruth:")
    for i, g in enumerate(truth):
        mid = (g["fade_start"] + g["fade_end"]) / 2
        near = min(tracks, key=lambda t: abs(t.start - mid))
        ok = g["fade_start"] - 5 <= near.start <= g["fade_end"] + 5
        err = near.start - mid
        print(f"{i + 1:02d} true {fmt_time(g['fade_start'])}-{fmt_time(g['fade_end'])}  cut {fmt_time(near.start, 2):>9} "
              f"({err:+6.1f}s) {'OK ' if ok else 'XX '} {g['name'][:45]:45s} | {near.artist} - {near.title}"[:150])


if __name__ == "__main__":
    main(*sys.argv[1:])
