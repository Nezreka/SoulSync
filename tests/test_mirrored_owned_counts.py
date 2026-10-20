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

import sqlite3

import pytest

from database.music_database import MusicDatabase


def _build_db(tmp_path):
    db = MusicDatabase(str(tmp_path / "owned_counts.db"))
    with db._get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO artists (id, name) VALUES (1, 'Kendrick Lamar')")
        c.execute("INSERT INTO albums (id, artist_id, title) VALUES (1, 1, 'DAMN.')")
        # The library's copy of HUMBLE. carries a different display title than
        # the playlist's — only the spotify id links them.
        c.execute("""INSERT INTO tracks (id, album_id, artist_id, title, spotify_track_id)
                     VALUES (1, 1, 1, 'HUMBLE. - Explicit Version', 'sp-humble')""")
        # DNA. matches by exact name; its spotify id was never enriched.
        c.execute("""INSERT INTO tracks (id, album_id, artist_id, title, spotify_track_id)
                     VALUES (2, 1, 1, 'DNA.', NULL)""")

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


@pytest.mark.parametrize("provider_id_column", [
    "spotify_track_id", "deezer_id", "tidal_id", "qobuz_id", "itunes_track_id",
])
def test_batched_owned_count_is_id_first(tmp_path, provider_id_column):
    db = _build_db(tmp_path)
    with db._get_connection() as conn:
        conn.execute("UPDATE tracks SET spotify_track_id = NULL WHERE id = 1")
        conn.execute(f"UPDATE tracks SET {provider_id_column} = 'sp-humble' WHERE id = 1")
        conn.commit()
    counts = db.get_all_mirrored_playlist_status_counts(profile_id=1)
    assert counts[10]['total'] == 3
    # HUMBLE. via spotify id + DNA. via exact name; the ghost stays unowned.
    assert counts[10]['in_library'] == 2
    assert db.get_mirrored_playlist_status_counts(10)['in_library'] == 2


@pytest.mark.parametrize("batched", [True, False], ids=["batched", "single"])
def test_owned_counts_stay_bounded_after_analyze(tmp_path, monkeypatch, batched):
    """Sparse provider IDs must not cause a library scan per unchecked track.

    Budget SQLite VM work rather than wall time, so slow CI machines do not
    fail this regression. The old multi-column OR exceeds this budget after
    ANALYZE makes the mostly-NULL provider indexes look unselective.
    """
    db = _build_db(tmp_path)
    with db._get_connection() as conn:
        conn.executemany(
            "INSERT INTO tracks (id, album_id, artist_id, title, spotify_track_id) "
            "VALUES (?, 1, 1, ?, ?)",
            ((i + 100, f"Library track {i}", f"library-{i}") for i in range(10_000)),
        )
        conn.executemany(
            "INSERT INTO mirrored_playlist_tracks "
            "(playlist_id, position, track_name, artist_name, source_track_id) "
            "VALUES (10, ?, ?, 'Missing artist', ?)",
            ((i + 3, f"Missing track {i}", f"missing-{i}") for i in range(100)),
        )
        # Reproduce persisted full-ANALYZE stats from an upgraded installation.
        # The production helper may sample, which hides the old query's bad plan.
        conn.execute("PRAGMA analysis_limit=0")
        conn.execute("ANALYZE")
        conn.commit()

    connect = db._get_connection
    steps = 0

    def limit_work():
        nonlocal steps
        steps += 1_000
        return steps >= 200_000

    def budgeted_connection():
        conn = connect()
        conn.set_progress_handler(limit_work, 1_000)
        return conn

    monkeypatch.setattr(db, "_get_connection", budgeted_connection)
    counts = (
        db.get_all_mirrored_playlist_status_counts(profile_id=1)[10]
        if batched else db.get_mirrored_playlist_status_counts(10)
    )
    assert steps < 200_000, (
        f"Status counts exhausted the SQLite work budget (SQLite {sqlite3.sqlite_version})"
    )
    expected = {"total": 103, "discovered": 0, "wishlisted": 0, "in_library": 2}
    if batched:
        # the playlist card's "N need a match" (#1721)
        expected.update(library_checked=0, needs_match=0)
    assert counts == expected


def test_per_playlist_owned_count_matches_batched(tmp_path):
    db = _build_db(tmp_path)
    counts = db.get_mirrored_playlist_status_counts(10)
    assert counts['total'] == 3
    assert counts['in_library'] == 2


def test_track_owned_by_id_and_name_counts_once(tmp_path):
    db = _build_db(tmp_path)
    with db._get_connection() as conn:
        # Make DNA.'s row ALSO id-matchable — must not double-count.
        conn.execute("UPDATE tracks SET spotify_track_id = 'sp-dna' WHERE id = 2")
        conn.commit()
    assert db.get_all_mirrored_playlist_status_counts(profile_id=1)[10]['in_library'] == 2
    assert db.get_mirrored_playlist_status_counts(10)['in_library'] == 2


def _build_deezer_db(tmp_path):
    """Spectro's case: a Deezer playlist whose tracks the library holds.

    The library files compilations under Various Artists (track_artist keeps
    the real performer) and the Deezer download stores its provider id in
    deezer_id — spotify_track_id is NULL. The old join only checked
    spotify_track_id and exact artists.name, so the dashboard reported 0.
    """
    db = MusicDatabase(str(tmp_path / "deezer_counts.db"))
    with db._get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'Various Artists', 'soulsync')")
        c.execute("INSERT INTO albums (id, artist_id, title, server_source) VALUES (1, 1, 'Best Of 2017', 'soulsync')")
        c.execute("""INSERT INTO tracks (id, album_id, artist_id, title, track_artist,
                                          server_source, deezer_id, spotify_track_id, file_path)
                     VALUES (1, 1, 1, 'Chained To The Rhythm', 'Katy Perry',
                             'soulsync', '509938442', NULL, '/app/Transfer/test.flac')""")
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
        conn.execute("UPDATE tracks SET deezer_id = NULL WHERE id = 1")
        conn.execute("UPDATE mirrored_playlist_tracks SET source_track_id = '' WHERE playlist_id = 10")
        conn.commit()
    assert db.get_all_mirrored_playlist_status_counts(profile_id=1)[10]['in_library'] == 1
    assert db.get_mirrored_playlist_status_counts(10)['in_library'] == 1
