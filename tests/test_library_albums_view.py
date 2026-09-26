"""the library page's album view reads its grid from get_library_albums.

the artists view has had one endpoint behind it since it was written;
the album view is its mirror, and this is the query. same scoping rules
(active server, owner profile), same alphabet semantics, same pagination
shape — the page reuses the pager and the empty state unchanged.

albums.track_count is NULL on every row a media-server import writes, so
the count on the card is counted off the tracks table, not read from it.
"""

import sqlite3

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    c = sqlite3.connect(str(d.database_path))
    c.execute("INSERT INTO artists (id, name, server_source) VALUES ('a1', 'Oasis', 'plex')")
    c.execute("INSERT INTO artists (id, name, server_source) VALUES ('a2', 'Blur', 'plex')")
    # 'plex' is the active server in the default config the suite runs with;
    # the jellyfin row is here to prove the grid does not mix servers.
    c.execute("INSERT INTO artists (id, name, server_source) VALUES ('a3', 'Pulp', 'jellyfin')")
    albums = [
        ('al1', 'a1', 'Definitely Maybe', 1994, '/art/1.jpg', 'plex'),
        ('al2', 'a1', 'Be Here Now', 1997, None, 'plex'),
        ('al3', 'a2', 'Parklife', 1994, '/art/3.jpg', 'plex'),
        ('al4', 'a2', '13', 1999, None, 'plex'),
        ('al5', 'a3', 'Different Class', 1995, None, 'jellyfin'),
    ]
    for album_id, artist_id, title, year, thumb, source in albums:
        c.execute(
            "INSERT INTO albums (id, artist_id, title, year, thumb_url, server_source) VALUES (?, ?, ?, ?, ?, ?)",
            (album_id, artist_id, title, year, thumb, source),
        )
    n = 0
    for album_id, artist_id, count in (('al1', 'a1', 11), ('al2', 'a1', 12), ('al3', 'a2', 16), ('al4', 'a2', 13)):
        for i in range(1, count + 1):
            n += 1
            c.execute(
                "INSERT INTO tracks (id, album_id, artist_id, title, server_source, file_path, track_number) VALUES (?, ?, ?, ?, 'plex', ?, ?)",
                (str(n), album_id, artist_id, f"Track {album_id}-{i}", f"/m/{album_id}/{i}.flac", i),
            )
    c.commit()
    c.close()
    return d


def titles(result):
    return [a['title'] for a in result['albums']]


def test_returns_every_album_on_the_active_server_ordered_by_title(db):
    result = db.get_library_albums()
    # Different Class is on jellyfin, so it is not in a plex library's grid.
    assert titles(result) == ['13', 'Be Here Now', 'Definitely Maybe', 'Parklife']


def test_carries_the_artist_and_the_art_the_card_renders(db):
    album = next(a for a in db.get_library_albums()['albums'] if a['title'] == 'Parklife')
    assert album['artist_name'] == 'Blur'
    assert album['artist_id'] == 'a2'
    assert album['year'] == 1994
    assert album['thumb_url'] == '/art/3.jpg'


def test_counts_tracks_because_the_stored_count_is_never_written(db):
    by_title = {a['title']: a for a in db.get_library_albums()['albums']}
    assert by_title['Definitely Maybe']['track_count'] == 11
    assert by_title['Parklife']['track_count'] == 16


def test_search_matches_the_album_title(db):
    assert titles(db.get_library_albums(search_query='here')) == ['Be Here Now']


def test_letter_filters_on_the_album_title(db):
    assert titles(db.get_library_albums(letter='b')) == ['Be Here Now']


def test_hash_letter_collects_titles_that_do_not_start_with_a_letter(db):
    assert titles(db.get_library_albums(letter='#')) == ['13']


def test_letter_all_is_no_filter(db):
    assert len(db.get_library_albums(letter='all')['albums']) == 4


def test_pagination_reports_the_full_count_and_the_page_window(db):
    page = db.get_library_albums(page=2, limit=3)
    assert titles(page) == ['Parklife']
    assert page['pagination'] == {
        'page': 2,
        'limit': 3,
        'total_count': 4,
        'total_pages': 2,
        'has_prev': True,
        'has_next': False,
    }


def test_the_count_respects_the_filters(db):
    assert db.get_library_albums(search_query='zzz')['pagination']['total_count'] == 0


# ── the metadata-source filter ───────────────────────────────────────────────
# The artists query has had one since it was written; an album carries its own
# provider ids, on its own columns, so the map is not the artists' map.

@pytest.fixture()
def sourced(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "sources.db"))
    c = sqlite3.connect(str(d.database_path))
    c.execute("INSERT INTO artists (id, name, server_source) VALUES ('a1', 'Blur', 'plex')")
    rows = [
        ('al1', 'Parklife', 'mbid-1', 'dz-1', None),
        ('al2', 'Leisure', None, 'dz-2', 'js-2'),
        ('al3', 'Modern Life', None, None, None),
    ]
    for album_id, title, mbid, deezer, jiosaavn in rows:
        c.execute(
            "INSERT INTO albums (id, artist_id, title, server_source, musicbrainz_release_id, deezer_id, jiosaavn_id)"
            " VALUES (?, 'a1', ?, 'plex', ?, ?, ?)",
            (album_id, title, mbid, deezer, jiosaavn),
        )
    c.commit()
    c.close()
    return d


def test_keeps_only_albums_matched_to_a_source(sourced):
    assert titles(sourced.get_library_albums(source_filter='musicbrainz')) == ['Parklife']


def test_the_bang_prefix_keeps_the_albums_that_source_never_matched(sourced):
    assert titles(sourced.get_library_albums(source_filter='!deezer')) == ['Modern Life']


def test_filters_on_a_source_an_artist_does_not_have(sourced):
    # JioSaavn is an ALBUM-level source; there is no artist column for it.
    assert titles(sourced.get_library_albums(source_filter='jiosaavn')) == ['Leisure']


def test_a_source_with_no_album_column_is_ignored_rather_than_emptying_the_grid(sourced):
    # 'genius' is artist-only. Carried over from the artists view it must not
    # silently filter every album away.
    assert len(sourced.get_library_albums(source_filter='genius')['albums']) == 3


def test_the_source_filter_counts_too(sourced):
    assert sourced.get_library_albums(source_filter='musicbrainz')['pagination']['total_count'] == 1


def test_carries_the_provider_ids_each_badge_is_drawn_from(sourced):
    album = next(a for a in sourced.get_library_albums()['albums'] if a['title'] == 'Parklife')
    assert album['musicbrainz_release_id'] == 'mbid-1'
    assert album['deezer_id'] == 'dz-1'
    # Present but empty for this row; the card needs the KEY either way, since
    # an absent key and a null one have to read the same to it.
    assert album['jiosaavn_id'] is None
    assert album['spotify_album_id'] is None
