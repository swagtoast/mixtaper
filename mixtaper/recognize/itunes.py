"""Track length / genre / year from Apple's public iTunes Lookup API (no key needed).

Shazam returns the Apple Music id of the recording; knowing its full length lets the
scanner predict where each track ends in the mix and jump straight to the next one.
"""

from __future__ import annotations

import asyncio

import aiohttp

_URL = "https://itunes.apple.com/lookup"


class ITunesLookup:
    def __init__(self, session: aiohttp.ClientSession):
        self._session = session
        self._cache: dict[str, dict | None] = {}
        self._lock = asyncio.Lock()

    async def get(self, apple_id: str) -> dict | None:
        if not apple_id:
            return None
        if apple_id in self._cache:
            return self._cache[apple_id]
        async with self._lock:
            if apple_id in self._cache:
                return self._cache[apple_id]
            result = None
            for country in ("US", "GB", "DE"):
                result = await self._lookup(apple_id, country)
                if result:
                    break
            self._cache[apple_id] = result
            return result

    async def _lookup(self, apple_id: str, country: str) -> dict | None:
        for attempt in range(3):
            try:
                async with self._session.get(
                    _URL, params={"id": apple_id, "country": country},
                    timeout=aiohttp.ClientTimeout(total=12),
                ) as r:
                    if r.status in (403, 429):
                        await asyncio.sleep(3 + 3 * attempt)
                        continue
                    if r.status != 200:
                        return None
                    data = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
                await asyncio.sleep(1 + attempt)
                continue
            for item in data.get("results") or []:
                if item.get("wrapperType") == "track" and item.get("trackTimeMillis"):
                    return {
                        "duration": item["trackTimeMillis"] / 1000.0,
                        "genre": item.get("primaryGenreName", ""),
                        "year": (item.get("releaseDate") or "")[:4],
                        "album": item.get("collectionName", ""),
                    }
            return None
        return None
