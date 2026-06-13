"""DB-level coverage for the per-artist watchlist auto_download ("follow only")
toggle — the column migrates in, defaults to auto-download, and round-trips
through get_watchlist_artists onto the WatchlistArtist dataclass."""

from __future__ import annotations

from database.music_database import MusicDatabase


def _get(db, profile_id=1):
    return {a.spotify_artist_id: a for a in db.get_watchlist_artists(profile_id=profile_id)}


def test_auto_download_defaults_true(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    assert db.add_artist_to_watchlist('sp1', 'Artist One', profile_id=1)
    artist = _get(db)['sp1']
    # New watchlist artists keep the existing behaviour: auto-download on.
    assert artist.auto_download is True


def test_auto_download_persists_when_disabled(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    db.add_artist_to_watchlist('sp1', 'Artist One', profile_id=1)

    # Flip it off the same way the config endpoint's UPDATE does.
    with db._get_connection() as conn:
        conn.execute(
            "UPDATE watchlist_artists SET auto_download = 0 WHERE spotify_artist_id = ?",
            ('sp1',),
        )
        conn.commit()

    artist = _get(db)['sp1']
    assert artist.auto_download is False


def test_auto_download_column_exists_after_migration(tmp_path):
    db = MusicDatabase(str(tmp_path / 'm.db'))
    with db._get_connection() as conn:
        cols = [c[1] for c in conn.execute("PRAGMA table_info(watchlist_artists)").fetchall()]
    assert 'auto_download' in cols
