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


def test_recent_is_insertion_order_not_the_text_id(db):
    # ids are text: by id, "10" would sort below "9". recent means added last.
    c = sqlite3.connect(str(db.database_path))
    c.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source, file_path) "
              "VALUES ('10', 'al1', 'a1', 'Newest Song', 'plex', '/m/10.flac')")
    c.commit()
    c.close()
    assert titles(db.get_library_songs(sort='recent'))[0] == 'Newest Song'


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


def _plan(db, sql, params):
    conn = sqlite3.connect(str(db.database_path))
    try:
        return [row[3] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}", params)]
    finally:
        conn.close()


def _reads_only_indexes(plan):
    tracks_scans = [line for line in plan
                    if line.startswith('SCAN') and line.split()[1] in ('tracks', 't')]
    return bool(tracks_scans) and all('COVERING INDEX' in line for line in tracks_scans)


def test_a_search_never_reads_the_whole_tracks_table(db):
    """the regression that made a typed search take 20 s on a real library:
    a candidate subquery asking for tracks.id (a text column in none of the
    indexes) planned as SCAN tracks, a read of every row. every scan the
    search plans must be an index's."""
    clause = db._songs_search_clause(ready=True)
    plan = _plan(db, f"SELECT 1 FROM tracks t WHERE {clause}", ['%x%'] * 3)
    assert _reads_only_indexes(plan), plan


def test_ranking_a_search_reads_only_the_indexes(db):
    """ranking every match by reading its row cost 5-8 s for "lo" or "the"
    on a 300k library; the ranking must come from the indexes alone."""
    plan = _plan(db, db._songs_rank_sql(ready=True), ['x', 'x%', '%x%', '%x%', '%x%'])
    assert _reads_only_indexes(plan), plan


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


def test_duplicates_only(db):
    # the same title by a different artist is a different song, not a copy
    c = sqlite3.connect(str(db.database_path))
    c.execute("INSERT INTO tracks (id, album_id, artist_id, title, server_source, file_path) "
              "VALUES ('20', 'al4', 'a3', 'Friends', 'plex', '/m/20.flac')")
    c.commit()
    c.close()
    result = db.get_library_songs(dupes=True)
    assert titles(result) == ['You Proof', 'You Proof']
    assert result['pagination']['total_count'] == 2


def test_lossless_only(db):
    result = titles(db.get_library_songs(quality='lossless'))
    assert result.count('You Proof') == 1           # the mp3 copy is out
    assert len(result) == 7


def test_could_be_better_is_the_pending_quality_findings(db):
    c = sqlite3.connect(str(db.database_path))
    c.execute("INSERT INTO repair_findings (job_id, finding_type, status, entity_type, entity_id, title) "
              "VALUES ('quality_upgrade', 'upgrade', 'pending', 'track', '3', 'x')")
    c.execute("INSERT INTO repair_findings (job_id, finding_type, status, entity_type, entity_id, title) "
              "VALUES ('quality_upgrade', 'upgrade', 'resolved', 'track', '4', 'x')")
    c.commit()
    c.close()
    assert titles(db.get_library_songs(quality='upgradable')) == ['Sand in My Boots']


def test_copies_compare_best_file_first(db):
    c = sqlite3.connect(str(db.database_path))
    c.execute("UPDATE tracks SET bitrate = 320 WHERE id = '2'")
    c.commit()
    c.close()
    copies = db.get_library_song_copies('2')
    # the flac first, though the mp3 was asked about
    assert [(s['id'], s['format']) for s in copies] == [('1', 'FLAC'), ('2', 'MP3')]
    assert {s['album_title'] for s in copies} == {'Dangerous', 'Dangerous: The Double Album'}
    assert db.get_library_song_copies('nope') == []


def test_artist_and_album_sorts_walk_an_index_not_a_full_sort(db):
    """sorting every track by artist or album name took 20 s on a real
    library; the walk must follow the name index and only sort within it.
    a tiny test db plans more forgivingly than a 300k one (a text-id
    tie-break plans fine here and sorted everything there), so this catches
    a whole-library sort, not every slow shape: measure on a real copy."""
    for sort in ('artist', 'album', 'title', 'recent'):
        source, order = db._songs_sort_sql(sort, ready=True)
        plan = _plan(db, f"SELECT t.id FROM {source} ORDER BY {order}", [])
        assert 'USE TEMP B-TREE FOR ORDER BY' not in plan, (sort, plan)
