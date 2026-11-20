"""exact soundcloud uploads on the wishlist.

the companion extension hands us a soundcloud track url and asks for THAT
upload, not "the song". so the row is keyed ``soundcloud_exact_<sc id>`` and
the download worker sends it down its own path (core/downloads/
soundcloud_exact_task.py): resolve the stored url, take only the upload with
the same soundcloud id, never search, never fall back to another recording.

the rest of the codebase matches by title and artist in a few places
(dedupe, the owned sweep, metadata removal); each of those has a one-line
guard keyed on ``is_exact_id`` so an exact row and a catalogue row of the
same song never touch each other.

the payload leaves ``album.id`` unset on purpose: canonicalize_wishlist_
identity only rewrites an id whose album has a provider alias, so without
one the row key stays ``soundcloud_exact_<id>``.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

from utils.logging_config import get_logger

logger = get_logger("wishlist.soundcloud_exact")

EXACT_ID_PREFIX = "soundcloud_exact_"
EXACT_SOURCE = "soundcloud_exact"
EXACT_BLOCK = "soundcloud_exact"

ADDED_FROM = "companion_soundcloud_exact"
FAILURE_REASON = "Exact SoundCloud upload (Companion)"

MAX_NAME_LENGTH = 300
MAX_ARTISTS = 10

# soundcloud.com/<first>/... pages that are not a user's track
_RESERVED_FIRST = frozenset({
    'discover', 'stream', 'search', 'you', 'upload', 'charts', 'feed', 'messages',
    'notifications', 'settings', 'pages', 'terms-of-use', 'people', 'jobs',
})
# soundcloud.com/<user>/<second> pages that are not a track
_RESERVED_SECOND = frozenset({
    'sets', 'likes', 'tracks', 'albums', 'reposts', 'popular-tracks', 'followers',
    'following', 'comments', 'spotlight', 'toptracks',
})
_TRACK_HOSTS = frozenset({'soundcloud.com', 'm.soundcloud.com', 'www.soundcloud.com'})
_SHORT_HOST = 'on.soundcloud.com'

# download_tasks states that mean "being worked on right now"
_LIVE_TASK_STATES = frozenset({'pending', 'searching', 'downloading', 'queued', 'post_processing'})


class ExactError(Exception):
    """a refusal the route maps onto the v1 error envelope."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


# ── ids ──────────────────────────────────────────────────────────────────────


def exact_track_id(sc_id: Any) -> str:
    text = str(sc_id if sc_id is not None else '').strip()
    if not text.isdigit():
        raise ValueError(f"not a SoundCloud track id: {sc_id!r}")
    return f"{EXACT_ID_PREFIX}{text}"


def sc_id_of(value: Any) -> Optional[str]:
    """the numeric soundcloud id inside an exact id, or None."""
    if not is_exact_id(value):
        return None
    return str(value).split('::', 1)[0][len(EXACT_ID_PREFIX):]


def is_exact_id(value: Any) -> bool:
    if not isinstance(value, str) or not value:
        return False
    bare = value.split('::', 1)[0]
    return bare.startswith(EXACT_ID_PREFIX) and bare[len(EXACT_ID_PREFIX):].isdigit()


def exact_id_of(track: Any) -> Optional[str]:
    """the bare exact id a track dict carries under any of its id keys."""
    if not isinstance(track, dict):
        return None
    for key in ('id', 'track_id', 'spotify_track_id'):
        if is_exact_id(track.get(key)):
            return str(track[key]).split('::', 1)[0]
    return None


def is_exact_track(track: Any) -> bool:
    return exact_id_of(track) is not None


# ── urls ─────────────────────────────────────────────────────────────────────


def _parts(url: Any):
    from core.soundcloud_client import is_soundcloud_url
    if not isinstance(url, str) or not is_soundcloud_url(url):
        return None
    raw = url.strip()
    candidate = raw if '://' in raw else f'https://{raw}'
    try:
        parsed = urlparse(candidate)
    except ValueError:
        return None
    host = (parsed.netloc or '').lower().split('@')[-1].split(':')[0]
    segments = [s for s in (parsed.path or '').split('/') if s]
    return host, segments


def needs_resolve(url: Any) -> bool:
    """an on.soundcloud.com short link: only a resolve knows which track it is."""
    parts = _parts(url)
    return bool(parts) and parts[0] == _SHORT_HOST and len(parts[1]) == 1


def normalize_track_url(url: Any) -> Optional[str]:
    """``https://soundcloud.com/<user>/<slug>[/s-<token>]`` for a track page,
    the short link itself for ``on.soundcloud.com/<code>``, else None (a set,
    a profile, likes, discover, anything that isn't one track)."""
    parts = _parts(url)
    if not parts:
        return None
    host, segments = parts
    if host == _SHORT_HOST:
        return f"https://{_SHORT_HOST}/{segments[0]}" if len(segments) == 1 else None
    if host not in _TRACK_HOSTS:
        return None
    if len(segments) not in (2, 3):
        return None
    if segments[0].lower() in _RESERVED_FIRST or segments[1].lower() in _RESERVED_SECOND:
        return None
    if len(segments) == 3 and not segments[2].startswith('s-'):
        return None
    return 'https://soundcloud.com/' + '/'.join(segments)


def download_url(block: Dict[str, Any]) -> str:
    """the private share link (it carries the token) or the on.soundcloud.com
    short link (it may stand for one) when that's what was added, else the
    permalink."""
    request_url = str(block.get('request_url') or '')
    if '/s-' in request_url or needs_resolve(request_url):
        return request_url
    return str(block.get('permalink_url') or request_url)


# ── payload ──────────────────────────────────────────────────────────────────


def split_title(title: str, uploader: str) -> Tuple[str, str]:
    from core.soundcloud_client import SoundcloudClient
    return SoundcloudClient._split_artist_from_title(title or '', uploader or '')


def _clean(value: Any) -> str:
    return ' '.join(str(value or '').split())[:MAX_NAME_LENGTH]


def _clean_names(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in values or ():
        name = _clean(value)
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            out.append(name)
    return out


def build_payload(result: Any, request_url: str, *, title: Optional[str] = None,
                  artists: Optional[Iterable[Any]] = None,
                  remixers: Optional[Iterable[Any]] = None) -> Dict[str, Any]:
    """the wishlist track_data for one resolved upload. the uploader is kept
    in the block only; the tags are what the user typed (or the title split)."""
    meta = getattr(result, '_source_metadata', None) or {}
    sc_id = str(meta.get('track_id') or '')
    track_id = exact_track_id(sc_id)
    permalink = str(meta.get('permalink_url') or '')
    uploader = _clean(meta.get('uploader'))
    upload_title = _clean(meta.get('upload_title') or _upload_title(result))
    split_artist, split_name = split_title(upload_title, uploader)

    name = _clean(title) or _clean(split_name) or upload_title or sc_id
    names = _clean_names(artists or ())
    if not names:
        names = _clean_names([split_artist or uploader])
    names = _clean_names([*names, *(remixers or ())])[:MAX_ARTISTS]
    if not names:
        names = ['Unknown Artist']

    duration_ms = int(getattr(result, 'duration', None) or 0)
    artwork = str(meta.get('artwork_url') or '')
    request_norm = normalize_track_url(request_url) or str(request_url or '')
    return {
        'id': track_id,
        'name': name,
        'artists': [{'name': a} for a in names],
        'album': {
            'name': name,
            'album_type': 'single',
            'total_tracks': 1,
            'images': [{'url': artwork}] if artwork else [],
            'artists': [{'name': names[0]}],
        },
        'duration_ms': duration_ms,
        'track_number': 1,
        'disc_number': 1,
        'source': EXACT_SOURCE,
        'provider': EXACT_SOURCE,
        'external_urls': {'soundcloud': permalink},
        EXACT_BLOCK: {
            'sc_id': sc_id,
            'permalink_url': permalink,
            'request_url': request_norm,
            'permalink_url_norm': normalize_track_url(permalink) or permalink,
            'request_url_norm': request_norm,
            'uploader': uploader,
            'upload_title': upload_title,
            'duration_ms': duration_ms,
            'artwork_url': artwork,
            'official_download': bool(meta.get('official_download')),
            'added_via': 'companion',
        },
    }


def _upload_title(result: Any) -> str:
    """the original soundcloud title, rebuilt from the split the client did
    when the metadata doesn't carry it."""
    artist = str(getattr(result, 'artist', '') or '')
    title = str(getattr(result, 'title', '') or '')
    uploader = str((getattr(result, '_source_metadata', None) or {}).get('uploader') or '')
    if artist and title and artist != uploader:
        return f"{artist} - {title}"
    return title


def upload_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    block = payload.get(EXACT_BLOCK) or {}
    return {
        'sc_id': block.get('sc_id'),
        'permalink_url': block.get('permalink_url'),
        'title': block.get('upload_title'),
        'uploader': block.get('uploader'),
        'duration_ms': block.get('duration_ms'),
        'artwork_url': block.get('artwork_url') or None,
        'official_download': bool(block.get('official_download')),
    }


# ── policy ───────────────────────────────────────────────────────────────────


def soundcloud_client(orch: Any):
    try:
        return orch.client('soundcloud') if orch is not None else None
    except Exception as exc:  # noqa: BLE001 - no client is an answer too
        logger.debug("soundcloud client lookup failed: %s", exc)
        return None


def source_state(orch: Any) -> Tuple[bool, Optional[str]]:
    """(enabled, reason). reason is 'source_unavailable' (no yt-dlp / no
    client) or 'source_disabled' (not in the active source chain)."""
    client = soundcloud_client(orch)
    try:
        available = client is not None and bool(client.is_available())
    except Exception:  # noqa: BLE001
        available = False
    if not available:
        return False, 'source_unavailable'
    from core.downloads.source_policy import resolve_source_policy
    try:
        policy = resolve_source_policy(
            mode=orch.mode,
            hybrid_order=orch.hybrid_order,
            hybrid_primary=orch.hybrid_primary,
            hybrid_secondary=orch.hybrid_secondary,
            normalize=orch._normalize_source_name,
            available_sources=orch.registry.names(),
        )
        if not policy.permits('soundcloud'):
            return False, 'source_disabled'
    except Exception as exc:  # noqa: BLE001 - an unreadable chain isn't a yes
        logger.debug("source policy unreadable: %s", exc)
        return False, 'source_disabled'
    return True, None


def _json_dict(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _valid_block(block: Any) -> Optional[Dict[str, Any]]:
    if isinstance(block, dict) and str(block.get('sc_id') or '').isdigit() and (
            block.get('permalink_url') or block.get('request_url')):
        return block
    return None


def exact_source_for(track_info: Any, db: Any = None, profile_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """the stored soundcloud block for an exact track: on the task, in its
    payload, on its wishlist row, or on a playlist row that names it. a
    rebuilt payload (a cancel, a playlist sync) can lose the block; the id
    alone finds it again."""
    if not isinstance(track_info, dict):
        return None
    block = _valid_block(track_info.get(EXACT_BLOCK))
    if block:
        return block
    for key in ('track_data', 'spotify_data'):
        block = _valid_block(_json_dict(track_info.get(key)).get(EXACT_BLOCK))
        if block:
            return block
    track_id = next((str(track_info.get(k)) for k in ('id', 'track_id', 'spotify_track_id')
                     if is_exact_id(track_info.get(k))), None)
    if not track_id or db is None:
        return None
    bare = track_id.split('::', 1)[0]
    try:
        row = db.get_wishlist_track(bare, profile_id=int(profile_id or 1))
    except Exception as exc:  # noqa: BLE001
        logger.debug("exact block: wishlist lookup failed: %s", exc)
        row = None
    if row:
        block = _valid_block(_json_dict(row.get('spotify_data')).get(EXACT_BLOCK))
        if block:
            return block
    try:
        with db._get_connection() as conn:
            sql = ("SELECT t.extra_data FROM mirrored_playlist_tracks t "
                   "JOIN mirrored_playlists p ON p.id = t.playlist_id "
                   "WHERE t.source_track_id = ?")
            params: List[Any] = [bare]
            if profile_id is not None:
                sql += " AND p.profile_id = ?"
                params.append(int(profile_id))
            rows = conn.execute(sql + " ORDER BY t.id DESC LIMIT 20", params).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("exact block: playlist lookup failed: %s", exc)
        rows = []
    for row in rows:
        matched = _json_dict(_json_dict(row[0]).get('matched_data'))
        block = _valid_block(matched.get(EXACT_BLOCK))
        if block:
            return block
    return None


# ── add ──────────────────────────────────────────────────────────────────────


def _run_async(coro):
    from utils.async_helpers import run_async
    return run_async(coro, timeout=60)


def _validate_playlist(db: Any, playlist_id: Optional[int], profile_id: int) -> Optional[Dict[str, Any]]:
    if playlist_id is None:
        return None
    from core.playlists import user_playlists
    playlist = db.get_mirrored_playlist(int(playlist_id), profile_id=int(profile_id))
    if not playlist:
        raise ExactError(400, 'BAD_PLAYLIST', 'That playlist was not found.')
    if not user_playlists.is_user_playlist(playlist):
        raise ExactError(422, 'PLAYLIST_NOT_EDITABLE',
                         'Exact uploads can only be added to your own SoulSync playlists.')
    return playlist


def _attach(db: Any, playlist: Optional[Dict[str, Any]], payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """the playlist half of an add. a failure here is reported, not raised:
    the wishlist write already happened."""
    if playlist is None:
        return None
    from core.playlists import user_playlists
    from core.playlists.naming import effective_mirrored_name
    try:
        res = user_playlists.add_exact_track(db, playlist, payload)
    except Exception as exc:  # noqa: BLE001
        logger.warning("exact upload %s: playlist %s add failed: %s", payload.get('id'), playlist.get('id'), exc)
        return {'id': int(playlist['id']), 'added': False, 'error': str(exc) or 'playlist write failed'}
    return {'id': int(playlist['id']), 'name': effective_mirrored_name(playlist),
            'added': bool(res.get('added')), 'already_present': bool(res.get('already_present'))}


def _drop_dead_matches(db: Any, track_id: str, profile_id: int) -> int:
    """delete this upload's id matches whose library file is gone (a dead
    match would hold every re-add off as 'already in the library'). dead by
    the same rule the status reads (_live_match_file)."""
    from core.library import manual_library_match as _mlm
    dropped = 0
    for match in _id_matches(db, track_id, profile_id):
        if _live_match_file(db, match) is None and _mlm.delete_match(db, int(match['id']), int(profile_id)):
            logger.info("exact upload %s: dropped a match whose file is gone (%s)",
                        track_id, match.get('library_file_path') or match.get('library_track_id'))
            dropped += 1
    return dropped


def add_exact(db: Any, orch: Any, *, url: Any, title: Any = None, artists: Any = None,
              remixers: Any = None, playlist_id: Optional[int] = None,
              quality_profile_id: Optional[int] = None, profile_id: int = 1,
              run_async=None) -> Tuple[int, Dict[str, Any]]:
    """wishlist one exact upload. returns (http status, data); refusals raise
    ExactError and write no row (a refusal from add_to_wishlist_detailed can
    land after it cleared a stale ignore entry, as for every user add)."""
    request_url = normalize_track_url(url)
    if not request_url:
        raise ExactError(400, 'NOT_SOUNDCLOUD_TRACK', 'That is not a SoundCloud track link.')

    enabled, reason = source_state(orch)
    if not enabled:
        if reason == 'source_disabled':
            raise ExactError(409, 'SOURCE_DISABLED',
                             'SoundCloud is not an enabled download source in Settings.')
        raise ExactError(503, 'SOURCE_UNAVAILABLE', 'SoundCloud downloads need yt-dlp on the server.')

    if quality_profile_id is not None and not db.quality_profile_exists(quality_profile_id):
        raise ExactError(400, 'BAD_REQUEST', 'Unknown quality_profile_id.')

    playlist = _validate_playlist(db, playlist_id, profile_id)

    client = soundcloud_client(orch)
    runner = run_async or _run_async
    try:
        results, _albums = runner(client.resolve_url(request_url))
    except Exception as exc:  # noqa: BLE001 - resolve_url never raises; a timeout can
        logger.warning("exact soundcloud resolve failed for %s: %s", request_url, exc)
        results = []
    results = list(results or [])
    if not results:
        raise ExactError(422, 'UPLOAD_UNAVAILABLE',
                         'SoundCloud could not find that upload (deleted, private or region-locked).')
    if len(results) != 1:
        raise ExactError(400, 'NOT_SOUNDCLOUD_TRACK', 'That link is not a single SoundCloud track.')
    result = results[0]
    sc_id = str((getattr(result, '_source_metadata', None) or {}).get('track_id') or '')
    if not sc_id.isdigit():
        raise ExactError(422, 'UPLOAD_UNAVAILABLE', 'SoundCloud did not return a track id for that upload.')

    payload = build_payload(result, request_url, title=title, artists=artists, remixers=remixers)
    track_id = payload['id']
    block = payload[EXACT_BLOCK]

    # the uploader isn't in the tags, so the add's own blocklist check can't see it
    if block.get('uploader') and db.blocklist_reason_for_track(
            profile_id, {'artists': [{'name': block['uploader']}], 'name': payload['name']}):
        raise ExactError(409, 'BLOCKLISTED', 'That uploader is on your blocklist.')

    existing = db.get_wishlist_track(track_id, profile_id=profile_id)
    if playlist is not None:
        from core.playlists.naming import effective_mirrored_name
        source_type = 'playlist'
        source_info: Optional[Dict[str, Any]] = {
            'playlist_id': playlist['source_playlist_id'],
            'playlist_name': effective_mirrored_name(playlist),
            'added_from': ADDED_FROM,
        }
    elif existing:
        source_type, source_info = existing.get('source_type') or 'companion', None
    else:
        source_type, source_info = 'companion', {'added_from': ADDED_FROM}

    upload = upload_summary(payload)
    if not existing and upload_acquired(db, payload, profile_id) and not db.blocklist_reason_for_track(
            profile_id, payload):
        # its own download is in the library (a live id match, or its history
        # file): nothing to queue, but it can still join the playlist
        return 200, {'created': False, 'state': 'acquired', 'track_id': track_id,
                     'upload': upload, 'track': None, 'playlist': _attach(db, playlist, payload)}

    def _add():
        return db.add_to_wishlist_detailed(
            payload,
            failure_reason=FAILURE_REASON,
            source_type=source_type,
            source_info=source_info,
            profile_id=profile_id,
            quality_profile_id=quality_profile_id,
            quality_profile_explicit=quality_profile_id is not None,
            user_initiated=True,
        )

    outcome = _add()
    status = outcome.get('status')
    if status == 'satisfied' and not upload_acquired(db, payload, profile_id):
        # a match whose file is gone holds the add off: drop it and queue
        if _drop_dead_matches(db, track_id, profile_id):
            outcome = _add()
            status = outcome.get('status')
    if status == 'satisfied':
        # only a live id match is left holding it off: the upload is in
        return 200, {'created': False, 'state': 'acquired', 'track_id': track_id,
                     'upload': upload, 'track': None, 'playlist': _attach(db, playlist, payload)}
    if status == 'skipped' and outcome.get('reason') == 'blocklisted':
        raise ExactError(409, 'BLOCKLISTED', 'That artist, track or album is on your blocklist.')
    if status == 'rejected':
        raise ExactError(400, 'REJECTED', outcome.get('reason') or 'Track rejected.')
    if status not in ('created', 'updated'):
        raise ExactError(500, 'WISHLIST_ERROR', outcome.get('reason') or 'Could not add the upload.')

    stored_key = outcome.get('track_id') or track_id
    if status == 'updated':
        try:
            db.reset_wishlist_retry_backoff(spotify_track_ids=[stored_key], profile_id=profile_id)
        except Exception as exc:  # noqa: BLE001 - the row is there; it just waits its turn
            logger.debug("exact re-add: backoff reset failed: %s", exc)

    playlist_out = _attach(db, playlist, payload)
    created = status == 'created'
    state = status_for(db, sc_id=sc_id, profile_id=profile_id).get('state') or 'queued'
    return (201 if created else 200), {
        'created': created,
        'state': state,
        'track_id': stored_key,
        'upload': upload,
        # the stored row; the route serializes it
        'track': db.get_wishlist_track(stored_key, profile_id=profile_id),
        'playlist': playlist_out,
    }


# ── status ───────────────────────────────────────────────────────────────────


def _without_token(url_norm: str) -> str:
    """``https://soundcloud.com/<user>/<slug>`` for a normalized share link
    (drops its ``/s-<token>`` segment); anything else as it is."""
    parts = url_norm.split('/')
    if len(parts) == 6 and parts[2] == 'soundcloud.com' and parts[5].startswith('s-'):
        return '/'.join(parts[:5])
    return url_norm


def _url_hit(url_norm: str, stored: Iterable[Any]) -> int:
    """2: one of the stored urls is the asked one. 1: the same user/slug,
    only the share token differs (added by the public permalink, asked by
    the share link, or back). 0: neither."""
    stored = [str(s) for s in stored if s]
    if url_norm in stored:
        return 2
    base = _without_token(url_norm)
    return 1 if any(_without_token(s) == base for s in stored) else 0


def _best_by_url(rows: Iterable[Any], url_norm: str, stored_urls) -> Optional[Any]:
    """the newest row with the asked url, else the newest with the same
    user/slug. ``rows`` come newest first."""
    fallback = None
    for row in rows:
        hit = _url_hit(url_norm, stored_urls(row))
        if hit == 2:
            return row
        if hit == 1 and fallback is None:
            fallback = row
    return fallback


def _row_by_url(db: Any, url_norm: str, profile_id: int) -> Optional[Dict[str, Any]]:
    def stored(row):
        block = _json_dict(_json_dict(row['spotify_data']).get(EXACT_BLOCK))
        return block.get('request_url_norm'), block.get('permalink_url_norm')
    try:
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM wishlist_tracks WHERE profile_id = ? "
                "AND spotify_track_id LIKE 'soundcloud_exact_%' ORDER BY id DESC",
                (int(profile_id),),
            ).fetchall()
            row = _best_by_url(rows, url_norm, stored)
            return dict(row) if row else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("exact status: url lookup failed: %s", exc)
        return None


def _matched_id_by_url(db: Any, url_norm: str, profile_id: int) -> Optional[str]:
    def stored(row):
        context = _json_dict(row[1])
        return context.get('request_url_norm'), context.get('permalink_url_norm')
    try:
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT source_track_id, source_context_json FROM manual_library_track_matches "
                "WHERE profile_id = ? AND source = ? ORDER BY updated_at DESC",
                (int(profile_id), EXACT_SOURCE),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("exact status: matched url lookup failed: %s", exc)
        return None
    row = _best_by_url(rows, url_norm, stored)
    value = str(row[0]) if row else ''
    return value if is_exact_id(value) else None


# what an exact download records in library_history
# (imports/side_effects.record_library_history_download): the search result is
# the candidate's __dict__, which has no track_id or id key, so
# source_track_id is the task's track_info id, the exact id. source_filename
# is the candidate's ``<sc id>||<url>||<name>`` dispatch key. only a track_info
# without an id leaves source_track_id to the key's bare sc id; that bare id
# counts only beside its own dispatch key, never on a row of another shape.
_HISTORY_ID_SQL = "(source_track_id = ? OR (source_track_id = ? AND source_filename LIKE ?))"


def _history_id_params(track_id: str, sc_id: str) -> Tuple[str, str, str]:
    if not str(sc_id or '').isdigit():
        return track_id, track_id, ''   # no bare leg: it repeats the exact one
    return track_id, sc_id, f"{sc_id}||%"


def _history_id_by_url(db: Any, url_norm: str, profile_id: int) -> Optional[str]:
    """the exact id of a downloaded upload by its url, from the download
    history alone (a media-server import whose library row didn't exist yet
    records no match). the history keeps the candidate's ``<sc id>||<url>||
    <name>`` dispatch key; the row must be that upload's exact download
    (_HISTORY_ID_SQL) and its file still in this profile's library."""
    def stored(row):
        parts = str(row[0] or '').split('||')
        return (normalize_track_url(parts[1]),) if len(parts) >= 2 else ()
    try:
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT source_filename, source_track_id FROM library_history WHERE event_type = 'download' "
                "AND download_source = 'SoundCloud' AND source_filename LIKE ? "
                "ORDER BY id DESC LIMIT 50",
                (f"%||{_without_token(url_norm)}%",),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        logger.debug("exact status: history url lookup failed: %s", exc)
        return None
    for row in rows:
        if not _url_hit(url_norm, stored(row)):
            continue
        sc_id = str(row[0]).split('||', 1)[0]
        if not sc_id.isdigit() or str(row[1] or '') not in (exact_track_id(sc_id), sc_id):
            continue   # a catalogue track's download of the same upload
        if _acquired(db, exact_track_id(sc_id), sc_id, profile_id):
            return exact_track_id(sc_id)
    return None


def match_context(block: Dict[str, Any]) -> str:
    """what the import's manual match keeps, so a status by url still finds
    the upload after its wishlist row is gone."""
    return json.dumps({
        'request_url_norm': block.get('request_url_norm'),
        'permalink_url_norm': block.get('permalink_url_norm'),
        'sc_id': block.get('sc_id'),
    })


def _live_task(track_id: str) -> bool:
    from core.runtime_state import download_tasks, tasks_lock
    with tasks_lock:
        for task in download_tasks.values():
            info = task.get('track_info') if isinstance(task, dict) else None
            if exact_id_of(info) != track_id:
                continue
            if task.get('status') in _LIVE_TASK_STATES:
                return True
    return False


def _match_file(db: Any, match: Dict[str, Any]) -> str:
    """the matched library row's current file (a rescan can move it), else
    the path the match stored."""
    lib_id = match.get('library_track_id')
    getter = getattr(db, 'api_get_tracks_by_ids', None)
    if lib_id and getter is not None:
        try:
            rows = getter([lib_id]) or []
            path = rows[0].get('file_path') if rows and isinstance(rows[0], dict) else None
            if path:
                return str(path)
        except Exception as exc:  # noqa: BLE001
            logger.debug("exact match file lookup failed for %s: %s", lib_id, exc)
    return str(match.get('library_file_path') or '')


def _id_matches(db: Any, track_id: str, profile_id: int) -> List[Dict[str, Any]]:
    """every manual match this profile holds for the exact id, under any
    source label or server (a dead one must not shadow a live one, or back)."""
    try:
        with db._get_connection() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM manual_library_track_matches WHERE profile_id = ? "
                "AND source_track_id = ? ORDER BY updated_at DESC, id DESC",
                (int(profile_id), track_id)).fetchall()]
    except Exception as exc:  # noqa: BLE001
        logger.debug("exact match lookup failed for %s: %s", track_id, exc)
        return []


# lib2 file states that mean the file is not in the library any more: a delete
# keeps its row as 'deleted' (library2/file_delete), a scan that can't find it
# marks it 'missing_confirmed' (library2/scan)
_GONE_FILE_STATES = ('deleted', 'missing_confirmed')


def _visibly_missing(path: str) -> bool:
    """is the file provably not on disk? only when this process can see where
    it should be: its folder, or a library folder it sits under, exists here.
    a media-server path SoulSync doesn't mount proves nothing either way."""
    if os.path.exists(path):
        return False
    try:
        from core.library.path_resolver import library_base_dirs, resolve_library_file_path
        from core.settings import config_manager
        if resolve_library_file_path(path, config_manager=config_manager, allow_fuzzy=False):
            return False
        if os.path.isdir(os.path.dirname(path)):
            return True
        norm = os.path.normcase(os.path.realpath(path))
        for root in library_base_dirs(config_manager):
            base = os.path.normcase(os.path.realpath(root))
            if os.path.commonpath([base, norm]) == base:
                return True
    except Exception as exc:  # noqa: BLE001 - can't check is not gone
        logger.debug("exact file check failed for %r: %s", path, exc)
    return False


def carries_upload_id(path: Any, sc_id: Optional[str]) -> bool:
    """is ``path`` named as an exact upload's import files it: ``[<sc id>]``
    on the stem (imports/paths.exact_upload_path)?"""
    if not sc_id or not path:
        return False
    stem = os.path.splitext(os.path.basename(str(path).replace('\\', '/')))[0]
    return stem.endswith(f" [{sc_id}]")


def _own_file_row(conn: Any, match: Dict[str, Any], stored: str) -> Optional[Any]:
    """the lib2 file row of the match's own file: the row at the path it
    stored, else (a reorganize moved it, a rescan re-keyed the track) the
    file on its track that has its name or carries the upload's id. never
    the track's primary: the catalogue can file the upload as a second file
    of the original's track, and the primary is then the original. a row at
    the stored path that is gone, or whose file is visibly missing, only
    stands when no live own file turns up on the track: a file moved outside
    SoulSync leaves its old row 'missing_confirmed' and the rescan adds an
    active one at the new path."""
    from core.library2.track_files import path_spellings, track_id_for_path
    forms = path_spellings(stored)
    gone = "(COALESCE(file_state,'active') IN ('deleted','missing_confirmed'))"
    row = conn.execute(
        f"SELECT path, file_state FROM lib2_track_files WHERE path IN ({','.join('?' * len(forms))}) "
        f"ORDER BY {gone}, id DESC LIMIT 1", forms).fetchone()
    if row and (row[1] or 'active') not in _GONE_FILE_STATES and not _visibly_missing(str(row[0] or stored)):
        return row
    track_ids = [t for t in (match.get('library_track_id'), track_id_for_path(conn, stored)) if t]
    name = os.path.basename(stored.replace('\\', '/'))
    sc_id = sc_id_of(match.get('source_track_id'))
    first = None
    for lib_id in dict.fromkeys(str(t) for t in track_ids):
        for cand in conn.execute(
                f"SELECT path, file_state FROM lib2_track_files WHERE track_id = ? "
                f"ORDER BY {gone}, id DESC", (lib_id,)).fetchall():
            path = str(cand[0] or '')
            if not path or path in forms or not (
                    os.path.basename(path.replace('\\', '/')) == name or carries_upload_id(path, sc_id)):
                continue
            if (cand[1] or 'active') not in _GONE_FILE_STATES and not _visibly_missing(path):
                return cand
            first = first or cand
    return row or first


def _live_match_file(db: Any, match: Dict[str, Any]) -> Optional[str]:
    """the file an id match stands for, or None when the match is dead.

    match_is_live calls a match live while its library track ROW exists, and a
    deleted or scan-missed file keeps its rows (file_state 'deleted' /
    'missing_confirmed'). an exact upload is that one file, so a local-library
    match needs ITS file (the path the match stored, _own_file_row) still live,
    and on disk where this process can see it; the track's primary file is
    read only for a match that stored no path. a media-server match ('server'
    kind) points at the server's id, not a file row, and keeps match_is_live's
    rule. '' when the match is live but its file is unknown; a failed lookup
    counts as live (a caller drops dead matches, and can't-check is not gone)."""
    from core.library import manual_library_match as _mlm
    if match.get('library_track_id_kind') == 'server':
        return _match_file(db, match) if _mlm.match_is_live(db, match) else None
    track_ids: List[Any] = []
    if match.get('library_track_id'):
        track_ids.append(match['library_track_id'])
    stored = match.get('library_file_path')
    try:
        with db._get_connection() as conn:
            if stored:
                row = _own_file_row(conn, match, str(stored))
                if row and (row[1] or 'active') in _GONE_FILE_STATES:
                    return None
                path = str(row[0]) if row and row[0] else str(stored)
                return None if _visibly_missing(path) else path
            for lib_id in track_ids:
                row = conn.execute(
                    "SELECT path, file_state FROM lib2_track_files WHERE track_id = ? "
                    "AND is_primary = 1 ORDER BY id DESC LIMIT 1", (lib_id,)).fetchone()
                if not row or not row[0] or (row[1] or 'active') in _GONE_FILE_STATES:
                    continue
                if not _visibly_missing(str(row[0])):
                    return str(row[0])
    except Exception as exc:  # noqa: BLE001
        logger.debug("exact match liveness failed for %s: %s", match.get('library_track_id'), exc)
        return str(stored or '')
    return None


def _acquired_file(db: Any, track_id: str, sc_id: str, profile_id: int) -> Optional[str]:
    """the file of this upload's own acquisition: a live id match's library
    file, else its download history file still in this profile's library.
    '' when its live match carries no path (acquired, file unknown); None
    when it isn't acquired."""
    for match in _id_matches(db, track_id, profile_id):
        path = _live_match_file(db, match)
        if path is not None:
            return path
    try:
        with db._get_connection() as conn:
            rows = conn.execute(
                "SELECT file_path FROM library_history WHERE event_type = 'download' "
                "AND download_source = 'SoundCloud' AND " + _HISTORY_ID_SQL +
                " ORDER BY id DESC LIMIT 5",
                _history_id_params(track_id, sc_id),
            ).fetchall()
            in_library = _library_path_test(conn, profile_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("exact status: history lookup failed: %s", exc)
        return None
    return next((str(row[0]) for row in rows
                 if row[0] and os.path.isfile(row[0]) and in_library(row[0])), None)


def _acquired(db: Any, track_id: str, sc_id: str, profile_id: int) -> bool:
    return _acquired_file(db, track_id, sc_id, profile_id) is not None


def _library_path_test(conn: Any, profile_id: int):
    """is a downloaded file in the library this profile reads?

    library_history has no profile column, and doesn't need one: whose
    library a file is in follows from its folder (core/library2/
    library_roots). with nobody keeping a library of their own there is one
    library and every profile reads it. otherwise the file counts only when
    its folder is this profile's library (its own folder, or the shared one
    for a profile on the shared library); a path under no known folder can't
    be placed, so it proves nothing."""
    from core.library_scope import any_own_library_exists, library_scope_for_profile, owner_for_scope
    if not any_own_library_exists():
        return lambda _path: True
    from core.library2.library_roots import load_roots, owner_for_path
    roots = load_roots(conn)
    wanted = owner_for_scope(library_scope_for_profile(profile_id))

    def test(path: Any) -> bool:
        known, owner = owner_for_path(roots, path)
        return known and owner == wanted
    return test


def _pid(profile_id: Any) -> int:
    try:
        return int(profile_id or 1)
    except (TypeError, ValueError):
        return 1


def upload_acquired(db: Any, track: Any, profile_id: Any = 1) -> bool:
    """is this exact upload's own download in the library (its id match, or
    its download history with the file still there)? no network."""
    track_id = exact_id_of(track)
    if not track_id:
        return False
    return _acquired(db, track_id, sc_id_of(track_id) or '', _pid(profile_id))


def upload_file(db: Any, track: Any, profile_id: Any = 1) -> Optional[str]:
    """the library file of this exact upload's own download (the same legs
    as upload_acquired), or None. never a file found by name."""
    track_id = exact_id_of(track)
    if not track_id:
        return None
    return _acquired_file(db, track_id, sc_id_of(track_id) or '', _pid(profile_id)) or None


def status_for(db: Any, *, sc_id: Optional[str] = None, url: Optional[str] = None,
               profile_id: int = 1) -> Dict[str, Any]:
    """where one exact upload stands for this profile. no network."""
    out: Dict[str, Any] = {'state': 'none', 'matched': False, 'track_id': None,
                           'failure_reason': None, 'retry_count': 0, 'last_attempted': None}
    row = None
    if sc_id:
        track_id = exact_track_id(sc_id)
        row = db.get_wishlist_track(track_id, profile_id=profile_id)
    else:
        url_norm = normalize_track_url(url) if url else None
        if not url_norm:
            return out
        row = _row_by_url(db, url_norm, profile_id)
        if row is not None:
            track_id = str(row['spotify_track_id']).split('::', 1)[0]
        else:
            # the row is gone once the download lands; the match recorded on
            # import (resolution.check_and_remove_from_wishlist) keeps the url,
            # else the download history does
            track_id = (_matched_id_by_url(db, url_norm, profile_id)
                        or _history_id_by_url(db, url_norm, profile_id))
            if not track_id:
                return out
        sc_id = sc_id_of(track_id)

    out['track_id'] = track_id
    if _live_task(track_id):
        out['state'] = 'downloading'
    if row:
        retry = int(row.get('retry_count') or 0)
        out['retry_count'] = retry
        out['last_attempted'] = row.get('last_attempted')
        if retry > 0:
            out['failure_reason'] = row.get('failure_reason')
        if out['state'] == 'none':
            out['state'] = 'failed' if retry > 0 else 'queued'
    elif out['state'] == 'none' and _acquired(db, track_id, sc_id or '', profile_id):
        out['state'] = 'acquired'
    # matched: something is known about this upload (a row, its match or
    # history, a live task), the same answer by sc_id as by url
    out['matched'] = out['state'] != 'none'
    return out


__all__ = [
    "EXACT_BLOCK",
    "EXACT_ID_PREFIX",
    "EXACT_SOURCE",
    "ExactError",
    "add_exact",
    "build_payload",
    "download_url",
    "exact_source_for",
    "exact_track_id",
    "is_exact_id",
    "is_exact_track",
    "needs_resolve",
    "normalize_track_url",
    "source_state",
    "split_title",
    "status_for",
    "upload_acquired",
    "upload_file",
]
