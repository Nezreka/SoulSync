"""§52.8 early materialization — resolve/create the lib2 Artist/Release/Track
for a CONFIRMED wishlist/acquisition intent before search/download starts."""

from __future__ import annotations

import pytest

from core.library2.materialize import (
    materialize_from_spotify_track,
    materialize_track_intent,
    materialize_wishlist_intent,
)
from core.library2.monitor_rules import PROVENANCE_WISHLIST


def _ids(conn, *, artist="Drake", album="Views", track="One Dance"):
    artist_id = conn.execute(
        "SELECT id FROM lib2_artists WHERE name=?", (artist,)
    ).fetchone()[0]
    album_id = conn.execute(
        "SELECT id FROM lib2_albums WHERE primary_artist_id=? AND title=?",
        (artist_id, album),
    ).fetchone()[0]
    track_id = conn.execute(
        "SELECT id FROM lib2_tracks WHERE album_id=? AND title=?",
        (album_id, track),
    ).fetchone()[0]
    return artist_id, album_id, track_id


def test_creates_new_artist_album_track(imported_conn):
    result = materialize_track_intent(
        imported_conn,
        artist_name="Brand New Artist",
        artist_spotify_id="sp-artist-new",
        album_title="Brand New Album",
        album_spotify_id="sp-album-new",
        track_title="Brand New Track",
        track_spotify_id="sp-track-new",
        track_number=1,
    )
    assert result["artist_id"] and result["album_id"] and result["track_id"]

    row = imported_conn.execute(
        """SELECT t.title AS track, al.title AS album, a.name AS artist
             FROM lib2_tracks t
             JOIN lib2_albums al ON al.id = t.album_id
             JOIN lib2_artists a ON a.id = al.primary_artist_id
            WHERE t.id=?""",
        (result["track_id"],),
    ).fetchone()
    assert row["track"] == "Brand New Track"
    assert row["album"] == "Brand New Album"
    assert row["artist"] == "Brand New Artist"


def test_reuses_existing_rows_by_spotify_id_no_duplicates(imported_conn):
    """Drake/Views/One Dance already exists (spotify_id='sp1' on the artist,
    per the legacy seed) — materializing the same track again must resolve
    to the exact same rows, never mint new ones."""
    artist_id, album_id, track_id = _ids(imported_conn)
    before_artists = imported_conn.execute("SELECT COUNT(*) c FROM lib2_artists").fetchone()["c"]
    before_tracks = imported_conn.execute("SELECT COUNT(*) c FROM lib2_tracks").fetchone()["c"]

    result = materialize_track_intent(
        imported_conn,
        artist_name="Drake",
        artist_spotify_id="sp1",
        album_title="Views",
        track_title="One Dance",
        track_number=1,
    )

    assert result["artist_id"] == artist_id
    assert result["album_id"] == album_id
    assert result["track_id"] == track_id
    assert imported_conn.execute("SELECT COUNT(*) c FROM lib2_artists").fetchone()["c"] == before_artists
    assert imported_conn.execute("SELECT COUNT(*) c FROM lib2_tracks").fetchone()["c"] == before_tracks


def test_call_is_idempotent(imported_conn):
    first = materialize_track_intent(
        imported_conn,
        artist_name="Idempotent Artist",
        track_title="Idempotent Track",
        track_spotify_id="sp-idem",
    )
    second = materialize_track_intent(
        imported_conn,
        artist_name="Idempotent Artist",
        track_title="Idempotent Track",
        track_spotify_id="sp-idem",
    )
    assert first == second


def test_explicit_profile_wins_and_is_marked_explicit(imported_conn):
    artist_id, album_id, track_id = _ids(imported_conn)
    # Give the album an explicit profile that would otherwise be inherited.
    imported_conn.execute(
        "UPDATE lib2_albums SET quality_profile_id=1, quality_profile_explicit=1 WHERE id=?",
        (album_id,),
    )

    result = materialize_track_intent(
        imported_conn,
        artist_name="Drake",
        artist_spotify_id="sp1",
        album_title="Views",
        track_title="One Dance",
        track_number=1,
        explicit_profile_id=2,
    )

    assert result["quality_profile"] == {
        "id": 2,
        "source": "track",
        "source_id": track_id,
        "explicit": True,
    }


def test_no_explicit_profile_leaves_track_uninherited_from_materialize(imported_conn):
    """Without an explicit_profile_id, materialize must not itself pin a
    track-level override — the resolved profile keeps coming from whichever
    ancestor level already owns it (here: the album)."""
    artist_id, album_id, track_id = _ids(imported_conn)
    imported_conn.execute(
        "UPDATE lib2_albums SET quality_profile_id=2, quality_profile_explicit=1 WHERE id=?",
        (album_id,),
    )

    result = materialize_track_intent(
        imported_conn,
        artist_name="Drake",
        artist_spotify_id="sp1",
        album_title="Views",
        track_title="One Dance",
        track_number=1,
    )

    assert result["quality_profile"]["source"] == "album"
    assert result["quality_profile"]["source_id"] == album_id
    assert result["quality_profile"]["id"] == 2
    track_row = imported_conn.execute(
        "SELECT quality_profile_explicit FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone()
    assert track_row["quality_profile_explicit"] == 0


def test_track_becomes_monitored_and_wanted_without_touching_artist(imported_conn):
    artist_id, album_id, track_id = _ids(imported_conn)
    # An existing track (has a file per the legacy seed) starts out however
    # the importer left it; explicitly unmonitor the artist first so we can
    # tell materialize did NOT cascade back up.
    imported_conn.execute("UPDATE lib2_artists SET monitored=0 WHERE id=?", (artist_id,))
    imported_conn.commit()

    materialize_track_intent(
        imported_conn,
        artist_name="Drake",
        artist_spotify_id="sp1",
        album_title="Views",
        track_title="One Dance",
        track_number=1,
    )

    wanted_row = imported_conn.execute(
        "SELECT wanted FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=1",
        (track_id,),
    ).fetchone()
    assert wanted_row is not None

    rule_row = imported_conn.execute(
        "SELECT monitored, provenance FROM lib2_monitor_rules "
        "WHERE entity_type='track' AND entity_id=? AND profile_id=1",
        (track_id,),
    ).fetchone()
    assert rule_row["monitored"] == 1
    assert rule_row["provenance"] == PROVENANCE_WISHLIST

    artist_row = imported_conn.execute(
        "SELECT monitored FROM lib2_artists WHERE id=?", (artist_id,)
    ).fetchone()
    assert artist_row["monitored"] == 0


def test_missing_artist_name_or_track_title_raises(imported_conn):
    with pytest.raises(ValueError):
        materialize_track_intent(imported_conn, artist_name="", track_title="X")
    with pytest.raises(ValueError):
        materialize_track_intent(imported_conn, artist_name="X", track_title="")


class TestMaterializeFromSpotifyTrack:
    def _spotify_track(self, **overrides):
        data = {
            "id": "sp-track-1",
            "name": "New Song",
            "artists": [{"name": "New Artist", "id": "sp-artist-1"}],
            "album": {
                "name": "New Album",
                "id": "sp-album-1",
                "album_type": "album",
                "total_tracks": 10,
            },
            "track_number": 3,
            "disc_number": 1,
        }
        data.update(overrides)
        return data

    def test_adapts_shape_and_creates_rows(self, imported_conn):
        result = materialize_from_spotify_track(imported_conn, self._spotify_track())
        assert result is not None
        row = imported_conn.execute(
            """SELECT t.title, t.track_number, t.disc_number, t.spotify_id,
                      al.title AS album, al.spotify_id AS album_sp,
                      a.name AS artist, a.spotify_id AS artist_sp
                 FROM lib2_tracks t
                 JOIN lib2_albums al ON al.id = t.album_id
                 JOIN lib2_artists a ON a.id = al.primary_artist_id
                WHERE t.id=?""",
            (result["track_id"],),
        ).fetchone()
        assert row["title"] == "New Song"
        assert row["track_number"] == 3
        assert row["spotify_id"] == "sp-track-1"
        assert row["album"] == "New Album"
        assert row["album_sp"] == "sp-album-1"
        assert row["artist"] == "New Artist"
        assert row["artist_sp"] == "sp-artist-1"

    def test_single_album_type_inferred_from_total_tracks(self, imported_conn):
        data = self._spotify_track()
        data["album"] = {"name": "A Single", "id": "sp-single-1", "total_tracks": 1}
        result = materialize_from_spotify_track(imported_conn, data)
        row = imported_conn.execute(
            "SELECT album_type FROM lib2_albums WHERE id=?", (result["album_id"],)
        ).fetchone()
        assert row["album_type"] == "single"

    def test_string_artist_entry_supported(self, imported_conn):
        data = self._spotify_track()
        data["artists"] = ["Plain String Artist"]
        result = materialize_from_spotify_track(imported_conn, data)
        assert result is not None
        row = imported_conn.execute(
            "SELECT name FROM lib2_artists WHERE id=?", (result["artist_id"],)
        ).fetchone()
        assert row["name"] == "Plain String Artist"

    def test_returns_none_for_wing_it_style_missing_fields(self, imported_conn):
        assert materialize_from_spotify_track(imported_conn, {"id": "wing_it_1"}) is None
        assert materialize_from_spotify_track(imported_conn, {"name": "No Artist"}) is None

    def test_returns_none_for_non_dict_payload(self, imported_conn):
        assert materialize_from_spotify_track(imported_conn, None) is None
        assert materialize_from_spotify_track(imported_conn, "not-a-dict") is None


class TestMaterializeWishlistIntent:
    def test_noop_when_feature_flag_disabled(self, monkeypatch, legacy_db, imported_conn):
        from config.settings import config_manager
        monkeypatch.setattr(
            config_manager, "get",
            lambda key, default=None: False if key == "features.library_v2" else default)
        monkeypatch.setattr("database.music_database.get_database", lambda: legacy_db)

        before = imported_conn.execute("SELECT COUNT(*) c FROM lib2_tracks").fetchone()["c"]
        result = materialize_wishlist_intent({
            "id": "sp-flagged-off",
            "name": "Should Not Materialize",
            "artists": [{"name": "Nobody"}],
            "album": {"name": "Nowhere"},
        })
        assert result is None
        assert imported_conn.execute(
            "SELECT COUNT(*) c FROM lib2_tracks"
        ).fetchone()["c"] == before

    def test_commits_and_returns_result_when_enabled(self, monkeypatch, legacy_db, imported_conn):
        from config.settings import config_manager
        real_get = config_manager.get

        def fake_get(key, default=None):
            if key == "features.library_v2":
                return True
            return real_get(key, default)

        monkeypatch.setattr(config_manager, "get", fake_get)
        monkeypatch.setattr("database.music_database.get_database", lambda: legacy_db)

        result = materialize_wishlist_intent({
            "id": "sp-flagged-on",
            "name": "Should Materialize",
            "artists": [{"name": "Somebody", "id": "sp-somebody"}],
            "album": {"name": "Somewhere", "id": "sp-somewhere"},
            "track_number": 1,
        })

        assert result is not None
        row = imported_conn.execute(
            "SELECT title FROM lib2_tracks WHERE id=?", (result["track_id"],)
        ).fetchone()
        assert row["title"] == "Should Materialize"

    def test_never_raises_on_internal_error(self, monkeypatch, legacy_db):
        from config.settings import config_manager
        real_get = config_manager.get

        def fake_get(key, default=None):
            if key == "features.library_v2":
                return True
            return real_get(key, default)

        monkeypatch.setattr(config_manager, "get", fake_get)

        def _boom():
            raise RuntimeError("db unavailable")

        monkeypatch.setattr("database.music_database.get_database", _boom)

        result = materialize_wishlist_intent({
            "id": "sp-x", "name": "X", "artists": [{"name": "Y"}], "album": {"name": "Z"},
        })
        assert result is None
