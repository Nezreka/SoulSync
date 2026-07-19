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

def _norm(s: Any) -> str:
    import re as _re
    return _re.sub(r'[^a-z0-9]+', ' ', str(s or '').lower()).strip()


def resolve_album_for_release(get_deezer: Optional[Callable], artist: str, album: str):
    """Resolve (artist, album) to a real Deezer album → (album_payload, tracks).
    Deezer gives browser-loadable CDN cover images + a track list, so scanned
    wishlist entries carry the SAME context a manual add does. Verifies the
    album name matches (never wishlists the WRONG album) and returns (None, [])
    on any uncertainty so the caller adds nothing rather than guessing."""
    if not get_deezer or not artist or not album:
        return None, []
    try:
        dz = get_deezer()
    except Exception:
        dz = None
    if dz is None:
        return None, []
    want = _norm(album)
    try:
        results = dz.search_albums(f"{artist} {album}", limit=5) or []
    except Exception as exc:
        logger.debug("label scan: deezer search failed for %s - %s: %s", artist, album, exc)
        return None, []
    match = None
    for a in results:
        name = _norm(getattr(a, 'name', ''))
        if name and (name == want or want in name or name in want):
            match = a
            break
    if match is None:
        return None, []
    try:
        full = dz.get_album_metadata(str(getattr(match, 'id', '')), include_tracks=True) or {}
    except Exception as exc:
        logger.debug("label scan: deezer album fetch failed for %s - %s: %s", artist, album, exc)
        return None, []
    items = (full.get('tracks') or {}).get('items') or []
    if not items:
        return None, []
    payload = {
        'id': str(full.get('id') or ''),
        'name': full.get('name') or album,
        'images': full.get('images') or [],
        'album_type': full.get('album_type') or 'album',
        'release_date': full.get('release_date') or '',
        'total_tracks': len(items),
        'artists': full.get('artists') or [{'name': artist}],
    }
    return payload, items


def build_default_seams(*, database=None, get_deezer: Optional[Callable] = None,
                        profile_id: int = 1):
    """Wire the real seams for run_label_watchlist_scan. Import-light so the
    pure engine + tests never pull these in."""
    from core.metadata import label_catalog as lc
    if database is None:
        from database.music_database import get_database
        database = get_database()

    def get_labels():
        try:
            return database.get_watchlist_labels() or []
        except Exception:
            logger.debug("label scan: get_watchlist_labels failed")
            return []

    def fetch_catalog(mbid):
        return lc.label_catalog(mbid)

    def is_owned(item):
        # Album-level ownership gate. check_album_exists returns
        # (album|None, confidence) — a truthy tuple even on a miss — so key off
        # the matched album object, not the tuple.
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

    def add_release(release, label):
        artist = str(release.get('artist') or '')
        album = str(release.get('album') or '')
        album_payload, tracks = resolve_album_for_release(get_deezer, artist, album)
        if not album_payload or not tracks:
            return False  # fail safe — resolved nothing, add nothing
        source_info = {'label_name': str((label or {}).get('label_name') or ''),
                       'label_mbid': str((label or {}).get('musicbrainz_label_id') or ''),
                       'album_name': album_payload['name']}
        added_any = False
        for t in tracks:
            payload = dict(t) if isinstance(t, dict) else {}
            payload['album'] = album_payload
            if not payload.get('artists'):
                payload['artists'] = album_payload['artists']
            if not payload.get('id') or not payload.get('name'):
                continue
            try:
                ok = database.add_to_wishlist(
                    spotify_track_data=payload,
                    failure_reason="Missing from library (found by label watchlist scan)",
                    source_type="watchlist_label",
                    source_info=source_info,
                    profile_id=profile_id,
                )
                added_any = added_any or bool(ok)
            except Exception:
                logger.exception("label scan: enqueue failed for a track of %s - %s", artist, album)
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


def auto_scan_watchlist_labels(config=None, deps=None):
    """Automation-kind handler: run the label scan with the real seams. Kept
    thin so the tested engine carries the logic."""
    database = None
    get_deezer = None
    if deps is not None:
        try:
            database = deps.get_database()
        except Exception:
            database = None
        get_deezer = getattr(deps, 'get_deezer_client', None)
    profile_id = 1
    try:
        if config and isinstance(config, dict):
            profile_id = int(config.get('profile_id', 1) or 1)
    except Exception:
        profile_id = 1
    seams = build_default_seams(database=database, get_deezer=get_deezer, profile_id=profile_id)
    return run_label_watchlist_scan(**seams)
