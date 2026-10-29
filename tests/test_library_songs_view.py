"""#1906: the library page's Songs view, on the Library v2 catalogue.

cremonies: let everyone search the library by song title, show the album so
duplicates can be told apart, respect multi-library setups and what a profile
may see. A song is a catalogue track with a live file in the caller's library
(core.library2.queries.list_songs), searchable by title and artist; a filtered
count stops at a cap and says so.

Ported from upstream's legacy-table fixture: "another server" is another
profile's library here, since the catalogue is not split by media server.
Upstream's EXPLAIN-plan tests guard the indexes of its legacy normalized
columns (title_norm/name_norm); Library v2 has none of those columns and
searches with LIKE over the same fold (normalize_for_comparison, computed per
row only for values with a non-ASCII character), so they have no counterpart.
The A-Z order uses the catalogue's A-Z key (leading punctuation skipped), as
the artist and album lists do, so "'Til Tomorrow" files under T.
"""

import pytest

from core import library_scope
from core.library2 import queries as Q
from core.library2.schema import ensure_library_v2_schema
from tests import lib2_seed


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    c = lib2_seed.row_conn(str(tmp_path / "music.db"))
    ensure_library_v2_schema(c)
    albums = {'Dangerous': ('Morgan Wallen', 2021), 'Dangerous: The Double Album': ('Morgan Wallen', 2021),
              'Famous Friends': ('Chris Young', 2021), 'Arcade': ('Love Arcade', 2010),
              'Elsewhere': ('Nobody', 2000)}
    for title, (artist, year) in albums.items():
        lib2_seed.album(c, artist, title, year=year, image_url=f'/art/{title}.jpg')
    tracks = [
        # (album, title, n, path)
        ('Dangerous', 'You Proof', 1, '/m/1.flac'),
        ('Dangerous: The Double Album', 'You Proof', 7, '/m/2.mp3'),   # the same song twice
        ('Dangerous', 'Sand in My Boots', 2, '/m/3.flac'),
        ('Famous Friends', 'Famous Friends', 1, '/m/4.flac'),
        ('Arcade', 'Arcadia', 1, '/m/5.flac'),                      # artist matches "love"
        ('Famous Friends', 'Whole Lotta Love', 2, '/m/6.flac'),
        ('Famous Friends', 'Friends', 3, '/m/8.flac'),
        ('Famous Friends', "'Til Tomorrow", 4, '/m/9.flac'),
        ('Elsewhere', 'You Proof', 1, '/m/7.flac'),                 # another library
    ]
    for album, title, n, path in tracks:
        lib2_seed.track(c, albums[album][0], album, title, path=path, track_number=n, duration=200000)
    c.execute("UPDATE lib2_track_files SET bitrate = 900")
    c.execute("UPDATE lib2_track_files SET owner_profile_id = 9 WHERE path = '/m/7.flac'")
    c.commit()
    # libraries are separated, and the caller reads the shared one
    monkeypatch.setattr(library_scope, 'any_own_library_exists', lambda: True)
    with library_scope.library_scope('shared'):
        yield c
    c.close()


def songs(conn, **kw):
    return Q.list_songs(conn, **kw)


def titles(result):
    return [s['title'] for s in result['songs']]


def test_every_song_in_the_library_ordered_by_title(conn):
    result = songs(conn)
    assert titles(result) == ['Arcadia', 'Famous Friends', 'Friends', 'Sand in My Boots',
                              "'Til Tomorrow", 'Whole Lotta Love', 'You Proof', 'You Proof']
    assert result['pagination']['total_count'] == 8
    assert result['pagination']['total_capped'] is False


def test_a_row_says_which_album_and_counts_its_copies(conn):
    proof = [s for s in songs(conn)['songs'] if s['title'] == 'You Proof']
    assert {s['album_title'] for s in proof} == {'Dangerous', 'Dangerous: The Double Album'}
    assert all(s['copies'] == 2 for s in proof)
    assert {s['format'] for s in proof} == {'FLAC', 'MP3'}
    one = proof[0]
    assert one['artist_name'] == 'Morgan Wallen' and one['year'] == 2021
    assert one['duration'] == 200000 and one['file_path'].startswith('/m/')
    assert one['file_ids'] and one['track_id'] == one['id']


def test_search_finds_titles_first_then_artists(conn):
    # "Arcadia" matches through its artist, Love Arcade
    assert titles(songs(conn, search='love')) == ['Whole Lotta Love', 'Arcadia']


def test_an_exact_title_outranks_a_title_containing_it(conn):
    assert titles(songs(conn, search='friends')) == ['Friends', 'Famous Friends']


def test_equal_matches_are_alphabetical_whatever_their_case(conn):
    lib2_seed.track(conn, 'Joji', 'Piss In The Wind', 'LOVE YOU LESS', path='/m/40.flac')
    lib2_seed.track(conn, 'Joji', 'Piss In The Wind', 'Love Me Better', path='/m/41.flac')
    conn.commit()
    assert titles(songs(conn, search='love'))[:2] == ['Love Me Better', 'LOVE YOU LESS']


def test_search_ignores_case_and_accents(conn):
    assert titles(songs(conn, search='SÁND')) == ['Sand in My Boots']


def test_an_accented_title_or_artist_is_found_without_its_accents(conn):
    lib2_seed.track(conn, 'Beyoncé', 'Lemonade', 'Mañana', path='/m/30.flac')
    lib2_seed.track(conn, 'Sigur Rós', 'Takk', 'Hoppípolla', path='/m/31.flac')
    conn.commit()
    assert titles(songs(conn, search='manana')) == ['Mañana']
    assert titles(songs(conn, search='beyonce')) == ['Mañana']
    assert titles(songs(conn, search='HOPPIPOLLA')) == ['Hoppípolla']
    # the exact title still ranks first once folded
    assert titles(songs(conn, search='hoppípolla'))[0] == 'Hoppípolla'


def test_a_query_with_nothing_left_after_folding_finds_nothing(conn):
    assert titles(songs(conn, search='♥')) == []


def test_nothing_found_is_an_empty_page_not_an_error(conn):
    result = songs(conn, search='zzzz')
    assert result['songs'] == [] and result['pagination']['total_count'] == 0


def test_letter_files_punctuated_titles_under_their_first_letter(conn):
    assert titles(songs(conn, letter='t')) == ["'Til Tomorrow"]
    assert titles(songs(conn, letter='y')) == ['You Proof', 'You Proof']
    assert titles(songs(conn, letter='#')) == []


def test_recent_is_newest_first(conn):
    assert titles(songs(conn, sort='recent'))[0] == "'Til Tomorrow"


def test_recent_is_insertion_order(conn):
    lib2_seed.track(conn, 'Morgan Wallen', 'Dangerous', 'Newest Song', path='/m/10.flac')
    conn.commit()
    assert titles(songs(conn, sort='recent'))[0] == 'Newest Song'


def test_pages(conn):
    first = songs(conn, limit=5, page=1)
    second = songs(conn, limit=5, page=2)
    assert len(first['songs']) == 5 and len(second['songs']) == 3
    assert first['pagination']['has_next'] and not second['pagination']['has_next']
    assert not set(titles(first)) & set(t for t in titles(second) if t != 'You Proof')


def test_a_broad_search_counts_up_to_the_cap_and_keeps_paging(conn, monkeypatch):
    monkeypatch.setattr(Q, 'SONGS_COUNT_CAP', 2)
    result = songs(conn, search='o', limit=2)
    assert result['pagination']['total_capped'] is True
    assert result['pagination']['total_count'] == 2
    # the count stopped, the songs didn't: a full page still offers the next
    assert result['pagination']['has_next'] is True


def test_another_profiles_own_library_is_not_listed(conn):
    conn.execute("UPDATE lib2_track_files SET owner_profile_id = 9 WHERE path = '/m/5.flac'")
    conn.commit()
    assert 'Arcadia' not in titles(songs(conn))


def test_a_removed_file_is_not_a_song(conn):
    conn.execute("UPDATE lib2_track_files SET file_state = 'deleted' WHERE path = '/m/5.flac'")
    conn.commit()
    assert 'Arcadia' not in titles(songs(conn))


# ── sorts and filters (best in class pass) ───────────────────────────────────

def test_artist_sort_groups_an_artists_albums_in_track_order(conn):
    assert titles(songs(conn, sort='artist')) == [
        # chris young, famous friends in track order
        'Famous Friends', 'Whole Lotta Love', 'Friends', "'Til Tomorrow",
        'Arcadia',                                   # love arcade
        'You Proof', 'Sand in My Boots', 'You Proof',  # morgan wallen, album by album
    ]


def test_album_sort_is_by_album_title_then_track(conn):
    assert titles(songs(conn, sort='album')) == [
        'Arcadia', 'You Proof', 'Sand in My Boots', 'You Proof',
        'Famous Friends', 'Whole Lotta Love', 'Friends', "'Til Tomorrow",
    ]


def test_year_sort_is_newest_album_first(conn):
    result = titles(songs(conn, sort='year'))
    assert result[-1] == 'Arcadia'                  # 2010, after every 2021 album
    assert result[:3] == ['You Proof', 'Sand in My Boots', 'You Proof']


def test_year_sort_pages(conn):
    first = titles(songs(conn, sort='year', limit=3, page=1))
    second = titles(songs(conn, sort='year', limit=3, page=2))
    assert first == ['You Proof', 'Sand in My Boots', 'You Proof']
    assert second == ['Famous Friends', 'Whole Lotta Love', 'Friends']


def test_a_search_keeps_best_match_whatever_the_sort(conn):
    assert titles(songs(conn, search='friends', sort='artist')) == ['Friends', 'Famous Friends']


def test_duplicates_only(conn):
    # the same title by a different artist is a different song, not a copy
    lib2_seed.track(conn, 'Love Arcade', 'Arcade', 'Friends', path='/m/20.flac')
    conn.commit()
    result = songs(conn, dupes=True)
    assert titles(result) == ['You Proof', 'You Proof']
    assert result['pagination']['total_count'] == 2


def test_two_files_of_one_track_are_two_copies(conn):
    sand = conn.execute("SELECT id FROM lib2_tracks WHERE title = 'Sand in My Boots'").fetchone()[0]
    conn.execute("INSERT INTO lib2_track_files (track_id, path, is_primary) VALUES (?, '/m/3.mp3', 0)",
                 (sand,))
    conn.commit()
    row = next(s for s in songs(conn)['songs'] if s['title'] == 'Sand in My Boots')
    assert row['copies'] == 2 and len(row['file_ids']) == 2
    assert row['file_path'] == '/m/3.flac'          # the primary file plays
    assert [c['file_path'] for c in Q.song_copies(conn, sand)] == ['/m/3.flac', '/m/3.mp3']


def test_lossless_only(conn):
    result = titles(songs(conn, quality='lossless'))
    assert result.count('You Proof') == 1           # the mp3 copy is out
    assert len(result) == 7


def test_could_be_better_is_the_cutoff_unmet_verdict(conn, monkeypatch):
    # this fork's "could be better": Wanted -> Cutoff Unmet, evaluated (and
    # tested) in core.library2.wanted_views
    sand = conn.execute("SELECT id FROM lib2_tracks WHERE title = 'Sand in My Boots'").fetchone()[0]
    monkeypatch.setattr('core.library2.wanted_views.iter_cutoff_unmet',
                        lambda conn, **kw: iter([{'track_id': sand}]))
    assert titles(songs(conn, quality='upgradable')) == ['Sand in My Boots']


def test_copies_compare_best_file_first(conn):
    conn.execute("UPDATE lib2_track_files SET bitrate = 320 WHERE path = '/m/2.mp3'")
    conn.commit()
    mp3 = conn.execute("SELECT track_id FROM lib2_track_files WHERE path = '/m/2.mp3'").fetchone()[0]
    copies = Q.song_copies(conn, mp3)
    # the flac first, though the mp3 was asked about; one row per file
    assert [(s['file_path'], s['format']) for s in copies] == [('/m/1.flac', 'FLAC'), ('/m/2.mp3', 'MP3')]
    assert {s['album_title'] for s in copies} == {'Dangerous', 'Dangerous: The Double Album'}
    assert all(s['id'] == f"{s['track_id']}:{s['file_ids'][0]}" for s in copies)
    assert Q.song_copies(conn, 999999) == []
