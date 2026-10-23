"""Adaptive scan of a DJ mix: which tracks play, and where the cuts go.

Shazam answers with the recognised track *and* the position inside it (offset) plus the
playback-rate deviation (timeskew). From a single probe we therefore know where the
track's 0:00 falls in the mix (t0) and, with its full length from iTunes, where it ends.
The scanner uses that to jump from track to track instead of sampling blindly, which
matters because the free endpoint only allows roughly one query every five seconds.

Phases
  1. scan    walk the mix, jumping to the predicted end of each recognised track
  2. verify  probe stretches that no recognised track explains (hidden short tracks)
  3. extra   optional ACRCloud pass over what is still unknown
  4. cuts    place every cut inside its transition (see novelty.py)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import aiohttp
import numpy as np

from . import ffmpeg, novelty
from .config import Settings, cache_dir
from .media import MediaInfo
from .model import Track
from .recognize import SR, WINDOW, Match, RecognitionUnavailable
from .recognize.acrcloud import ACRCloudClient
from .recognize.itunes import ITunesLookup
from .recognize.shazam import ShazamClient
from .titles import identity, song_identity, split_remix

# max_jump      longest step between two probes of the same track
# trust_end     believe a track plays to its very end (fast) or verify the stretch (others)
# bisect_to     narrow every transition with extra probes until it is this short (0 = off)
MODES = {
    "fast": {"max_jump": 240.0, "nudge": 30.0, "verify_rounds": 1, "trust_end": True, "bisect_to": 0.0},
    "balanced": {"max_jump": 150.0, "nudge": 20.0, "verify_rounds": 2, "trust_end": False, "bisect_to": 40.0},
    "accurate": {"max_jump": 90.0, "nudge": 15.0, "verify_rounds": 3, "trust_end": False, "bisect_to": 20.0},
}
ID_MIN = 60.0           # an unexplained stretch this long becomes its own "ID - ID" track
TYPICAL_TRACK = 300.0   # used to split very long unidentified stretches
MIN_TRACK = 20.0
HALF = WINDOW / 2

Emit = Callable[..., None]


def file_key(path: str) -> str:
    st = os.stat(path)
    h = hashlib.sha1(f"{st.st_size}".encode())
    with open(path, "rb") as f:
        h.update(f.read(1 << 18))
        f.seek(max(0, st.st_size - (1 << 18)))
        h.update(f.read(1 << 18))
    return h.hexdigest()[:20]


@dataclass
class Occurrence:
    """One continuous appearance of a recording in the mix."""

    ident: str
    hits: list[tuple[float, Match]] = field(default_factory=list)

    def add(self, t: float, m: Match) -> None:
        self.hits.append((t, m))
        self.hits.sort(key=lambda h: h[0])

    @property
    def first(self) -> float:
        return self.hits[0][0]

    @property
    def last(self) -> float:
        return self.hits[-1][0]

    @property
    def rate(self) -> float:
        return statistics.median(m.rate for _, m in self.hits)

    @property
    def t0(self) -> float:
        """Where the track's 0:00 falls in the mix."""
        return statistics.median(t - m.offset / m.rate for t, m in self.hits)

    @property
    def duration(self) -> float | None:
        return next((m.duration for _, m in self.hits if m.duration), None)

    @property
    def end(self) -> float | None:
        d = self.duration
        return self.t0 + d / self.rate if d else None

    def _anchor_cluster(self) -> list[tuple[float, Match]]:
        """Hits whose implied t0 agree (within 1.5 s) with the largest number of other hits."""
        t0s = [t - m.offset / m.rate for t, m in self.hits]
        if len(t0s) == 1:
            return list(self.hits)
        best = max(range(len(t0s)), key=lambda i: sum(abs(t0s[i] - x) <= 1.5 for x in t0s))
        group = [h for h, x in zip(self.hits, t0s) if abs(t0s[best] - x) <= 1.5]
        return group if len(group) >= 2 else []

    @property
    def anchor(self) -> float | None:
        """Reliable t0 (where the track enters), when its hits agree on it."""
        group = self._anchor_cluster()
        return statistics.median(t - m.offset / m.rate for t, m in group) if group else None

    @property
    def anchor_first(self) -> float:
        group = self._anchor_cluster()
        return group[0][0] if group else self.first

    @property
    def anchor_end(self) -> float | None:
        group = self._anchor_cluster()
        if not group:
            return None
        dur = next((m.duration for _, m in group if m.duration), None)
        if not dur:
            return None
        rate = statistics.median(m.rate for _, m in group)
        return statistics.median(t - m.offset / m.rate for t, m in group) + dur / rate

    @property
    def best(self) -> Match:
        keys = [m.key for _, m in self.hits]
        top = max(set(keys), key=keys.count)
        cands = [m for _, m in self.hits if m.key == top]
        return max(cands, key=lambda m: (bool(m.apple_id), bool(m.label), bool(m.year)))


@dataclass
class Slot:
    occ: Occurrence | None      # None = unidentified stretch
    lo: float = 0.0             # rough extent, used for unidentified stretches
    hi: float = 0.0
    nones: list[float] = field(default_factory=list)   # failed probes inside an unidentified stretch

    @property
    def first_probe(self) -> float | None:
        return self.occ.first if self.occ else (min(self.nones) if self.nones else None)

    @property
    def last_probe(self) -> float | None:
        return self.occ.last if self.occ else (max(self.nones) if self.nones else None)


class Analyzer:
    def __init__(self, info: MediaInfo, settings: Settings, emit: Emit | None = None):
        self.info = info
        self.settings = settings
        self.mode = MODES.get(settings.scan_mode, MODES["balanced"])
        self.emit: Emit = emit or (lambda kind, **kw: None)
        self.key = file_key(info.path)
        self.duration = info.duration
        self.pcm: np.ndarray | None = None
        self.probes: dict[float, Match | None] = {}
        self.occs: list[Occurrence] = []
        self.occ_of: dict[float, Occurrence] = {}
        self._cache_path = cache_dir() / "scans" / f"{self.key}.json"
        self._cache = self._load_cache()
        self.queries = 0

    # -- cache -------------------------------------------------------------
    def _load_cache(self) -> dict:
        try:
            data = json.loads(self._cache_path.read_text("utf-8"))
            data.setdefault("probes", {})
            return data
        except (OSError, ValueError):
            return {"probes": {}}

    def _save_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._cache_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._cache), "utf-8")
            os.replace(tmp, self._cache_path)
        except OSError:
            pass

    # -- audio -------------------------------------------------------------
    async def prepare(self) -> None:
        if self.pcm is not None:
            return
        pcm_dir = cache_dir() / "pcm"
        pcm_dir.mkdir(parents=True, exist_ok=True)
        dst = pcm_dir / f"{self.key}.s16"
        if not dst.exists() or dst.stat().st_size == 0:
            self.emit("phase", name="decode")
            await ffmpeg.decode_mono16k(
                self.info.path, dst, self.info.duration, lambda f: self.emit("decode", frac=f)
            )
            _prune_pcm(pcm_dir, keep=dst)
        self.pcm = np.memmap(dst, dtype="<i2", mode="r")
        self.duration = len(self.pcm) / SR

    def window(self, t: float) -> np.ndarray:
        assert self.pcm is not None
        a = max(0, int(t * SR))
        return np.asarray(self.pcm[a: a + int(WINDOW * SR)])

    # -- main entry -----------------------------------------------------------
    async def run(self) -> list[Track]:
        await self.prepare()
        async with aiohttp.ClientSession() as http:
            self._clients(http)
            self.emit("phase", name="scan")
            await self._scan()
            self.emit("phase", name="verify")
            await self._verify()
            await self._refine()
            if self.acr:
                self.emit("phase", name="extra")
                await self._acr_pass()
        self.emit("phase", name="cuts")
        tracks = await asyncio.to_thread(self.build_tracks)
        self.emit("phase", name="done")
        return tracks

    def _clients(self, http: aiohttp.ClientSession) -> None:
        self.shazam = ShazamClient(http)
        self.shazam.on_throttle = lambda w: self.emit("throttle", wait=w)
        self.itunes = ITunesLookup(http)
        s = self.settings
        self.acr = ACRCloudClient(http, s.acr_host, s.acr_key, s.acr_secret) if s.acr_enabled else None

    # -- probing --------------------------------------------------------------
    async def _probe(self, t: float, backend: str = "shazam") -> Match | None:
        t = round(max(0.0, t), 1)
        if t in self.probes and backend == "shazam":
            return self.probes[t]
        ck = f"{t:.1f}" if backend == "shazam" else f"{backend}:{t:.1f}"
        cached = ck in self._cache["probes"]
        if cached:
            raw = self._cache["probes"][ck]
            m = Match.from_dict(raw) if raw else None
        else:
            client = self.shazam if backend == "shazam" else self.acr
            m = await client.identify(self.window(t)) if client else None
            self.queries += 1
            if m:
                await self._enrich(m)
            self._cache["probes"][ck] = m.to_dict() if m else None
            self._save_cache()
        if m or t not in self.probes:
            self.probes[t] = m
        if m:
            self._assign(t, m)
        self.emit("probe", t=t, match=m, cached=cached)
        return m

    async def _enrich(self, m: Match) -> None:
        if m.duration or not m.apple_id:
            return
        info = await self.itunes.get(m.apple_id)
        if info:
            m.duration = info["duration"]
            m.genre = m.genre or info.get("genre", "")
            m.year = m.year or info.get("year", "")

    def _assign(self, t: float, m: Match) -> None:
        ident = identity(m.artist, m.title)
        t0 = t - m.offset / m.rate
        known = sorted(self.occ_of)
        prev = max((p for p in known if p < t), default=None)
        nxt = min((p for p in known if p > t), default=None)
        neighbours = {id(self.occ_of[p]) for p in (prev, nxt) if p is not None}
        chosen = None
        for o in self.occs:
            if o.ident == ident and (id(o) in neighbours or abs(o.t0 - t0) < 30):
                chosen = o
                break
        if chosen is None:
            chosen = Occurrence(ident)
            self.occs.append(chosen)
        old = self.occ_of.get(t)
        if old is not None and old is not chosen:
            old.hits = [h for h in old.hits if h[0] != t]
            if not old.hits:
                self.occs.remove(old)
        if old is not chosen:
            chosen.add(t, m)
        self.occ_of[t] = chosen

    async def _scan(self) -> None:
        last = max(0.0, self.duration - WINDOW)
        t = min(15.0, last)
        nones = 0
        while True:
            self.emit("position", t=t)
            m = await self._probe(t)
            if t >= last:
                break
            if m:
                nones = 0
                occ = self.occ_of[round(t, 1)]
                nxt = t + self.mode["max_jump"]
                end = occ.end
                if end is not None:
                    nxt = min(nxt, max(t + 25.0, end + 8.0))
            else:
                nones += 1
                nxt = t + self.mode["nudge"] * (1 if nones < 3 else 2)
            t = min(nxt, last)
        self.emit("position", t=self.duration)

    def _gap_targets(self) -> list[float]:
        ts = sorted(self.probes)
        out: list[float] = []
        for a, b in zip(ts, ts[1:]):
            if b - a < 40:
                continue
            oa, ob = self.occ_of.get(a), self.occ_of.get(b)
            if oa is not None and oa is ob:
                continue
            if oa is None and ob is None:
                continue
            lo, hi = a + WINDOW, b
            if oa is not None and oa.end is not None and self.mode["trust_end"]:
                lo = max(lo, min(oa.end, b))
            if ob is not None:
                hi = min(hi, max(ob.t0, a + WINDOW))
            if hi - lo >= 45:
                tt = round((lo + hi) / 2 - HALF, 1)
                if all(abs(tt - p) > 10 for p in ts):
                    out.append(tt)
        return out

    async def _verify(self) -> None:
        for _ in range(self.mode["verify_rounds"]):
            targets = self._gap_targets()
            if not targets:
                break
            for tt in targets:
                await self._probe(tt)

    async def _acr_pass(self) -> None:
        for slot in self._slots(self._clean()):
            if slot.occ is None and slot.hi - slot.lo >= ID_MIN:
                mid = (slot.lo + slot.hi) / 2 - HALF
                await self._probe(mid, backend="acrcloud")

    async def identify_range(self, lo: float, hi: float, max_probes: int = 5) -> Match | None:
        """Re-scan one stretch more densely (used from the editor on ID tracks)."""
        await self.prepare()
        span = max(0.0, hi - lo - WINDOW)
        n = max(1, min(max_probes, int(span // 25) + 1))
        ts = [lo + span * (i + 1) / (n + 1) for i in range(n)] if n > 1 else [lo + span / 2]
        found: list[Match] = []
        async with aiohttp.ClientSession() as http:
            self._clients(http)
            for t in ts:
                m = await self._probe(t)
                if not m and self.acr:
                    m = await self._probe(t, backend="acrcloud")
                if m:
                    found.append(m)
        if not found:
            return None
        keys = [identity(m.artist, m.title) for m in found]
        top = max(set(keys), key=keys.count)
        return next(m for m, k in zip(found, keys) if k == top)

    # -- assembling the tracklist -----------------------------------------------
    def _clean(self) -> list[Occurrence]:
        ts = sorted(self.occ_of)
        keep: list[Occurrence] = []
        for o in self.occs:
            if len(o.hits) == 1:
                t = o.first
                i = ts.index(t)
                p = self.occ_of[ts[i - 1]] if i > 0 else None
                n = self.occ_of[ts[i + 1]] if i + 1 < len(ts) else None
                if p is not None and p is n and t - ts[i - 1] < 200 and ts[i + 1] - t < 200:
                    continue  # a lone hit inside another track: a false positive
            keep.append(o)
        keep.sort(key=lambda o: o.first)
        merged: list[Occurrence] = []
        for o in keep:
            if merged and _same_song(merged[-1], o):
                merged[-1] = Occurrence(merged[-1].ident, sorted(merged[-1].hits + o.hits, key=lambda h: h[0]))
            else:
                merged.append(o)
        return merged

    def _nones_between(self, lo: float, hi: float) -> list[float]:
        return [t for t, m in self.probes.items() if m is None and lo < t < hi]

    def _slots(self, occs: list[Occurrence]) -> list[Slot]:
        """Interleave recognised tracks with the stretches nothing explains."""
        D = self.duration
        slots: list[Slot] = []
        prev: Occurrence | None = None
        for o in occs + [None]:  # type: ignore[list-item]
            after = prev.last if prev else -1.0
            lo = 0.0 if prev is None else prev.last + WINDOW
            if o is None:
                hi = D
            elif o.t0 > lo:
                hi = min(o.first, o.t0)       # the next track is audible from its t0 on
            else:
                hi = o.first                  # started mid-track: its entry point is unknown
            nones = [n for n in self._nones_between(after, o.first if o else D + 1) if n < hi - 2]
            if o is None and prev is not None and prev.end is not None:
                # a fading final track often defeats recognition: only what follows its end counts
                nones = [n for n in nones if n >= prev.end - 20]
            if nones and prev is not None and prev.end is not None and prev.end < min(nones):
                lo = max(lo, prev.end)
            if nones and hi - lo >= ID_MIN and (len(nones) >= 2 or hi - lo >= 90):
                slots.append(Slot(None, lo, hi, nones))
            if o is not None:
                slots.append(Slot(o, o.first, o.last + WINDOW))
            prev = o
        if not slots:
            slots.append(Slot(None, 0.0, D, sorted(self.probes)))
        return slots

    async def _refine(self) -> None:
        """Bisect each transition with extra probes so the cut search window is short."""
        target = self.mode["bisect_to"]
        if not target:
            return
        self.emit("phase", name="refine")
        for _ in range(4):
            changed = False
            slots = self._slots(self._clean())
            for a, b in zip(slots, slots[1:]):
                left, right = a.last_probe, b.first_probe
                if left is None or right is None or right - left <= target:
                    continue
                mid = round((left + right) / 2, 1)
                if any(abs(mid - p) < 5 for p in self.probes):
                    continue
                await self._probe(mid)
                changed = True
            if not changed:
                break

    def _cut_between(self, a: Slot, b: Slot) -> float:
        assert self.pcm is not None
        left, right = a.last_probe, b.first_probe
        ref_a = ref_b = None
        if left is None or right is None:
            lo, hi = a.hi - 15, b.lo + 15
        elif right < left:                     # interleaved detections inside a long blend
            lo, hi = right, left + WINDOW
        else:
            lo, hi = left + HALF / 2, right + WINDOW - HALF / 2
            ref_a, ref_b = (left, left + WINDOW), (right, right + WINDOW)
        oa, ob = a.occ, b.occ
        if oa is not None and ob is not None and self.settings.cut_mode != "blend":
            # Commercial mix CDs put the index where the incoming track enters: its t0.
            b0 = ob.anchor
            if b0 is not None and max(oa.last - 20, oa.first + MIN_TRACK) <= b0 <= ob.anchor_first + HALF:
                return b0
            a1 = oa.anchor_end
            if a1 is not None and oa.last <= a1 <= ob.first + HALF:
                return a1
        if ob is not None and lo < ob.t0 < hi:
            if hi - ob.t0 > 2:
                lo = ob.t0                      # the incoming track cannot lead before it starts
        if oa is not None and oa.end is not None and lo + 2 < oa.end < hi:
            hi = oa.end                         # ...and the outgoing one cannot outlast its end
        return novelty.transition_cut(self.pcm, lo, hi, ref_a, ref_b)

    def build_tracks(self) -> list[Track]:
        assert self.pcm is not None
        dropped: set[int] = set()
        for _ in range(5):
            occs = [o for o in self._clean() if id(o.hits[0][1]) not in dropped]
            kept = self._place(self._slots(occs))
            # a lone recognition that ends up owning only a few seconds is a false positive:
            # drop it and place the cuts again without it
            ends = [k[0] for k in kept[1:]] + [self.duration]
            bad = [k[1].occ for k, e in zip(kept, ends) if k[1].occ and len(k[1].occ.hits) == 1 and e - k[0] < 45]
            if not bad or len(bad) == len(kept):
                break
            dropped |= {id(o.hits[0][1]) for o in bad}
        tracks = [_track_from(slot, s) for s, slot in kept]
        tracks[0].start = 0.0
        return tracks

    def _place(self, slots: list[Slot]) -> list[tuple[float, Slot]]:
        starts: list[tuple[float, Slot]] = [(0.0, slots[0])]
        for a, b in zip(slots, slots[1:]):
            starts.append((self._cut_between(a, b), b))
        # long unidentified stretches probably hold several tracks
        extra: list[tuple[float, Slot]] = []
        for i, (s, slot) in enumerate(starts):
            if slot.occ is None:
                e = starts[i + 1][0] if i + 1 < len(starts) else self.duration
                k = int(round((e - s) / TYPICAL_TRACK)) - 1
                if e - s > 1.4 * TYPICAL_TRACK and k > 0:
                    extra += [(t, Slot(None)) for t in novelty.split_points(self.pcm, s, e, k)]
        starts = sorted(starts + extra, key=lambda x: x[0])

        kept: list[tuple[float, Slot]] = []
        for s, slot in starts:
            if s > self.duration - MIN_TRACK and kept:
                continue
            if kept and s - kept[-1][0] < MIN_TRACK:
                if _strength(slot) > _strength(kept[-1][1]) and len(kept) > 1:
                    kept[-1] = (s, slot)
                continue
            kept.append((s, slot))
        return kept


def _strength(slot: Slot) -> int:
    return len(slot.occ.hits) if slot.occ else 0


def _same_song(a: Occurrence, b: Occurrence) -> bool:
    ma, mb = a.best, b.best
    return a.ident == b.ident or song_identity(ma.artist, ma.title) == song_identity(mb.artist, mb.title)


def _track_from(slot: Slot, start: float) -> Track:
    if slot.occ is None:
        return Track(start=round(start, 3))
    m = slot.occ.best
    title, remixer = split_remix(m.title)
    year = "".join(ch for ch in (m.year or "") if ch.isdigit())[:4]
    return Track(
        start=round(start, 3), artist=m.artist or "ID", title=title or "ID", remixer=remixer,
        label=m.label, year=year, genre=m.genre, isrc=m.isrc, release=m.album,
        source=m.source, hits=len(slot.occ.hits), key=m.key, apple_id=m.apple_id,
    )


def _prune_pcm(folder: Path, keep: Path, max_files: int = 3) -> None:
    files = sorted(folder.glob("*.s16"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in files[max_files:]:
        if p != keep:
            try:
                p.unlink()
            except OSError:
                pass


__all__ = ["Analyzer", "MODES", "RecognitionUnavailable", "file_key"]
