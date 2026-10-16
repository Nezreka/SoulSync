"""The Auto-Sync 'owned' count must be id-first.

The in_library tally used to be ONLY an exact case-sensitive
artist-name + track-title join — so any track the sync matcher landed under
a normalized name ("feat." formatting, remaster suffixes, casing) was never
credited, and the dashboard showed 45/50 owned against a 50/50 sync
(Boulder's Hot Hits report). Now a mirrored track counts as in-library when
its source id matches an enriched track's spotify_track_id, OR the old
exact-name join still holds.
"""

from __future__ import annotations

from database.music_database import MusicDatabase


def _build_db(tmp_path):
    db = MusicDatabase(str(tmp_path / "owned_counts.db"))
    with db._get_connection() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO lib2_artists (id, name, name_key)"
            " VALUES (1, 'Kendrick Lamar', 'kendrick lamar')"
        )
        c.execute(
            "INSERT INTO lib2_albums (id, primary_artist_id, title)"
            " VALUES (1, 1, 'DAMN.')"
        )
        # The library's copy of HUMBLE. carries a different display title than
        # the playlist's — only the spotify id links them.
        c.execute("""INSERT INTO lib2_tracks (id, album_id, title, spotify_id)
                     VALUES (1, 1, 'HUMBLE. - Explicit Version', 'sp-humble')""")
        # DNA. matches by exact name; its spotify id was never enriched.
        c.execute("""INSERT INTO lib2_tracks (id, album_id, title, spotify_id)
                     VALUES (2, 1, 'DNA.', NULL)""")
        c.executemany(
            "INSERT INTO lib2_track_artists (track_id, artist_id, role, position)"
            " VALUES (?, 1, 'primary', 0)", [(1,), (2,)],
        )
        c.executemany(
            "INSERT INTO lib2_track_files"
            " (track_id, path, is_primary, file_state, source)"
            " VALUES (?, ?, 1, 'active', 'import')",
            [(1, '/music/humble.flac'), (2, '/music/dna.flac')],
        )

        c.execute("""INSERT INTO mirrored_playlists (id, source, source_playlist_id, name, profile_id)
                     VALUES (10, 'spotify', 'plX', 'Test Mix', 1)""")
        rows = [
            # (position, track_name, artist_name, source_track_id)
            (0, 'HUMBLE.', 'Kendrick Lamar', 'sp-humble'),   # id match ONLY (names differ)
            (1, 'DNA.', 'Kendrick Lamar', 'sp-dna'),         # name match ONLY (id not in library)
            (2, 'Ghost Track', 'Nobody Real', ''),           # neither
        ]
        for pos, name, artist, sid in rows:
            c.execute("""INSERT INTO mirrored_playlist_tracks
                         (playlist_id, position, track_name, artist_name, source_track_id)
                         VALUES (10, ?, ?, ?, ?)""", (pos, name, artist, sid))
        conn.commit()
    return db


def test_batched_owned_count_is_id_first(tmp_path):
    db = _build_db(tmp_path)
    counts = db.get_all_mirrored_playlist_status_counts(profile_id=1)
    assert counts[10]['total'] == 3
    # HUMBLE. via spotify id + DNA. via exact name; the ghost stays unowned.
    assert counts[10]['in_library'] == 2


def test_per_playlist_owned_count_matches_batched(tmp_path):
    db = _build_db(tmp_path)
    counts = db.get_mirrored_playlist_status_counts(10)
    assert counts['total'] == 3
    assert counts['in_library'] == 2


def test_track_owned_by_id_and_name_counts_once(tmp_path):
    db = _build_db(tmp_path)
    with db._get_connection() as conn:
        # Make DNA.'s row ALSO id-matchable — must not double-count.
        conn.execute("UPDATE lib2_tracks SET spotify_id = 'sp-dna' WHERE id = 2")
        conn.commit()
    assert db.get_all_mirrored_playlist_status_counts(profile_id=1)[10]['in_library'] == 2
    assert db.get_mirrored_playlist_status_counts(10)['in_library'] == 2


def test_current_provider_identity_overrules_old_missing_verdict(tmp_path):
    db = _build_db(tmp_path)
    with db._get_connection() as conn:
        conn.execute("UPDATE mirrored_playlist_tracks SET extra_data=? WHERE source_track_id='sp-humble'",
                     ('{"in_library": false, "library_checked_at": 1}',))
    assert db.get_mirrored_playlist_status_counts(10)['in_library'] == 2
    assert db.get_all_mirrored_playlist_status_counts(1)[10]['in_library'] == 2


def _build_deezer_db(tmp_path):
    """Spectro's case: a Deezer playlist whose tracks the library holds.

    The library files compilations under Various Artists (the per-track credit
    keeps the real performer) and the Deezer download stores its provider id
    in external_ids, the spotify id is NULL. Only matching the spotify id and
    the album artist's exact name reported 0. Library v2 port of upstream
    4c738a0c0/68aad44ac.
    """
    db = MusicDatabase(str(tmp_path / "deezer_counts.db"))
    with db._get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO lib2_artists (id, name, name_key) VALUES (1, 'Various Artists', 'various artists')")
        c.execute("INSERT INTO lib2_artists (id, name, name_key) VALUES (2, 'Katy Perry', 'katy perry')")
        c.execute("INSERT INTO lib2_albums (id, primary_artist_id, title) VALUES (1, 1, 'Best Of 2017')")
        c.execute("""INSERT INTO lib2_tracks (id, album_id, title, spotify_id, external_ids)
                     VALUES (1, 1, 'Chained To The Rhythm', NULL, '{"deezer": "509938442"}')""")
        c.execute("INSERT INTO lib2_track_artists (track_id, artist_id, role, position) VALUES (1, 2, 'primary', 0)")
        c.execute("""INSERT INTO lib2_track_files (track_id, path, is_primary, file_state, source)
                     VALUES (1, '/app/Transfer/test.flac', 1, 'active', 'import')""")
        c.execute("""INSERT INTO mirrored_playlists (id, source, source_playlist_id, name, profile_id)
                     VALUES (10, 'deezer', '15846267061', 'My Music', 1)""")
        c.execute("""INSERT INTO mirrored_playlist_tracks
                     (playlist_id, position, track_name, artist_name, source_track_id)
                     VALUES (10, 0, 'Chained To The Rhythm', 'Katy Perry', '509938442')""")
        conn.commit()
    return db


def test_deezer_id_match_counts_in_library(tmp_path):
    db = _build_deezer_db(tmp_path)
    assert db.get_all_mirrored_playlist_status_counts(profile_id=1)[10]['in_library'] == 1
    assert db.get_mirrored_playlist_status_counts(10)['in_library'] == 1


def test_compilation_track_artist_match_counts_in_library(tmp_path):
    """Even without any provider id, the per-track credit must match."""
    db = _build_deezer_db(tmp_path)
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_tracks SET external_ids = '{}' WHERE id = 1")
        conn.execute("UPDATE mirrored_playlist_tracks SET source_track_id = '' WHERE playlist_id = 10")
        conn.commit()
    assert db.get_all_mirrored_playlist_status_counts(profile_id=1)[10]['in_library'] == 1
    assert db.get_mirrored_playlist_status_counts(10)['in_library'] == 1


def test_provider_id_match_rides_an_index_not_a_catalogue_scan(tmp_path):
    """each provider arm has an expression index, so the OR stays a lookup"""
    from database.music_database import _MIRRORED_ID_MATCH

    db = _build_deezer_db(tmp_path)
    with db._get_connection() as conn:
        plan = " ".join(str(r[-1]) for r in conn.execute(
            "EXPLAIN QUERY PLAN SELECT 1 FROM mirrored_playlist_tracks mpt"
            " WHERE EXISTS (SELECT 1 FROM lib2_tracks t WHERE " + _MIRRORED_ID_MATCH + ")"))
    assert "idx_lib2_tracks_ext_deezer" in plan
    assert "SCAN t" not in plan
