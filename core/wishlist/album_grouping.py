"""Wishlist album grouping for the per-album bundle dispatch.

When the auto-wishlist cycle is ``'albums'`` the user expects each
album with missing tracks to fire ONE album-bundle search instead
of one per-track search per missing track. Track lists in the
wishlist may span multiple albums in one cycle, so we group them
upfront + emit one sub-batch per album.

Pure function — no IO, no runtime-state dependency — so it can be
unit-tested without standing up the wishlist runner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _extract_track_data(track: Dict[str, Any]) -> Dict[str, Any]:
    """Mirror of ``classification._extract_track_data``: unwrap nested
    Spotify payloads regardless of which key the wishlist row chose
    to stash them under."""
    for key in ("track_data", "spotify_data", "metadata", "track"):
        data = track.get(key)
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        if isinstance(data, dict) and data:
            nested = (
                data.get("track_data")
                or data.get("spotify_data")
                or data.get("metadata")
                or data.get("track")
            )
            if isinstance(nested, str):
                try:
                    nested = json.loads(nested)
                except Exception:
                    nested = {}
            if isinstance(nested, dict) and nested:
                return nested
            return data
    return {}


def _album_key(spotify_data: Dict[str, Any]) -> Optional[str]:
    """Derive a stable grouping key from a track's Spotify metadata.

    Prefers album id (canonical). Falls back to a name-normalized
    key when the album row has no id (older wishlist rows can be
    missing it). Returns ``None`` when no album information is
    available at all — those tracks can't participate in an
    album-bundle search and stay on the residual per-track flow.
    """
    album = spotify_data.get('album') or {}
    if not isinstance(album, dict):
        return None
    album_id = album.get('id')
    if isinstance(album_id, str) and album_id.strip():
        return album_id.strip()
    name = album.get('name')
    if isinstance(name, str) and name.strip():
        return f"_name_{name.strip().lower()}"
    return None


def _artist_name_from_track(spotify_data: Dict[str, Any], track: Dict[str, Any]) -> str:
    """Pick a primary artist name from the track's metadata.

    Album-bundle search needs an artist string. Prefer the first
    Spotify artist (most accurate), fall back to ``track_info['artist']``
    or ``track['artist_name']`` from the wishlist row, then to empty
    string (caller will skip the bundle).
    """
    artists = spotify_data.get('artists') or []
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict):
            name = first.get('name')
            if isinstance(name, str) and name.strip():
                return name.strip()
        elif isinstance(first, str) and first.strip():
            return first.strip()
    for key in ('artist_name', 'artist'):
        val = track.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ''


@dataclass
class WishlistAlbumGroup:
    """One album's worth of wishlist tracks ready for a sub-batch."""

    album_key: str
    album_context: Dict[str, Any]
    artist_context: Dict[str, Any]
    tracks: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class WishlistGroupingResult:
    """Aggregated grouping output.

    - ``album_groups``: one entry per resolvable album. Each carries
      enough context to be submitted as an album-bundle batch.
    - ``residual_tracks``: tracks that couldn't be grouped (no
      album metadata + no artist). They fall through to the normal
      per-track flow.
    """

    album_groups: List[WishlistAlbumGroup] = field(default_factory=list)
    residual_tracks: List[Dict[str, Any]] = field(default_factory=list)


def group_wishlist_tracks_by_album(
    tracks: List[Dict[str, Any]],
    *,
    min_tracks_per_album: int = 2,
) -> WishlistGroupingResult:
    """Group wishlist tracks by their owning album.

    ``min_tracks_per_album`` controls the threshold for promoting an
    album to its own sub-batch. Default ``2`` means an album needs at
    least two missing tracks before the album-bundle search engages —
    single-track items fall to ``residual_tracks`` and take the
    classic per-track path. The 1-track case used to default to bundle
    too, but real-world wishlists frequently look like "26 single
    tracks from 26 different albums," and engaging bundle for each
    one downloads ~85% of bandwidth as unwanted files, hammers slskd
    with concurrent searches, and re-downloads the same album every
    cycle when the staging-match step doesn't claim the requested
    track. Bundle shines when several tracks from the same album are
    missing — that's the case worth the bandwidth premium.

    Override via the ``wishlist.album_bundle_min_tracks`` config key
    or by passing ``min_tracks_per_album=N`` explicitly (kept for
    tests + power users who want different behaviour).
    """
    result = WishlistGroupingResult()
    if not tracks:
        return result

    # First pass: bucket by album key.
    buckets: Dict[str, WishlistAlbumGroup] = {}
    unbucketable: List[Dict[str, Any]] = []

    for track in tracks:
        spotify_data = _extract_track_data(track)
        key = _album_key(spotify_data)
        if key is None:
            unbucketable.append(track)
            continue

        artist_name = _artist_name_from_track(spotify_data, track)
        if not artist_name:
            unbucketable.append(track)
            continue

        album = spotify_data.get('album') or {}
        if not isinstance(album, dict):
            album = {}
        album_name = album.get('name', '')
        if not (isinstance(album_name, str) and album_name.strip()):
            unbucketable.append(track)
            continue

        group = buckets.get(key)
        if group is None:
            album_context = {
                'id': album.get('id') or key,
                'name': album_name.strip(),
                'release_date': album.get('release_date', ''),
                'total_tracks': album.get('total_tracks', 0),
                'album_type': album.get('album_type', 'album'),
                'images': album.get('images', []),
                'artists': album.get('artists', []),
            }
            artist_context = {
                'id': 'wishlist',
                'name': artist_name,
                'genres': [],
            }
            group = WishlistAlbumGroup(
                album_key=key,
                album_context=album_context,
                artist_context=artist_context,
            )
            buckets[key] = group
        group.tracks.append(track)

    # Second pass: promote groups meeting the threshold; demote
    # smaller groups to residual.
    for group in buckets.values():
        if len(group.tracks) >= min_tracks_per_album:
            result.album_groups.append(group)
        else:
            result.residual_tracks.extend(group.tracks)

    result.residual_tracks.extend(unbucketable)
    return result


__all__ = [
    'group_wishlist_tracks_by_album',
    'WishlistAlbumGroup',
    'WishlistGroupingResult',
]
