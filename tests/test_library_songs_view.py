"""#1906: the library page's Songs view reads from get_library_songs.

cremonies: let everyone search the library by song title, show the album so
duplicates can be told apart, respect multi-library setups and what a profile
may see. Same scoping as the artists and albums views (active server, owner
profile), searchable by title and artist, and built so a 300k-track library
stays fast: a filtered count stops at a cap and says so.
"""

import sqlite3

import pytest

from database.music_database import MusicDatabase


@pytest.fixture()
def db(tmp_path):
    d = MusicDatabase(database_path=str(tmp_path / "music.db"))
    c = sqlite3.connect(str(d.database_path))
    for aid, name, src in (('a1', 'Morgan Wallen', 'plex'), ('a2', 'Chris Young', 'plex'),
                           ('a3', 'Love Arcade', 'plex'), ('a4', 'Nobody', 'jellyfin')):
        c.execute("INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)", (aid, name, src))
    for alid, aid, title, year, src in (('al1', 'a1', 'Dangerous', 2021, 'plex'),
                                        ('al2', 'a1', 'Dangerous: The Double Album', 2021, 'plex'),
                                        ('al3', 'a2', 'Famous Friends', 2021, 'plex'),
                                        ('al4', 'a3', 'Arcade', 2010, 'plex'),
                                        ('al5', 'a4', 'Elsewhere', 2000, 'jellyfin')):
        c.execute("INSERT INTO albums (id, artist_id, title, year, thumb_url, server_source) "
                  "VALUES (?, ?, ?, ?, ?, ?)", (alid, aid, title, year, f'/art/{alid}.jpg', src))
    tracks = [
        # (id, album, artist, title, n, src, path)
        ('1', 'al1', 'a1', 'You Proof', 1, 'plex', '/m/1.flac'),
        ('2', 'al2', 'a1', 'You Proof', 7, 'plex', '/m/2.mp3'),       # the same song twice
        ('3', 'al1', 'a1', 'Sand in My Boots', 2, 'plex', '/m/3.flac'),
        ('4', 'al3', 'a2', 'Famous Friends', 1, 'plex', '/m/4.flac'),
        ('5', 'al4', 'a3', 'Arcadia', 1, 'plex', '/m/5.flac'),          # artist matches "love"
        ('6', 'al3', 'a2', 'Whole Lotta Love', 2, 'plex', '/m/6.flac'),
        ('8', 'al3', 'a2', 'Friends', 3, 'plex', '/m/8.flac'),
        ('9', 'al3', 'a2', "'Til Tomorrow", 4, 'plex', '/m/9.flac'),
        ('7', 'al5', 'a4', 'You Proof', 1, 'jellyfin', '/m/7.flac'),   # another server
    ]
    for tid, alid, aid, title, n, src, path in tracks:
        c.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, "
                  "bitrate, server_source, file_path) VALUES (?, ?, ?, ?, ?, 200000, 900, ?, ?)",
                  (tid, alid, aid, title, n, src, path))
    c.commit()
    c.close()
    return d


def titles(result):
    return [s['title'] for s in result['songs']]


def test_every_song_on_the_active_server_ordered_by_title(db):
    result = db.get_library_songs()
    # the jellyfin copy of You Proof isn't in a plex library's list
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


def test_search_finds_titles_first_then_artists(db):
    # "love" is in one title and one artist's name: the title match leads,
    # though alphabetically the artist's song comes first
    assert titles(db.get_library_songs(search_query='love')) == ['Whole Lotta Love', 'Arcadia']


def test_an_exact_title_outranks_a_title_containing_it(db):
    # alphabetically "famous friends" comes first; the exact title leads
    assert titles(db.get_library_songs(search_query='friends')) == ['Friends', 'Famous Friends']


def test_search_ignores_case_and_accents(db):
    assert titles(db.get_library_songs(search_query='SÁND')) == ['Sand in My Boots']


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


def test_another_profiles_own_library_is_not_listed(db):
    c = sqlite3.connect(str(db.database_path))
    c.execute("UPDATE tracks SET owner_profile_id = 9 WHERE id = '5'")
    c.commit()
    c.close()
    assert 'Arcadia' not in titles(db.get_library_songs())
