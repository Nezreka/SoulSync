from __future__ import annotations

import pytest

from core.library2.artist_settings import (
    ArtistSettingsError,
    get_artist_settings,
    update_artist_settings,
)


WATCHLIST_DDL = """
CREATE TABLE watchlist_artists(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_artist_id TEXT,
    itunes_artist_id TEXT,
    deezer_artist_id TEXT,
    discogs_artist_id TEXT,
    amazon_artist_id TEXT,
    musicbrainz_artist_id TEXT,
    artist_name TEXT NOT NULL,
    image_url TEXT,
    include_albums INTEGER DEFAULT 1,
    include_eps INTEGER DEFAULT 1,
    include_singles INTEGER DEFAULT 1,
    include_live INTEGER DEFAULT 0,
    include_remixes INTEGER DEFAULT 0,
    include_acoustic INTEGER DEFAULT 0,
    include_compilations INTEGER DEFAULT 0,
    include_instrumentals INTEGER DEFAULT 0,
    auto_download INTEGER DEFAULT 1,
    lookback_days INTEGER,
    preferred_metadata_source TEXT,
    last_scan_timestamp TEXT,
    updated_at TEXT,
    profile_id INTEGER NOT NULL DEFAULT 1
)
"""


def _seed_watchlist(conn, *, profile_id=1, name="Renamed in Watchlist"):
    conn.execute(WATCHLIST_DDL)
    conn.execute(
        """INSERT INTO watchlist_artists(
               spotify_artist_id, artist_name, image_url, include_albums,
               include_eps, include_singles, auto_download, lookback_days,
               preferred_metadata_source, last_scan_timestamp, profile_id)
           VALUES('sp1', ?, 'https://img/artist.jpg', 1, 1, 0, 0, 30,
                  'deezer', '2026-07-01', ?)""",
        (name, profile_id),
    )
    conn.commit()


def _artist_id(conn):
    return conn.execute("SELECT id FROM lib2_artists WHERE spotify_id='sp1'").fetchone()[0]


def test_reads_the_existing_watchlist_row_by_provider_identity(imported_conn):
    _seed_watchlist(imported_conn)

    settings = get_artist_settings(imported_conn, _artist_id(imported_conn))

    assert settings["watchlist_name"] == "Renamed in Watchlist"
    assert settings["provider_ids"]["spotify"] == "sp1"
    assert settings["include_singles"] is False
    assert settings["auto_download"] is False
    assert settings["lookback_days"] == 30
    assert settings["preferred_metadata_source"] == "deezer"


def test_updates_watchlist_and_lib2_settings_without_a_parallel_copy(imported_conn):
    _seed_watchlist(imported_conn)
    artist_id = _artist_id(imported_conn)

    updated = update_artist_settings(
        imported_conn,
        artist_id,
        {
            "include_albums": False,
            "include_eps": True,
            "include_singles": True,
            "include_live": True,
            "include_instrumentals": True,
            "auto_download": True,
            "lookback_days": 90,
            "preferred_metadata_source": "spotify",
            "monitor_new_items": "new",
        },
        allowed_metadata_sources={"spotify", "deezer"},
    )
    imported_conn.commit()

    row = imported_conn.execute("SELECT * FROM watchlist_artists").fetchone()
    assert row["include_albums"] == 0
    assert row["include_eps"] == 1
    assert row["include_singles"] == 1
    assert row["include_live"] == 1
    assert row["include_instrumentals"] == 1
    assert row["auto_download"] == 1
    assert row["lookback_days"] == 90
    assert row["preferred_metadata_source"] == "spotify"
    assert row["last_scan_timestamp"] is None
    assert imported_conn.execute(
        "SELECT monitor_new_items FROM lib2_artists WHERE id=?", (artist_id,)
    ).fetchone()[0] == "new"
    assert updated["monitor_new_items"] == "new"


def test_rejects_no_core_release_types_and_unknown_provider(imported_conn):
    _seed_watchlist(imported_conn)
    artist_id = _artist_id(imported_conn)

    with pytest.raises(ArtistSettingsError, match="At least one"):
        update_artist_settings(
            imported_conn,
            artist_id,
            {"include_albums": False, "include_eps": False},
            allowed_metadata_sources={"spotify"},
        )
    with pytest.raises(ArtistSettingsError, match="not available"):
        update_artist_settings(
            imported_conn,
            artist_id,
            {"preferred_metadata_source": "unknown"},
            allowed_metadata_sources={"spotify"},
        )


def test_does_not_read_another_profiles_watchlist_row(imported_conn):
    _seed_watchlist(imported_conn, profile_id=2, name="Other Profile")

    with pytest.raises(ArtistSettingsError, match="admin watchlist") as exc:
        get_artist_settings(imported_conn, _artist_id(imported_conn), profile_id=1)
    assert exc.value.status == 409
