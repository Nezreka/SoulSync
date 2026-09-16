"""Audiobook post-processor — tags the chapters and writes the sidecars.

A finished download is a folder of audio files whose tags say whatever the
uploader's ripper said, which is usually the wrong thing: chapter 4 of a
27-hour book tagged as a track on an album called "audiobook". Every audiobook
server (Audiobookshelf, Plex, Jellyfin, Booksonic) reads those tags to decide
what the folder IS, so leaving them alone means a correctly organized library
that still shows up as noise.

What it writes:
  - In-file tags via mutagen: ID3 for mp3/wav, MP4 atoms for m4a/m4b/mp4,
    Vorbis comments for flac/ogg/opus.
  - The cover, embedded and as ``cover.jpg`` beside the files.
  - ``metadata.opf`` — what Audiobookshelf and Plex's audiobook agents read.
  - ``book.nfo`` — what Kodi, Jellyfin and Emby read.

Two sidecars rather than one because the servers genuinely disagree about
which to look for, they are both a few hundred bytes, and a book filed for the
wrong server is a book the user has to re-import by hand.

Narrator goes in the COMPOSER field. That is not a guess: it is the convention
every audiobook server settled on, because there is no "narrator" tag in ID3
and composer is the one free slot nothing else in an audiobook uses.

Best effort throughout. Every step is guarded on its own, and a failure here
logs and moves on. The files are already safely in the library by the time
this runs, so nothing it does is worth losing a book over.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

from utils.logging_config import get_logger

logger = get_logger("audiobook_post_processor")

# Written next to the audio so a folder carries its own identity. The library
# scan reads the asin back out of these, which is what lets a re-scan
# recognise a book SoulSync imported months ago without asking Audible.
OPF_NAME = "metadata.opf"
NFO_NAME = "book.nfo"
COVER_NAME = "cover.jpg"

# Mirrors core/settings.py. There is no deep merge of new defaults into an
# existing config row, so every read has to carry its own default or an
# install that predates these keys reads None and turns everything off.
_DEFAULTS = {
    "embed_metadata": True,
    "embed_artwork": True,
    "save_artwork": True,
    "write_nfo": True,
}


def settings() -> Dict[str, bool]:
    """The four post-processing switches, with defaults that survive an old config."""
    values = dict(_DEFAULTS)
    try:
        from core.settings import config_manager
        for key, fallback in _DEFAULTS.items():
            values[key] = bool(config_manager.get(f"audiobooks.{key}", fallback))
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read audiobook post-processing settings: %s", exc)
    return values


# ---------------------------------------------------------------------------
# Reading a book dict
# ---------------------------------------------------------------------------

def _first(values: Any) -> str:
    """First non-empty entry of a list of names or name dicts."""
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("name")
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _names(values: Any) -> List[str]:
    out: List[str] = []
    for value in values or []:
        if isinstance(value, dict):
            value = value.get("name")
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def book_facts(book: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten a catalogue book into the handful of fields tagging needs.

    Accepts either shape the rest of the subsystem passes around: the full
    ``to_dict()`` payload, or the thinner row the download monitor falls back
    to when the catalogue could not be reached at import time.
    """
    book = book or {}
    authors = _names(book.get("author_names") or book.get("authors"))
    narrators = _names(book.get("narrator_names") or book.get("narrators"))

    series_list = book.get("series") or []
    series = series_list[0] if series_list else {}
    if not isinstance(series, dict):
        series = {}

    release_date = str(book.get("release_date") or "").strip()
    year = release_date[:4] if len(release_date) >= 4 and release_date[:4].isdigit() else ""

    genres = [str(g).strip() for g in (book.get("genres") or []) if str(g).strip()]

    summary = str(book.get("summary") or book.get("short_summary") or "").strip()

    return {
        "asin": str(book.get("asin") or "").strip(),
        "title": str(book.get("title") or "").strip() or "Unknown Title",
        "subtitle": str(book.get("subtitle") or "").strip(),
        "authors": authors,
        "author": authors[0] if authors else "",
        "narrators": narrators,
        # One narrator per book is the rule the grab side enforces, so joining
        # here would only ever paper over a mixed folder. Take the first.
        "narrator": narrators[0] if narrators else "",
        "series": str(series.get("title") or "").strip(),
        "series_sequence": str(series.get("sequence") or "").strip(),
        "publisher": str(book.get("publisher") or "").strip(),
        "summary": summary,
        "release_date": release_date,
        "year": year,
        "genres": genres,
        # "Audiobook" rather than nothing: a genre-less file lands in a media
        # server's uncategorised bucket, which is where books go to be lost.
        "genre": genres[0] if genres else "Audiobook",
        "language": str(book.get("language") or "").strip(),
        "runtime_minutes": int(book.get("runtime_minutes") or 0),
        "cover_url": str(book.get("cover_url_large") or book.get("cover_url") or "").strip(),
    }


# ---------------------------------------------------------------------------
# Sidecars
# ---------------------------------------------------------------------------

def build_opf(book: Dict[str, Any]) -> str:
    """A Calibre-flavoured OPF package, which is what audiobook servers read.

    The asin is written as a scheme-tagged identifier so the library scan can
    read it back and know exactly which book a folder holds, without guessing
    from the folder name.
    """
    facts = book_facts(book)
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uuid_id">',
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:opf="http://www.idpf.org/2007/opf">',
        f'    <dc:title>{escape(facts["title"])}</dc:title>',
    ]
    if facts["subtitle"]:
        lines.append(f'    <dc:subtitle>{escape(facts["subtitle"])}</dc:subtitle>')
    for author in facts["authors"]:
        lines.append(
            f'    <dc:creator opf:role="aut" opf:file-as="{escape(author)}">'
            f'{escape(author)}</dc:creator>'
        )
    for narrator in facts["narrators"]:
        lines.append(f'    <dc:creator opf:role="nrt">{escape(narrator)}</dc:creator>')
    if facts["publisher"]:
        lines.append(f'    <dc:publisher>{escape(facts["publisher"])}</dc:publisher>')
    if facts["summary"]:
        lines.append(f'    <dc:description>{escape(facts["summary"])}</dc:description>')
    if facts["release_date"]:
        lines.append(f'    <dc:date>{escape(facts["release_date"])}</dc:date>')
    if facts["language"]:
        lines.append(f'    <dc:language>{escape(facts["language"])}</dc:language>')
    for genre in facts["genres"]:
        lines.append(f'    <dc:subject>{escape(genre)}</dc:subject>')
    if facts["asin"]:
        lines.append(f'    <dc:identifier opf:scheme="ASIN">{escape(facts["asin"])}</dc:identifier>')
    if facts["series"]:
        lines.append(f'    <meta name="calibre:series" content="{escape(facts["series"])}"/>')
        if facts["series_sequence"]:
            lines.append(
                f'    <meta name="calibre:series_index" '
                f'content="{escape(facts["series_sequence"])}"/>'
            )
    lines.append('    <meta name="soulsync:media_type" content="audiobook"/>')
    lines += ['  </metadata>', '</package>', '']
    return "\n".join(lines)


def build_nfo(book: Dict[str, Any]) -> str:
    """An ``<album>`` document, which is the shape Kodi and Jellyfin expect.

    An audiobook has no NFO type of its own in that world, and album is the
    honest fit: one work, ordered parts, one set of shared credits.
    """
    facts = book_facts(book)
    lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<album>',
        f'  <title>{escape(facts["title"])}</title>',
    ]
    if facts["subtitle"]:
        lines.append(f'  <subtitle>{escape(facts["subtitle"])}</subtitle>')
    for author in facts["authors"]:
        lines.append(f'  <artist>{escape(author)}</artist>')
    if facts["author"]:
        lines.append(f'  <albumartist>{escape(facts["author"])}</albumartist>')
    for narrator in facts["narrators"]:
        lines.append(f'  <composer>{escape(narrator)}</composer>')
    if facts["year"]:
        lines.append(f'  <year>{escape(facts["year"])}</year>')
    if facts["release_date"]:
        lines.append(f'  <releasedate>{escape(facts["release_date"])}</releasedate>')
    for genre in facts["genres"]:
        lines.append(f'  <genre>{escape(genre)}</genre>')
    if facts["publisher"]:
        lines.append(f'  <label>{escape(facts["publisher"])}</label>')
    if facts["summary"]:
        lines.append(f'  <review>{escape(facts["summary"])}</review>')
    if facts["runtime_minutes"]:
        lines.append(f'  <duration>{facts["runtime_minutes"] * 60}</duration>')
    if facts["asin"]:
        lines.append(f'  <uniqueid type="asin" default="true">{escape(facts["asin"])}</uniqueid>')
    lines += ['  <type>Audiobook</type>', '</album>', '']
    return "\n".join(lines)


def read_asin_from_folder(folder: Path) -> str:
    """The asin a previous import recorded here, or "" if it never did.

    Reads the sidecars this module writes. Cheap string matching rather than an
    XML parse: the file may have been hand-edited into something that does not
    parse, and one malformed sidecar must not stop a whole library scan.
    """
    folder = Path(folder)

    opf = folder / OPF_NAME
    try:
        text = opf.read_text(encoding="utf-8", errors="ignore")
        marker = 'opf:scheme="ASIN">'
        if marker in text:
            value = text.split(marker, 1)[1].split("<", 1)[0].strip()
            if value:
                return value
    except OSError:
        pass

    nfo = folder / NFO_NAME
    try:
        text = nfo.read_text(encoding="utf-8", errors="ignore")
        marker = 'type="asin"'
        if marker in text:
            tail = text.split(marker, 1)[1]
            value = tail.split(">", 1)[1].split("<", 1)[0].strip() if ">" in tail else ""
            if value:
                return value
    except OSError:
        pass

    return ""


# ---------------------------------------------------------------------------
# Artwork
# ---------------------------------------------------------------------------

def fetch_cover(url: str) -> Optional[Tuple[bytes, str]]:
    """Cover bytes and mime type, or None.

    Borrows the podcast fetcher rather than growing a second one: same job,
    same headers, same failure handling.
    """
    if not url:
        return None
    try:
        from core.podcast_post_processor import fetch_artwork_bytes
        return fetch_artwork_bytes(url)
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not fetch the cover from %s: %s", url, exc)
        return None


def existing_cover(folder: Path) -> Optional[Tuple[bytes, str]]:
    """A cover the release already shipped, so a book can be tagged offline.

    Uploaders normally include one, and reading it costs nothing next to a
    round trip that may not be available at import time anyway.
    """
    for name in ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.png"):
        candidate = Path(folder) / name
        try:
            if candidate.is_file() and candidate.stat().st_size > 100:
                mime = "image/png" if candidate.suffix.lower() == ".png" else "image/jpeg"
                return candidate.read_bytes(), mime
        except OSError:
            continue
    return None


def save_cover(folder: Path, art: Optional[Tuple[bytes, str]]) -> str:
    """Write ``cover.jpg`` beside the audio. Returns the path, or ""."""
    if not art:
        return ""
    data, _mime = art
    target = Path(folder) / COVER_NAME
    if target.exists():
        return str(target)
    try:
        target.write_bytes(data)
        return str(target)
    except OSError as exc:
        logger.debug("Could not save the cover in %s: %s", folder, exc)
        return ""


# ---------------------------------------------------------------------------
# Tagging
# ---------------------------------------------------------------------------

def chapter_title(facts: Dict[str, Any], index: int, total: int) -> str:
    """What a single file should call itself.

    A book split into parts gets numbered part titles, because a player that
    shows only the tag has to be able to tell part 3 from part 12. A book that
    is one file just uses the title.
    """
    if total <= 1:
        return facts["title"]
    width = max(2, len(str(total)))
    return f"{facts['title']} - Part {index:0{width}d}"


def embed_tags(
    path: Path,
    book: Dict[str, Any],
    index: int = 1,
    total: int = 1,
    art: Optional[Tuple[bytes, str]] = None,
    embed_artwork: bool = True,
) -> bool:
    """Write audiobook tags into one file. True when something was saved."""
    path = Path(path)
    if not path.is_file():
        return False

    facts = book_facts(book)
    title = chapter_title(facts, index, total)
    art_bytes, art_mime = (art or (None, None))

    try:
        from mutagen import File as MutagenFile
        from mutagen.flac import FLAC, Picture
        from mutagen.id3 import (
            APIC,
            COMM,
            ID3,
            TALB,
            TCOM,
            TCON,
            TDRC,
            TIT2,
            TPE1,
            TPE2,
            TPOS,
            TPUB,
            TRCK,
            TXXX,
        )
        from mutagen.mp4 import MP4, MP4Cover

        audio = MutagenFile(str(path))
        if audio is None:
            logger.debug("Mutagen could not read %s", path.name)
            return False
        if audio.tags is None:
            try:
                audio.add_tags()
            except Exception as exc:                        # noqa: BLE001
                # Some containers already carry tags mutagen reports as None.
                # Tagging below still works, so this is a note, not a failure.
                logger.debug("add_tags refused for %s: %s", path.name, exc)

        if isinstance(audio.tags, ID3):
            tags: ID3 = audio.tags
            for frame in ("TIT2", "TALB", "TPE1", "TPE2", "TCOM", "TRCK",
                          "TCON", "TDRC", "TPUB", "TPOS"):
                tags.delall(frame)

            tags.add(TIT2(encoding=3, text=[title]))
            tags.add(TALB(encoding=3, text=[facts["title"]]))
            if facts["author"]:
                tags.add(TPE1(encoding=3, text=[", ".join(facts["authors"])]))
                tags.add(TPE2(encoding=3, text=[facts["author"]]))
            if facts["narrator"]:
                tags.add(TCOM(encoding=3, text=[facts["narrator"]]))
            tags.add(TRCK(encoding=3, text=[f"{index}/{total}"]))
            tags.add(TCON(encoding=3, text=[facts["genre"]]))
            if facts["release_date"]:
                tags.add(TDRC(encoding=3, text=[facts["release_date"]]))
            if facts["publisher"]:
                tags.add(TPUB(encoding=3, text=[facts["publisher"]]))
            if facts["summary"]:
                tags.delall("COMM")
                tags.add(COMM(encoding=3, lang="eng", desc="Description",
                              text=[facts["summary"]]))

            # Series lives in TXXX because ID3 has nowhere else for it, and
            # these are the exact keys Audiobookshelf and Beets look for.
            for desc, value in (("SERIES", facts["series"]),
                                ("SERIES-PART", facts["series_sequence"]),
                                ("ASIN", facts["asin"]),
                                ("NARRATOR", facts["narrator"])):
                tags.delall(f"TXXX:{desc}")
                if value:
                    tags.add(TXXX(encoding=3, desc=desc, text=[value]))

            if embed_artwork and art_bytes:
                tags.delall("APIC")
                tags.add(APIC(encoding=3, mime=art_mime or "image/jpeg", type=3,
                              desc="Cover", data=art_bytes))

        elif isinstance(audio, MP4):
            audio["\xa9nam"] = [title]
            audio["\xa9alb"] = [facts["title"]]
            if facts["author"]:
                audio["\xa9ART"] = [", ".join(facts["authors"])]
                audio["aART"] = [facts["author"]]
            if facts["narrator"]:
                audio["\xa9wrt"] = [facts["narrator"]]
            audio["trkn"] = [(index, total)]
            audio["\xa9gen"] = [facts["genre"]]
            if facts["release_date"]:
                audio["\xa9day"] = [facts["release_date"]]
            if facts["publisher"]:
                audio["\xa9pub"] = [facts["publisher"]]
            if facts["summary"]:
                audio["\xa9cmt"] = [facts["summary"][:255]]
                audio["desc"] = [facts["summary"][:255]]
                audio["ldes"] = [facts["summary"]]
            # stik 2 is Apple's "Audiobook" media kind, and pgap keeps parts
            # from clicking at the joins. Both are what makes an m4b behave
            # like a book instead of an album in every Apple-lineage player.
            audio["stik"] = [2]
            audio["pgap"] = [True]
            if facts["series"]:
                audio["\xa9grp"] = [facts["series"]]
            if embed_artwork and art_bytes:
                fmt = MP4Cover.FORMAT_PNG if (art_mime and "png" in art_mime) \
                    else MP4Cover.FORMAT_JPEG
                audio["covr"] = [MP4Cover(art_bytes, imageformat=fmt)]

        elif hasattr(audio, "tags") and hasattr(audio.tags, "__setitem__"):
            audio["TITLE"] = [title]
            audio["ALBUM"] = [facts["title"]]
            if facts["author"]:
                audio["ARTIST"] = [", ".join(facts["authors"])]
                audio["ALBUMARTIST"] = [facts["author"]]
            if facts["narrator"]:
                audio["COMPOSER"] = [facts["narrator"]]
                audio["NARRATOR"] = [facts["narrator"]]
            audio["TRACKNUMBER"] = [str(index)]
            audio["TRACKTOTAL"] = [str(total)]
            audio["GENRE"] = [facts["genre"]]
            if facts["release_date"]:
                audio["DATE"] = [facts["release_date"]]
            if facts["publisher"]:
                audio["ORGANIZATION"] = [facts["publisher"]]
            if facts["summary"]:
                audio["DESCRIPTION"] = [facts["summary"]]
            if facts["series"]:
                audio["SERIES"] = [facts["series"]]
                if facts["series_sequence"]:
                    audio["SERIES-PART"] = [facts["series_sequence"]]
            if facts["asin"]:
                audio["ASIN"] = [facts["asin"]]

            if embed_artwork and art_bytes and isinstance(audio, FLAC):
                try:
                    audio.clear_pictures()
                    picture = Picture()
                    picture.data = art_bytes
                    picture.type = 3
                    picture.mime = art_mime or "image/jpeg"
                    picture.desc = "Cover"
                    audio.add_picture(picture)
                except Exception as exc:                    # noqa: BLE001
                    logger.debug("Could not embed the cover in %s: %s", path.name, exc)
        else:
            logger.debug("No tag format matched %s", path.name)
            return False

        # Reuse the shared atomic writer so a crash mid-save cannot leave a
        # truncated audio file behind.
        from types import SimpleNamespace

        from core.metadata.common import save_audio_file
        save_audio_file(audio, SimpleNamespace(ID3=ID3, FLAC=FLAC, File=MutagenFile))
        return True

    except Exception as exc:                                # noqa: BLE001
        logger.warning("Could not tag %s: %s", path.name, exc)
        return False


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def post_process_book(
    folder: str,
    book: Dict[str, Any],
    files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Tag a freshly organized book and write its sidecars.

    ``files`` is what the organizer actually wrote, in order. When it is not
    given the folder is read instead, which is what makes re-running this over
    an already filed book work.

    Returns a report rather than raising. The caller has a book on disk either
    way, and the worst outcome of a failure here is a folder a media server
    labels badly.
    """
    report = {"tagged": 0, "failed": 0, "cover": "", "sidecars": [], "skipped": False}

    path = Path(folder)
    if not path.is_dir():
        report["skipped"] = True
        return report

    options = settings()
    if not any(options.values()):
        report["skipped"] = True
        return report

    from core.audiobook_organizer import collect_audio_files

    if files:
        audio = [Path(f) for f in files if Path(f).is_file()]
    else:
        audio = collect_audio_files(path)
    audio.sort(key=lambda p: p.name.lower())

    facts = book_facts(book)

    # Prefer the cover already in the folder. It is free, it is what the
    # release shipped, and it means an import still gets art when Audible is
    # unreachable.
    art = None
    if options["embed_artwork"] or options["save_artwork"]:
        art = existing_cover(path) or fetch_cover(facts["cover_url"])

    if options["save_artwork"]:
        report["cover"] = save_cover(path, art)

    if options["embed_metadata"]:
        total = len(audio)
        for index, item in enumerate(audio, start=1):
            if embed_tags(item, book, index, total, art,
                          embed_artwork=options["embed_artwork"]):
                report["tagged"] += 1
            else:
                report["failed"] += 1

    if options["write_nfo"]:
        for name, builder in ((OPF_NAME, build_opf), (NFO_NAME, build_nfo)):
            target = path / name
            try:
                target.write_text(builder(book), encoding="utf-8")
                report["sidecars"].append(str(target))
            except OSError as exc:
                logger.debug("Could not write %s: %s", target, exc)

    logger.info("Post-processed %s: %d tagged, %d failed, %d sidecars",
                facts["title"], report["tagged"], report["failed"],
                len(report["sidecars"]))
    return report
