"""The flat watchlist recent-releases feed (the dashboard's Fresh Releases rail).

`recent_releases` was only ever read per-artist before (the watchlist artist
detail's six-release strip); `get_watchlist_recent_releases` is the newest-first
view across the whole watchlist. Pinned here: the join carries the artist's
name and spotify id (the rail's subtitle and deep link), ordering is by
release_date, and the profile filter actually filters.
"""

from database.music_database import MusicDatabase
from tests.support.catalogue_seed import seed_album, seed_artist, seed_track


def _db(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO watchlist_artists (spotify_artist_id, artist_name, profile_id)"
            " VALUES ('sp-ado', 'Ado', 1)")
        ado = cursor.lastrowid
        cursor.execute(
            "INSERT INTO watchlist_artists (spotify_artist_id, artist_name, profile_id)"
            " VALUES ('sp-other', 'Other Profile Artist', 2)")
        other = cursor.lastrowid
        rows = [
            (ado, 'sp-alb-1', 'Kyougen II', '2026-08-01', 'k2.jpg', 12),
            (ado, 'sp-alb-2', 'Older One', '2025-01-15', 'old.jpg', 10),
            (other, 'sp-alb-3', 'Not Mine', '2026-08-05', 'nm.jpg', 8),
        ]
        for artist_id, alb_id, name, date, cover, tracks in rows:
            cursor.execute(
                "INSERT INTO recent_releases (watchlist_artist_id, album_spotify_id,"
                " album_name, release_date, album_cover_url, track_count)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (artist_id, alb_id, name, date, cover, tracks))
        conn.commit()
    return db


def test_flat_feed_is_newest_first_with_artist_joined(tmp_path):
    releases = _db(tmp_path).get_watchlist_recent_releases(limit=20, profile_id=1)
    assert [r['album_name'] for r in releases] == ['Kyougen II', 'Older One']
    top = releases[0]
    assert top['artist_name'] == 'Ado'
    assert top['spotify_artist_id'] == 'sp-ado'
    assert top['album_cover_url'] == 'k2.jpg'
    assert top['track_count'] == 12


def test_profile_filter_hides_other_profiles_watchlists(tmp_path):
    releases = _db(tmp_path).get_watchlist_recent_releases(limit=20, profile_id=1)
    assert all(r['album_name'] != 'Not Mine' for r in releases)
    other = _db(tmp_path / 'p2').get_watchlist_recent_releases(limit=20, profile_id=2)
    assert [r['album_name'] for r in other] == ['Not Mine']


def test_deezer_release_carries_its_album_id(tmp_path):
    """A deezer-sourced release must expose album_deezer_id in the feed —
    the dashboard click builds /api/discover/album/deezer/<id> from it, and
    omitting the column made every deezer card fail with 'No deezer album ID
    available' (Boulder's live repro, deezer metadata source)."""
    db = _db(tmp_path)
    with db._get_connection() as conn:
        conn.execute(
            "INSERT INTO recent_releases (watchlist_artist_id, album_deezer_id,"
            " source, album_name, release_date, album_cover_url, track_count)"
            " SELECT id, 'dz-9', 'deezer', 'Deezer Drop', '2026-08-09', 'd.jpg', 7"
            " FROM watchlist_artists WHERE artist_name = 'Ado'")
        conn.commit()
    releases = db.get_watchlist_recent_releases(limit=20, profile_id=1)
    dz = next(r for r in releases if r['album_name'] == 'Deezer Drop')
    assert dz['album_deezer_id'] == 'dz-9'
    assert dz['source'] == 'deezer'


def test_owned_flag_marks_albums_the_library_already_has(tmp_path):
    """The rail badges releases the library already holds (and the click
    plays them). Owned = an albums row matching (artist, album) by name,
    case-insensitively — track completeness stays the click-time check."""
    db = _db(tmp_path)
    with db._get_connection() as conn:
        artist_id = seed_artist(
            conn, server_id="ar1", name="Ado", server_source="test"
        )
        album_id = seed_album(
            conn,
            server_id="al1",
            title="kyougen ii",
            artist_id=artist_id,
            server_source="test",
        )
        seed_track(
            conn,
            server_id="tr1",
            title="Track 1",
            album_id=album_id,
            artist_id=artist_id,
            server_source="test",
            file_path="/music/Ado/Kyougen II/01 Track 1.flac",
        )
        conn.commit()
    releases = db.get_watchlist_recent_releases(limit=20, profile_id=1)
    by_name = {r['album_name']: r['owned'] for r in releases}
    assert by_name['Kyougen II'] is True   # case-insensitive name match
    assert by_name['Older One'] is False


def test_limit_caps_the_feed(tmp_path):
    releases = _db(tmp_path).get_watchlist_recent_releases(limit=1, profile_id=1)
    assert len(releases) == 1
    assert releases[0]['album_name'] == 'Kyougen II'
