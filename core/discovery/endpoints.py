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
