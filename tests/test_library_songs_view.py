"""#1906: the library page's Songs view reads from get_library_songs.

cremonies: let everyone search the library by song title, show the album so
duplicates can be told apart, respect multi-library setups and what a profile
may see. On the Library v2 catalogue a song is a track with a live file in the
caller's library, searchable by title and artist, and built so a 300k-track
library stays fast: a filtered count stops at a cap and says so.
"""

import sqlite3

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path, monkeypatch):
    # one shared library plus profile 9's own: the shared one is what's asked
    import core.library_scope as library_scope
    monkeypatch.setattr(library_scope, 'any_own_library_exists', lambda: True)
    monkeypatch.setattr(library_scope, 'current_library_scope', lambda: 'shared')
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    c = sqlite3.connect(str(d.database_path))
    artists = {}
    for key, name in (('a1', 'Morgan Wallen'), ('a2', 'Chris Young'), ('a3', 'Love Arcade'),
                      ('a4', 'Nobody')):
        artists[key] = c.execute("INSERT INTO lib2_artists (name, name_key) VALUES (?, ?)",
                                 (name, name.lower())).lastrowid
    albums = {}
    for key, artist, title, year in (('al1', 'a1', 'Dangerous', 2021),
                                     ('al2', 'a1', 'Dangerous: The Double Album', 2021),
                                     ('al3', 'a2', 'Famous Friends', 2021),
                                     ('al4', 'a3', 'Arcade', 2010),
                                     ('al5', 'a4', 'Elsewhere', 2000)):
        albums[key] = c.execute(
            "INSERT INTO lib2_albums (primary_artist_id, title, year, image_url) VALUES (?, ?, ?, ?)",
            (artists[artist], title, year, f'/art/{key}.jpg')).lastrowid
    tracks = [
        # (album, title, n, path, owner)
        ('al1', 'You Proof', 1, '/m/1.flac', None),
        ('al2', 'You Proof', 7, '/m/2.mp3', None),          # the same song twice
        ('al1', 'Sand in My Boots', 2, '/m/3.flac', None),
        ('al3', 'Famous Friends', 1, '/m/4.flac', None),
        ('al4', 'Arcadia', 1, '/m/5.flac', None),            # artist matches "love"
        ('al3', 'Whole Lotta Love', 2, '/m/6.flac', None),
        ('al3', 'Friends', 3, '/m/8.flac', None),
        ('al3', "'Til Tomorrow", 4, '/m/9.flac', None),
        ('al5', 'You Proof', 1, '/m/7.flac', 9),             # another profile's own library
    ]
    for album, title, n, path, owner in tracks:
        tid = c.execute("INSERT INTO lib2_tracks (album_id, title, track_number, duration) "
                        "VALUES (?, ?, ?, 200000)", (albums[album], title, n)).lastrowid
        c.execute("INSERT INTO lib2_track_files (track_id, path, format, bitrate, is_primary, "
                  "file_state, owner_profile_id) VALUES (?, ?, ?, 900, 1, 'active', ?)",
                  (tid, path, path.rsplit('.', 1)[1], owner))
    # a catalogue row with no file is a wanted song, not one of yours
    c.execute("INSERT INTO lib2_tracks (album_id, title, track_number) VALUES (?, 'Missing One', 9)",
              (albums['al3'],))
    c.commit()
    c.close()
    return d


def titles(result):
    return [s['title'] for s in result['songs']]


def test_every_song_you_have_a_file_for_ordered_by_title(db):
    result = db.get_library_songs()
    # the other profile's copy of You Proof and the fileless row aren't yours
    assert titles(result) == ["'Til Tomorrow", 'Arcadia', 'Famous Friends', 'Friends',
                              'Sand in My Boots', 'Whole Lotta Love', 'You Proof', 'You Proof']
    assert result['pagination']['total_count'] == 8
    assert result['pagination']['total_capped'] is False


def test_a_row_says_which_album_and_counts_its_copies(db):
    proof = [s for s in db.get_library_songs(search_query='you proof')['songs']]
    assert {s['album_title'] for s in proof} == {'Dangerous', 'Dangerous: The Double Album'}
    assert all(s['copies'] == 2 for s in proof)
    assert {s['format'] for s in proof} == {'FLAC', 'MP3'}
    one = proof[0]
    assert one['artist_name'] == 'Morgan Wallen' and one['year'] == 2021
    assert one['duration'] == 200000 and one['file_path'].startswith('/m/')
    assert one['album_thumb_url'].startswith('/art/')


def test_a_row_carries_the_ids_the_player_reads(db):
    # the player keys a library play by lib2_track_id; `id` alone is a
    # catalogue id the media server can't stream by
    c = sqlite3.connect(str(db.database_path))
    c.execute("UPDATE lib2_tracks SET legacy_track_id = 4242 WHERE title = 'Arcadia'")
    c.commit()
    c.close()
    rows = {s['title']: s for s in db.get_library_songs()['songs']}
    arcadia = rows['Arcadia']
    assert arcadia['lib2_track_id'] == arcadia['id']
    assert arcadia['legacy_track_id'] == 4242 and arcadia['server_track_id'] == 4242
    assert rows['Friends']['legacy_track_id'] is None


def test_search_finds_titles_first_then_artists(db):
    # "love" is in one title and one artist's name: the title match leads,
    # though alphabetically the artist's song comes first
    assert titles(db.get_library_songs(search_query='love')) == ['Whole Lotta Love', 'Arcadia']


def test_an_exact_title_outranks_a_title_containing_it(db):
    # alphabetically "famous friends" comes first; the exact title leads
    assert titles(db.get_library_songs(search_query='friends')) == ['Friends', 'Famous Friends']


def test_search_ignores_case_and_accents(db):
    assert titles(db.get_library_songs(search_query='SÁND')) == ['Sand in My Boots']


def test_an_accented_title_is_found_without_the_accent(db):
    c = sqlite3.connect(str(db.database_path))
    album = c.execute("SELECT id FROM lib2_albums WHERE title = 'Arcade'").fetchone()[0]
    tid = c.execute("INSERT INTO lib2_tracks (album_id, title) VALUES (?, 'Café Amour')",
                    (album,)).lastrowid
    c.execute("INSERT INTO lib2_track_files (track_id, path, is_primary, file_state) "
              "VALUES (?, '/m/cafe.flac', 1, 'active')", (tid,))
    c.commit()
    c.close()
    assert titles(db.get_library_songs(search_query='cafe')) == ['Café Amour']


def test_nothing_found_is_an_empty_page_not_an_error(db):
    result = db.get_library_songs(search_query='zzzz')
    assert result['songs'] == [] and result['pagination']['total_count'] == 0


def test_letter_files_punctuated_titles_under_their_first_letter(db):
    # 'Til files under T, like the albums view files "Weird Al" under W
    assert titles(db.get_library_songs(letter='t')) == ["'Til Tomorrow"]
    assert titles(db.get_library_songs(letter='y')) == ['You Proof', 'You Proof']
    assert titles(db.get_library_songs(letter='#')) == []


def test_recent_is_newest_first(db):
    assert titles(db.get_library_songs(sort='recent'))[0] == "'Til Tomorrow"


def test_pages(db):
    first = db.get_library_songs(limit=5, page=1)
    second = db.get_library_songs(limit=5, page=2)
    assert len(first['songs']) == 5 and len(second['songs']) == 3
    assert first['pagination']['has_next'] and not second['pagination']['has_next']
    assert not set(titles(first)) & set(t for t in titles(second) if t != 'You Proof')


def test_a_broad_search_counts_up_to_the_cap_and_keeps_paging(db, monkeypatch):
    monkeypatch.setattr(MusicDatabase, 'SONGS_COUNT_CAP', 2)
    result = db.get_library_songs(search_query='o', limit=2)
    assert result['pagination']['total_capped'] is True
    assert result['pagination']['total_count'] == 2
    # the count stopped, the songs didn't: a full page still offers the next
    assert result['pagination']['has_next'] is True


def test_a_deleted_file_is_not_a_song(db):
    c = sqlite3.connect(str(db.database_path))
    c.execute("UPDATE lib2_track_files SET file_state = 'deleted' WHERE path = '/m/5.flac'")
    c.commit()
    c.close()
    assert 'Arcadia' not in titles(db.get_library_songs())


def test_another_profiles_own_library_is_not_listed(db):
    c = sqlite3.connect(str(db.database_path))
    c.execute("UPDATE lib2_track_files SET owner_profile_id = 9 WHERE path = '/m/5.flac'")
    c.commit()
    c.close()
    assert 'Arcadia' not in titles(db.get_library_songs())


def _plan(db, sql, params):
    conn = sqlite3.connect(str(db.database_path))
    try:
        return [row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params)]
    finally:
        conn.close()


@pytest.mark.parametrize('sort', ['title', 'artist', 'album'])
def test_a_sorted_page_walks_an_index_instead_of_sorting_the_library(db, sort):
    """a plain ORDER BY sorted every track in the library before the LIMIT
    (20 s on 300k). the title, artist and album sorts must walk their own
    LOWER() name index."""
    source, order = db._songs_sort_sql(sort)
    _from, owned = db._songs_base()
    plan = _plan(db, f"SELECT t.id FROM {source} WHERE {owned} ORDER BY {order} LIMIT 5", [])
    assert not any('USE TEMP B-TREE FOR ORDER BY' in line for line in plan), plan


# ── sorts and filters (best in class pass) ───────────────────────────────────

def test_artist_sort_groups_an_artists_albums_in_track_order(db):
    assert titles(db.get_library_songs(sort='artist')) == [
        # chris young, famous friends in track order
        'Famous Friends', 'Whole Lotta Love', 'Friends', "'Til Tomorrow",
        'Arcadia',                                   # love arcade
        'You Proof', 'Sand in My Boots', 'You Proof',  # morgan wallen, album by album
    ]


def test_album_sort_is_by_album_title_then_track(db):
    assert titles(db.get_library_songs(sort='album')) == [
        'Arcadia', 'You Proof', 'Sand in My Boots', 'You Proof',
        'Famous Friends', 'Whole Lotta Love', 'Friends', "'Til Tomorrow",
    ]


def test_year_sort_is_newest_album_first(db):
    result = titles(db.get_library_songs(sort='year'))
    assert result[-1] == 'Arcadia'                  # 2010, after every 2021 album
    assert result[:3] == ['You Proof', 'Sand in My Boots', 'You Proof']


def test_year_sort_pages(db):
    first = titles(db.get_library_songs(sort='year', limit=3, page=1))
    second = titles(db.get_library_songs(sort='year', limit=3, page=2))
    assert first == ['You Proof', 'Sand in My Boots', 'You Proof']
    assert second == ['Famous Friends', 'Whole Lotta Love', 'Friends']


def test_a_search_keeps_best_match_whatever_the_sort(db):
    assert titles(db.get_library_songs(search_query='friends', sort='artist')) == [
        'Friends', 'Famous Friends']


def _add_song(db, album_title, title, path, bitrate=900):
    c = sqlite3.connect(str(db.database_path))
    album = c.execute("SELECT id FROM lib2_albums WHERE title = ?", (album_title,)).fetchone()[0]
    tid = c.execute("INSERT INTO lib2_tracks (album_id, title) VALUES (?, ?)",
                    (album, title)).lastrowid
    c.execute("INSERT INTO lib2_track_files (track_id, path, format, bitrate, is_primary, file_state) "
              "VALUES (?, ?, ?, ?, 1, 'active')", (tid, path, path.rsplit('.', 1)[1], bitrate))
    c.commit()
    c.close()
    return tid


def _track_id(db, path):
    c = sqlite3.connect(str(db.database_path))
    try:
        return c.execute("SELECT track_id FROM lib2_track_files WHERE path = ?", (path,)).fetchone()[0]
    finally:
        c.close()


def test_duplicates_only(db):
    # the same title by a different artist is a different song, not a copy
    _add_song(db, 'Arcade', 'Friends', '/m/20.flac')
    result = db.get_library_songs(dupes=True)
    assert titles(result) == ['You Proof', 'You Proof']
    assert result['pagination']['total_count'] == 2


def test_lossless_only(db):
    result = titles(db.get_library_songs(quality='lossless'))
    assert result.count('You Proof') == 1           # the mp3 copy is out
    assert len(result) == 7


def test_could_be_better_is_the_pending_quality_findings(db):
    sand, friends = _track_id(db, '/m/3.flac'), _track_id(db, '/m/4.flac')
    c = sqlite3.connect(str(db.database_path))
    c.execute("INSERT INTO repair_findings (job_id, finding_type, status, entity_type, entity_id, title) "
              "VALUES ('quality_upgrade', 'upgrade', 'pending', 'track', ?, 'x')", (str(sand),))
    c.execute("INSERT INTO repair_findings (job_id, finding_type, status, entity_type, entity_id, title) "
              "VALUES ('quality_upgrade', 'upgrade', 'resolved', 'track', ?, 'x')", (str(friends),))
    c.commit()
    c.close()
    assert titles(db.get_library_songs(quality='upgradable')) == ['Sand in My Boots']


def test_copies_compare_best_file_first(db):
    flac, mp3 = _track_id(db, '/m/1.flac'), _track_id(db, '/m/2.mp3')
    c = sqlite3.connect(str(db.database_path))
    c.execute("UPDATE lib2_track_files SET bitrate = 320 WHERE path = '/m/2.mp3'")
    c.commit()
    c.close()
    copies = db.get_library_song_copies(str(mp3))
    # the flac first, though the mp3 was asked about
    assert [(s['id'], s['format']) for s in copies] == [(flac, 'FLAC'), (mp3, 'MP3')]
    assert {s['album_title'] for s in copies} == {'Dangerous', 'Dangerous: The Double Album'}
    assert db.get_library_song_copies('nope') == []
