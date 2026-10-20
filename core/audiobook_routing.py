"""Send chosen artists' music downloads to the audiobook library instead.

Children's audio plays (Bobby & Max, Die fantastische Fibi, ...) are sold as
albums on the streaming services. They are not in Audible's catalogue, so the
audiobook search never finds them; the music search does, and files them in
the music library next to the songs. A media server that cannot tell them
apart (Navidrome) then shows "Folge 14: Omni braucht Hilfe!" as an album.

A route says "this artist is spoken word". The import still runs the normal
music pipeline (same search, tagging and ``Artist/Album/NN - Title`` layout);
only the ROOT folder changes, from the music library to the audiobook folder.
Audiobookshelf reads ``Artist/Album`` as author and book, so no new layout is
needed.

Routes are keyed on the normalized artist name because the artist is the one
thing every download path knows: playlist sync, wishlist and watchlist scans
all end in the same import context. A per-playlist key would not survive the
wishlist, which forgets which playlist a track came from.

Nothing here moves or deletes a file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("audiobook_routing")

KIND_ARTIST = "artist"


def route_key(name: Any) -> str:
    """The key a route is stored and looked up under: the normalized artist name."""
    from core.worker_utils import normalize_artist_name
    return normalize_artist_name(str(name or ""))


def _database():
    from database.music_database import get_database
    return get_database()


def is_routed_artist(name: Any, database: Any = None) -> bool:
    """True when downloads by this artist go to the audiobook library."""
    key = route_key(name)
    if not key:
        return False
    try:
        return (database or _database()).has_audiobook_route(KIND_ARTIST, key)
    except Exception as exc:  # noqa: BLE001 - a lookup failure must never break an import
        logger.debug("audiobook route lookup failed for %r: %s", name, exc)
        return False


def artist_names_for_context(context: Any) -> List[str]:
    """Every artist name an import context carries, primary first."""
    from core.imports.context import (
        extract_artist_name, get_import_context_artist, get_import_track_info)
    if not isinstance(context, dict):
        return []
    names: List[str] = []
    primary = extract_artist_name(get_import_context_artist(context))
    if primary:
        names.append(primary)
    artists = get_import_track_info(context).get("artists") or []
    if isinstance(artists, (str, dict)):
        artists = [artists]
    for artist in artists:
        name = extract_artist_name(artist)
        if name and name not in names:
            names.append(name)
    return names


def audiobook_folder_configured() -> bool:
    """True once the user has set an audiobook folder.

    ``library_root()`` falls back to ``./audiobooks`` (``/app/audiobooks`` in
    Docker) when nothing is set. That is a folder inside the container: a route
    that wrote there would lose every download on the next rebuild.
    """
    try:
        from core.settings import config_manager
        return bool(str(config_manager.get("library.audiobooks_path", "")
                        or config_manager.get("audiobooks.download_path", "")).strip())
    except Exception:  # noqa: BLE001
        return False


def audiobook_root() -> str:
    """The audiobook library folder, resolved for this container."""
    from core.audiobook_organizer import library_root
    from core.imports.paths import docker_resolve_path
    return docker_resolve_path(library_root())


def routed_root_for_context(context: Any, database: Any = None) -> Optional[str]:
    """The audiobook folder when this import's artist is routed, else None.

    Only the primary (album) artist decides. A guest on a routed artist's track
    must not drag a song into the audiobook folder, and a routed artist
    appearing as a guest on a normal album must not either.
    """
    names = artist_names_for_context(context)
    if not names or not is_routed_artist(names[0], database):
        return None
    if not audiobook_folder_configured():  # never fall back to a folder inside the container
        return None
    return audiobook_root() or None


def routed_names(names: List[str], database: Any = None) -> List[str]:
    """The given names that are routed, as passed in (one query, not one per name)."""
    keys = {route_key(n): n for n in names if route_key(n)}
    if not keys:
        return []
    routed = {r["route_key"] for r in (database or _database()).list_audiobook_routes()
              if r["kind"] == KIND_ARTIST}
    return [n for n in names if route_key(n) in routed]


def list_routes(database: Any = None) -> List[Dict[str, Any]]:
    return (database or _database()).list_audiobook_routes()


def add_route(name: str, database: Any = None) -> bool:
    key = route_key(name)
    if not key:
        return False
    return (database or _database()).add_audiobook_route(KIND_ARTIST, key, str(name).strip())


def remove_route(name: str, database: Any = None) -> bool:
    key = route_key(name)
    if not key:
        return False
    return (database or _database()).remove_audiobook_route(KIND_ARTIST, key)


def existing_download_path(file_path: Any) -> Optional[str]:
    """Where a recorded download still exists, or None.

    A routed download is recorded at the path it was imported to, but a file
    that was filed under the music library before the artist was routed (and
    then moved by hand) keeps its old ``Transfer`` path. The layout inside the
    library is identical, so the same relative path under the audiobook folder
    is the same file.
    """
    import os
    from core.imports.paths import shared_transfer_root
    path = str(file_path or "")
    if not path:
        return None
    if os.path.isfile(path):
        return path
    try:
        transfer = os.path.normpath(shared_transfer_root())
        normalized = os.path.normpath(path)
        if normalized.startswith(transfer + os.sep):
            moved = os.path.join(audiobook_root(), os.path.relpath(normalized, transfer))
            if os.path.isfile(moved):
                return moved
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not map %r into the audiobook folder: %s", path, exc)
    return None


def _music_root() -> str:
    from core.imports.paths import shared_transfer_root
    return shared_transfer_root()


def plan_move(name: str) -> List[Dict[str, str]]:
    """What moving a routed artist's existing albums would do.

    Albums are the folders directly under ``<music library>/<artist>/``; the
    layout is identical in both libraries, so each lands at the same relative
    path under the audiobook folder. Every item says what would happen:
    ``move``, or ``exists`` when the destination is already there (never
    overwritten). Symlinks are left alone.
    """
    import os
    key = route_key(name)
    music, books = os.path.normpath(_music_root()), os.path.normpath(audiobook_root())
    if not key or not os.path.isdir(music) or music == books:
        return []
    items: List[Dict[str, str]] = []
    for artist_dir in sorted(os.listdir(music)):
        artist_path = os.path.join(music, artist_dir)
        if artist_dir.startswith(".") or route_key(artist_dir) != key:
            continue
        if os.path.islink(artist_path) or not os.path.isdir(artist_path):
            continue
        for entry in sorted(os.listdir(artist_path)):
            src = os.path.join(artist_path, entry)
            if os.path.islink(src):
                continue
            dst = os.path.join(books, artist_dir, entry)
            items.append({"src": src, "dst": dst,
                          "action": "exists" if os.path.lexists(dst) else "move"})
    return items


def move_existing(name: str, apply: bool = False) -> Dict[str, Any]:
    """Preview (default) or perform :func:`plan_move`.

    Nothing is overwritten or deleted except the artist folder in the music
    library once it is empty. Failures are reported per item and do not stop
    the others.
    """
    import os
    import shutil
    items = plan_move(name)
    result: Dict[str, Any] = {"applied": bool(apply), "items": items,
                              "moved": 0, "skipped": 0, "failed": 0}
    for item in items:
        if item["action"] != "move":
            result["skipped"] += 1
            continue
        if not apply:
            continue
        try:
            os.makedirs(os.path.dirname(item["dst"]), exist_ok=True)
            shutil.move(item["src"], item["dst"])
            item["action"] = "moved"
            result["moved"] += 1
            write_album_sidecars(item["dst"])
        except OSError as exc:
            item["action"] = "failed"
            item["error"] = str(exc)
            result["failed"] += 1
            logger.warning("could not move %s: %s", item["src"], exc)
    if apply:
        for parent in {os.path.dirname(i["src"]) for i in items}:
            try:
                os.rmdir(parent)  # only succeeds when empty
            except OSError:
                pass
    return result


# ---------------------------------------------------------------------------
# Ownership: what the audiobook library holds
# ---------------------------------------------------------------------------

def routed_library_match(title: Any, artist: Any, database: Any = None,
                         books: Optional[List[Dict[str, Any]]] = None) -> Optional[Dict[str, Any]]:
    """The audiobook-library entry for a routed artist's album, or None.

    A routed album's files sit in the audiobook folder, outside the media
    server's library, so the discography can never find them in ``albums``. The
    audiobook library index (filled by the folder scan) is what actually holds
    them, whoever put them there: the same view Audiobookshelf has.

    Matched on the artist (the book's author) and the title. "Folge 93: ..."
    from a streaming card and "Folge 93: ..." from the tags are the same text,
    but an episode number on both sides must agree, so "Folge 9" never stands in
    for "Folge 90".
    """
    if not is_routed_artist(artist, database):
        return None
    key = route_key(artist)
    wanted = route_key(title)
    if not key or not wanted:
        return None
    if books is None:
        try:
            from core.audiobook_database import get_audiobook_db
            books = get_audiobook_db().get_library()
        except Exception as exc:  # noqa: BLE001 - a lookup failure must never break a page
            logger.debug("audiobook library lookup failed: %s", exc)
            return None
    wanted_number = _EPISODE_NUMBER.match(str(title or ""))
    for book in books:
        if route_key(book.get("author")) != key:
            continue
        if route_key(book.get("title")) == wanted:
            return book
        have_number = _EPISODE_NUMBER.match(str(book.get("title") or ""))
        if wanted_number and have_number and wanted_number.group(1) == have_number.group(1):
            # Same episode, titled slightly differently (punctuation, a subtitle).
            a = route_key(re.sub(_EPISODE_NUMBER, "", str(title or "")))
            b = route_key(re.sub(_EPISODE_NUMBER, "", str(book.get("title") or "")))
            if a and b and (a in b or b in a):
                return book
    return None


# ---------------------------------------------------------------------------
# metadata.opf for routed albums
# ---------------------------------------------------------------------------

# "Folge 16: ...", "Episode 3 - ...", "Band 2: ...": the number a spoken-word
# series puts at the front of an album title.
_EPISODE_NUMBER = re.compile(r"^\s*(?:folge|episode|band|teil|kapitel|vol(?:ume)?\.?)\s*(\d+)\b", re.I)


def album_book_from_folder(folder: Any) -> Optional[Dict[str, Any]]:
    """What the album's own tags say about the book, in the shape ``build_opf`` takes.

    The tags are the source because they are what the import wrote, and they
    survive a hand-move: the same code serves a fresh download, a moved album
    and a backfill. The artist is both author and series (a children's audio
    play is "Bobby & Max, Folge 84"), and the number at the front of the album
    title becomes the position in that series, so a media server can order the
    episodes and group them.

    None when the folder has no tagged audio, or the tags name no artist:
    without an author an OPF would only claim things nobody knows.
    """
    from core.audiobook_organizer import AUDIO_EXTENSIONS
    try:
        files = sorted(p for p in Path(folder).iterdir()
                       if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS)
    except OSError:
        return None
    for path in files:
        tags = _read_tags(path)
        artist = tags.get("albumartist") or tags.get("artist")
        if not artist:
            continue
        title = tags.get("album") or Path(folder).name
        match = _EPISODE_NUMBER.match(title)
        return {
            "title": title,
            "authors": [artist],
            "series": [{"title": artist, "sequence": match.group(1) if match else ""}],
            "release_date": tags.get("date", ""),
        }
    return None


def _read_tags(path: Path) -> Dict[str, str]:
    try:
        from mutagen import File
        audio = File(str(path), easy=True)
        tags = audio.tags if audio is not None else None
        if not tags:
            raise ValueError("no tags")
    except Exception:  # noqa: BLE001 - an unreadable file is just a file without tags
        if path.suffix.lower() != ".mp3":
            return {}
        try:  # an mp3 whose audio mutagen can't parse can still carry readable ID3
            from mutagen.easyid3 import EasyID3
            tags = EasyID3(str(path))
        except Exception:  # noqa: BLE001
            return {}
    return {key: str(tags[key][0]).strip() for key in ("albumartist", "artist", "album", "date")
            if key in tags and tags[key]}


def write_album_sidecars(folder: Any) -> bool:
    """Write ``metadata.opf`` into an album folder. True when the file changed.

    Unchanged content is left alone: rewriting an identical file would bump its
    mtime and have a media server rescan the book for nothing.
    """
    from core.audiobook_post_processor import OPF_NAME, build_opf
    book = album_book_from_folder(folder)
    if book is None:
        return False
    target = Path(folder) / OPF_NAME
    content = build_opf(book)
    try:
        if target.is_file() and target.read_text(encoding="utf-8") == content:
            return False
        tmp = target.with_name(OPF_NAME + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
        return True
    except OSError as exc:
        logger.warning("could not write %s: %s", target, exc)
        return False


def write_album_sidecars_for_file(path: Any) -> bool:
    """Write the sidecar for the album a just-imported file landed in, but only
    when that album is in the audiobook folder; music imports are untouched."""
    if not path:
        return False
    folder = os.path.dirname(os.path.normpath(str(path)))
    books = os.path.normpath(audiobook_root())
    if folder != books and not folder.startswith(books + os.sep):
        return False
    return write_album_sidecars(folder)


def refresh_sidecars(name: str) -> Dict[str, int]:
    """(Re)write ``metadata.opf`` for every album of a routed artist already in the
    audiobook folder, e.g. albums moved there by hand."""
    key = route_key(name)
    books = audiobook_root()
    result = {"written": 0, "unchanged": 0}
    if not key or not os.path.isdir(books):
        return result
    for artist_dir in sorted(os.listdir(books)):
        artist_path = os.path.join(books, artist_dir)
        if artist_dir.startswith(".") or route_key(artist_dir) != key or not os.path.isdir(artist_path):
            continue
        for album in sorted(os.listdir(artist_path)):
            album_path = os.path.join(artist_path, album)
            if album.startswith(".") or not os.path.isdir(album_path):
                continue
            result["written" if write_album_sidecars(album_path) else "unchanged"] += 1
    return result
