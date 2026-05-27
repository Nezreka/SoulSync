"""Tests for the wishlist-cycle album grouping helper that drives
the per-album bundle dispatch.

Pins the bucketing contract so future changes to the dispatch flow
don't silently regress the user-visible behavior: wishlist 'albums'
cycle should emit one album-bundle search per missing album, not
one per missing track.
"""

from __future__ import annotations

from core.wishlist.album_grouping import (
    WishlistAlbumGroup,
    WishlistGroupingResult,
    group_wishlist_tracks_by_album,
)


def _wt(track_name, artist, album_id, album_name, **extra):
    """Build a wishlist row in the shape the wishlist service returns."""
    return {
        'track_name': track_name,
        'artist_name': artist,
        'spotify_data': {
            'name': track_name,
            'artists': [{'name': artist}],
            'album': {
                'id': album_id,
                'name': album_name,
                **extra,
            },
        },
    }


def test_empty_input_returns_empty_result():
    res = group_wishlist_tracks_by_album([])
    assert res.album_groups == []
    assert res.residual_tracks == []


def test_single_album_groups_all_tracks_together():
    tracks = [
        _wt('Dragon Soul', 'Ryoto', 'alb1', 'Cha-La Head-Cha-La'),
        _wt('Cha-La Head-Cha-La', 'Ryoto', 'alb1', 'Cha-La Head-Cha-La'),
        _wt('Zenkai Power', 'Ryoto', 'alb1', 'Cha-La Head-Cha-La'),
    ]
    res = group_wishlist_tracks_by_album(tracks)
    assert len(res.album_groups) == 1
    g = res.album_groups[0]
    assert g.album_key == 'alb1'
    assert g.album_context['name'] == 'Cha-La Head-Cha-La'
    assert g.artist_context['name'] == 'Ryoto'
    assert len(g.tracks) == 3


def test_multiple_albums_emit_separate_groups():
    tracks = [
        _wt('Song A', 'Artist 1', 'alb1', 'Album 1'),
        _wt('Song B', 'Artist 1', 'alb1', 'Album 1'),
        _wt('Song C', 'Artist 2', 'alb2', 'Album 2'),
    ]
    res = group_wishlist_tracks_by_album(tracks)
    assert len(res.album_groups) == 2
    keys = {g.album_key for g in res.album_groups}
    assert keys == {'alb1', 'alb2'}
    for g in res.album_groups:
        if g.album_key == 'alb1':
            assert len(g.tracks) == 2
        else:
            assert len(g.tracks) == 1


def test_missing_album_metadata_falls_through_to_residual():
    tracks = [
        # No spotify_data.album at all
        {'track_name': 'Orphan', 'artist_name': 'X', 'spotify_data': {'artists': [{'name': 'X'}]}},
        # Empty album dict
        {'track_name': 'Empty Album', 'artist_name': 'X', 'spotify_data': {'album': {}, 'artists': [{'name': 'X'}]}},
    ]
    res = group_wishlist_tracks_by_album(tracks)
    assert res.album_groups == []
    assert len(res.residual_tracks) == 2


def test_missing_artist_demotes_to_residual():
    """Album-bundle search needs an artist; if we can't recover one,
    skip the bundle path and let the track go through per-track."""
    tracks = [{
        'track_name': 'Song',
        'spotify_data': {
            'artists': [],
            'album': {'id': 'a', 'name': 'Album'},
        },
    }]
    res = group_wishlist_tracks_by_album(tracks)
    assert res.album_groups == []
    assert res.residual_tracks == tracks


def test_min_tracks_threshold_demotes_solos():
    """When ``min_tracks_per_album=2``, single-track albums fall to
    residual so the user doesn't fire a bundle search for a 1-track
    rip when per-track would do."""
    tracks = [
        _wt('Solo Track', 'Artist 1', 'alb1', 'Album 1'),
        _wt('Song A', 'Artist 2', 'alb2', 'Album 2'),
        _wt('Song B', 'Artist 2', 'alb2', 'Album 2'),
    ]
    res = group_wishlist_tracks_by_album(tracks, min_tracks_per_album=2)
    assert len(res.album_groups) == 1
    assert res.album_groups[0].album_key == 'alb2'
    assert len(res.residual_tracks) == 1
    assert res.residual_tracks[0]['track_name'] == 'Solo Track'


def test_default_threshold_promotes_solo_albums():
    """Default ``min_tracks_per_album=1`` — even one missing track
    triggers the album-bundle path. Matches the user's stated
    preference (don't gate on track count)."""
    tracks = [_wt('Solo', 'Artist 1', 'alb1', 'Album 1')]
    res = group_wishlist_tracks_by_album(tracks)
    assert len(res.album_groups) == 1
    assert res.residual_tracks == []


def test_album_without_id_uses_name_normalized_key():
    """Some older wishlist rows are missing the album id. Group by
    a name-normalized key so they still bucket together."""
    tracks = [
        _wt('S1', 'Artist', None, 'Same Album'),
        _wt('S2', 'Artist', None, 'Same Album'),
    ]
    # First track has explicit id=None which is filtered; the fallback
    # is ``_name_<lowercase trimmed name>``. Build manually so the
    # helper sees no id at all.
    for t in tracks:
        del t['spotify_data']['album']['id']
    res = group_wishlist_tracks_by_album(tracks)
    assert len(res.album_groups) == 1
    assert res.album_groups[0].album_key == '_name_same album'
    assert len(res.album_groups[0].tracks) == 2


def test_nested_track_data_payloads_normalized():
    """The wishlist service sometimes nests spotify_data under
    track_data (JSON-string in DB → re-parsed). Ensure the grouper
    digs through the same shapes ``classify_wishlist_track`` does."""
    tracks = [{
        'track_data': {
            'spotify_data': {
                'artists': [{'name': 'Artist'}],
                'album': {'id': 'a', 'name': 'Album'},
            },
        },
    }]
    res = group_wishlist_tracks_by_album(tracks)
    assert len(res.album_groups) == 1
    assert res.album_groups[0].album_key == 'a'
