"""Label watchlist scan — the studio-scan analog for record labels.

Purely additive: a NEW automation kind + endpoint. It never touches the
artist watchlist scan. Structure mirrors video_scan_watchlist_studios — a pure,
unit-testable selection core (`select_label_release_gaps`) plus an orchestrator
(`run_label_watchlist_scan`) whose every I/O is an injected seam, so the loop /
dedupe / backlog gating / mark-scanned / cancel / error-isolation logic is
proven with fakes and no network.

Model: for each followed label, fetch its distinct-album catalog (each item
already carries the REAL artist, never the label), skip what's owned or already
wishlisted, and enqueue the rest — resolving each release to its real artist so
downloads file correctly.

The default enqueue seam (`_default_add_release`) FAILS SAFE: it only adds a
release once it has confidently resolved that album to real tracks and reused
the artist scanner's proven per-track ownership + wishlist primitives. On any
resolution uncertainty it no-ops — worst case it adds nothing, never the wrong
thing. The live resolution path wants a smoke-test before being relied on.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("automation.scan_watchlist_labels")


def select_label_release_gaps(
    catalog: List[Dict[str, Any]],
    *,
    is_owned: Callable[[Dict[str, Any]], bool],
    is_wishlisted: Callable[[Dict[str, Any]], bool],
    backlog: bool,
    floor_year: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Releases from a label catalog worth wishlisting.

    ``catalog`` is ``[{artist, album, year, release_group_id}]`` (newest-first).
    When ``backlog`` is False we monitor GOING FORWARD only: releases older than
    ``floor_year`` (the year the label was followed) are treated as back-catalog
    and skipped. When True, the whole catalog is eligible. ``is_owned`` /
    ``is_wishlisted`` dedupe; any error from them skips the item (never over-add).
    """
    picks: List[Dict[str, Any]] = []
    floor = str(floor_year or '')
    for item in catalog or []:
        if not isinstance(item, dict):
            continue
        year = str(item.get('year') or '')
        if not backlog and floor and year and year < floor:
            continue
        try:
            if is_owned(item):
                continue
            if is_wishlisted(item):
                continue
        except Exception as exc:
            logger.debug("label gap dedupe failed for %r: %s", item.get('album'), exc)
            continue
        picks.append(item)
    return picks


def _floor_year_of(label: Dict[str, Any]) -> Optional[str]:
    """Going-forward floor = the year the label was followed (date_added)."""
    da = str((label or {}).get('date_added') or '')
    return da[:4] if da[:4].isdigit() else None


def run_label_watchlist_scan(
    *,
    get_labels: Callable[[], List[Dict[str, Any]]],
    fetch_catalog: Callable[[str], List[Dict[str, Any]]],
    is_owned: Callable[[Dict[str, Any]], bool],
    is_wishlisted: Callable[[Dict[str, Any]], bool],
    add_release: Callable[[Dict[str, Any], Dict[str, Any]], bool],
    mark_scanned: Callable[[str], Any],
    floor_year_of: Callable[[Dict[str, Any]], Optional[str]] = _floor_year_of,
    cancel_check: Optional[Callable[[], bool]] = None,
    on_progress: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Scan every followed label, wishlisting new releases. All I/O injected.

    Returns a summary dict. One label failing (catalog fetch or an add) never
    aborts the run — it's counted in ``errors`` and the scan moves on.
    """
    labels = list(get_labels() or [])
    result: Dict[str, Any] = {
        'status': 'completed',
        'labels_total': len(labels),
        'labels_scanned': 0,
        'releases_found': 0,
        'releases_added': 0,
        'errors': 0,
    }

    for idx, label in enumerate(labels):
        if cancel_check and cancel_check():
            result['status'] = 'cancelled'
            break
        if not isinstance(label, dict):
            continue
        mbid = str(label.get('musicbrainz_label_id') or '').strip()
        if not mbid:
            continue
        name = str(label.get('label_name') or '')

        if on_progress:
            try:
                on_progress(index=idx, total=len(labels), label_name=name)
            except Exception as exc:
                logger.debug("label scan progress cb failed: %s", exc)

        try:
            catalog = fetch_catalog(mbid) or []
        except Exception:
            logger.exception("label scan: catalog fetch failed for %s (%s)", name, mbid)
            result['errors'] += 1
            continue

        gaps = select_label_release_gaps(
            catalog, is_owned=is_owned, is_wishlisted=is_wishlisted,
            backlog=bool(label.get('backlog')), floor_year=floor_year_of(label),
        )
        result['releases_found'] += len(gaps)

        for release in gaps:
            try:
                if add_release(release, label):
                    result['releases_added'] += 1
            except Exception:
                logger.exception("label scan: add failed for %r on %s",
                                 release.get('album'), name)
                result['errors'] += 1

        try:
            mark_scanned(mbid)
        except Exception as exc:
            logger.debug("label scan: mark_scanned failed for %s: %s", mbid, exc)
        result['labels_scanned'] += 1

    return result


# ---------------------------------------------------------------------------
# Default (live) seams — construct the real wiring. Kept defensive + fail-safe:
# the enqueue only adds tracks it has confidently resolved, reusing the artist
# scanner's proven ownership + add_to_wishlist primitives. NEEDS a live smoke
# test before being relied on (source-dependent album resolution).
# ---------------------------------------------------------------------------

def _resolve_album_tracks(metadata_service, artist: str, album: str) -> List[Any]:
    """Best-effort resolve (artist, album) → track list via the active metadata
    service. Returns [] on ANY uncertainty (so the caller adds nothing rather
    than guessing). Verifies the resolved album's artist matches before trusting
    it, so a release can never be filed under the wrong artist."""
    if metadata_service is None or not artist or not album:
        return []
    try:
        searcher = getattr(metadata_service, 'search_albums_via_artist', None)
        if not callable(searcher):
            return []
        candidates = searcher(artist, album) or []
        want = album.strip().lower()
        for cand in candidates:
            cand_name = str((cand or {}).get('name') or '').strip().lower()
            cand_artist = str((cand or {}).get('artist')
                              or ((cand or {}).get('artists') or [{}])[0].get('name', '')).strip().lower()
            if cand_name == want and cand_artist == artist.strip().lower():
                getter = getattr(metadata_service, 'get_album', None)
                full = getter(cand.get('id'), include_tracks=True) if callable(getter) else None
                items = (full or {}).get('tracks', {})
                items = items.get('items') if isinstance(items, dict) else items
                return items or []
    except Exception as exc:
        logger.debug("label album resolution failed for %s — %s: %s", artist, album, exc)
    return []


def build_default_seams(*, database=None, metadata_service=None, profile_id: int = 1,
                        scan_state: Optional[dict] = None):
    """Wire the real seams for run_label_watchlist_scan. Import-light so the
    pure engine + tests never pull these in."""
    from core.metadata import label_catalog as lc
    if database is None:
        from database.music_database import get_database
        database = get_database()

    scanner = None
    try:
        from core.watchlist_scanner import WatchlistScanner
        scanner = WatchlistScanner(metadata_service=metadata_service)
    except Exception as exc:
        logger.debug("label scan: could not build WatchlistScanner: %s", exc)

    def get_labels():
        return database.get_watchlist_labels() or []

    def fetch_catalog(mbid):
        return lc.label_catalog(mbid)

    def is_owned(item):
        # Album-level guard when the DB exposes one; otherwise let the per-track
        # missing check inside add_release be the ownership gate. Note:
        # check_album_exists returns (album|None, confidence) — a truthy tuple
        # even on a miss — so key off the matched album object, not the tuple.
        checker = getattr(database, 'check_album_exists', None)
        if callable(checker):
            try:
                match = checker(item.get('album', ''), item.get('artist', ''))
                return bool(match[0]) if isinstance(match, tuple) else bool(match)
            except Exception:
                return False
        return False

    def is_wishlisted(_item):
        # add_to_wishlist is idempotent (returns False if already queued), so the
        # DB is the dedupe — mirror the artist scan and don't pre-check here.
        return False

    def add_release(release, _label):
        artist = str(release.get('artist') or '')
        album = str(release.get('album') or '')
        tracks = _resolve_album_tracks(metadata_service, artist, album)
        if not tracks:
            return False  # fail safe — resolved nothing, add nothing
        added_any = False
        for track in tracks:
            try:
                if scanner is not None and not scanner.is_track_missing_from_library(
                        track, album_name=album):
                    continue  # already owned
                ok = database.add_to_wishlist(
                    spotify_track_data=_track_payload(track, artist, album, release),
                    failure_reason="Missing from library (found by label watchlist scan)",
                    source_type="watchlist_label",
                    source_info={'label_name': str((_label or {}).get('label_name') or ''),
                                 'label_mbid': str((_label or {}).get('musicbrainz_label_id') or ''),
                                 'album_name': album},
                    profile_id=profile_id,
                )
                added_any = added_any or bool(ok)
            except Exception:
                logger.exception("label scan: enqueue failed for a track of %s — %s", artist, album)
        return added_any

    def mark_scanned(mbid):
        return database.mark_watchlist_label_scanned(mbid)

    return {
        'get_labels': get_labels,
        'fetch_catalog': fetch_catalog,
        'is_owned': is_owned,
        'is_wishlisted': is_wishlisted,
        'add_release': add_release,
        'mark_scanned': mark_scanned,
    }


def _track_payload(track, artist, album, release):
    """A Spotify-shaped payload for database.add_to_wishlist, carrying the REAL
    artist so the download files correctly."""
    t = track if isinstance(track, dict) else {}
    name = str(t.get('name') or getattr(track, 'name', '') or '')
    year = str(release.get('year') or '')
    return {
        'id': t.get('id') or '',
        'name': name,
        'artists': [{'name': artist}],
        'album': {
            'name': album,
            'id': release.get('release_group_id') or '',
            'release_date': year,
            'artists': [{'name': artist}],
            'images': [],
        },
        'duration_ms': t.get('duration_ms') or 0,
        'track_number': t.get('track_number') or 0,
    }


def auto_scan_watchlist_labels(config=None, deps=None):
    """Automation-kind handler: run the label scan with the real seams. Kept
    thin so the tested engine carries the logic."""
    metadata_service = getattr(deps, 'metadata_service', None) if deps else None
    profile_id = 1
    try:
        if config and isinstance(config, dict):
            profile_id = int(config.get('profile_id', 1) or 1)
    except Exception:
        profile_id = 1
    seams = build_default_seams(metadata_service=metadata_service, profile_id=profile_id)
    return run_label_watchlist_scan(**seams)
