"""User settings and per-platform storage locations."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path

APP = "mixtaper"


def _base_dir(kind: str) -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        root = home / "Library" / ("Caches" if kind == "cache" else "Application Support")
    elif os.name == "nt":
        var = "LOCALAPPDATA" if kind == "cache" else "APPDATA"
        root = Path(os.environ.get(var) or home)
    else:
        var, default = ("XDG_CACHE_HOME", ".cache") if kind == "cache" else ("XDG_CONFIG_HOME", ".config")
        root = Path(os.environ.get(var) or home / default)
    path = root / APP
    path.mkdir(parents=True, exist_ok=True)
    return path


def cache_dir() -> Path:
    return _base_dir("cache")


def config_dir() -> Path:
    return _base_dir("config")


@dataclass
class Settings:
    lang: str = ""                # "" = follow the system locale
    scan_mode: str = "balanced"   # fast | balanced | accurate
    out_format: str = "same"      # same | mp3 | flac | aiff | m4a
    out_dir: str = ""             # "" = next to the input file
    cut_mode: str = "smart"       # smart | start
    compilation: bool = True
    acr_host: str = ""
    acr_key: str = ""
    acr_secret: str = ""
    last_dir: str = ""

    @classmethod
    def path(cls) -> Path:
        return config_dir() / "settings.json"

    @classmethod
    def load(cls) -> "Settings":
        try:
            raw = json.loads(cls.path().read_text("utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in raw.items() if k in known})

    def save(self) -> None:
        try:
            self.path().write_text(json.dumps(asdict(self), indent=2), "utf-8")
        except OSError:
            pass

    @property
    def acr_enabled(self) -> bool:
        return bool(self.acr_host and self.acr_key and self.acr_secret)
