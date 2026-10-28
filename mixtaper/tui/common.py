"""Small helpers shared by the screens."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from rich.text import Text

from ..i18n import t

LOGO = (
    "╭─────────╮\n"
    "│ ◉ ═══ ◉ │  [b]mixtaper[/b]\n"
    "╰─────────╯  [dim]{tagline}[/dim]\n"
    "             [dim]{build}[/dim]"
)

PALETTE = ["#ff7a1a", "#4fc1ff", "#c792ea", "#7bd88f", "#ffcb6b", "#f78c6c", "#89ddff", "#ff5370",
           "#c3e88d", "#82aaff"]


def clean_path(text: str) -> str:
    """Turn whatever a terminal pastes on drag & drop into a plain path."""
    s = (text or "").strip()
    if not s:
        return ""
    s = s.splitlines()[0].strip()
    if s.startswith("file://"):
        s = unquote(urlparse(s).path)
        if re.match(r"^/[A-Za-z]:/", s):
            s = s[1:]
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "'\"":
        s = s[1:-1]
    elif os.name != "nt":
        s = re.sub(r"\\(.)", r"\1", s)   # "My\ Mix.mp3" -> "My Mix.mp3"
    return os.path.expanduser(s)


def key_bar(items: list[tuple[str, str]]) -> Text:
    """Footer-like line: [key] description · ..."""
    out = Text()
    for i, (k, desc) in enumerate(items):
        if i:
            out.append("  ")
        out.append(f" {k} ", style="bold reverse")
        out.append(" " + t(desc), style="dim")
    return out


def start_dir(*candidates: str) -> str:
    for c in candidates:
        if c:
            p = Path(c).expanduser()
            if p.is_file():
                p = p.parent
            if p.is_dir():
                return str(p)
    return str(Path.home())
