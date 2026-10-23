"""Title / remixer parsing and composition, identity normalization and safe file names."""

from __future__ import annotations

import re
import unicodedata

MIX_WORDS = (
    "remix|rmx|mix|edit|re-edit|reedit|rework|re-work|dub|version|vip|bootleg|refix|"
    "remake|reprise|interpretation|redux|remodel|treatment|flip|reshape|revision"
)
_GENERIC = {
    "original", "extended", "radio", "club", "album", "single", "main", "instrumental",
    "vocal", "dub", "short", "long", "12\"", "7\"", "12''", "12 inch", "7 inch", "12inch",
    "full", "clean", "explicit", "remastered", "remaster", "mono", "stereo", "live",
    "acoustic", "the", "a", "edit", "mix", "version", "special", "alternate", "alt",
    "bonus", "deep", "dj", "lp", "ep", "beats", "tool", "mixed", "unmixed", "continuous",
    "accapella", "acapella", "a cappella", "reprise", "intro", "outro", "vip",
}
_SUFFIX = re.compile(r"\s*[(\[]\s*([^()\[\]]+?)\s+(" + MIX_WORDS + r")\s*[)\]]\s*$", re.I)
_DASH = re.compile(r"\s+[-–]\s+([^-–()\[\]]+?)\s+(" + MIX_WORDS + r")\s*$", re.I)
_ENDS_WITH_WORD = re.compile(r"\b(" + MIX_WORDS + r")\s*$", re.I)
_ORIGINAL_MIX = re.compile(r"\s*[(\[]\s*original( mix)?\s*[)\]]\s*$", re.I)


def _is_generic(name: str) -> bool:
    words = re.split(r"\s+", name.lower().strip())
    return all(w in _GENERIC for w in words)


def split_remix(title: str) -> tuple[str, str]:
    """'Song (feat. X) [Y Remix]' -> ('Song (feat. X)', 'Y').

    Non-'Remix' versions keep their word: 'Song (Y Edit)' -> ('Song', 'Y Edit').
    Generic versions ('Extended Mix', 'Radio Edit') stay in the title.
    """
    title = _ORIGINAL_MIX.sub("", (title or "").strip())
    for rx in (_SUFFIX, _DASH):
        m = rx.search(title)
        if not m:
            continue
        name, word = m.group(1).strip(), m.group(2).strip()
        if _is_generic(name):
            continue
        base = title[: m.start()].rstrip()
        if not base:
            continue
        remixer = name if word.lower() in ("remix", "rmx") else f"{name} {word}"
        return base, remixer
    return title, ""


def full_title(title: str, remixer: str) -> str:
    """Compose the iTunes-style title: 'Song (Y Remix)' / 'Song (feat. X) [Y Remix]'."""
    title = (title or "").strip()
    remixer = (remixer or "").strip()
    if not remixer or remixer.lower() in title.lower():
        return title
    label = remixer if _ENDS_WITH_WORD.search(remixer) else f"{remixer} Remix"
    if title.endswith(")"):
        return f"{title} [{label}]"
    return f"{title} ({label})"


def remixer_name(remixer: str) -> str:
    """'Y Edit' -> 'Y' (for the dedicated REMIXER / TPE4 tag)."""
    return _ENDS_WITH_WORD.sub("", remixer or "").strip()


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[(\[]\s*(feat|ft|featuring|with)\b[^)\]]*[)\]]", " ", s)
    s = re.sub(r"\b(feat|ft|featuring)\b\.?.*$", " ", s)
    s = s.replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "", s)


def identity(artist: str, title: str) -> str:
    """A loose key used to decide whether two recognitions are the same recording."""
    return f"{_fold(artist)}|{_fold(title)}"


_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_filename(name: str, maxlen: int = 140) -> str:
    name = unicodedata.normalize("NFC", name or "")
    name = re.sub(r"[/\\:]", "_", name)
    name = re.sub(r'[*?"<>|\x00-\x1f]', "", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if not name:
        name = "untitled"
    if name.split(".")[0].upper() in _WIN_RESERVED:
        name = "_" + name
    if len(name) > maxlen:
        name = name[:maxlen].rstrip(". ")
    return name
