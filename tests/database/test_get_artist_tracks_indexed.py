"""Tests for `MusicDatabase.get_artist_tracks_indexed` — the indexed
two-step lookup that backs the sync candidate pool fast path."""

from __future__ import annotations

from database.music_database import MusicDatabase


def _seed(db: MusicDatabase, rows):
    """Insert (artist_name, album_title, track_title, server_source) tuples.
    IDs are TEXT PRIMARY KEY in this schema so we hand-mint string IDs to
    keep foreign-key wiring happy."""
    conn = db._get_connection()
    cursor = conn.cursor()
    artist_ids: dict = {}
    album_ids: dict = {}
    track_counter = 0
    for artist_name, album_title, track_title, server_source in rows:
        if artist_name not in artist_ids:
            aid = f"a-{len(artist_ids) + 1}"
            cursor.execute(
                "INSERT INTO artists (id, name, server_source) VALUES (?, ?, ?)",
                (aid, artist_name, server_source),
            )
            artist_ids[artist_name] = aid
        album_key = (artist_name, album_title, server_source)
        if album_key not in album_ids:
            alid = f"al-{len(album_ids) + 1}"
            cursor.execute(
                "INSERT INTO albums (id, artist_id, title, server_source) VALUES (?, ?, ?, ?)",
                (alid, artist_ids[artist_name], album_title, server_source),
            )
            album_ids[album_key] = alid
        track_counter += 1
        cursor.execute(
            "INSERT INTO tracks (id, album_id, artist_id, title, server_source) VALUES (?, ?, ?, ?, ?)",
            (f"t-{track_counter}", album_ids[album_key], artist_ids[artist_name], track_title, server_source),
        )
    conn.commit()


def test_exact_name_match_returns_tracks(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed(db, [
        ('Drake', 'For All The Dogs', 'First Person Shooter', 'plex'),
        ('Drake', 'For All The Dogs', 'Slime You Out', 'plex'),
        ('SZA', 'SOS', 'Kill Bill', 'plex'),
    ])
    tracks = db.get_artist_tracks_indexed('Drake')
    titles = sorted(t.title for t in tracks)
    assert titles == ['First Person Shooter', 'Slime You Out']


def test_case_insensitive_fallback_finds_artist(tmp_path):
    """Exact match misses 'DRAKE' (case-sensitive index lookup), but the
    fallback LOWER() comparison still finds the canonical 'Drake' row."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed(db, [
        ('Drake', 'FATD', 'IDGAF', 'plex'),
    ])
    tracks = db.get_artist_tracks_indexed('DRAKE')
    assert len(tracks) == 1
    assert tracks[0].title == 'IDGAF'


def test_artist_absent_returns_empty_list(tmp_path):
    """Genuinely missing artists must fall straight through both steps
    and return [] — that's what lets the caller skip the slow LIKE
    fallback when the artist isn't in the library at all."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed(db, [
        ('Drake', 'FATD', 'IDGAF', 'plex'),
    ])
    assert db.get_artist_tracks_indexed('Nonexistent Artist') == []


def test_empty_name_returns_empty(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    assert db.get_artist_tracks_indexed('') == []


def test_server_source_filter_excludes_other_servers(tmp_path):
    """The pool is per-server — Plex sync must not see Jellyfin tracks
    even when the artist exists on both."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed(db, [
        ('Drake', 'Plex Album', 'Plex Track', 'plex'),
        ('Drake', 'Jellyfin Album', 'Jellyfin Track', 'jellyfin'),
    ])
    plex_tracks = db.get_artist_tracks_indexed('Drake', server_source='plex')
    jellyfin_tracks = db.get_artist_tracks_indexed('Drake', server_source='jellyfin')
    assert [t.title for t in plex_tracks] == ['Plex Track']
    assert [t.title for t in jellyfin_tracks] == ['Jellyfin Track']


def test_no_server_filter_returns_all_servers(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed(db, [
        ('Drake', 'Plex Album', 'Plex Track', 'plex'),
        ('Drake', 'Jellyfin Album', 'Jellyfin Track', 'jellyfin'),
    ])
    tracks = db.get_artist_tracks_indexed('Drake')
    titles = sorted(t.title for t in tracks)
    assert titles == ['Jellyfin Track', 'Plex Track']


def test_limit_caps_result_set(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed(db, [('Drake', 'Album', f'Track {i}', 'plex') for i in range(10)])
    tracks = db.get_artist_tracks_indexed('Drake', limit=3)
    assert len(tracks) == 3


def test_returned_tracks_carry_artist_and_album_fields(tmp_path):
    """check_track_exists' batched path reads `artist_name` and
    `album_title` off each track for confidence scoring — verify the
    indexed query attaches them like search_tracks does."""
    db = MusicDatabase(str(tmp_path / "music.db"))
    _seed(db, [
        ('Drake', 'For All The Dogs', 'First Person Shooter', 'plex'),
    ])
    tracks = db.get_artist_tracks_indexed('Drake')
    assert len(tracks) == 1
    t = tracks[0]
    assert t.artist_name == 'Drake'
    assert t.album_title == 'For All The Dogs'
    assert t.title == 'First Person Shooter'
