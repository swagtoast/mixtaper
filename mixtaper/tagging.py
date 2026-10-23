"""iTunes-style tags for every output format (ID3v2.3 / Vorbis comments / MP4 atoms)."""

from __future__ import annotations

from dataclasses import dataclass

from .titles import remixer_name


@dataclass
class TagSet:
    title: str
    artist: str
    album: str
    album_artist: str
    track: int
    total: int
    disc: int = 1
    discs: int = 1
    year: str = ""
    genre: str = ""
    compilation: bool = False
    remixer: str = ""
    label: str = ""
    isrc: str = ""
    comment: str = ""


def write_tags(path: str, fmt: str, t: TagSet, cover: bytes | None = None, cover_mime: str = "image/jpeg") -> None:
    if fmt == "flac":
        _flac(path, t, cover, cover_mime)
    elif fmt == "m4a":
        _mp4(path, t, cover, cover_mime)
    else:
        _id3(path, fmt, t, cover, cover_mime)


def _id3(path: str, fmt: str, t: TagSet, cover: bytes | None, mime: str) -> None:
    from mutagen.id3 import APIC, COMM, ID3, TALB, TCMP, TCON, TDRC, TIT2, TPE1, TPE2, TPE4, TPOS, TPUB, TRCK, TSRC

    frames = [
        TIT2(encoding=3, text=t.title),
        TPE1(encoding=3, text=t.artist),
        TALB(encoding=3, text=t.album),
        TPE2(encoding=3, text=t.album_artist),
        TRCK(encoding=3, text=f"{t.track}/{t.total}"),
        TPOS(encoding=3, text=f"{t.disc}/{t.discs}"),
    ]
    if t.year:
        frames.append(TDRC(encoding=3, text=t.year))
    if t.genre:
        frames.append(TCON(encoding=3, text=t.genre))
    if t.compilation:
        frames.append(TCMP(encoding=3, text="1"))
    if t.remixer:
        frames.append(TPE4(encoding=3, text=remixer_name(t.remixer)))
    if t.label:
        frames.append(TPUB(encoding=3, text=t.label))
    if t.isrc:
        frames.append(TSRC(encoding=3, text=t.isrc))
    if t.comment:
        frames.append(COMM(encoding=3, lang="eng", desc="", text=t.comment))
    if cover:
        frames.append(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=cover))

    if fmt == "mp3":
        tags = ID3()
        for f in frames:
            tags.add(f)
        tags.save(path, v2_version=3)
        return
    if fmt == "aiff":
        from mutagen.aiff import AIFF as Container
    else:
        from mutagen.wave import WAVE as Container
    audio = Container(path)
    if audio.tags is None:
        audio.add_tags()
    for f in frames:
        audio.tags.add(f)
    audio.save(v2_version=3)


def _flac(path: str, t: TagSet, cover: bytes | None, mime: str) -> None:
    from mutagen.flac import FLAC, Picture

    audio = FLAC(path)
    audio.delete()
    audio.clear_pictures()
    tags = {
        "TITLE": t.title, "ARTIST": t.artist, "ALBUM": t.album, "ALBUMARTIST": t.album_artist,
        "TRACKNUMBER": str(t.track), "TRACKTOTAL": str(t.total), "TOTALTRACKS": str(t.total),
        "DISCNUMBER": str(t.disc), "DISCTOTAL": str(t.discs), "TOTALDISCS": str(t.discs),
        "DATE": t.year, "GENRE": t.genre, "LABEL": t.label, "ISRC": t.isrc, "COMMENT": t.comment,
        "REMIXER": remixer_name(t.remixer), "COMPILATION": "1" if t.compilation else "",
    }
    for k, v in tags.items():
        if v:
            audio[k] = v
    if cover:
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.desc = "Cover"
        pic.data = cover
        audio.add_picture(pic)
    audio.save()


def _mp4(path: str, t: TagSet, cover: bytes | None, mime: str) -> None:
    from mutagen.mp4 import MP4, MP4Cover, MP4FreeForm

    audio = MP4(path)
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    tags.clear()
    tags["\xa9nam"] = [t.title]
    tags["\xa9ART"] = [t.artist]
    tags["\xa9alb"] = [t.album]
    tags["aART"] = [t.album_artist]
    tags["trkn"] = [(t.track, t.total)]
    tags["disk"] = [(t.disc, t.discs)]
    tags["cpil"] = bool(t.compilation)
    tags["pgap"] = True   # gapless album: play the tracks back to back without gaps
    if t.year:
        tags["\xa9day"] = [t.year]
    if t.genre:
        tags["\xa9gen"] = [t.genre]
    if t.comment:
        tags["\xa9cmt"] = [t.comment]
    for key, value in (("REMIXER", remixer_name(t.remixer)), ("LABEL", t.label), ("ISRC", t.isrc)):
        if value:
            tags[f"----:com.apple.iTunes:{key}"] = [MP4FreeForm(value.encode("utf-8"))]
    if cover:
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        tags["covr"] = [MP4Cover(cover, imageformat=fmt)]
    audio.save()
