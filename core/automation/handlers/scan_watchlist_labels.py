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
                        profile_id: int = 1, on_add: Optional[Callable] = None):
    """Wire the real seams for run_label_watchlist_scan. Import-light so the
    pure engine + tests never pull these in. ``on_add(track_name, artist_name,
    album_name, album_image_url)`` fires per wishlisted track — lets the caller
    drive a live progress feed (the shared watchlist-scan display)."""
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
                if ok:
                    added_any = True
                    if on_add:
                        try:
                            imgs = album_payload.get('images') or []
                            on_add(str(payload.get('name') or ''),
                                   str((payload.get('artists') or [{}])[0].get('name') or artist),
                                   album_payload.get('name') or album,
                                   (imgs[0].get('url') if imgs else '') or '')
                        except Exception as exc:
                            logger.debug("label scan on_add callback failed: %s", exc)
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


def run_label_scan_phase(scan_state: dict, *, database, get_deezer: Optional[Callable],
                         profile_id: int = 1, cancel_check: Optional[Callable] = None) -> int:
    """Run the label watchlist phase, writing live progress into an EXISTING
    watchlist ``scan_state`` dict — the same one the artist scan + live UI use —
    so labels ride the normal watchlist scan with the shared per-item display.
    Returns the number of label tracks wishlisted (to fold into the summary).

    Shared by the manual scan (web_server.run_scan) AND the scheduled automation
    (core/watchlist/auto_scan) so the two paths can never drift."""
    counts = {'tracks': 0}
    try:
        labels = database.get_watchlist_labels() or []
    except Exception:
        labels = []
    if not labels:
        return 0

    scan_state.update({
        # keep 'scanning' so the frontend poller + live panel stay up (the artist
        # scanner may have already flipped status to 'completed').
        'status': 'scanning', 'current_phase': 'scanning_labels',
        'current_artist_name': 'Record labels', 'current_artist_image_url': '',
        'current_album': '', 'current_album_image_url': '', 'current_track_name': '',
        'total_labels': len(labels), 'current_label_index': 0,
    })

    def _progress(index=0, total=0, label_name=''):
        scan_state.update({
            'current_phase': 'scanning_labels',
            'current_artist_name': label_name or 'Record labels',
            'current_artist_image_url': '',
            'total_labels': total, 'current_label_index': index,
            'current_album': '', 'current_track_name': '',
        })

    def _on_add(track_name, artist_name, album_name, album_image_url):
        counts['tracks'] += 1
        scan_state['tracks_found_this_scan'] = scan_state.get('tracks_found_this_scan', 0) + 1
        scan_state['tracks_added_this_scan'] = scan_state.get('tracks_added_this_scan', 0) + 1
        scan_state['current_album'] = album_name
        scan_state['current_album_image_url'] = album_image_url
        scan_state['current_track_name'] = track_name
        feed = scan_state.setdefault('recent_wishlist_additions', [])
        feed.insert(0, {'track_name': track_name, 'artist_name': artist_name,
                        'album_image_url': album_image_url})
        if len(feed) > 10:
            feed.pop()
        events = scan_state.setdefault('scan_track_events', [])
        if len(events) < 500:
            events.append({'track_name': track_name, 'artist_name': artist_name,
                           'album_name': album_name, 'album_image_url': album_image_url,
                           'status': 'added'})

    seams = build_default_seams(database=database, get_deezer=get_deezer,
                                profile_id=profile_id, on_add=_on_add)
    result = run_label_watchlist_scan(
        **seams, on_progress=_progress,
        cancel_check=cancel_check or (lambda: scan_state.get('cancel_requested', False)))
    logger.info("Label watchlist phase: %s", result)
    return counts['tracks']
