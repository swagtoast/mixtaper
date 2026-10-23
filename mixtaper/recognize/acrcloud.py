"""Optional ACRCloud back-end (the engine behind trackid-style services).

Used only when the user enters their own project credentials in the settings; it is
queried for the stretches Shazam could not identify.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import aiohttp
import numpy as np

from . import SR, WINDOW, Match, wav_bytes


class ACRCloudClient:
    def __init__(self, session: aiohttp.ClientSession, host: str, key: str, secret: str):
        self._session = session
        self._host = host.replace("https://", "").replace("http://", "").strip("/")
        self._key = key
        self._secret = secret.encode()

    async def identify(self, pcm: np.ndarray) -> Match | None:
        sample = wav_bytes(pcm)
        ts = str(int(time.time()))
        to_sign = "\n".join(["POST", "/v1/identify", self._key, "audio", "1", ts])
        sig = base64.b64encode(hmac.new(self._secret, to_sign.encode(), hashlib.sha1).digest()).decode()
        form = aiohttp.FormData()
        form.add_field("access_key", self._key)
        form.add_field("sample_bytes", str(len(sample)))
        form.add_field("timestamp", ts)
        form.add_field("signature", sig)
        form.add_field("data_type", "audio")
        form.add_field("signature_version", "1")
        form.add_field("sample", sample, filename="sample.wav", content_type="audio/wav")
        try:
            async with self._session.post(
                f"https://{self._host}/v1/identify", data=form, timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                data = await r.json(content_type=None)
        except Exception:
            return None
        if (data.get("status") or {}).get("code") != 0:
            return None
        music = ((data.get("metadata") or {}).get("music") or [])
        if not music:
            return None
        m = music[0]
        artists = ", ".join(a.get("name", "") for a in m.get("artists") or [] if a.get("name"))
        window = len(pcm) / SR or WINDOW
        offset = max(0.0, (m.get("play_offset_ms") or 0) / 1000.0 - window)
        dur = (m.get("duration_ms") or 0) / 1000.0 or None
        genres = m.get("genres") or [{}]
        return Match(
            key=f"acr:{m.get('acrid', '')}",
            title=m.get("title", ""),
            artist=artists,
            offset=offset,
            album=(m.get("album") or {}).get("name", ""),
            label=m.get("label", "") or "",
            year=(m.get("release_date") or "")[:4],
            genre=genres[0].get("name", "") if genres else "",
            isrc=((m.get("external_ids") or {}).get("isrc") or ""),
            duration=dur,
            source="acrcloud",
        )
