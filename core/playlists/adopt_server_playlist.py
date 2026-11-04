"""adopt a server playlist soulsync didn't make: it becomes one of yours.

a playlist made in navidrome (or plex, or jellyfin) showed up under "other
server playlists" with nothing to match it against and no way to manage it
here. adopting copies it into My Playlists under the same name, so the next
sync writes back to that same server playlist instead of making a second one.

every adopted track is pinned to the exact server track it came from, the same
hard match a Find & Add makes. the default sync mode reconciles the server
playlist toward soulsync's copy, so a track the matcher couldn't place would
otherwise be taken off the server playlist on the first sync.

a playlist the server won't let you edit (a smart playlist, someone else's) is
refused: the server builds it, soulsync could never write it back.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List

from core.playlists import user_playlists as up
from core.sync.match_overrides import record_manual_match
from utils.logging_config import get_logger

logger = get_logger("playlists.adopt_server")


class AdoptError(ValueError):
    """what to tell the user when a playlist can't be adopted"""


def _name_taken(db: Any, profile_id: int, name: str) -> bool:
    """a playlist of this profile, mirror or yours, already syncs under the name"""
    from core.playlists.naming import effective_mirrored_name
    wanted = name.strip().casefold()
    return any(
        str(effective_mirrored_name(p) or '').strip().casefold() == wanted
        for p in (db.get_mirrored_playlists(profile_id=int(profile_id)) or [])
    )


def adopt_server_playlist(db: Any, profile_id: int, server: str, playlist_name: Any,
                          server_tracks: Iterable[Dict[str, Any]], *,
                          readonly: bool = False) -> Dict[str, Any]:
    """copy a server playlist into My Playlists and pin each track to its
    server track. ``server_tracks`` in playlist order: id, title, artist,
    album, duration (ms)."""
    if readonly:
        raise AdoptError("This playlist is read-only on the server (a smart playlist, "
                         "or one you can't edit), so it stays the server's")
    name = up.clean_name(playlist_name)
    if not name:
        raise AdoptError("This playlist has no name")
    if _name_taken(db, profile_id, name):
        raise AdoptError(f'You already have a playlist called "{name}" in SoulSync')

    server_tracks = list(server_tracks or [])
    kept: List[Dict[str, Any]] = []
    raw: List[Dict[str, Any]] = []
    for track in server_tracks:
        candidate = {
            'track_name': track.get('title'),
            'artist_name': track.get('artist'),
            'album_name': track.get('album'),
            'duration_ms': track.get('duration') or 0,
        }
        if track.get('id') in (None, '') or up.clean_track(candidate) is None:
            continue
        kept.append(track)
        raw.append(candidate)
    if not raw:
        raise AdoptError("This playlist has no tracks to adopt")

    playlist_id = up.create_playlist(db, int(profile_id), name)
    playlist = db.get_mirrored_playlist(playlist_id, profile_id=int(profile_id))
    if not playlist:
        raise RuntimeError('could not create the playlist')
    # the server's copy as it is, duplicates included
    up.add_tracks(db, playlist, raw, allow_duplicates=True)

    stored = up._stored_tracks(db, playlist_id)
    pinned = 0
    for row, track in zip(stored, kept, strict=False):
        if record_manual_match(
                db, row.get('source_track_id') or '', server, track['id'],
                server_track_title=str(track.get('title') or ''),
                source_title=row.get('track_name') or '',
                source_artist=row.get('artist_name') or ''):
            pinned += 1
    skipped = len(server_tracks) - len(kept)
    logger.info("adopted %s playlist '%s' as user playlist %s: %d tracks, %d pinned, %d skipped",
                server, name, playlist_id, len(stored), pinned, skipped)
    return {'id': int(playlist_id), 'name': name, 'track_count': len(stored),
            'pinned': pinned, 'skipped': skipped}
