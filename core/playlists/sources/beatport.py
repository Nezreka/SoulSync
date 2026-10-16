"""beatport playlist source adapter: a mirrored beatport list, refreshed.

a beatport mirror's source_playlist_id is its list ref (core/beatport/refs.py),
so refresh fetches the same list again and the mirror follows the live chart:
the week's new tracks come in, dropped ones go out. tracks carry no extra_data,
so a track that stays keeps its stored identification, and the fetch files each
track's isrc in the metadata cache, which is what identifies the new ones
(core.discovery.direct_match).

mirrors from before refs (a random chart hash) and releases (which never
change) aren't refreshable: see ``is_refreshable_mirror``.
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from core.beatport.playlist import display_name
from core.beatport.refs import parse_ref
from core.playlists.sources.base import (
    NormalizedTrack,
    PlaylistDetail,
    PlaylistMeta,
    PlaylistSource,
    SOURCE_BEATPORT,
)


def is_snapshot_mirror(playlist: Any) -> bool:
    """a dated, frozen copy of a beatport list (core.beatport.refs.parse_snapshot)."""
    from core.beatport.refs import parse_snapshot
    return (isinstance(playlist, dict) and playlist.get('source') == SOURCE_BEATPORT
            and parse_snapshot(playlist.get('source_playlist_id')) is not None)


def is_refreshable_mirror(playlist: Any) -> bool:
    """true for a beatport mirror whose id is a live list. the refresh, the
    pipeline and the pipeline run button all ask this."""
    if not isinstance(playlist, dict) or playlist.get('source') != SOURCE_BEATPORT:
        return False
    ref = parse_ref(playlist.get('source_playlist_id'))
    return bool(ref and ref.refreshable)


def _default_fetch(ref: str) -> Optional[dict]:
    from core.beatport.fetch import fetch_list
    # a refresh wants beatport now, not the page browsed ten minutes ago
    return fetch_list(ref, use_cache=False)


class BeatportPlaylistSource(PlaylistSource):
    name = SOURCE_BEATPORT
    supports_listing = False
    supports_refresh = True
    requires_auth = False

    def __init__(self, fetch: Callable[[str], Optional[dict]] = _default_fetch):
        self._fetch = fetch

    def is_authenticated(self) -> bool:
        return True

    def list_playlists(self) -> List[PlaylistMeta]:
        return []

    def get_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        ref = parse_ref(playlist_id)
        if ref is None:
            return None
        listed = self._fetch(str(ref))
        if not listed or not listed.get('tracks'):
            return None
        meta = PlaylistMeta(
            source=self.name,
            source_playlist_id=listed['ref'],
            name=listed.get('name') or ref.default_name(),
            track_count=len(listed['tracks']),
            owner=listed.get('creator'),
            description=listed.get('url') or ref.url,
            image_url=listed.get('image') or None,
            source_url=listed.get('url') or ref.url,
        )
        return PlaylistDetail(meta=meta, tracks=[self._track(t, i) for i, t in enumerate(listed['tracks'])])

    def refresh_playlist(self, playlist_id: str) -> Optional[PlaylistDetail]:
        return self.get_playlist(playlist_id)

    @staticmethod
    def _track(track: dict, position: int) -> NormalizedTrack:
        names = [a.get('name') for a in track.get('artists') or [] if isinstance(a, dict) and a.get('name')]
        return NormalizedTrack(
            position=position,
            track_name=display_name(track),
            artist_name=', '.join(names) or track.get('artist') or 'Unknown Artist',
            album_name=track.get('release_name') or None,
            duration_ms=int(track.get('duration_ms') or 0),
            source_track_id=track.get('id') or None,
            image_url=track.get('release_image') or None,
            needs_discovery=False,
            # empty on purpose: extra_data here would replace the
            # identification a surviving track already has
            extra={},
        )
