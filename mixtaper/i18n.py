"""Italian / English strings."""

from __future__ import annotations

import locale
import os
import subprocess
import sys

_lang = "en"

S: dict[str, tuple[str, str]] = {
    # key: (italiano, english)
    "tagline": ("dal mix alla tracklist, traccia per traccia", "from a mix to a tracklist, track by track"),
    "sec.mix": ("MIX", "MIX"),
    "sec.album": ("ALBUM", "ALBUM"),
    "sec.output": ("OUTPUT", "OUTPUT"),
    "f.source": ("File audio", "Audio file"),
    "f.source.ph": ("trascina qui il mix (mp3, flac, aiff, m4a)", "drop the mix here (mp3, flac, aiff, m4a)"),
    "f.album_artist": ("Artista album", "Album artist"),
    "f.album_artist.ph": ("chi ha fatto il mix", "who made the mix"),
    "f.album": ("Titolo album", "Album title"),
    "f.album.ph": ("titolo del mix", "mix title"),
    "f.year": ("Anno", "Year"),
    "f.genre": ("Genere", "Genre"),
    "f.cover": ("Copertina", "Cover"),
    "f.cover.ph": ("trascina qui un'immagine (jpg, png, webp…)", "drop an image here (jpg, png, webp…)"),
    "f.format": ("Formato", "Format"),
    "f.mode": ("Scansione", "Scan"),
    "f.out": ("Cartella", "Folder"),
    "f.out.ph": ("vuoto = accanto al mix", "empty = next to the mix"),
    "f.compilation": ("Compilation (iTunes)", "Compilation (iTunes)"),
    "fmt.same": ("come l'input", "same as input"),
    "mode.fast": ("veloce", "fast"),
    "mode.balanced": ("bilanciata", "balanced"),
    "mode.accurate": ("accurata", "accurate"),
    "b.browse": ("sfoglia", "browse"),
    "b.analyze": ("Analizza ▸", "Analyze ▸"),
    "b.resume": ("Riprendi sessione", "Resume session"),
    "b.import": ("Importa tracklist", "Import tracklist"),
    "b.settings": ("Impostazioni", "Settings"),
    "b.cancel": ("Annulla", "Cancel"),
    "b.stop": ("Ferma e modifica", "Stop and edit"),
    "b.ok": ("OK", "OK"),
    "b.save": ("Salva", "Save"),
    "b.apply": ("Applica", "Apply"),
    "b.export": ("Esporta ▸", "Export ▸"),
    "b.open_folder": ("Apri cartella", "Open folder"),
    "b.back_editor": ("Torna all'editor", "Back to editor"),
    "b.quit": ("Esci", "Quit"),
    "b.choose_dir": ("Usa questa cartella", "Use this folder"),
    "hint.setup": (
        "I dati dell'album si possono inserire ora o dopo l'analisi.  ctrl+o file · ctrl+l lingua · ctrl+q esci",
        "Album details can be entered now or after the analysis.  ctrl+o file · ctrl+l language · ctrl+q quit",
    ),
    "info.session_found": ("Sessione salvata per questo mix: puoi riprenderla.", "A saved session exists for this mix: you can resume it."),
    "err.no_file": ("Scegli prima un file audio.", "Choose an audio file first."),
    "err.bad_file": ("File non leggibile: {e}", "Cannot read file: {e}"),
    "err.shazam": (
        "Shazam non risponde (troppe richieste). Uso i risultati parziali: puoi riprovare più tardi, quelli già ottenuti restano in cache.",
        "Shazam is not answering (rate limited). Using partial results: retry later, what was found stays cached.",
    ),
    "err.ffmpeg": ("ffmpeg non trovato.", "ffmpeg not found."),
    "phase.decode": ("decodifica audio", "decoding audio"),
    "phase.scan": ("scansione", "scanning"),
    "phase.verify": ("verifica dei buchi", "checking gaps"),
    "phase.refine": ("rifinitura delle transizioni", "refining transitions"),
    "phase.extra": ("secondo motore (ACRCloud)", "second engine (ACRCloud)"),
    "phase.cuts": ("calcolo dei tagli", "placing cuts"),
    "phase.done": ("fatto", "done"),
    "scan.title": ("Analisi", "Analysis"),
    "scan.status": ("{phase} · {pos} / {dur} · {found} tracce · {q} richieste", "{phase} · {pos} / {dur} · {found} tracks · {q} queries"),
    "scan.throttle": ("Shazam chiede di rallentare: attendo {s:.0f}s", "Shazam asks to slow down: waiting {s:.0f}s"),
    "scan.none": ("nessun risultato", "no match"),
    "ed.title": ("Tracklist", "Tracklist"),
    "ed.summary": ("{n} tracce · {ids} ID · {dur}", "{n} tracks · {ids} ID · {dur}"),
    "col.n": ("#", "#"),
    "col.start": ("Inizio", "Start"),
    "col.len": ("Durata", "Length"),
    "col.artist": ("Artista", "Artist"),
    "col.title": ("Titolo", "Title"),
    "col.remixer": ("Remixer", "Remixer"),
    "col.label": ("Etichetta", "Label"),
    "col.source": ("Fonte", "Source"),
    "f.start": ("Inizio", "Start"),
    "f.artist": ("Artista", "Artist"),
    "f.title": ("Titolo", "Title"),
    "f.remixer": ("Remixer", "Remixer"),
    "f.label": ("Etichetta", "Label"),
    "ed.form_hint": (
        "invio: conferma · esc: torna alla lista · remixer 'Nome' = (Nome Remix), 'Nome Edit' resta così",
        "enter: confirm · esc: back to list · remixer 'Name' = (Name Remix), 'Name Edit' stays as is",
    ),
    "k.edit": ("modifica", "edit"),
    "k.play": ("ascolta taglio", "play cut"),
    "k.split": ("dividi", "split"),
    "k.merge": ("unisci", "merge"),
    "k.delete": ("elimina taglio", "remove cut"),
    "k.rescan": ("riconosci", "identify"),
    "k.id": ("segna ID", "mark ID"),
    "k.album": ("album", "album"),
    "k.import": ("importa", "import"),
    "k.export": ("esporta", "export"),
    "k.save": ("salva", "save"),
    "k.settings": ("impostazioni", "settings"),
    "k.lang": ("lingua", "language"),
    "k.help": ("aiuto", "help"),
    "k.quit": ("esci", "quit"),
    "k.nudge": ("sposta taglio", "nudge cut"),
    "ed.saved": ("Sessione salvata: {p}", "Session saved: {p}"),
    "ed.split_prompt": ("Nuovo taglio a (mm:ss):", "New cut at (mm:ss):"),
    "ed.bad_time": ("Tempo non valido.", "Invalid time."),
    "ed.rescanning": ("Riconosco {a}–{b}…", "Identifying {a}–{b}…"),
    "ed.rescan_none": ("Nessun risultato: resta ID.", "No match: stays ID."),
    "ed.rescan_ok": ("Trovato: {x}", "Found: {x}"),
    "ed.first_cut": ("Il primo brano inizia sempre a 0:00.", "The first track always starts at 0:00."),
    "ed.quit_confirm": ("Uscire? La sessione viene salvata automaticamente.", "Quit? The session is saved automatically."),
    "ed.no_album": ("Mancano artista/titolo dell'album: premi 'a' per inserirli.", "Album artist/title missing: press 'a' to add them."),
    "src.shazam": ("Shazam", "Shazam"),
    "src.acrcloud": ("ACRCloud", "ACRCloud"),
    "src.manual": ("manuale", "manual"),
    "src.import": ("importata", "imported"),
    "src.id": ("—", "—"),
    "album.title": ("Dati dell'album", "Album details"),
    "import.title": ("Importa tracklist", "Import tracklist"),
    "import.help": (
        "Incolla una tracklist (es. da 1001tracklists) oppure il percorso di un file .txt/.cue.\n"
        "Righe con orario (00:00 Artista - Titolo) sostituiscono i tagli; senza orario riempiono le tracce in ordine.",
        "Paste a tracklist (e.g. from 1001tracklists) or the path of a .txt/.cue file.\n"
        "Lines with a time (00:00 Artist - Title) replace the cuts; without times they fill the tracks in order.",
    ),
    "import.done": ("Importate {n} righe.", "Imported {n} lines."),
    "import.empty": ("Nessuna riga 'Artista - Titolo' trovata.", "No 'Artist - Title' lines found."),
    "export.title": ("Esportazione", "Export"),
    "export.running": ("Esporto…", "Exporting…"),
    "export.done": ("{n} file · {size} (originale {src})", "{n} files · {size} (original {src})"),
    "q.copy": (
        "Copia diretta dei dati audio: qualità identica all'originale, nessuna ricodifica.",
        "Direct copy of the audio data: identical quality, no re-encoding.",
    ),
    "q.lossless": ("Conversione lossless: qualità identica all'originale.", "Lossless conversion: identical quality."),
    "q.lossy_to_lossless": (
        "Da lossy a lossless: la qualità resta quella dell'originale ma i file pesano molto di più.",
        "Lossy to lossless: quality stays that of the original, but files get much bigger.",
    ),
    "q.to_lossy": (
        "Conversione in formato compresso (320 kbps / 256 kbps AAC): file più leggeri, leggera perdita.",
        "Converting to a compressed format (320 kbps / 256 kbps AAC): smaller files, slight loss.",
    ),
    "q.lossy_to_lossy": (
        "Ricodifica da lossy a lossy: piccola perdita di qualità. Scegli 'come l'input' per evitarla.",
        "Lossy to lossy re-encode: small quality loss. Pick 'same as input' to avoid it.",
    ),
    "picker.title": ("Scegli un file", "Choose a file"),
    "picker.dir_title": ("Scegli una cartella", "Choose a folder"),
    "settings.title": ("Impostazioni", "Settings"),
    "settings.lang": ("Lingua", "Language"),
    "settings.lang.auto": ("automatica", "automatic"),
    "settings.cut": ("Punto di taglio", "Cut point"),
    "settings.cut.start": ("all'ingresso del nuovo brano, come nei CD mixati (consigliato)", "where the next track enters, like mix CDs (recommended)"),
    "settings.cut.blend": ("a metà della transizione", "in the middle of the hand-over"),
    "settings.acr": (
        "ACRCloud (facoltativo, a pagamento): secondo motore per i brani che Shazam non trova",
        "ACRCloud (optional, paid): second engine for tracks Shazam misses",
    ),
    "settings.saved": ("Impostazioni salvate.", "Settings saved."),
    "help.title": ("Comandi", "Keys"),
    "confirm.yes": ("Sì", "Yes"),
    "confirm.no": ("No", "No"),
}


def detect() -> str:
    for var in ("MIXTAPER_LANG", "LC_ALL", "LC_MESSAGES", "LANG"):
        v = os.environ.get(var, "")
        if v and v not in ("C", "POSIX", "C.UTF-8"):
            return "it" if v.lower().startswith("it") else "en"
    if sys.platform == "darwin":
        try:
            out = subprocess.run(["defaults", "read", "-g", "AppleLocale"], capture_output=True, text=True, timeout=2)
            if out.stdout.strip():
                return "it" if out.stdout.strip().lower().startswith("it") else "en"
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        loc = locale.getlocale()[0] or ""
    except ValueError:
        loc = ""
    if os.name == "nt" and not loc:
        try:
            import ctypes

            loc = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "")
        except Exception:
            loc = ""
    return "it" if loc.lower().startswith(("it", "italian")) else "en"


def set_lang(lang: str) -> None:
    global _lang
    _lang = lang if lang in ("it", "en") else "en"


def get_lang() -> str:
    return _lang


def t(key: str, **kw) -> str:
    pair = S.get(key)
    if pair is None:
        return key
    text = pair[0] if _lang == "it" else pair[1]
    return text.format(**kw) if kw else text
