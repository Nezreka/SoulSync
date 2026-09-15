"""Tests for candidate completeness caching and discography check optimizations."""

import types
import pytest
from database.music_database import DatabaseAlbum, DatabaseTrack, MusicDatabase


def test_build_candidate_completeness_cache():
    db = MusicDatabase()

    # Two sibling albums (split album scenario: e.g. Navidrome split)
    album1 = DatabaseAlbum(
        id=101,
        artist_id=5,
        title="Kid A",
        year=2000,
        track_count=10,
    )
    album2 = DatabaseAlbum(
        id=102,
        artist_id=5,
        title="Kid A",
        year=2000,
        track_count=10,
    )
    # Different album
    album3 = DatabaseAlbum(
        id=103,
        artist_id=5,
        title="Amnesiac",
        year=2001,
        track_count=11,
    )

    candidate_albums = [album1, album2, album3]

    candidate_tracks = [
        # Tracks for album 101
        DatabaseTrack(id=1, album_id=101, artist_id=5, title="Everything In Its Right Place", track_number=1, file_path="/music/kid_a/01.flac", bitrate=None),
        DatabaseTrack(id=2, album_id=101, artist_id=5, title="Kid A", track_number=2, file_path="/music/kid_a/02.flac", bitrate=None),
        # Track for album 102 (sibling - distinct track)
        DatabaseTrack(id=3, album_id=102, artist_id=5, title="The National Anthem", track_number=3, file_path="/music/kid_a/03.mp3", bitrate=320),
        # Duplicate track across siblings (same title & track_number)
        DatabaseTrack(id=4, album_id=102, artist_id=5, title="Kid A", track_number=2, file_path="/music/kid_a/02_dup.flac", bitrate=None),
        # Track for album 103
        DatabaseTrack(id=5, album_id=103, artist_id=5, title="Packt Like Sardines", track_number=1, file_path="/music/amnesiac/01.mp3", bitrate=256),
    ]

    cache = db.build_candidate_completeness_cache(candidate_albums, candidate_tracks)

    assert 101 in cache
    assert 102 in cache
    assert 103 in cache

    # 101 and 102 should share the same merged stats
    kid_a_stats = cache[101]
    assert kid_a_stats == cache[102]
    # Distinct tracks: "Everything In Its Right Place" (1), "Kid A" (2), "The National Anthem" (3) = 3 unique tracks
    assert kid_a_stats['owned_tracks'] == 3
    assert kid_a_stats['stored_track_count'] == 10
    assert kid_a_stats['formats'] == ['FLAC', 'MP3-320']
    assert set(kid_a_stats['sibling_ids']) == {101, 102}

    # 103 stats
    amnesiac_stats = cache[103]
    assert amnesiac_stats['owned_tracks'] == 1
    assert amnesiac_stats['stored_track_count'] == 11
    assert amnesiac_stats['formats'] == ['MP3-256']
    assert amnesiac_stats['sibling_ids'] == [103]


def test_check_album_completeness_with_cache():
    db = MusicDatabase()

    cache = {
        101: {
            'owned_tracks': 10,
            'stored_track_count': 10,
            'formats': ['FLAC'],
        },
        102: {
            'owned_tracks': 3,
            'stored_track_count': 10,
            'formats': ['MP3-320'],
        }
    }

    # Complete album: 10 owned out of 10 expected
    owned, expected, is_complete, formats = db.check_album_completeness(101, expected_track_count=10, completeness_cache=cache)
    assert owned == 10
    assert expected == 10
    assert is_complete is True
    assert formats == ['FLAC']

    # Partial album: 3 owned out of 10 expected
    owned, expected, is_complete, formats = db.check_album_completeness(102, expected_track_count=10, completeness_cache=cache)
    assert owned == 3
    assert expected == 10
    assert is_complete is False
    assert formats == ['MP3-320']

    # Deluxe vs standard edition: user owns standard edition (10 tracks, complete) but Spotify reports 15
    # Since owned (10) >= stored (10) and stored >= 15 * 0.6 (9), should treat as complete with expected=10
    owned, expected, is_complete, formats = db.check_album_completeness(101, expected_track_count=15, completeness_cache=cache)
    assert owned == 10
    assert expected == 10
    assert is_complete is True


def test_check_album_exists_with_completeness_uses_cache():
    db = MusicDatabase()

    album = DatabaseAlbum(
        id=201,
        artist_id=9,
        title="OK Computer",
        year=1997,
        track_count=12,
    )
    album.artist_name = "Radiohead"

    cache = {
        201: {
            'owned_tracks': 12,
            'stored_track_count': 12,
            'formats': ['FLAC'],
        }
    }

    res_album, confidence, owned, expected, is_complete, formats = db.check_album_exists_with_completeness(
        title="OK Computer",
        artist="Radiohead",
        expected_track_count=12,
        candidate_albums=[album],
        completeness_cache=cache,
    )

    assert res_album is not None
    assert res_album.id == 201
    assert confidence >= 0.8
    assert owned == 12
    assert expected == 12
    assert is_complete is True
    assert formats == ['FLAC']


def test_completeness_parity_sql_vs_cache(tmp_path):
    """Direct parity test: verify that check_album_completeness with completeness_cache
    produces the exact same results as the original un-cached SQL execution path across
    various real-world library scenarios.
    """
    db_path = str(tmp_path / 'parity_test.db')
    db = MusicDatabase(db_path)

    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'Daft Punk', 'local')")

        # Case 1: Complete album (single DB row)
        conn.execute("INSERT INTO albums (id, artist_id, title, year, track_count, server_source) VALUES (10, 1, 'Discovery', 2001, 14, 'local')")
        for i in range(1, 15):
            conn.execute(
                "INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, bitrate, server_source) "
                "VALUES (?, 10, 1, ?, ?, ?, ?, 'local')",
                (100 + i, f"Track {i}", i, f"/music/discovery/{i:02d}.flac", None)
            )

        # Case 2: Partial album (3 of 10 tracks) with MP3 bitrates
        conn.execute("INSERT INTO albums (id, artist_id, title, year, track_count, server_source) VALUES (20, 1, 'Homework', 1997, 10, 'local')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, bitrate, server_source) VALUES (201, 20, 1, 'Daftendirekt', 1, '/music/hw/01.mp3', 320, 'local')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, bitrate, server_source) VALUES (202, 20, 1, 'WDPK 83.7 FM', 2, '/music/hw/02.mp3', 320, 'local')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, bitrate, server_source) VALUES (203, 20, 1, 'Revolution 909', 3, '/music/hw/03.mp3', 256, 'local')")

        # Case 3: Split album across 2 rows (same title, year, artist_id) with duplicate track and empty file_path
        conn.execute("INSERT INTO albums (id, artist_id, title, year, track_count, server_source) VALUES (30, 1, 'Random Access Memories', 2013, 13, 'local')")
        conn.execute("INSERT INTO albums (id, artist_id, title, year, track_count, server_source) VALUES (31, 1, 'Random Access Memories', 2013, 13, 'local')")

        # Tracks on album 30: 1, 2, 3, 4
        for i in range(1, 5):
            conn.execute(
                "INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, bitrate, server_source) "
                "VALUES (?, 30, 1, ?, ?, ?, ?, 'local')",
                (300 + i, f"RAM Track {i}", i, f"/music/ram/30_{i}.flac", None)
            )
        # Duplicate track on album 31 (same title and track_number as track 1)
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, bitrate, server_source) VALUES (399, 31, 1, 'RAM Track 1', 1, '/music/ram/dup_1.flac', NULL, 'local')")
        # Unique tracks on album 31: 5, 6
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, bitrate, server_source) VALUES (305, 31, 1, 'RAM Track 5', 5, '/music/ram/31_5.mp3', 320, 'local')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, bitrate, server_source) VALUES (306, 31, 1, 'RAM Track 6', 6, '/music/ram/31_6.mp3', 320, 'local')")
        # Track with empty file path (should be excluded by both SQL and cache)
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, bitrate, server_source) VALUES (398, 31, 1, 'Ghost Track', 7, '', NULL, 'local')")

        conn.commit()

    candidate_albums = db.get_candidate_albums_for_artist('Daft Punk', server_source='local')
    candidate_tracks = db.get_candidate_tracks_for_albums([a.id for a in candidate_albums])
    cache = db.build_candidate_completeness_cache(candidate_albums, candidate_tracks)

    test_cases = [
        # (album_id, expected_track_count)
        (10, None),
        (10, 14),
        (10, 16),  # Deluxe expected (14 >= 16 * 0.6)
        (10, 30),  # Stored 14 < 30 * 0.6 -> incomplete
        (20, None),
        (20, 10),
        (20, 3),
        (30, None),
        (30, 6),
        (30, 13),
        (31, None),
        (31, 6),
        (31, 13),
    ]

    for album_id, expected_count in test_cases:
        # 1. Original un-cached execution (runs raw SQL)
        sql_result = db.check_album_completeness(album_id, expected_track_count=expected_count, completeness_cache=None)

        # 2. Optimized cached execution (runs in-memory lookup)
        cached_result = db.check_album_completeness(album_id, expected_track_count=expected_count, completeness_cache=cache)

        # Ensure exact parity across all 4 return values: (owned_tracks, expected_tracks, is_complete, formats)
        assert sql_result == cached_result, (
            f"Parity mismatch for album_id={album_id}, expected={expected_count}!\n"
            f"  SQL:    {sql_result}\n"
            f"  Cached: {cached_result}"
        )


def test_check_album_completion_parity_uncached_vs_cached(tmp_path):
    """End-to-end parity test: verify that check_album_completion produces the exact
    same dictionary output whether called with or without caches.
    """
    from core.metadata.completion import check_album_completion

    db_path = str(tmp_path / 'e2e_parity.db')
    db = MusicDatabase(db_path)

    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'Justice', 'local')")
        conn.execute("INSERT INTO albums (id, artist_id, title, year, track_count, server_source, deezer_id) VALUES (50, 1, 'Cross', 2007, 12, 'local', 'DZ-50')")
        for i in range(1, 13):
            conn.execute(
                "INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path, bitrate, server_source) "
                "VALUES (?, 50, 1, ?, ?, ?, ?, 'local')",
                (500 + i, f"Genesis {i}", i, f"/music/cross/{i:02d}.flac", None)
            )
        conn.commit()

    candidate_albums = db.get_candidate_albums_for_artist('Justice', server_source='local')
    candidate_tracks = db.get_candidate_tracks_for_albums([a.id for a in candidate_albums])
    completeness_cache = db.build_candidate_completeness_cache(candidate_albums, candidate_tracks)
    album_source_ids_cache = db.get_album_source_ids([a.id for a in candidate_albums])

    card = {
        'id': 'DZ-50',
        'name': 'Cross',
        'total_tracks': 12,
        'album_type': 'album',
        'year': 2007,
    }

    # 1. Without caches (original code path)
    res_uncached = check_album_completion(
        db, card, 'Justice',
        source_override='deezer',
        candidate_albums=candidate_albums,
    )

    # 2. With all caches (optimized code path)
    res_cached = check_album_completion(
        db, card, 'Justice',
        source_override='deezer',
        candidate_albums=candidate_albums,
        candidate_tracks=candidate_tracks,
        completeness_cache=completeness_cache,
        album_source_ids_cache=album_source_ids_cache,
        canonical_cache={},
        track_cache={},
    )

    assert res_uncached == res_cached
    assert res_cached['status'] == 'completed'
    assert res_cached['owned_tracks'] == 12
    assert res_cached['expected_tracks'] == 12
    assert res_cached['confidence'] == 1.0
    assert res_cached['formats'] == ['FLAC']


def test_unowned_releases_do_not_call_external_api(tmp_path, monkeypatch):
    """Verify that unowned albums, EPs, and singles with missing/zero track counts
    never make external HTTP calls to fetch tracklists, returning 'missing' immediately.
    """
    from core.metadata import completion as metadata_completion
    from core.metadata.completion import check_album_completion, check_single_completion

    db_path = str(tmp_path / 'no_external.db')
    db = MusicDatabase(db_path)

    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'Bicep', 'local')")
        conn.commit()

    candidate_albums = db.get_candidate_albums_for_artist('Bicep', server_source='local')

    # Spy on get_album_tracks_for_source — if called, fail the test
    external_calls = []

    def mock_get_album_tracks(source, album_id):
        external_calls.append((source, album_id))
        return []

    monkeypatch.setattr(metadata_completion, 'get_album_tracks_for_source', mock_get_album_tracks)

    # 1. Unowned album with total_tracks = 0 (e.g. Deezer discography item)
    unowned_album = {
        'id': 'DZ-999',
        'name': 'Isles',
        'total_tracks': 0,
        'album_type': 'album',
        'year': 2021,
    }
    res_album = check_album_completion(
        db, unowned_album, 'Bicep',
        source_override='deezer',
        candidate_albums=candidate_albums,
    )
    assert res_album['status'] == 'missing'
    assert res_album['owned_tracks'] == 0
    assert res_album['found_in_db'] is False
    assert len(external_calls) == 0, "External API was called for an unowned album!"

    # 2. Unowned EP with total_tracks = 0
    unowned_ep = {
        'id': 'DZ-888',
        'name': 'Glue EP',
        'total_tracks': 0,
        'album_type': 'ep',
        'year': 2017,
    }
    res_ep = check_single_completion(
        db, unowned_ep, 'Bicep',
        source_override='deezer',
        candidate_albums=candidate_albums,
    )
    assert res_ep['status'] == 'missing'
    assert res_ep['owned_tracks'] == 0
    assert res_ep['found_in_db'] is False
    assert len(external_calls) == 0, "External API was called for an unowned EP!"

    # 3. Unowned single with total_tracks = 0
    unowned_single = {
        'id': 'DZ-777',
        'name': 'Apricots',
        'total_tracks': 0,
        'album_type': 'single',
        'year': 2020,
    }
    res_single = check_single_completion(
        db, unowned_single, 'Bicep',
        source_override='deezer',
        candidate_albums=candidate_albums,
    )
    assert res_single['status'] == 'missing'
    assert res_single['owned_tracks'] == 0
    assert len(external_calls) == 0, "External API was called for an unowned single!"





def test_track_count_cache_separates_sources(monkeypatch):
    from core.metadata import completion as c
    monkeypatch.setattr(c, 'get_album_tracks_for_source', lambda source, rid: [{}] * (10 if source == 'itunes' else 3))
    cache = {}
    assert c._resolve_completion_track_total({'id': '123'}, ['itunes'], cache) == 10
    assert c._resolve_completion_track_total({'id': '123'}, ['deezer'], cache) == 3


def test_pin_existence_cache_keeps_actual_track_count(monkeypatch):
    from core.metadata import completion as c
    monkeypatch.setattr(c, 'get_album_tracks_for_source', lambda *args: [{}] * 10)
    db = types.SimpleNamespace(
        get_album_canonical=lambda aid: {'source': 'itunes', 'album_id': aid},
        check_album_completeness=lambda aid, total, **kw: (2, total, 2 >= total, []),
    )
    cache = {}
    assert c._canonical_pin_denies_card(db, types.SimpleNamespace(id='A'), 'itunes', 'B', pin_tracks_cache=cache)
    result = c._resolve_canonical_album_completion(db, types.SimpleNamespace(id='B'), pin_tracks_cache=cache)
    assert result['canonical_track_count'] == 10
    assert result['is_complete'] is False


def test_unknown_count_single_uses_release_completeness(monkeypatch):
    from core.metadata import completion as c
    monkeypatch.setattr(c, 'get_album_tracks_for_source', lambda *args: [{}] * 3)
    album = types.SimpleNamespace(id='local')
    db = types.SimpleNamespace(
        check_album_exists_with_completeness=lambda **kw: (album, 1, 1, 3, False, []),
        check_album_completeness=lambda *args, **kw: (1, 3, False, []),
        get_album_canonical=lambda aid: None,
    )
    result = c.check_single_completion(db, {'id': 'release', 'name': 'Song', 'album_type': 'single', 'total_tracks': 0}, 'Artist', source_chain=['discogs'], candidate_albums=[album])
    assert result['owned_tracks'] == 1
    assert result['expected_tracks'] == 3
    assert result['status'] != 'completed'


def test_cached_completeness_matches_sql_title_semantics():
    import sqlite3
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.executescript('CREATE TABLE albums(id,title,year,artist_id,track_count); CREATE TABLE tracks(album_id,title,track_number,file_path,bitrate);')
    db = object.__new__(MusicDatabase)
    db._get_connection = lambda: conn
    albums = [types.SimpleNamespace(id=i, title=title, year=2000, artist_id=1, track_count=10) for i, title in [(1, 'Album'), (2, 'album'), (3, 'Album ')]]
    tracks = [types.SimpleNamespace(album_id=i, title=title, track_number=1, file_path='/song.flac', bitrate=None) for i in [1, 2, 3] for title in ['Song', 'song', 'song ', 'Ä', 'ä']]
    conn.executemany('INSERT INTO albums VALUES(?,?,?,?,?)', [(a.id, a.title, a.year, a.artist_id, a.track_count) for a in albums])
    conn.executemany('INSERT INTO tracks VALUES(?,?,?,?,?)', [(t.album_id, t.title, t.track_number, t.file_path, t.bitrate) for t in tracks])
    try:
        cache = db.build_candidate_completeness_cache(albums, tracks)
        for album in albums:
            assert cache[album.id]['sibling_ids'] == [album.id]
            assert db.check_album_completeness(album.id, completeness_cache=cache) == db.check_album_completeness(album.id)
    finally:
        conn.close()
