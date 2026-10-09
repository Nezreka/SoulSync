"""playlists the user makes inside soulsync.

a user playlist is a mirrored playlist with no upstream: source 'soulsync',
a random source_playlist_id, owned by the profile that made it. everything
else (identify, sync to the server, auto-sync, download missing) is the
mirrored machinery as-is, so there is no second playlist system to keep
in step.

tracks are just artist + title, like a listenbrainz list. album and
duration ride along when the page had them because identify uses them to
pick the right version.

every edit rewrites the whole track list through mirror_playlist. that path
keeps each track's discovery data (keyed on source_track_id), so a match
survives an add, a remove or a reorder. the lock keeps two quick adds from
reading the same list and one of them losing its track.
"""

from __future__ import annotations

import json
import re
import threading
import unicodedata
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("playlists.user_playlists")

USER_PLAYLIST_SOURCE = 'soulsync'

# sources with nothing upstream to pull from. refresh skips them; the
# pipeline still runs identify / sync / download on them.
NO_UPSTREAM_SOURCES = ('file', 'beatport', USER_PLAYLIST_SOURCE)

# sources the pipeline won't run at all (it 400s them). user playlists are
# NOT in here, they get the whole pipeline.
PIPELINE_SKIPPED_SOURCES = ('file', 'beatport')

MAX_NAME_LENGTH = 200

_edit_lock = threading.Lock()


class UserPlaylistError(ValueError):
    """a bad request: the message is safe to show the user."""


def is_user_playlist(playlist: Optional[Dict[str, Any]]) -> bool:
    return bool(playlist) and playlist.get('source') == USER_PLAYLIST_SOURCE


def clean_name(name: Any) -> str:
    cleaned = ' '.join(str(name or '').split())
    if not cleaned:
        raise UserPlaylistError('Give the playlist a name')
    return cleaned[:MAX_NAME_LENGTH]


def clean_track(raw: Any) -> Optional[Dict[str, Any]]:
    """artist + title, plus album / duration / art when present. None when
    there's no title or no artist, since identify can't do anything with it."""
    if not isinstance(raw, dict):
        return None
    title = ' '.join(str(raw.get('track_name') or raw.get('title') or raw.get('name') or '').split())
    artist = ' '.join(str(raw.get('artist_name') or raw.get('artist') or '').split())
    if not title or not artist:
        return None
    album = ' '.join(str(raw.get('album_name') or raw.get('album') or '').split())
    try:
        duration_ms = max(0, int(raw.get('duration_ms') or 0))
    except (TypeError, ValueError):
        duration_ms = 0
    image = raw.get('image_url')
    return {
        'track_name': title,
        'artist_name': artist,
        'album_name': album,
        'duration_ms': duration_ms,
        'image_url': image if isinstance(image, str) and image.startswith(('http://', 'https://', '/')) else None,
    }


_BRACKETED = re.compile(r'\s*[\(\[][^\)\]]*[\)\]]')
_DASH_SUFFIX = re.compile(r'\s+-\s+.*$')
_FEAT = re.compile(r'\s+(feat\.?|ft\.?|featuring|with)\s+.*$', re.I)
_ARTIST_SPLIT = re.compile(r'\s*(,|&|\bx\b|\band\b|;|/)\s*', re.I)
_NON_WORD = re.compile(r'[^\w\s]')


def _plain(text: str) -> str:
    folded = unicodedata.normalize('NFKD', text)
    folded = ''.join(c for c in folded if not unicodedata.combining(c))
    return ' '.join(_NON_WORD.sub(' ', folded.lower()).split())


def track_key(track: Dict[str, Any]) -> Tuple[str, str]:
    """what counts as LIKELY the same song for the already-in-this-playlist
    check. loose on purpose: "Alright", "Alright (Remastered 2015)" and
    "Alright - Radio Edit" by "Kendrick Lamar, Zacari" all land on one key.
    the user is asked, never blocked, so a false match costs a click."""
    title = str(track.get('track_name') or '')
    stripped = _FEAT.sub('', _DASH_SUFFIX.sub('', _BRACKETED.sub('', title)))
    # a title that is ALL brackets ("(Interlude)") keeps them rather than
    # collapsing to nothing and matching every other such title
    title_key = _plain(stripped) or _plain(title)
    artist = str(track.get('artist_name') or '')
    main = _ARTIST_SPLIT.split(_FEAT.sub('', artist))[0]
    artist_key = _plain(main) or _plain(artist)
    if artist_key.startswith('the '):
        artist_key = artist_key[4:]
    return (artist_key, title_key)


def _stored_tracks(db: Any, playlist_id: int) -> List[Dict[str, Any]]:
    """the current rows in mirror_playlist's input shape, discovery data kept."""
    out = []
    for row in db.get_mirrored_playlist_tracks(playlist_id):
        extra = row.get('extra_data')
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except (json.JSONDecodeError, TypeError):
                extra = None
        out.append({
            'track_name': row.get('track_name') or '',
            'artist_name': row.get('artist_name') or '',
            'album_name': row.get('album_name') or '',
            'duration_ms': row.get('duration_ms') or 0,
            'image_url': row.get('image_url'),
            'source_track_id': row.get('source_track_id'),
            'extra_data': extra or None,
        })
    return out


def _write(db: Any, playlist: Dict[str, Any], tracks: List[Dict[str, Any]]) -> None:
    saved = db.mirror_playlist(
        source=USER_PLAYLIST_SOURCE,
        source_playlist_id=playlist['source_playlist_id'],
        name=playlist['name'],
        tracks=tracks,
        profile_id=int(playlist.get('profile_id') or 1),
    )
    if saved is None:
        raise RuntimeError('could not save the playlist')


def create_playlist(db: Any, profile_id: int, name: Any) -> int:
    cleaned = clean_name(name)
    playlist_id = db.mirror_playlist(
        source=USER_PLAYLIST_SOURCE,
        source_playlist_id=uuid.uuid4().hex,
        name=cleaned,
        tracks=[],
        profile_id=int(profile_id),
    )
    if playlist_id is None:
        raise RuntimeError('could not create the playlist')
    logger.info("created user playlist '%s' (%s) for profile %s", cleaned, playlist_id, profile_id)
    return int(playlist_id)


def list_playlists(db: Any, profile_id: int) -> List[Dict[str, Any]]:
    """this profile's user playlists, newest edit first, for the picker."""
    from core.playlists.naming import effective_mirrored_name
    rows = [p for p in db.get_mirrored_playlists(profile_id=int(profile_id)) if is_user_playlist(p)]
    rows.sort(key=lambda p: str(p.get('updated_at') or p.get('mirrored_at') or ''), reverse=True)
    return [{
        'id': p['id'],
        'name': effective_mirrored_name(p),
        'track_count': int(p.get('track_count') or 0),
        'image_url': p.get('image_url'),
    } for p in rows]


def add_tracks(
    db: Any,
    playlist: Dict[str, Any],
    raw_tracks: Iterable[Any],
    *,
    allow_duplicates: bool = False,
) -> Dict[str, Any]:
    """append tracks. a song already in the playlist is skipped and reported
    unless allow_duplicates, so the ui can ask "add anyway?"."""
    incoming = [t for t in (clean_track(r) for r in raw_tracks or []) if t]
    if not incoming:
        raise UserPlaylistError('No track to add')
    with _edit_lock:
        current = _stored_tracks(db, playlist['id'])
        # key -> the copy already there, so the prompt can name it
        have = {track_key(t): t for t in current}
        added: List[Dict[str, Any]] = []
        duplicates: List[Dict[str, Any]] = []
        for track in incoming:
            key = track_key(track)
            if key in have and not allow_duplicates:
                duplicates.append({'track': track, 'existing': have[key]})
                continue
            have.setdefault(key, track)
            added.append(track)
        if added:
            _write(db, playlist, current + added)
    return {
        'added': len(added),
        'duplicates': [{
            'track_name': d['track']['track_name'],
            'artist_name': d['track']['artist_name'],
            # the copy it matched, which may be spelled differently
            'existing_track_name': d['existing']['track_name'],
            'existing_artist_name': d['existing']['artist_name'],
        } for d in duplicates],
        'track_count': len(current) + len(added),
    }


def remove_track(db: Any, playlist: Dict[str, Any], position: int) -> int:
    """drop the track at a 1-based position; returns the new count."""
    with _edit_lock:
        current = _stored_tracks(db, playlist['id'])
        if not 1 <= int(position) <= len(current):
            raise UserPlaylistError('That track is not in the playlist')
        del current[int(position) - 1]
        _write(db, playlist, current)
        return len(current)


def reorder_tracks(db: Any, playlist: Dict[str, Any], order: Iterable[Any]) -> None:
    """order is every current 1-based position, each once, in the new order.
    a stale list (someone added a track since) is refused, not guessed at."""
    try:
        positions = [int(p) for p in order]
    except (TypeError, ValueError):
        raise UserPlaylistError('Bad track order') from None
    with _edit_lock:
        current = _stored_tracks(db, playlist['id'])
        if sorted(positions) != list(range(1, len(current) + 1)):
            raise UserPlaylistError('The playlist changed, reload it and try again')
        _write(db, playlist, [current[p - 1] for p in positions])
