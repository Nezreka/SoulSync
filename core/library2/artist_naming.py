"""What an artist is filed and tagged as, for the artist page (#2165).

The rule lives in ``core.imports.library_names``: a download is named after
the library artist it belongs to. This reads it back for one artist so the
page can say it plainly: what the download source calls them, what the
library calls them, whether that name is pinned, and the folder a new album
goes to.
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.artist_naming")

# what a source calls an artist barely ever changes; one ask an hour, hit or miss
_SOURCE_TTL = 3600.0
_SOURCE_WAIT = 4.0
_source_names: Dict[tuple, tuple] = {}
_source_lock = threading.Lock()


def clear_cache() -> None:
    with _source_lock:
        _source_names.clear()


def _ask_source(provider: str, artist_id: str) -> str:
    """the source's own name for the artist, '' when it can't say."""
    from core.metadata.registry import get_client_for_source
    client = get_client_for_source(provider)
    if client is None:
        return ""
    for method in ("get_artist_info", "get_artist"):
        getter = getattr(client, method, None)
        if not callable(getter):
            continue
        found = getter(str(artist_id))
        name = found.get("name") if isinstance(found, dict) else getattr(found, "name", "")
        if name:
            return str(name).strip()
    return ""


def source_name(provider: str, artist_id: str,
                ask: Optional[Callable[[str, str], str]] = None) -> str:
    """What ``provider`` calls the artist with ``artist_id``, cached an hour.
    Never waits on a slow source for more than a few seconds."""
    if not provider or not artist_id:
        return ""
    key = (provider, str(artist_id))
    now = time.monotonic()
    with _source_lock:
        hit = _source_names.get(key)
        if hit and now - hit[0] < _SOURCE_TTL:
            return hit[1]
    ask = ask or _ask_source
    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    try:
        name = pool.submit(ask, provider, str(artist_id)).result(timeout=_SOURCE_WAIT) or ""
    except Exception as exc:  # noqa: BLE001 - the page shows the rest without it
        logger.debug("source name for %s %s unavailable: %s", provider, artist_id, exc)
        name = ""
    finally:
        pool.shutdown(wait=False)
    with _source_lock:
        if len(_source_names) > 2048:
            _source_names.clear()
        _source_names[key] = (now, name)
    return name


def _example_folder(conn, artist_id: int, name: str) -> Optional[str]:
    """the folder the artist's newest album goes to under the album template,
    None when file organization is off."""
    row = conn.execute(
        "SELECT title, year, release_date FROM lib2_albums WHERE primary_artist_id=? "
        "ORDER BY COALESCE(release_date, CAST(year AS TEXT), '') DESC, id DESC LIMIT 1",
        (int(artist_id),)).fetchone()
    from core.imports.paths import get_file_path_from_template
    year = ""
    if row is not None:
        year = str(row["year"] or "") or str(row["release_date"] or "")[:4]
    folder, _file = get_file_path_from_template({
        "artist": name, "albumartist": name,
        "album": (row["title"] if row is not None else "") or "Album",
        "title": "Song", "track_number": 1, "disc_number": 1, "total_discs": 1,
        "year": year, "albumtype": "Album", "atypes": "",
    }, "album_path")
    if not folder:
        return None
    return folder.replace("\\", "/")


def artist_naming(conn, artist_id: int, *, primary_source: str = "",
                  ask: Optional[Callable[[str, str], str]] = None) -> Optional[Dict[str, Any]]:
    """The artist page's "Folder and tag name" facts, None for no such artist."""
    row = conn.execute(
        "SELECT id, name FROM lib2_artists WHERE id=?", (int(artist_id),)).fetchone()
    if row is None:
        return None
    from core.imports.library_names import (
        _fold,
        _pinned,
        _stored_ids,
        library_artist_name,
        provider_of,
    )
    from core.library2.metadata_overrides import effective_artist_name
    from core.metadata.registry import METADATA_SOURCE_LABELS, METADATA_SOURCE_PRIORITY

    scanned = str(row["name"] or "")
    library_name = str(effective_artist_name(conn, artist_id, scanned) or scanned)
    pin = _pinned(conn, [artist_id]).get(int(artist_id))
    ids = _stored_ids(conn, [artist_id]).get(int(artist_id), {})

    # the source downloads take their names from first, then any other one
    # the artist has an id for
    order = [provider_of(primary_source)] + [provider_of(s) for s in METADATA_SOURCE_PRIORITY]
    source = None
    for provider in dict.fromkeys(p for p in order if p):
        if ids.get(provider):
            name = source_name(provider, ids[provider], ask)
            if name:
                source = {
                    "provider": provider,
                    "label": METADATA_SOURCE_LABELS.get(provider, provider.title()),
                    "id": ids[provider],
                    "name": name,
                }
                break

    try:
        folder = _example_folder(conn, artist_id, library_name)
    except Exception as exc:  # noqa: BLE001 - the page shows the names without it
        logger.debug("example folder for artist %s failed: %s", artist_id, exc)
        folder = None

    differs = bool(source and _fold(source["name"]) != _fold(library_name))
    # the rule itself, not a second guess at it: what a download from that
    # source is filed under
    applied = bool(differs and library_artist_name(
        source["name"], {source["provider"]: source["id"]}) == library_name)
    return {
        "artist_id": int(artist_id),
        "library_name": library_name,
        "scanned_name": scanned,
        "pinned": bool(pin),
        "source": source,
        # the source spells them differently and downloads take the library's name
        "renamed": applied,
        # the source spells them differently but downloads keep its name
        # (another artist in the library goes by it)
        "kept_source_name": differs and not applied,
        "folder": folder,
    }


__all__ = ["artist_naming", "clear_cache", "source_name"]
