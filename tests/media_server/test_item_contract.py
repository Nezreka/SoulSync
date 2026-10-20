"""One contract test for every wrapper family a media server client hands
to the library scan.

The scan and ``insert_or_update_media_*`` read a fixed set of attribute
names (plexapi's) from whatever object a client returns. Until now that
set lived in three copies of near-identical wrapper classes. This test
builds one artist, album and track per family from the smallest payload
each server sends and checks the contract in ``core.media_server.items``
holds: every legacy attribute exists, the canonical names mirror them,
the unit conversions are the documented ones, and the parent lookups go
through the client.
"""

from __future__ import annotations

import pytest

from core.media_server import items


class _Client:
    """Records parent lookups so the test can see the wrappers delegate."""

    def __init__(self):
        self.calls = []

    def get_artist_by_id(self, artist_id):
        self.calls.append(('artist', artist_id))
        return f'artist:{artist_id}'

    def get_album_by_id(self, album_id):
        self.calls.append(('album', album_id))
        return f'album:{album_id}'

    def get_albums_for_artist(self, artist_id):
        self.calls.append(('albums', artist_id))
        return []

    def get_tracks_for_album(self, album_id):
        self.calls.append(('tracks', album_id))
        return []


def _navidrome(client):
    from core.navidrome_client import NavidromeAlbum, NavidromeArtist, NavidromeTrack
    artist = NavidromeArtist({'id': 'ar1', 'name': 'A', 'dateAdded': '2026-01-02T03:04:05Z',
                              'biography': 'bio'}, client)
    album = NavidromeAlbum({'id': 'al1', 'name': 'B', 'year': 2001, 'artistId': 'ar1',
                            'coverArt': 'cv1', 'created': '2026-01-02T03:04:05Z'}, client)
    track = NavidromeTrack({'id': 't1', 'title': 'C', 'duration': 61, 'track': 3, 'discNumber': 2,
                            'year': 2001, 'userRating': 4, 'path': '/m/A/B/c.flac', 'bitRate': 900,
                            'size': '12345', 'suffix': 'flac', 'musicBrainzId': 'mb-1',
                            'albumId': 'al1', 'artistId': 'ar1',
                            'created': '2026-01-02T03:04:05Z'}, client)
    return artist, album, track


def _jellyfin(client):
    from core.jellyfin_client import JellyfinAlbum, JellyfinArtist, JellyfinTrack
    artist = JellyfinArtist({'Id': 'ar1', 'Name': 'A', 'DateCreated': '2026-01-02T03:04:05Z',
                             'Overview': 'bio', 'ImageTags': {'Primary': 'x'}}, client)
    album = JellyfinAlbum({'Id': 'al1', 'Name': 'B', 'ProductionYear': 2001, 'DateCreated': '2026-01-02T03:04:05Z',
                           'AlbumArtists': [{'Id': 'ar1'}], 'ImageTags': {'Primary': 'x'}}, client)
    track = JellyfinTrack({'Id': 't1', 'Name': 'C', 'RunTimeTicks': 61 * 10_000_000,
                           'IndexNumber': 3, 'ParentIndexNumber': 2, 'ProductionYear': 2001,
                           'UserData': {'Rating': 4}, 'Path': '/m/A/B/c.flac',
                           'MediaSources': [{'Bitrate': 900_000, 'Size': 12345}],
                           'AlbumId': 'al1', 'ArtistItems': [{'Id': 'ar1'}, {'Id': 'ar2'}],
                           'DateCreated': '2026-01-02T03:04:05Z'}, client)
    return artist, album, track


def _soulsync(client, tmp_path):
    from core.soulsync_client import SoulSyncAlbum, SoulSyncArtist, SoulSyncTrack
    f = tmp_path / 'A' / 'B' / 'c.flac'
    f.parent.mkdir(parents=True)
    f.write_bytes(b'x' * 12345)
    tags = {'title': 'C', 'duration_ms': 61000, 'track_number': 3, 'disc_number': 2,
            'year': '2001', 'bitrate': 900, 'genre': 'rock'}
    track = SoulSyncTrack(str(f), tags)
    album = SoulSyncAlbum('A/B', 'B', 2001, track_list=[track])
    artist = SoulSyncArtist('A', 'A', album_list=[album])
    track._album_ref = album
    track._artist_ref = artist
    album._artist_ref = artist
    return artist, album, track


FAMILIES = ['navidrome', 'jellyfin', 'soulsync']


@pytest.fixture
def family(request, tmp_path):
    client = _Client()
    name = request.param
    if name == 'navidrome':
        artist, album, track = _navidrome(client)
    elif name == 'jellyfin':
        artist, album, track = _jellyfin(client)
    else:
        artist, album, track = _soulsync(client, tmp_path)
    return name, client, artist, album, track


@pytest.mark.parametrize('family', FAMILIES, indirect=True)
def test_every_family_is_built_on_the_shared_item_classes(family):
    name, _, artist, album, track = family
    assert isinstance(artist, items.MediaArtist)
    assert isinstance(album, items.MediaAlbum)
    assert isinstance(track, items.MediaTrack)
    assert artist.server == album.server == track.server == name


@pytest.mark.parametrize('family', FAMILIES, indirect=True)
def test_legacy_attribute_contract(family):
    _, _, artist, album, track = family
    for attr in items.ARTIST_ATTRS:
        assert hasattr(artist, attr), attr
    for attr in items.ALBUM_ATTRS:
        assert hasattr(album, attr), attr
    for attr in items.TRACK_ATTRS:
        assert hasattr(track, attr), attr
    for method in items.ARTIST_METHODS:
        assert callable(getattr(artist, method)), method
    for method in items.ALBUM_METHODS:
        assert callable(getattr(album, method)), method
    for method in items.TRACK_METHODS:
        assert callable(getattr(track, method)), method


@pytest.mark.parametrize('family', FAMILIES, indirect=True)
def test_values_and_units(family):
    name, _, artist, album, track = family
    assert artist.title == 'A' and album.title == 'B' and track.title == 'C'
    assert track.duration == 61000, 'duration is milliseconds'
    assert track.bitRate == 900, 'bitrate is kbps'
    assert track.file_size == 12345, 'file size is bytes'
    assert track.trackNumber == 3 and track.discNumber == 2 and track.year == 2001
    assert album.year == 2001
    assert track.path.endswith('/A/B/c.flac')
    if name != 'soulsync':
        assert artist.addedAt.year == 2026 and track.addedAt.year == 2026
        assert artist.summary == 'bio'
        assert track.userRating == 4
        assert artist.thumb and album.thumb
    if name == 'navidrome':
        assert track.suffix == 'flac' and track.musicBrainzId == 'mb-1'
        assert album.thumb == '/rest/getCoverArt?id=cv1', 'coverArt key wins over the album id'
    if name == 'jellyfin':
        assert track._artist_ids == ['ar1', 'ar2'] and track._artist_id == 'ar1'


@pytest.mark.parametrize('family', FAMILIES, indirect=True)
def test_canonical_names_mirror_the_legacy_ones(family):
    _, _, artist, album, track = family
    assert artist.server_id == artist.ratingKey and artist.name == artist.title
    assert artist.added_at == artist.addedAt and artist.biography == artist.summary
    assert album.image_url == album.thumb
    assert track.duration_ms == track.duration
    assert track.track_number == track.trackNumber
    assert track.disc_number == track.discNumber
    assert track.bitrate_kbps == track.bitRate
    assert track.musicbrainz_id == track.musicBrainzId
    assert track.album_id == track._album_id
    assert track.artist_ids == list(track._artist_ids)


@pytest.mark.parametrize('family', ['navidrome', 'jellyfin'], indirect=True)
def test_server_backed_families_resolve_parents_through_the_client(family):
    _, client, artist, album, track = family
    assert track.album() == 'album:al1'
    assert track.artist() == 'artist:ar1'
    assert album.artist() == 'artist:ar1'
    album.tracks()
    artist.albums()
    assert ('tracks', 'al1') in client.calls and ('albums', 'ar1') in client.calls


def test_standalone_family_holds_its_own_lists(tmp_path):
    artist, album, track = _soulsync(_Client(), tmp_path)
    assert artist.albums() == [album]
    assert album.tracks() == [track]
    assert track.album() is album and track.artist() is artist
    assert album.leafCount == 1 and album.duration == 61000
    assert artist.genres == ['rock'] and album.genres == ['rock']


def test_parse_iso_date_is_forgiving():
    assert items.parse_iso_date(None) is None
    assert items.parse_iso_date('not a date') is None
    assert items.parse_iso_date('2026-01-02T03:04:05Z').year == 2026
