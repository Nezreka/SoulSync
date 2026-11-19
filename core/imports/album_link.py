"""Look a release up straight from a pasted link or id (#2203).

Ericsson: the import search only takes text, and text needs fixing up
(spelling, spaces, dashes) before the right release shows, while the
release's MusicBrainz page is already open in another tab. So the search box
also takes a link (or a bare MBID): ``core.library.direct_id.detect_link``
reads the service and id off it, and this fetches that exact release through
the same client the match step then asks for its tracklist.

Answers None for ordinary text (the caller searches as before), else
``{"source", "albums", "message"}``: the release (every edition, for a
MusicBrainz release group), or no albums and a message saying plainly why.
Nothing here raises on a bad link.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional

from core.library.direct_id import detect_link

logger = logging.getLogger(__name__)

# sources whose release ids the import match step can take: the metadata
# clients that answer an album by id in the shape the matcher reads
LINK_SOURCES = ('musicbrainz', 'spotify', 'deezer', 'itunes', 'discogs')

_LABELS = {
    'musicbrainz': 'MusicBrainz', 'spotify': 'Spotify', 'deezer': 'Deezer',
    'itunes': 'Apple Music', 'discogs': 'Discogs', 'tidal': 'Tidal', 'qobuz': 'Qobuz',
    'amazon': 'Amazon Music', 'jiosaavn': 'JioSaavn', 'bandcamp': 'Bandcamp',
}

_WHICH = 'Paste a MusicBrainz, Spotify, Deezer, Apple Music or Discogs release link, or search by name.'


def _default_client(source: str) -> Any:
    from core.metadata.registry import get_client_for_source
    return get_client_for_source(source)


def _album_of(track: Any) -> Dict[str, Any]:
    album = track.get('album') if isinstance(track, dict) else None
    return album if isinstance(album, dict) else {}


def _fetch_album(client: Any, source: str, album_id: str) -> Optional[Dict[str, Any]]:
    if source == 'spotify':
        # a spotify id must come back as spotify, never the fallback source's guess
        return client.get_album(album_id, allow_fallback=False)
    if source == 'deezer':
        return client.get_album_metadata(album_id, include_tracks=False)
    return client.get_album(album_id, include_tracks=False)


def _fetch_track(client: Any, source: str, track_id: str) -> Optional[Dict[str, Any]]:
    if source == 'spotify':
        return client.get_track_details(track_id, allow_fallback=False)
    return client.get_track_details(track_id)


def _editions(client: Any, group_id: str) -> List[Any]:
    fetch = getattr(client, 'release_group_editions', None)
    if fetch is None:
        return []
    try:
        return list(fetch(group_id) or [])
    except Exception as e:  # noqa: BLE001 - a miss, the single release still shows
        logger.debug("release group %s editions failed: %s", group_id, e)
        return []


def _is_group(album: Dict[str, Any]) -> bool:
    url = str((album.get('external_urls') or {}).get('musicbrainz') or '')
    return '/release-group/' in url


def lookup_album_link(
    text: str,
    client_for: Optional[Callable[[str], Any]] = None,
) -> Optional[Dict[str, Any]]:
    """The release a pasted link or MBID names, or None when ``text`` isn't one."""
    ref = detect_link(text)
    if ref is None:
        return None
    client_for = client_for or _default_client
    label = _LABELS.get(ref.service, ref.service)

    def miss(message: str) -> Dict[str, Any]:
        return {'source': ref.service, 'albums': [], 'message': message}

    if not ref.id:
        return miss(f"That {label} link doesn't point at a release. {_WHICH}")
    if ref.service not in LINK_SOURCES:
        return miss(f"{label} links can't be matched here yet. {_WHICH}")
    if ref.kind == 'artist':
        return miss(f"That's a {label} artist link. Paste the link to the release itself.")
    if ref.service == 'musicbrainz' and ref.kind == 'track':
        return miss('That MusicBrainz recording is on more than one release. '
                    'Paste the release link instead.')
    try:
        client = client_for(ref.service)
    except Exception as e:  # noqa: BLE001
        logger.debug("no %s client: %s", ref.service, e)
        client = None
    if client is None:
        return miss(f"{label} isn't set up, so its links can't be looked up.")

    not_found = miss(f"{label} has no release with that id. Check the link and try again.")
    album_id = ref.id
    try:
        if ref.kind == 'track':
            # a song link: the release it's on
            album_id = str(_album_of(_fetch_track(client, ref.service, ref.id)).get('id') or '')
            if not album_id:
                return not_found
        if ref.kind == 'release-group':
            editions = _editions(client, album_id)
            if editions:
                return {'source': ref.service, 'albums': editions, 'message': ''}
        album = _fetch_album(client, ref.service, album_id)
    except Exception as e:  # noqa: BLE001 - a bad id is a miss, never an error
        logger.debug("%s album %s lookup failed: %s", ref.service, album_id, e)
        return not_found
    if not isinstance(album, dict) or not (album.get('name') or album.get('title')):
        return not_found
    if ref.service == 'musicbrainz' and ref.kind is None and _is_group(album):
        # a bare group id: every edition beats one picked for you
        editions = _editions(client, album_id)
        if editions:
            return {'source': ref.service, 'albums': editions, 'message': ''}
    album = dict(album)
    if ref.service != 'musicbrainz':
        # keep the id that was looked up: a discogs id carries its r/m tag,
        # which says release or master to the match step
        album['id'] = album_id
    return {'source': ref.service, 'albums': [album], 'message': ''}
