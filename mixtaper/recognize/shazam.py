"""Shazam recognition.

Signatures are computed locally by shazamio-core (Rust port of the SongRec algorithm);
the request format follows shazamio (https://github.com/shazamio/ShazamIO).
The public endpoint rate-limits aggressively (~1 request / 5 s per IP), so requests are
paced with an AIMD interval that backs off on HTTP 429 and slowly speeds up again.
"""

from __future__ import annotations

import asyncio
import random
import time
import uuid
from typing import Callable

import aiohttp
import numpy as np
from shazamio_core import Recognizer, SearchParams

from . import WINDOW, Match, RecognitionUnavailable, wav_bytes

_URL = (
    "https://amp.shazam.com/discovery/v5/{lang}/{country}/{device}/-/tag/{u1}/{u2}"
    "?sync=true&webv3=true&sampling=true&connected=&shazamapiversion=v3&sharehub=true"
    "&hubv5minorversion=v5.1&hidelb=true&video=v3"
)
_USER_AGENTS = [
    "Dalvik/2.1.0 (Linux; U; Android 5.0.2; VS980 4G Build/LRX22G)",
    "Dalvik/2.1.0 (Linux; U; Android 5.1.1; SM-P905V Build/LMY47X)",
    "Dalvik/2.1.0 (Linux; U; Android 6.0.1; SM-G920F Build/MMB29K)",
    "Dalvik/2.1.0 (Linux; U; Android 7.0; SM-G930F Build/NRD90M)",
]


def parse_response(data: dict) -> Match | None:
    track = data.get("track")
    if not track:
        return None
    m = (data.get("matches") or [{}])[0]
    meta: dict[str, str] = {}
    for section in track.get("sections") or []:
        if section.get("type") == "SONG":
            for item in section.get("metadata") or []:
                meta[item.get("title", "")] = item.get("text", "")
    apple_id = ""
    for action in (track.get("hub") or {}).get("actions") or []:
        if action.get("type") == "applemusicplay" and action.get("id"):
            apple_id = str(action["id"])
            break
    return Match(
        key=str(track.get("key", "")),
        title=track.get("title", "") or "",
        artist=track.get("subtitle", "") or "",
        offset=float(m.get("offset") or 0.0),
        skew=float(m.get("timeskew") or 0.0),
        album=meta.get("Album", ""),
        label=meta.get("Label", ""),
        year=meta.get("Released", ""),
        genre=((track.get("genres") or {}).get("primary") or ""),
        isrc=track.get("isrc", "") or "",
        apple_id=apple_id,
        source="shazam",
    )


class ShazamClient:
    MIN_INTERVAL = 4.0
    MAX_INTERVAL = 40.0
    ATTEMPTS = 12

    def __init__(self, session: aiohttp.ClientSession, lang: str = "en-US", country: str = "GB"):
        self._session = session
        self._lang = lang
        self._country = country
        self._rec = Recognizer(segment_duration_seconds=int(WINDOW))
        self._interval = 4.8
        self._next_slot = 0.0
        self._lock = asyncio.Lock()
        self.on_throttle: Callable[[float], None] | None = None

    async def _pace(self) -> None:
        async with self._lock:
            wait = self._next_slot - time.monotonic()
            if wait > 0:
                if wait > 8 and self.on_throttle:   # only real back-offs, not the normal pacing
                    self.on_throttle(wait)
                await asyncio.sleep(wait)
            self._next_slot = time.monotonic() + self._interval * random.uniform(0.95, 1.1)

    async def identify(self, pcm: np.ndarray) -> Match | None:
        sig = await self._rec.recognize_bytes(wav_bytes(pcm), SearchParams(segment_duration_seconds=int(WINDOW)))
        payload = {
            "timezone": "Europe/Rome",
            "signature": {"uri": sig.signature.uri, "samplems": sig.signature.samples},
            "timestamp": 0,
            "context": {},
            "geolocation": {},
        }
        for attempt in range(self.ATTEMPTS):
            await self._pace()
            payload["timestamp"] = int(time.time() * 1000)
            url = _URL.format(
                lang=self._lang, country=self._country, device=random.choice(("iphone", "android", "web")),
                u1=str(uuid.uuid4()).upper(), u2=str(uuid.uuid4()).upper(),
            )
            headers = {
                "X-Shazam-Platform": "IPHONE",
                "X-Shazam-AppVersion": "14.1.0",
                "Accept": "*/*",
                "Accept-Language": self._lang,
                "Accept-Encoding": "gzip, deflate",
                "User-Agent": random.choice(_USER_AGENTS),
            }
            try:
                async with self._session.post(
                    url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=20)
                ) as r:
                    if r.status == 429:
                        self._interval = min(self.MAX_INTERVAL, self._interval * 1.35 + 0.5)
                        self._next_slot = time.monotonic() + self._interval
                        continue
                    if r.status >= 500:
                        await asyncio.sleep(min(30, 2 + 2 * attempt))
                        continue
                    if r.status != 200:
                        return None
                    data = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                await asyncio.sleep(min(30, 2 + 2 * attempt))
                continue
            self._interval = max(self.MIN_INTERVAL, self._interval * 0.96)
            return parse_response(data or {})
        raise RecognitionUnavailable("shazam")
