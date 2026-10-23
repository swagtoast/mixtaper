# mixtaper

Dal mix alla tracklist, traccia per traccia. *From a DJ mix to a tagged album, track by track.*

mixtaper prende un mixtape o un DJ set (MP3, FLAC, AIFF, M4A, WAV), riconosce i brani in stile
trackid.net, lo taglia nei punti di passaggio e produce una cartella di file singoli con metadati
in stile iTunes, come un album della serie *DJ-Kicks*: artista album = chi ha fatto il mix,
album = titolo del mix, e per ogni traccia titolo, artista, remixer, etichetta, copertina.

```
╭─────────╮
│ ◉ ═══ ◉ │  mixtaper
╰─────────╯  dal mix alla tracklist, traccia per traccia
```

## Come funziona

1. **Scansione adattiva.** Shazam restituisce il brano riconosciuto *e* la posizione esatta al suo
   interno, oltre alla velocità di riproduzione (il pitch del DJ). Da un solo campione si ricava
   quindi dove inizia il brano nel mix e, con la durata presa dall'API pubblica di iTunes, dove
   finisce. mixtaper salta da un brano al successivo invece di campionare alla cieca: un mix di
   un'ora richiede poche decine di richieste.
2. **Verifica e bisezione.** I tratti che nessun brano riconosciuto spiega vengono ricontrollati;
   se restano sconosciuti diventano tracce `ID - ID`. Ogni transizione viene ristretta con
   qualche richiesta in più.
3. **Punto di taglio.** Come nei CD mixati commerciali, il taglio cade dove entra il brano
   successivo. Quando non è ricavabile, si usa il passaggio di dominanza timbrica/armonica fra i
   due brani, agganciato alla cassa più vicina.
4. **Esportazione senza perdita.**
   - MP3 → MP3: copia dei frame originali, senza ricodifica, con header LAME gapless. Riprodotti
     uno dopo l'altro, i file ricompongono il mix campione per campione.
   - AAC → M4A: copia dei pacchetti, tagli allineati alla griglia AAC (± 12 ms).
   - FLAC / AIFF / WAV / ALAC: decodifica e codifica lossless, tagli esatti al campione.
   - Conversioni verso altri formati: un solo passaggio di ffmpeg, con avviso sulla qualità.
5. **Metadati e file extra.** Tag ID3v2.3 / Vorbis / MP4 con copertina incorporata, più
   `cover.jpg`, `tracklist.txt`, un file `.cue` per il mix originale, una playlist `.m3u8` e la
   sessione `mixtaper-session.json` per riaprire e correggere il lavoro in seguito.

Sul *DJ-Kicks* di Kruder & Dorfmeister (73 minuti, MP3 da 128 kbps), 15 tagli su 17 cadono entro
un secondo dagli indici del CD originale.

## Avvio

- **macOS:** scarica `Mixtaper-…-macOS-arm64.zip` dalle Release e fai doppio clic su
  `Mixtaper.app` (oppure su `Mixtaper.command`). Se macOS blocca l'app perché scaricata da
  internet: tasto destro → Apri, oppure `xattr -dr com.apple.quarantine Mixtaper.app`.
- **Windows:** scarica `Mixtaper-…-Windows.zip` e avvia `Mixtaper.exe`.
- **Dai sorgenti:** doppio clic su `Mixtaper.command` (macOS/Linux) o `Mixtaper.bat` (Windows).
  Installano [uv](https://github.com/astral-sh/uv) se manca e avviano il programma.

ffmpeg è incluso negli eseguibili e non serve altro.

## Uso

1. Trascina il mix nella finestra (o usa *sfoglia*). Artista album, titolo, anno, genere e
   copertina si possono inserire subito oppure dopo l'analisi.
2. **Analizza ▸**: la timeline mostra i brani man mano che vengono riconosciuti.
3. Nell'editor correggi o completa la tracklist:

| tasto | azione |
|---|---|
| ↑ ↓ / invio | scegli / modifica la traccia (artista, titolo, remixer, etichetta, inizio) |
| spazio | ascolta il taglio (con un piccolo segnale sul punto esatto) |
| `,` `.` / `<` `>` | sposta il taglio di ±0,25 s / ±2 s |
| `s` | dividi: nuovo taglio a un orario |
| `m` / canc | unisci con la successiva / elimina il taglio |
| `r` | riconosci di nuovo il tratto selezionato |
| `i` | segna come `ID - ID` |
| `a` | dati dell'album e copertina |
| `t` | importa una tracklist (testo tipo 1001tracklists o file .cue) |
| `e` | esporta |
| ctrl+s, F2, ctrl+l, ? | salva, impostazioni, lingua, aiuto |

Il remixer si scrive come nome: `Opolopo` diventa *Titolo (Opolopo Remix)*, mentre
`Floating Points Edit` resta *Titolo (Floating Points Edit)*.

### Riga di comando

```bash
mixtaper mix.mp3                                  # interfaccia
mixtaper mix.mp3 --scan-only                      # stampa la tracklist
mixtaper mix.mp3 --headless --artist "DJ" --album "Titolo" --cover cover.jpg --format flac
mixtaper "cartella/mixtaper-session.json" --headless --format m4a   # riesporta una sessione
```

## Impostazioni

- **Scansione:** veloce / bilanciata / accurata (più richieste = transizioni più precise).
- **Punto di taglio:** all'ingresso del nuovo brano (come nei CD) o a metà della transizione.
- **ACRCloud (facoltativo):** con le tue credenziali, un secondo motore interroga i tratti che
  Shazam non riconosce (è il servizio usato da trackid.net; a pagamento).

## Limiti noti

- Shazam limita le richieste (circa una ogni 5 s): l'analisi rallenta da sola quando serve e i
  risultati restano in cache, quindi rianalizzare lo stesso mix è istantaneo.
- Se il database contiene un medley come brano unico, esce una traccia sola: si divide con `s`.
- Nella copia AAC ogni traccia ripete un campione (0,02 ms) del brano precedente. Nella
  conversione verso AAC l'ultimo pacchetto può aggiungere fino a 23 ms di silenzio.

## Build

```bash
pip install . pyinstaller pillow
python packaging/build.py      # crea dist/Mixtaper-<versione>-<sistema>.zip
```

Le build per macOS, Windows e Linux partono su GitHub Actions a ogni tag `v*` e vengono allegate
alla Release.

## Progetti open source usati

[shazamio](https://github.com/shazamio/ShazamIO) e
[shazamio-core](https://github.com/shazamio/shazamio-core) (porting dell'algoritmo di
[SongRec](https://github.com/marin-m/SongRec)), [Textual](https://github.com/Textualize/textual),
[mutagen](https://github.com/quodlibet/mutagen), [FFmpeg](https://ffmpeg.org) tramite
[imageio-ffmpeg](https://github.com/imageio/imageio-ffmpeg), [NumPy](https://numpy.org),
[aiohttp](https://github.com/aio-libs/aiohttp), [PyInstaller](https://pyinstaller.org).

---

**English.** mixtaper recognises the tracks in a DJ mix (Shazam, plus optional ACRCloud), places
each cut where the next track enters, and writes one file per track with iTunes-style tags and
cover art, DJ-Kicks style. MP3 and AAC are split without re-encoding (gapless); lossless formats
are cut sample-exact. Everything happens in a keyboard-driven terminal UI (Italian/English), or
headless from the command line.
