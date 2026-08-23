"""Wishlist controller helpers for Flask-style endpoints."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict

from core.metadata import normalize_image_url
from core.metadata.artwork import is_internal_image_host, is_soulsync_image_url
from core.wishlist.reporting import build_wishlist_stats_payload
from core.wishlist.selection import prepare_wishlist_tracks_for_display
from core.wishlist.service import get_wishlist_service
from core.wishlist.state import get_wishlist_cycle as _get_wishlist_cycle
from core.wishlist.state import set_wishlist_cycle as _set_wishlist_cycle
from utils.logging_config import get_logger


module_logger = get_logger("wishlist.routes")
logger = module_logger


@dataclass
class WishlistRouteRuntime:
    """Dependencies needed to service wishlist HTTP endpoints outside the controller."""

    get_music_database: Callable[[], Any]
    profile_id: int
    download_batches: Dict[str, Dict[str, Any]]
    download_tasks: Dict[str, Dict[str, Any]]
    tasks_lock: Any
    is_wishlist_actually_processing: Callable[[], bool]
    reset_wishlist_processing_state: Callable[[], None]
    add_activity_item: Callable[[Any, Any, Any, Any], Any]
    active_server: str
    logger: Any = module_logger
    get_next_run_seconds: Callable[[str], int] | None = None
    thread_factory: Callable[..., Any] = threading.Thread


def _bare_wishlist_track_id(value: Any) -> str:
    return str(value or "").split("::", 1)[0]


def _wishlist_descriptors_for_ids(
    tracks: list[dict[str, Any]], track_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return pre-delete rows represented by the requested wishlist keys.

    A composite ``track::album`` request is exact. A legacy bare request keeps
    its historical all-releases behavior because the database removal method
    still treats it as a wildcard.
    """
    wanted = [str(track_id or "") for track_id in track_ids or []]
    descriptors = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        row_key = str(
            track.get("spotify_track_id") or track.get("track_id") or track.get("id")
        )
        matches = track_ids is None or any(
            row_key == requested if "::" in requested
            else _bare_wishlist_track_id(row_key) == _bare_wishlist_track_id(requested)
            for requested in wanted
        )
        if matches:
            descriptors.append(track)
    return descriptors


def _load_wishlist_descriptors(service: Any, profile_id: int) -> list[dict[str, Any]]:
    loader = getattr(service, "get_wishlist_tracks_for_download", None)
    if not callable(loader):
        return []
    try:
        return [
            row for row in loader(profile_id=profile_id)
            if isinstance(row, dict)
        ]
    except Exception:  # noqa: BLE001 - removal must remain fail-open
        return []


def _sync_user_wishlist_removal(
    runtime: WishlistRouteRuntime,
    service: Any,
    descriptors: list[dict[str, Any]],
) -> None:
    """Best-effort Library-v2 reverse edge for HTTP/user removals only."""
    if not descriptors:
        return
    db = getattr(service, "database", None)
    if db is None:
        getter = getattr(runtime, "get_music_database", None)
        db = getter() if callable(getter) else None
    if db is None:
        return
    try:
        from core.settings import config_manager
        from core.library2.monitor_sync import sync_wishlist_removal
        sync_wishlist_removal(
            db,
            config_manager,
            descriptors,
            profile_id=runtime.profile_id,
        )
    except Exception as exc:  # noqa: BLE001
        runtime.logger.debug("wishlist reverse-sync skipped: %s", exc)


def _build_album_images(album: Dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(album.get("images"), list) and album.get("images"):
        return list(album["images"])
    if album.get("image_url"):
        return [{"url": album["image_url"], "height": 640, "width": 640}]
    return []


def _build_track_data(track: Dict[str, Any], album: Dict[str, Any]) -> Dict[str, Any]:
    """Project a wishlist-modal add payload into the canonical
    wishlist track shape.

    Pre-fix this used default values (``track_number=1``,
    ``disc_number=1``, ``total_tracks=1``, ``release_date=''``) when
    the upstream UI omitted a field. That silently poisoned every
    wishlist row added from the library "add to wishlist" modal:
    track_number locked to 1 regardless of source position, year
    dropped from folder paths because release_date was empty. The
    library modal flow is what's used for "add this album's missing
    tracks to wishlist" and "add this playlist to wishlist" bulk
    actions — the most common user path, so the regression was
    everywhere.

    Now preserve missing values explicitly (None for numeric
    positions, omit-or-empty for release_date) so the downstream
    import pipeline can detect-and-recover via
    ``core/imports/track_number.py:resolve_track_number`` instead
    of locking to 1.
    """
    album_images = _build_album_images(album)
    return {
        "id": track.get("id"),
        "name": track.get("name"),
        "artists": track.get("artists", []),
        "album": {
            "id": album.get("id"),
            "name": album.get("name"),
            "artists": album.get("artists", []),
            "images": album_images,
            "album_type": album.get("album_type", "album"),
            # release_date stays as whatever the upstream sent
            # (including '' when truly unknown). Path template
            # gracefully omits the year when empty; we don't fake
            # a date.
            "release_date": album.get("release_date", ""),
            # total_tracks=None preserves "we don't know"; UI uses
            # this for category classification + path math. Pre-fix
            # default of 1 mislabelled multi-track albums as singles.
            "total_tracks": album.get("total_tracks"),
        },
        "duration_ms": track.get("duration_ms", 0),
        # Numeric positions: None when missing, not 1.
        "track_number": track.get("track_number"),
        "disc_number": track.get("disc_number"),
        "explicit": track.get("explicit", False),
        "popularity": track.get("popularity", 0),
        "preview_url": track.get("preview_url"),
        "external_urls": track.get("external_urls", {}),
    }


def _build_spotify_track_data(track: Dict[str, Any], album: Dict[str, Any]) -> Dict[str, Any]:
    """Backward-compatible wrapper for `_build_track_data`."""
    return _build_track_data(track, album)


def _load_track_spotify_data(track: Dict[str, Any]) -> Dict[str, Any]:
    spotify_data = track.get("spotify_data", {})
    if isinstance(spotify_data, str):
        try:
            spotify_data = json.loads(spotify_data)
        except Exception:
            spotify_data = {}
    if not isinstance(spotify_data, dict):
        spotify_data = {}
    return spotify_data


def _album_lookup_id(spotify_data: Dict[str, Any]) -> tuple[str | None, Dict[str, Any]]:
    album_data = spotify_data.get("album") or {}
    if not isinstance(album_data, dict):
        album_data = {}

    track_album_id = album_data.get("id")
    if not track_album_id:
        album_name = album_data.get("name", "Unknown Album")
        artists = spotify_data.get("artists", [])
        if isinstance(artists, list) and artists and isinstance(artists[0], dict):
            artist_name = artists[0].get("name", "Unknown Artist")
        elif isinstance(artists, list) and artists and isinstance(artists[0], str):
            artist_name = artists[0]
        else:
            artist_name = "Unknown Artist"
        custom_id = f"{album_name}_{artist_name}"
        track_album_id = re.sub(r"[^a-zA-Z0-9\s_-]", "", custom_id)
        track_album_id = re.sub(r"\s+", "_", track_album_id).lower()

    return track_album_id, album_data


def process_wishlist_api(
    runtime: WishlistRouteRuntime,
    *,
    start_processing: Callable[[], None],
) -> tuple[Dict[str, Any], int]:
    """Trigger wishlist processing in the background."""
    try:
        if runtime.is_wishlist_actually_processing():
            return {"success": False, "error": "Wishlist processing already in progress"}, 409

        thread = runtime.thread_factory(target=start_processing, daemon=True)
        thread.start()
        return {"success": True, "message": "Wishlist processing started"}, 200
    except Exception as exc:
        runtime.logger.error("Error starting wishlist processing: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def get_wishlist_count(runtime: WishlistRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Return the current wishlist count for the active profile."""
    try:
        count = get_wishlist_service().get_wishlist_count(profile_id=runtime.profile_id)
        return {"count": count}, 200
    except Exception as exc:
        runtime.logger.error("Error getting wishlist count: %s", exc)
        return {"error": str(exc)}, 500


def get_wishlist_stats(runtime: WishlistRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Return wishlist statistics for the UI."""
    try:
        raw_tracks = get_wishlist_service().get_wishlist_tracks_for_download(profile_id=runtime.profile_id)
        next_run_in_seconds = runtime.get_next_run_seconds("process_wishlist") if runtime.get_next_run_seconds else 0
        is_processing = runtime.is_wishlist_actually_processing()
        current_cycle = _get_wishlist_cycle(runtime.get_music_database)
        active_batches = 0
        with runtime.tasks_lock:
            active_batches = sum(
                1
                for batch in runtime.download_batches.values()
                if batch.get("playlist_id") == "wishlist"
                and batch.get("phase") not in ["complete", "error", "cancelled"]
            )

        payload = build_wishlist_stats_payload(
            raw_tracks,
            next_run_in_seconds=next_run_in_seconds,
            is_auto_processing=is_processing,
            current_cycle=current_cycle,
        )
        payload["active_batches"] = active_batches
        return payload, 200
    except Exception as exc:
        runtime.logger.error("Error getting wishlist stats: %s", exc)
        return {"error": str(exc)}, 500


def get_wishlist_cycle(runtime: WishlistRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Return the current wishlist cycle."""
    try:
        cycle = _get_wishlist_cycle(runtime.get_music_database)
        return {"cycle": cycle}, 200
    except Exception as exc:
        runtime.logger.error("Error getting wishlist cycle: %s", exc)
        return {"error": str(exc)}, 500


def set_wishlist_cycle(runtime: WishlistRouteRuntime, cycle: str) -> tuple[Dict[str, Any], int]:
    """Persist the wishlist cycle."""
    try:
        if cycle not in ["albums", "singles"]:
            return {"error": "Invalid cycle. Must be 'albums' or 'singles'"}, 400

        _set_wishlist_cycle(runtime.get_music_database, cycle)
        runtime.logger.info("Wishlist cycle set to: %s", cycle)
        return {"success": True, "cycle": cycle}, 200
    except Exception as exc:
        runtime.logger.error("Error setting wishlist cycle: %s", exc)
        return {"error": str(exc)}, 500


def _needs_image_fix(url: str | None) -> bool:
    """True when an image URL won't render in the browser as-is — a media-server RELATIVE
    path (/library/.., /Items/.., /rest/..) or an internal/localhost host. Spotify/iTunes CDN
    URLs render directly and are left untouched, so already-working items never change.

    SoulSync's own endpoints (image proxy/cache, Library-v2 artwork) are already
    browser-facing and are explicitly NOT a fix target — `/api/library/v2/artwork/..`
    otherwise looks like a relative path and would be rewritten into a
    media-server URL by `normalize_image_url`."""
    if not url or not isinstance(url, str):
        return False
    if is_soulsync_image_url(url):
        return False
    if url.startswith('/') and not url.startswith('//'):
        return True
    if url.startswith('http://') or url.startswith('https://'):
        return is_internal_image_host(url)
    return False


# How many bind parameters one lookup may use. SQLite's compiled-in limit is
# 999 on older builds, and a wishlist with a few hundred distinct artists was
# close enough to it that a larger library would have started raising
# "too many SQL variables" inside the art enrichment.
_SQL_PARAM_CHUNK = 400


def _chunked(values: list[Any], size: int = _SQL_PARAM_CHUNK):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _row_source_info(track: dict[str, Any]) -> dict[str, Any]:
    """The row's `source_info`, which reaches us as a dict or as JSON text."""
    raw = track.get('source_info')
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return {}
    return raw if isinstance(raw, dict) else {}


def _first_usable_album_image(album: dict[str, Any]) -> str | None:
    images = album.get('images')
    if not isinstance(images, list):
        return None
    for img in images:
        url = img.get('url') if isinstance(img, dict) else img
        if isinstance(url, str) and url.strip():
            return url
    return None


def _lib2_artist_ids_for_tracks(conn, track_ids: list[int]) -> dict[int, int]:
    """lib2 track id -> its PRIMARY artist id, for the rows that have one."""
    mapping: dict[int, int] = {}
    for chunk in _chunked(track_ids):
        marks = ','.join('?' * len(chunk))
        rows = conn.execute(
            f"""SELECT ta.track_id, ta.artist_id
                  FROM lib2_track_artists ta
                 WHERE ta.track_id IN ({marks})
                 ORDER BY ta.track_id, ta.position""",
            chunk,
        ).fetchall()
        for row in rows:
            mapping.setdefault(int(row['track_id']), int(row['artist_id']))
    return mapping


def _lib2_album_image_urls(conn, album_ids: list[int]) -> dict[int, Any]:
    """lib2 album id -> stored image_url, fetched in bulk.

    Bulk because the alternative is one SELECT per row, and a Library-v2
    wishlist is hundreds of rows long — the production report had 373 of them.
    """
    found: dict[int, Any] = {}
    for chunk in _chunked(album_ids):
        marks = ','.join('?' * len(chunk))
        rows = conn.execute(
            f"SELECT id, image_url FROM lib2_albums WHERE id IN ({marks})", chunk,
        ).fetchall()
        for row in rows:
            found[int(row['id'])] = row['image_url']
    return found


def _lib2_artist_image_urls(conn, artist_ids: list[int]) -> dict[int, Any]:
    """lib2 artist id -> stored image_url, fetched in bulk."""
    found: dict[int, Any] = {}
    for chunk in _chunked(artist_ids):
        marks = ','.join('?' * len(chunk))
        rows = conn.execute(
            f"SELECT id, image_url FROM lib2_artists WHERE id IN ({marks})", chunk,
        ).fetchall()
        for row in rows:
            found[int(row['id'])] = row['image_url']
    return found


def _lib2_artists_by_name(conn, names: list[str]) -> dict[str, tuple[int, Any]]:
    """Folded artist name -> (lib2 artist id, stored image_url).

    Folded on `name_key` (the catalogue's own Unicode-aware key) with a
    lowercase fallback, because the previous exact-`name` match missed every
    artist whose wishlist spelling differed in case or diacritics from the
    catalogue's.
    """
    # `name_key` is additive; a database from before it existed must still get
    # photos rather than silently getting none.
    has_name_key = True
    try:
        has_name_key = any(
            row[1] == 'name_key'
            for row in conn.execute("PRAGMA table_info(lib2_artists)").fetchall()
        )
    except Exception:  # noqa: BLE001
        has_name_key = False

    found: dict[str, tuple[int, Any]] = {}
    for chunk in _chunked(names):
        marks = ','.join('?' * len(chunk))
        if has_name_key:
            rows = conn.execute(
                f"""SELECT id, name, name_key, image_url
                      FROM lib2_artists
                     WHERE LOWER(name) IN ({marks})
                        OR COALESCE(name_key, '') IN ({marks})""",
                chunk + chunk,
            ).fetchall()
            keys_of = lambda row: (row['name_key'], row['name'])  # noqa: E731
        else:
            rows = conn.execute(
                f"SELECT id, name, image_url FROM lib2_artists "
                f"WHERE LOWER(name) IN ({marks})",
                chunk,
            ).fetchall()
            keys_of = lambda row: (row['name'],)  # noqa: E731
        for row in rows:
            for key in keys_of(row):
                if key:
                    found.setdefault(str(key).lower(), (int(row['id']), row['image_url']))
    return found


def _enrich_wishlist_images(
    tracks: list[dict[str, Any]], db: Any,
) -> tuple[dict[str, str], dict[str, str]]:
    """Make wishlist art browser-renderable using the library data we already have.

    Three jobs, all done on READ so rows already sitting in the wishlist are
    repaired without rewriting a single stored payload:

      1. Normalize each track's album.images[*].url that needs it (relative/internal only —
         CDN URLs are left as-is to avoid regressing items that already render).
      2. Backfill a cover for Library-v2 rows that have none. Those rows are
         written by ``core.library2.wishlist_mirror`` and, before this, carried
         no ``album.images`` at all — 373 of 611 rows in the production report
         had no album image of any kind, 100% correlated with that origin. The
         cover is resolved from ``source_info.lib2_album_id`` via
         ``core.library2.wishlist_art``.
      3. Build an artist-name -> photo map for the UI. Resolution goes through
         Library-v2 identities (``source_info.lib2_track_id`` -> primary artist,
         else a folded name match) rather than the old exact-name match on
         ``lib2_artists.image_url``, and it never returns a media-server path or
         a known provider placeholder — the two things that produced the
         report's 218 permanently-404ing artist images and its 129 rows showing
         the generic Last.fm star.

    Returns ``(photos, fallbacks)``, both keyed by lowercased artist name.
    ``photos`` holds the local Library-v2 artwork URL — the long-term truth,
    served off disk, no media server involved. ``fallbacks`` holds the provider
    CDN photo where the catalogue has a usable one, which the client paints
    while a cold local build is still running. Same split, and same reasoning,
    as ``image_url``/``remote_image_url`` on the Library v2 pages.
    """
    from core.library2.wishlist_art import (
        album_images, artist_image_url, artist_remote_image_url,
    )

    artist_names: set[str] = set()
    lib2_album_rows: list[tuple[dict[str, Any], int]] = []
    lib2_track_ids: list[int] = []
    row_track_ids: dict[int, int] = {}      # id(track) -> lib2 track id

    for track in tracks:
        source_info = _row_source_info(track)
        sd = track.get('spotify_data')
        album = sd.get('album') if isinstance(sd, dict) else None
        if isinstance(album, dict):
            images = album.get('images')
            if isinstance(images, list):
                for img in images:
                    if isinstance(img, dict) and _needs_image_fix(img.get('url')):
                        fixed = normalize_image_url(img['url'])
                        if fixed:
                            img['url'] = fixed
            album_id = source_info.get('lib2_album_id')
            if album_id is not None and not _first_usable_album_image(album):
                try:
                    lib2_album_rows.append((album, int(album_id)))
                except (TypeError, ValueError):
                    pass

        track_id = source_info.get('lib2_track_id')
        if track_id is not None:
            try:
                track_id = int(track_id)
            except (TypeError, ValueError):
                track_id = None
            if track_id is not None:
                row_track_ids[id(track)] = track_id
                lib2_track_ids.append(track_id)

        name = track.get('artist_name')
        if name and name != 'Unknown Artist':
            artist_names.add(name)

    artist_images: dict[str, str] = {}
    artist_fallbacks: dict[str, str] = {}
    if not artist_names and not lib2_album_rows:
        return artist_images, artist_fallbacks

    try:
        conn = db._get_connection()
        try:
            # Covers and photos get their own guard each: they read different
            # tables, and a failure in one used to take the other's result down
            # with it silently.
            try:
                stored_covers = (
                    _lib2_album_image_urls(conn, sorted({a for _, a in lib2_album_rows}))
                    if lib2_album_rows else {}
                )
                for album, album_id in lib2_album_rows:
                    album['images'] = album_images(
                        None, album_id, database=db,
                        stored_image_url=stored_covers.get(album_id),
                    )
            except Exception as exc:  # noqa: BLE001
                logger.debug("Could not backfill Library-v2 wishlist covers: %s", exc)

            artist_id_by_track = (
                _lib2_artist_ids_for_tracks(conn, sorted(set(lib2_track_ids)))
                if lib2_track_ids else {}
            )
            by_name = (
                _lib2_artists_by_name(conn, sorted({n.lower() for n in artist_names}))
                if artist_names else {}
            )

            # Library-v2 rows resolve through their own track -> artist edge, so
            # a spelling difference between the wishlist payload and the
            # catalogue can no longer cost an artist their photo.
            resolved_ids: dict[str, int] = {}
            for track in tracks:
                name = track.get('artist_name')
                if not name or name == 'Unknown Artist':
                    continue
                key = name.lower()
                if key in resolved_ids:
                    continue
                artist_id = artist_id_by_track.get(row_track_ids.get(id(track), -1))
                if artist_id is None:
                    match = by_name.get(key)
                    if match is None:
                        continue
                    artist_id = match[0]
                resolved_ids[key] = artist_id

            stored_photos = _lib2_artist_image_urls(
                conn, sorted(set(resolved_ids.values())),
            )
            for key, artist_id in resolved_ids.items():
                resolved = artist_image_url(None, artist_id, database=db)
                if resolved:
                    artist_images[key] = resolved
                remote = artist_remote_image_url(
                    None, artist_id,
                    stored_image_url=stored_photos.get(artist_id),
                )
                if remote:
                    artist_fallbacks[key] = remote
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 — art is cosmetic, never fail the tracks endpoint
        logger.debug("Could not build wishlist artist-image map: %s", exc)
    return artist_images, artist_fallbacks


def get_wishlist_tracks(
    runtime: WishlistRouteRuntime,
    *,
    category: str | None = None,
    limit: int | None = None,
) -> tuple[Dict[str, Any], int]:
    """Return wishlist tracks for the modal UI.

    READ-ONLY. This used to open with ``remove_wishlist_duplicates()``, so
    merely looking at the wishlist — or running a diagnostic against it —
    deleted rows. That is wrong on its own terms (a GET must not mutate), and
    it also made the endpoint impossible to reason about: it was the only
    difference between this path and ``/api/wishlist/stats``, so any
    disagreement between the two counts could never be attributed. The cleanup
    still runs where it belongs: the ``cleanup_wishlist`` maintenance
    automation (``core.automation.handlers.maintenance``) and the wishlist
    processing cycle (``core.wishlist.processing``).
    """
    try:
        db = runtime.get_music_database()

        raw_tracks = get_wishlist_service().get_wishlist_tracks_for_download(profile_id=runtime.profile_id)
        prepared = prepare_wishlist_tracks_for_display(raw_tracks, category=category, limit=limit)

        # Rows the API drops are reported, not just logged. The production
        # report saw `/api/wishlist/count` = 614 and this endpoint = 611 with
        # no way to tell which stage ate the difference; `hidden_rows` names it
        # in the response itself so the next such gap explains itself.
        stored_rows = len(raw_tracks)
        hidden_rows = stored_rows - len(prepared["tracks"]) if not (category or limit) else 0
        if prepared["duplicates_found"] > 0:
            runtime.logger.warning(
                "[API-Wishlist-Tracks] Hid %s duplicate track id(s) during sanitization",
                prepared["duplicates_found"],
            )

        # Make library-sourced art renderable + supply artist photos (see _enrich_wishlist_images).
        artist_images, artist_fallbacks = _enrich_wishlist_images(prepared["tracks"], db)

        payload: Dict[str, Any] = {
            "tracks": prepared["tracks"],
            "total": prepared["total"],
            "artist_images": artist_images,
            # The CDN stand-in a client paints while a cold local build runs —
            # the wishlist's counterpart to `remote_image_url` on the Library
            # v2 pages.
            "artist_images_fallback": artist_fallbacks,
            "stored_rows": stored_rows,
            "hidden_rows": hidden_rows,
            "duplicates_found": prepared["duplicates_found"],
        }
        if category:
            runtime.logger.info(
                "Wishlist filter: %s/%s tracks in '%s' category (limit: %s)",
                len(prepared["tracks"]),
                prepared["total"],
                category,
                limit or "none",
            )
            payload["category"] = category
        return payload, 200
    except Exception as exc:
        runtime.logger.error("Error getting wishlist tracks: %s", exc)
        return {"error": str(exc)}, 500


def clear_wishlist(runtime: WishlistRouteRuntime) -> tuple[Dict[str, Any], int]:
    """Clear the wishlist and cancel active wishlist batches."""
    try:
        service = get_wishlist_service()
        # Capture exact Library-v2/provider identities before the rows vanish.
        descriptors = _load_wishlist_descriptors(service, runtime.profile_id)
        success = service.clear_wishlist(profile_id=runtime.profile_id)

        if success:
            _sync_user_wishlist_removal(runtime, service, descriptors)
            cancelled_count = 0
            with runtime.tasks_lock:
                for _batch_id, batch_data in runtime.download_batches.items():
                    if batch_data.get("playlist_id") == "wishlist" and batch_data.get("phase") not in (
                        "complete",
                        "error",
                        "cancelled",
                    ):
                        batch_data["phase"] = "cancelled"
                        for task_id in batch_data.get("queue", []):
                            if task_id in runtime.download_tasks and runtime.download_tasks[task_id]["status"] not in (
                                "completed",
                                "failed",
                                "not_found",
                                "cancelled",
                            ):
                                runtime.download_tasks[task_id]["status"] = "cancelled"
                                cancelled_count += 1

            runtime.reset_wishlist_processing_state()

            if cancelled_count > 0:
                runtime.logger.warning("[Wishlist Clear] Cancelled %s active wishlist downloads", cancelled_count)
                runtime.add_activity_item("", "Wishlist Cleared", f"Wishlist cleared and {cancelled_count} downloads cancelled", "Now")

            return {
                "success": True,
                "message": "Wishlist cleared successfully",
                "cancelled_downloads": cancelled_count,
            }, 200

        return {"success": False, "error": "Failed to clear wishlist"}, 500
    except Exception as exc:
        runtime.logger.error("Error clearing wishlist: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def remove_track_from_wishlist(
    runtime: WishlistRouteRuntime,
    spotify_track_id: str | None,
) -> tuple[Dict[str, Any], int]:
    """Remove a single track from the wishlist."""
    try:
        if not spotify_track_id:
            return {"success": False, "error": "No spotify_track_id provided"}, 400

        service = get_wishlist_service()
        _db = getattr(service, "database", None)
        descriptors = _wishlist_descriptors_for_ids(
            _load_wishlist_descriptors(service, runtime.profile_id),
            [spotify_track_id],
        )
        # #874: capture the track's display info BEFORE removal (the row is
        # gone afterwards) so the ignore-list entry carries a human label.
        _ignore_data = None
        try:
            if _db is not None:
                _ignore_data = _db.get_wishlist_spotify_data(
                    spotify_track_id, profile_id=runtime.profile_id)
        except Exception:
            _ignore_data = None

        success = service.remove_track_from_wishlist(
            spotify_track_id,
            profile_id=runtime.profile_id,
        )

        if success:
            # #874: a user-initiated remove means "stop auto-requeuing this".
            # Record a TTL'd ignore so the watchlist/auto-processor doesn't
            # re-add it. Best-effort — never fails the remove.
            from core.wishlist.ignore import ignore_wishlist_track, REASON_REMOVED
            ignore_wishlist_track(_db, runtime.profile_id,
                                  spotify_track_id, REASON_REMOVED, spotify_data=_ignore_data)
            _sync_user_wishlist_removal(runtime, service, descriptors or [{
                "spotify_track_id": spotify_track_id,
                "spotify_data": _ignore_data or {},
            }])
            runtime.logger.info("Successfully removed track from wishlist: %s", spotify_track_id)
            return {"success": True, "message": "Track removed from wishlist"}, 200

        runtime.logger.warning("Failed to remove track from wishlist: %s", spotify_track_id)
        return {"success": False, "error": "Track not found in wishlist"}, 404
    except Exception as exc:
        runtime.logger.error("Error removing track from wishlist: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def remove_album_from_wishlist(
    runtime: WishlistRouteRuntime,
    *,
    album_id: str | None = None,
    album_name_filter: str | None = None,
) -> tuple[Dict[str, Any], int]:
    """Remove every wishlist track that belongs to the selected album."""
    try:
        if not album_id and not album_name_filter:
            return {"success": False, "error": "No album_id or album_name provided"}, 400

        wishlist_service = get_wishlist_service()
        all_tracks = wishlist_service.get_wishlist_tracks_for_download(profile_id=runtime.profile_id)

        tracks_to_remove = []
        for track in all_tracks:
            spotify_data = _load_track_spotify_data(track)
            track_album_id, album_data = _album_lookup_id(spotify_data)

            matched = False
            if album_id and track_album_id == album_id:
                matched = True
            elif album_name_filter:
                track_album_name = album_data.get("name", "")
                if isinstance(spotify_data.get("album"), str):
                    track_album_name = spotify_data["album"]
                if track_album_name and track_album_name.lower().strip() == album_name_filter.lower().strip():
                    matched = True

            if matched:
                spotify_track_id = track.get("track_id") or track.get("spotify_track_id") or track.get("id")
                if spotify_track_id:
                    # Keep the loaded spotify_data alongside the id so the #874
                    # ignore entry can be labelled without a second DB read.
                    tracks_to_remove.append((spotify_track_id, spotify_data, track))

        from core.wishlist.ignore import ignore_wishlist_track, REASON_REMOVED
        _db = getattr(wishlist_service, "database", None)
        removed_count = 0
        removed_descriptors = []
        album_remove_pid = runtime.profile_id
        for spotify_track_id, track_spotify_data, descriptor in tracks_to_remove:
            if wishlist_service.remove_track_from_wishlist(spotify_track_id, profile_id=album_remove_pid):
                removed_count += 1
                removed_descriptors.append(descriptor)
                # #874: user removed the whole album → ignore each track.
                ignore_wishlist_track(_db, album_remove_pid,
                                      spotify_track_id, REASON_REMOVED, spotify_data=track_spotify_data)

        if removed_count > 0:
            _sync_user_wishlist_removal(
                runtime, wishlist_service, removed_descriptors,
            )
            runtime.logger.info("Successfully removed %s tracks from album %s", removed_count, album_id)
            return {
                "success": True,
                "message": f"Removed {removed_count} track(s) from wishlist",
                "removed_count": removed_count,
            }, 200

        runtime.logger.warning("No tracks found for album %s", album_id)
        return {"success": False, "error": "No tracks found for this album"}, 404
    except Exception as exc:
        runtime.logger.error("Error removing album from wishlist: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def _primary_artist_name(spotify_data: Dict[str, Any]) -> str:
    artists = spotify_data.get("artists") or []
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict):
            return str(first.get("name") or "")
        return str(first or "")
    return ""


def remove_artist_from_wishlist(
    runtime: WishlistRouteRuntime,
    artist_name: str,
) -> tuple[Dict[str, Any], int]:
    """Remove EVERY wishlist track whose primary artist matches (#1065).

    QT3496: excluding one artist from a big discography wishlist meant
    unchecking / deleting every album one by one. Mirrors the per-album
    removal exactly — each removed track gets an ignore entry (#874) so the
    next watchlist/discography pass doesn't quietly re-add the artist."""
    try:
        artist_name = str(artist_name or "").strip()
        if not artist_name:
            return {"success": False, "error": "Missing artist_name"}, 400
        wanted = artist_name.lower()

        wishlist_service = get_wishlist_service()
        all_tracks = wishlist_service.get_wishlist_tracks_for_download(profile_id=runtime.profile_id)

        tracks_to_remove = []
        for track in all_tracks:
            spotify_data = _load_track_spotify_data(track)
            if _primary_artist_name(spotify_data).lower().strip() != wanted:
                continue
            spotify_track_id = track.get("track_id") or track.get("spotify_track_id") or track.get("id")
            if spotify_track_id:
                tracks_to_remove.append((spotify_track_id, spotify_data))

        from core.wishlist.ignore import ignore_wishlist_track, REASON_REMOVED
        _db = getattr(wishlist_service, "database", None)
        removed_count = 0
        pid = runtime.profile_id
        for spotify_track_id, track_spotify_data in tracks_to_remove:
            if wishlist_service.remove_track_from_wishlist(spotify_track_id, profile_id=pid):
                removed_count += 1
                ignore_wishlist_track(_db, pid, spotify_track_id, REASON_REMOVED,
                                      spotify_data=track_spotify_data)

        if removed_count > 0:
            runtime.logger.info("Removed %s wishlist track(s) for artist %r",
                                removed_count, artist_name)
            return {
                "success": True,
                "message": f"Removed {removed_count} track(s) by {artist_name}",
                "removed_count": removed_count,
            }, 200
        return {"success": False, "error": "No wishlist tracks found for this artist"}, 404
    except Exception as exc:
        runtime.logger.error("Error removing artist from wishlist: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def remove_batch_from_wishlist(
    runtime: WishlistRouteRuntime,
    spotify_track_ids,
) -> tuple[Dict[str, Any], int]:
    """Remove a batch of tracks from the wishlist."""
    try:
        if not spotify_track_ids or not isinstance(spotify_track_ids, list):
            return {"success": False, "error": "Missing or invalid spotify_track_ids"}, 400

        from core.wishlist.ignore import ignore_wishlist_track, REASON_REMOVED
        service = get_wishlist_service()
        _db = getattr(service, "database", None)
        descriptors_by_id = {
            _bare_wishlist_track_id(
                descriptor.get("spotify_track_id")
                or descriptor.get("track_id")
                or descriptor.get("id")
            ): descriptor
            for descriptor in _load_wishlist_descriptors(service, runtime.profile_id)
            if isinstance(descriptor, dict)
        }
        removed = 0
        removed_descriptors = []
        pid = runtime.profile_id
        for track_id in spotify_track_ids:
            # Capture label before the row is deleted (#874).
            _data = None
            try:
                if _db is not None:
                    _data = _db.get_wishlist_spotify_data(track_id, profile_id=pid)
            except Exception:
                _data = None
            if service.remove_track_from_wishlist(track_id, profile_id=pid):
                removed += 1
                removed_descriptors.append(
                    descriptors_by_id.get(_bare_wishlist_track_id(track_id)) or {
                        "spotify_track_id": track_id,
                        "spotify_data": _data or {},
                    }
                )
                ignore_wishlist_track(_db, pid, track_id, REASON_REMOVED, spotify_data=_data)

        _sync_user_wishlist_removal(runtime, service, removed_descriptors)
        runtime.logger.info("Batch removed %s track(s) from wishlist", removed)
        return {
            "success": True,
            "removed": removed,
            "message": f"Removed {removed} track{'s' if removed != 1 else ''} from wishlist",
        }, 200
    except Exception as exc:
        runtime.logger.error("Error batch removing from wishlist: %s", exc)
        return {"success": False, "error": str(exc)}, 500


def add_album_track_to_wishlist(
    runtime: WishlistRouteRuntime,
    *,
    track: Dict[str, Any] | None,
    artist: Dict[str, Any] | None,
    album: Dict[str, Any] | None,
    source_type: str = "album",
    source_context: Dict[str, Any] | None = None,
    quality_profile_id: int | None = None,
) -> tuple[Dict[str, Any], int]:
    """Add a single album track to the wishlist.

    ``quality_profile_id`` is the durable acquisition intent chosen in the
    shared "Tracks to Add to Wishlist" dialog. It travels all the way to the
    stored row so the same track gets the same rules regardless of which page
    the user started from (P1-01). ``None`` keeps the app-wide default.
    """
    try:
        if not track or not artist or not album:
            return {"success": False, "error": "Missing required fields: track, artist, album"}, 400

        track_data = _build_track_data(track, album)
        if quality_profile_id is not None:
            track_data["quality_profile_id"] = quality_profile_id

        # #825: don't add a track that's already in the library, unless the user
        # has opted into duplicates. The manual album "add to wishlist" modal
        # otherwise dumped owned tracks straight into the wishlist with no check
        # (carlosjfcasero) — and the auto-cleanup may not reliably remove them.
        # Respects the same wishlist.allow_duplicate_tracks toggle the watchlist
        # scan + cleanup use: OFF → skip owned, ON → add anyway. (The quality
        # re-download flow uses a different endpoint, so it's unaffected.)
        try:
            from core.settings import config_manager as _cfg
            if not _cfg.get('wishlist.allow_duplicate_tracks', True):
                _db = runtime.get_music_database()
                _existing, _conf = _db.check_track_exists(
                    track.get('name', ''), artist.get('name', ''),
                    confidence_threshold=0.7,
                    server_source=runtime.active_server,
                    album=album.get('name', ''),
                )
                if _existing and _conf >= 0.7:
                    runtime.logger.info(
                        "[Wishlist Add] skipping '%s' by '%s' — already in library "
                        "(allow_duplicate_tracks is off)",
                        track.get('name'), artist.get('name'))
                    return {"success": True, "skipped": True,
                            "message": f"'{track.get('name')}' is already in your library"}, 200
        except Exception as _own_err:
            runtime.logger.debug("Wishlist add ownership check failed (adding anyway): %s", _own_err)

        enhanced_source_context = {
            **(source_context or {}),
            "artist_id": artist.get("id"),
            "artist_name": artist.get("name"),
            "album_id": album.get("id"),
            "album_name": album.get("name"),
            "added_via": "library_wishlist_modal",
        }

        success = get_wishlist_service().add_track_to_wishlist(
            track_data=track_data,
            failure_reason="Added from library (incomplete album)",
            source_type=source_type,
            source_context=enhanced_source_context,
            profile_id=runtime.profile_id,
            # Explicit user click in the album modal — must bypass + clear the
            # ignore-list, even if the user previously cancelled this track
            # (otherwise the add is silently dropped — carlosjfcasero, #897).
            user_initiated=True,
            quality_profile_id=quality_profile_id,
        )

        if success:
            runtime.logger.info("Added track '%s' by '%s' to wishlist", track.get("name"), artist.get("name"))
            # §52.8: a confirmed "Add to Wishlist" click is a confirmed
            # acquisition intent — materialize the lib2 Artist/Release/Track
            # now so the entity is readable even if the later download fails,
            # quarantines, or never starts. Best-effort/fail-open (never
            # raises), so it can't affect the already-succeeded wishlist add.
            from core.library2.materialize import materialize_wishlist_intent
            materialize_wishlist_intent({
                "id": track.get("id"),
                "name": track.get("name"),
                "artists": [artist],
                "album": album,
                "track_number": track.get("track_number"),
                "disc_number": track.get("disc_number"),
            }, profile_id=runtime.profile_id,
               actor_profile_id=runtime.profile_id)
            return {"success": True, "message": f"Added '{track.get('name')}' to wishlist"}, 200

        runtime.logger.error("Failed to add track '%s' to wishlist", track.get("name"))
        return {"success": False, "error": "Failed to add track to wishlist"}, 200
    except Exception as exc:
        runtime.logger.error("Error adding track to wishlist: %s", exc)
        import traceback

        traceback.print_exc()
        return {"success": False, "error": str(exc)}, 500


__all__ = [
    "WishlistRouteRuntime",
    "process_wishlist_api",
    "get_wishlist_count",
    "get_wishlist_stats",
    "get_wishlist_cycle",
    "set_wishlist_cycle",
    "get_wishlist_tracks",
    "clear_wishlist",
    "remove_track_from_wishlist",
    "remove_album_from_wishlist",
    "remove_batch_from_wishlist",
    "add_album_track_to_wishlist",
]
