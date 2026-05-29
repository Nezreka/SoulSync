"""Generic, source-agnostic helpers for the playlist-discovery route layer.

The discovery/sync endpoints in ``web_server.py`` were copy-pasted once per
source (Tidal, Deezer, Qobuz, Spotify-public, iTunes-link, YouTube,
ListenBrainz, Beatport). The per-source copies differ only by a source label
string and which ``<source>_discovery_states`` global they read. This module
lifts the source-agnostic pieces into importable, unit-testable helpers so the
route functions become thin wrappers — exactly preserving behavior (1:1).

Each helper is lifted verbatim from its web_server.py counterpart; any
per-source quirk that genuinely differs (e.g. Beatport's distinct result
shape) is intentionally NOT routed through here and stays in its own function.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Tuple

from utils.logging_config import get_logger

logger = get_logger("discovery.endpoints")


def convert_results_to_spotify_tracks(
    discovery_results: List[Dict[str, Any]],
    source_label: str,
) -> List[Dict[str, Any]]:
    """Convert a source's discovery results into the Spotify-track dicts the
    sync pipeline expects.

    Lifted verbatim from the per-source ``convert_<source>_results_to_spotify_tracks``
    functions (and the already-generic ``_convert_link_results_to_spotify_tracks``),
    which were byte-identical apart from the ``source_label`` used in the log
    line. Two input shapes are supported, matching the originals exactly:

    - ``spotify_data`` (manual-fix shape): copied through, preserving optional
      ``track_number`` / ``disc_number``.
    - ``spotify_track`` + ``status_class == 'found'`` (auto-discovery shape):
      rebuilt from the flat ``spotify_*`` fields.

    Any result matching neither shape is skipped, identical to the originals.

    NOTE: Beatport deliberately does NOT use this — its converter coerces
    artist objects to strings and emits a different track shape (``source``
    field, album dict), so it keeps its own implementation.
    """
    spotify_tracks: List[Dict[str, Any]] = []

    for result in discovery_results:
        # Support both data formats: spotify_data (manual fixes) and individual
        # fields (automatic discovery).
        if result.get('spotify_data'):
            spotify_data = result['spotify_data']
            track = {
                'id': spotify_data['id'],
                'name': spotify_data['name'],
                'artists': spotify_data['artists'],
                'album': spotify_data['album'],
                'duration_ms': spotify_data.get('duration_ms', 0),
            }
            if spotify_data.get('track_number'):
                track['track_number'] = spotify_data['track_number']
            if spotify_data.get('disc_number'):
                track['disc_number'] = spotify_data['disc_number']
            spotify_tracks.append(track)
        elif result.get('spotify_track') and result.get('status_class') == 'found':
            spotify_tracks.append({
                'id': result.get('spotify_id', 'unknown'),
                'name': result.get('spotify_track', 'Unknown Track'),
                'artists': [result.get('spotify_artist', 'Unknown Artist')] if result.get('spotify_artist') else ['Unknown Artist'],
                'album': result.get('spotify_album', 'Unknown Album'),
                'duration_ms': 0,
            })

    logger.info(f"Converted {len(spotify_tracks)} {source_label} matches to Spotify tracks for sync")
    return spotify_tracks


def cancel_sync(
    states: Dict[str, Any],
    key: str,
    *,
    label: str,
    not_found_message: str,
    sync_lock: Any,
    sync_states: Dict[str, Any],
    active_sync_workers: Dict[str, Any],
) -> Tuple[Dict[str, Any], int]:
    """Cancel an in-progress sync for one discovery playlist.

    1:1 lift of the byte-identical ``cancel_<source>_sync`` bodies (Tidal,
    Deezer, Qobuz, Spotify-Public, iTunes-Link, YouTube, ListenBrainz). The
    caller passes the already-resolved state key (ListenBrainz transforms it
    via ``_lb_state_key`` first), the source ``label``, the exact 404 message
    (iTunes-Link uses "iTunes Link not found", not "... playlist not found"),
    and the shared sync infrastructure (so this stays free of web_server
    globals / Flask).

    Returns ``(payload_dict, status_code)``; the caller wraps in ``jsonify``.

    Beatport is NOT routed here — it cancels a stored ``sync_future`` and
    returns a different payload.
    """
    try:
        if key not in states:
            return {"error": not_found_message}, 404

        state = states[key]
        state['last_accessed'] = time.time()
        sync_playlist_id = state.get('sync_playlist_id')

        if sync_playlist_id:
            with sync_lock:
                sync_states[sync_playlist_id] = {"status": "cancelled"}
            if sync_playlist_id in active_sync_workers:
                del active_sync_workers[sync_playlist_id]

        state['phase'] = 'discovered'
        state['sync_playlist_id'] = None
        state['sync_progress'] = {}

        return {"success": True, "message": f"{label} sync cancelled"}, 200
    except Exception as e:
        logger.error(f"Error cancelling {label} sync: {e}")
        return {"error": str(e)}, 500


def delete_playlist_state(
    states: Dict[str, Any],
    key: str,
    *,
    label: str,
    not_found_message: str,
) -> Tuple[Dict[str, Any], int]:
    """Delete a discovery playlist's state entry, cancelling any active
    discovery first.

    1:1 lift of the byte-identical ``delete_<source>_playlist`` bodies
    (Tidal, Deezer, Qobuz, Spotify-Public). Returns ``(payload, status_code)``.

    The iTunes-Link / YouTube / ListenBrainz / Beatport deletes intentionally
    keep their own bodies — they differ in success message, info-log wording,
    name extraction, and/or key transform.
    """
    try:
        if key not in states:
            return {"error": not_found_message}, 404

        state = states[key]
        if 'discovery_future' in state and state['discovery_future']:
            state['discovery_future'].cancel()
        del states[key]

        logger.info(f"Deleted {label} playlist state: {key}")
        return {"success": True, "message": "Playlist deleted"}, 200
    except Exception as e:
        logger.error(f"Error deleting {label} playlist: {e}")
        return {"error": str(e)}, 500


# --- playlist-name accessors -------------------------------------------------
# The per-source sync-status handlers read the display name three different
# ways. Each is reproduced verbatim so the 1:1 behavior (including which ones
# raise vs. fall back to 'Unknown Playlist') is preserved.

def playlist_name_attr_or_unknown(state: Dict[str, Any]) -> str:
    """Tidal: playlist is an object — use ``.name`` or 'Unknown Playlist'."""
    pl = state.get('playlist')
    return pl.name if pl and hasattr(pl, 'name') else 'Unknown Playlist'


def playlist_name_strict(state: Dict[str, Any]) -> str:
    """Deezer / Qobuz / Spotify-Public / iTunes-Link: strict dict access —
    raises (→ 500) if 'playlist' is missing, exactly like the originals."""
    return state['playlist']['name']


def playlist_name_safe(state: Dict[str, Any]) -> str:
    """YouTube / ListenBrainz: safe dict access, defaulting to 'Unknown
    Playlist'."""
    return state.get('playlist', {}).get('name', 'Unknown Playlist')


def playlist_name_obj(state: Dict[str, Any]) -> str:
    """Tidal start-sync: playlist is an object — strict ``.name`` (raises if
    absent, exactly like the original)."""
    return state['playlist'].name


def playlist_image_obj(state: Dict[str, Any]) -> str:
    """Tidal: ``getattr(playlist, 'image_url', '')`` (object attribute)."""
    return getattr(state['playlist'], 'image_url', '')


def playlist_image_dict(state: Dict[str, Any]) -> str:
    """Deezer/Qobuz/Spotify-Public/YouTube: ``playlist.get('image_url', '')``
    (dict access)."""
    return state['playlist'].get('image_url', '')


def get_sync_status(
    states: Dict[str, Any],
    key: str,
    *,
    not_found_message: str,
    error_label: str,
    activity_subject: str,
    playlist_name_getter,
    sync_lock: Any,
    sync_states: Dict[str, Any],
    add_activity_item,
) -> Tuple[Dict[str, Any], int]:
    """Report sync status for one discovery playlist, posting an activity-feed
    item when the sync finishes or errors.

    1:1 lift of the ``get_<source>_sync_status`` bodies (Tidal, Deezer, Qobuz,
    Spotify-Public, iTunes-Link, YouTube, ListenBrainz). Per-source variation
    is captured by the parameters:

    - ``not_found_message`` — the 404 string (iTunes-Link drops "playlist").
    - ``error_label`` — used in the except log ("Error getting <X> sync status").
    - ``activity_subject`` — the activity-feed prefix; note Spotify-Public uses
      "Spotify Link playlist" while its error_label is "Spotify Public".
    - ``playlist_name_getter`` — one of the accessors above (attr/strict/safe);
      the strict one can raise, matching the originals (→ 500). The state's
      phase/sync_progress are mutated BEFORE the name is read, so a raising
      getter leaves the same partial mutation the original did.

    Beatport is NOT routed here — it returns a different payload (``status``
    not ``sync_status``, includes ``sync_id``, no lock, ``chart`` key).
    """
    try:
        if key not in states:
            return {"error": not_found_message}, 404

        state = states[key]
        state['last_accessed'] = time.time()
        sync_playlist_id = state.get('sync_playlist_id')

        if not sync_playlist_id:
            return {"error": "No sync in progress"}, 404

        with sync_lock:
            sync_state = sync_states.get(sync_playlist_id, {})

        response = {
            'phase': state['phase'],
            'sync_status': sync_state.get('status', 'unknown'),
            'progress': sync_state.get('progress', {}),
            'complete': sync_state.get('status') == 'finished',
            'error': sync_state.get('error'),
        }

        if sync_state.get('status') == 'finished':
            state['phase'] = 'sync_complete'
            state['sync_progress'] = sync_state.get('progress', {})
            playlist_name = playlist_name_getter(state)
            add_activity_item("", "Sync Complete", f"{activity_subject} '{playlist_name}' synced successfully", "Now")
        elif sync_state.get('status') == 'error':
            state['phase'] = 'discovered'
            playlist_name = playlist_name_getter(state)
            add_activity_item("", "Sync Failed", f"{activity_subject} '{playlist_name}' sync failed", "Now")

        return response, 200
    except Exception as e:
        logger.error(f"Error getting {error_label} sync status: {e}")
        return {"error": str(e)}, 500


def get_discovery_status(
    states: Dict[str, Any],
    key: str,
    *,
    not_found_message: str,
    error_label: str,
) -> Tuple[Dict[str, Any], int]:
    """Report real-time discovery progress/results for one playlist.

    1:1 lift of the byte-identical ``get_<source>_discovery_status`` bodies.
    Unlike sync-status, this shape is identical for ALL eight sources —
    Beatport included — so it folds in too. Only the 404 message
    (".../discovery not found" vs ".../playlist not found" vs "Beatport chart
    not found") and the except-log label vary, both passed in. The caller
    resolves the key (ListenBrainz via ``_lb_state_key``).

    Returns ``(payload, status_code)``.
    """
    try:
        if key not in states:
            return {"error": not_found_message}, 404

        state = states[key]
        state['last_accessed'] = time.time()

        return {
            'phase': state['phase'],
            'status': state['status'],
            'progress': state['discovery_progress'],
            'spotify_matches': state['spotify_matches'],
            'spotify_total': state['spotify_total'],
            'results': state['discovery_results'],
            'complete': state['phase'] == 'discovered',
        }, 200
    except Exception as e:
        logger.error(f"Error getting {error_label} discovery status: {e}")
        return {"error": str(e)}, 500


def reset_playlist(
    states: Dict[str, Any],
    key: str,
    *,
    label: str,
    not_found_message: str,
) -> Tuple[Dict[str, Any], int]:
    """Reset a discovery playlist back to the 'fresh' phase, clearing all
    discovery/sync data while preserving the original playlist payload.

    1:1 lift of the byte-identical ``reset_<source>_playlist`` bodies
    (Tidal, Deezer, Qobuz, Spotify-Public). Returns ``(payload, status_code)``.

    NOT folded in (genuinely divergent): YouTube (status -> 'parsed', no
    download_process_id, logs the playlist name, "reset to fresh state"),
    ListenBrainz (status -> 'cached', logs playlist title, returns
    {"phase": "fresh"}), iTunes-Link (uses state.update, no info log, distinct
    message). Those keep their own bodies.
    """
    try:
        if key not in states:
            return {"error": not_found_message}, 404

        state = states[key]
        if 'discovery_future' in state and state['discovery_future']:
            state['discovery_future'].cancel()

        state['phase'] = 'fresh'
        state['status'] = 'fresh'
        state['discovery_results'] = []
        state['discovery_progress'] = 0
        state['spotify_matches'] = 0
        state['sync_playlist_id'] = None
        state['converted_spotify_playlist_id'] = None
        state['download_process_id'] = None
        state['sync_progress'] = {}
        state['discovery_future'] = None
        state['last_accessed'] = time.time()

        logger.info(f"Reset {label} playlist to fresh: {key}")
        return {"success": True, "message": "Playlist reset to fresh phase"}, 200
    except Exception as e:
        logger.error(f"Error resetting {label} playlist: {e}")
        return {"error": str(e)}, 500


def get_playlist_states(
    states: Dict[str, Any],
    *,
    error_label: str,
    info_log_label: str = None,
) -> Tuple[Dict[str, Any], int]:
    """Return all stored discovery states for a source as a list for frontend
    card hydration (``{"states": [...]}``).

    1:1 lift of the ``get_<source>_playlist_states`` bodies (Tidal, Deezer,
    Qobuz, Spotify-Public, iTunes-Link), which build the same per-entry dict.
    iTunes-Link is the only one without the "Returning N ..." info log, so
    ``info_log_label`` is optional (pass None to suppress it, as iTunes did).

    NOT folded in: the YouTube/ListenBrainz ``get_all_*_playlists`` endpoints —
    they return ``{"playlists": [...]}`` (different key + fields: url/created_at,
    no discovery_results) and filter mirrored/profile-scoped entries.
    """
    try:
        result = []
        current_time = time.time()

        for key, state in states.items():
            state['last_accessed'] = current_time
            result.append({
                'playlist_id': key,
                'phase': state['phase'],
                'status': state['status'],
                'discovery_progress': state['discovery_progress'],
                'spotify_matches': state['spotify_matches'],
                'spotify_total': state['spotify_total'],
                'discovery_results': state['discovery_results'],
                'converted_spotify_playlist_id': state.get('converted_spotify_playlist_id'),
                'download_process_id': state.get('download_process_id'),
                'last_accessed': state['last_accessed'],
            })

        if info_log_label:
            logger.info(f"Returning {len(result)} stored {info_log_label} playlist states for hydration")
        return {"states": result}, 200
    except Exception as e:
        logger.error(f"Error getting {error_label} playlist states: {e}")
        return {"error": str(e)}, 500


def update_playlist_phase(
    states: Dict[str, Any],
    key: str,
    get_json,
    *,
    not_found_message: str,
    error_label: str,
    valid_phases: List[str],
    apply_extra_fields: bool,
) -> Tuple[Dict[str, Any], int]:
    """Update a discovery playlist's phase (used when the modal closes, e.g. to
    reset download_complete -> discovered).

    1:1 lift of the ``update_<source>_playlist_phase`` bodies for the five
    sources with the identical validation + full-message response (Tidal,
    Deezer, Qobuz, Spotify-Public, YouTube). Per-source params:

    - ``valid_phases`` — YouTube's list additionally includes 'parsed'.
    - ``apply_extra_fields`` — Deezer/Qobuz/Spotify-Public also persist
      download_process_id / converted_spotify_playlist_id from the body;
      Tidal/YouTube do NOT (so pass False to keep them 1:1).
    - ``not_found_message`` / ``error_label``; ``get_json`` invoked inside the
      try like the original ``request.get_json()``.

    Returns ``(payload, status_code)``.

    NOT folded in: iTunes-Link — it uses ``data.get('phase')`` (no separate
    "Phase not provided" 400) and returns a no-message payload.
    """
    try:
        if key not in states:
            return {"error": not_found_message}, 404

        data = get_json()
        if not data or 'phase' not in data:
            return {"error": "Phase not provided"}, 400

        new_phase = data['phase']
        if new_phase not in valid_phases:
            return {"error": f"Invalid phase. Must be one of: {', '.join(valid_phases)}"}, 400

        state = states[key]
        old_phase = state.get('phase', 'unknown')
        state['phase'] = new_phase
        state['last_accessed'] = time.time()

        if apply_extra_fields:
            if 'download_process_id' in data:
                state['download_process_id'] = data['download_process_id']
            if 'converted_spotify_playlist_id' in data:
                state['converted_spotify_playlist_id'] = data['converted_spotify_playlist_id']

        logger.info(f"Updated {error_label} playlist {key} phase: {old_phase} → {new_phase}")
        return {"success": True, "message": f"Phase updated to {new_phase}", "old_phase": old_phase, "new_phase": new_phase}, 200
    except Exception as e:
        logger.error(f"Error updating {error_label} playlist phase: {e}")
        return {"error": str(e)}, 500


def first_artist_str_or_obj(original_track: Dict[str, Any]) -> str:
    """Tidal: first artist from an artists list that may hold strings OR
    objects ({'name': ...}); '' when empty."""
    artists = original_track.get('artists', [])
    if artists:
        return artists[0] if isinstance(artists[0], str) else artists[0].get('name', '')
    return ''


def first_artist_plain(original_track: Dict[str, Any]) -> str:
    """Deezer/Qobuz/Spotify-Public: first artist assuming a list of strings;
    '' when empty."""
    artists = original_track.get('artists', [])
    return artists[0] if artists else ''


def update_discovery_match(
    states: Dict[str, Any],
    get_json,
    *,
    source_log_label: str,
    error_label: str,
    original_track_key: str,
    original_artist_getter,
    join_artist_names,
    extract_artist_name,
    build_fix_modal_spotify_data,
    get_discovery_cache_key,
    get_database,
    get_active_discovery_source,
) -> Tuple[Dict[str, Any], int]:
    """Apply a manually-selected Spotify track to a discovery result (the
    fix-modal flow) and persist it to the discovery cache.

    1:1 lift of the ``update_<source>_discovery_match`` bodies for the four
    sources with the identical structure (Tidal, Deezer, Qobuz, Spotify-Public).
    Per-source pieces are params:

    - ``source_log_label`` (lowercase, e.g. "tidal") for the "Manual match
      updated: ..." line; ``error_label`` for the except log.
    - ``original_track_key`` — the raw-source track key on the result
      ('tidal_track', 'deezer_track', ...).
    - ``original_artist_getter`` — Tidal handles string-or-object artists
      (``first_artist_str_or_obj``); the rest assume strings
      (``first_artist_plain``).
    - the web_server helpers (join/extract artist, build_fix_modal_spotify_data,
      cache-key, get_database, active-discovery-source) are injected so this
      stays free of those globals.
    - ``get_json`` is called INSIDE the try (like the original's
      ``request.get_json()``) so a malformed body yields the same 500.

    Returns ``(payload, status_code)``.

    NOT folded in: iTunes-Link (saves spotify_data directly via a different
    cache signature), YouTube (multi-key original_track fallback), ListenBrainz
    (entirely different unmatch-capable structure, no cache write), Beatport.
    """
    try:
        data = get_json()
        identifier = data.get('identifier')
        track_index = data.get('track_index')
        spotify_track = data.get('spotify_track')

        if not identifier or track_index is None or not spotify_track:
            return {'error': 'Missing required fields'}, 400

        state = states.get(identifier)
        if not state:
            return {'error': 'Discovery state not found'}, 404

        if track_index >= len(state['discovery_results']):
            return {'error': 'Invalid track index'}, 400

        result = state['discovery_results'][track_index]
        old_status = result.get('status')

        result['status'] = 'Found'
        result['status_class'] = 'found'
        result['spotify_track'] = spotify_track['name']
        result['spotify_artist'] = join_artist_names(spotify_track['artists']) if isinstance(spotify_track['artists'], list) else extract_artist_name(spotify_track['artists'])
        result['spotify_album'] = spotify_track['album']
        result['spotify_id'] = spotify_track['id']

        duration_ms = spotify_track.get('duration_ms', 0)
        if duration_ms:
            minutes = duration_ms // 60000
            seconds = (duration_ms % 60000) // 1000
            result['duration'] = f"{minutes}:{seconds:02d}"
        else:
            result['duration'] = '0:00'

        result['spotify_data'] = build_fix_modal_spotify_data(spotify_track)
        result['wing_it_fallback'] = False
        result['manual_match'] = True

        if old_status != 'found' and old_status != 'Found':
            state['spotify_matches'] = state.get('spotify_matches', 0) + 1

        logger.info(f"Manual match updated: {source_log_label} - {identifier} - track {track_index}")
        logger.info(f"   → {result['spotify_artist']} - {result['spotify_track']}")

        try:
            original_track = result.get(original_track_key, {})
            original_name = original_track.get('name', spotify_track['name'])
            original_artist = original_artist_getter(original_track)

            cache_key = get_discovery_cache_key(original_name, original_artist)
            artists_list = spotify_track['artists']
            if isinstance(artists_list, list):
                artists_list = [a if isinstance(a, str) else a.get('name', '') for a in artists_list]
            image_url = spotify_track.get('image_url') or ''
            album_raw = spotify_track.get('album', '')
            if isinstance(album_raw, dict):
                album_obj = dict(album_raw)
                if image_url and not album_obj.get('image_url'):
                    album_obj['image_url'] = image_url
                if image_url and not album_obj.get('images'):
                    album_obj['images'] = [{'url': image_url}]
            else:
                album_obj = {'name': album_raw or ''}
                if image_url:
                    album_obj['image_url'] = image_url
                    album_obj['images'] = [{'url': image_url}]

            matched_data = {
                'id': spotify_track['id'],
                'name': spotify_track['name'],
                'artists': artists_list,
                'album': album_obj,
                'duration_ms': spotify_track.get('duration_ms', 0),
                'image_url': image_url,
                'source': 'spotify',
            }
            cache_db = get_database()
            cache_db.save_discovery_cache_match(
                cache_key[0], cache_key[1], get_active_discovery_source(), 1.0, matched_data,
                original_name, original_artist
            )
            logger.info(f"Manual fix saved to discovery cache: {original_name} by {original_artist}")
        except Exception as cache_err:
            logger.error(f"Error saving manual fix to discovery cache: {cache_err}")

        return {'success': True, 'result': result}, 200
    except Exception as e:
        logger.error(f"Error updating {error_label} discovery match: {e}")
        return {'error': str(e)}, 500


def start_sync(
    states: Dict[str, Any],
    key: str,
    *,
    sync_id_prefix: str,
    not_found_message: str,
    not_ready_message: str,
    convert_fn,
    playlist_name_getter,
    playlist_image_getter,
    activity_label: str,
    error_label: str,
    sync_lock: Any,
    sync_states: Dict[str, Any],
    active_sync_workers: Dict[str, Any],
    submit_sync_task,
    add_activity_item,
) -> Tuple[Dict[str, Any], int]:
    """Kick off a playlist sync from a source's discovered Spotify matches.

    1:1 lift of the ``start_<source>_sync`` bodies for the five sources with
    the identical flow (Tidal, Deezer, Qobuz, Spotify-Public, YouTube). The
    per-source pieces are parameters:

    - ``sync_id_prefix`` — the ``f"{prefix}_{key}"`` sync id.
    - ``convert_fn`` — the source's discovery->spotify-tracks converter.
    - ``playlist_name_getter`` / ``playlist_image_getter`` — Tidal reads an
      object (``.name`` / ``getattr``), the rest read a dict; lifted as the
      ``playlist_name_obj``/``playlist_image_obj`` vs ``playlist_name_strict``/
      ``playlist_image_dict`` accessors.
    - ``activity_label`` vs ``error_label`` — these DIFFER for Spotify-Public:
      activity says "Spotify Link Sync Started" while logs say "Spotify Public".
    - ``submit_sync_task(sync_playlist_id, playlist_name, spotify_tracks,
      playlist_image_url) -> Future`` — wraps sync_executor/_run_sync_task/
      get_current_profile_id so this stays free of those globals.

    Returns ``(payload, status_code)``.

    NOT folded in: iTunes-Link (no final info log), ListenBrainz (submits the
    task without an image arg), Beatport (extra debug logging, 'chart' key).
    """
    try:
        if key not in states:
            return {"error": not_found_message}, 404

        state = states[key]
        state['last_accessed'] = time.time()

        if state['phase'] not in ['discovered', 'sync_complete', 'download_complete']:
            return {"error": not_ready_message}, 400

        spotify_tracks = convert_fn(state['discovery_results'])
        if not spotify_tracks:
            return {"error": "No Spotify matches found for sync"}, 400

        sync_playlist_id = f"{sync_id_prefix}_{key}"
        playlist_name = playlist_name_getter(state)

        add_activity_item("", f"{activity_label} Sync Started", f"'{playlist_name}' - {len(spotify_tracks)} tracks", "Now")

        state['phase'] = 'syncing'
        state['sync_playlist_id'] = sync_playlist_id
        state['sync_progress'] = {}

        with sync_lock:
            sync_states[sync_playlist_id] = {"status": "starting", "progress": {}}

        playlist_image_url = playlist_image_getter(state)
        future = submit_sync_task(sync_playlist_id, playlist_name, spotify_tracks, playlist_image_url)
        active_sync_workers[sync_playlist_id] = future

        logger.info(f"Started {error_label} sync for: {playlist_name} ({len(spotify_tracks)} tracks)")
        return {"success": True, "sync_playlist_id": sync_playlist_id}, 200
    except Exception as e:
        logger.error(f"Error starting {error_label} sync: {e}")
        return {"error": str(e)}, 500
