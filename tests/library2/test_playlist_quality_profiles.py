"""F-03/F-09: Playlist is the last inherited Quality Profile tier."""

import sqlite3

from core.library2.profile_lookup import (
    assign_quality_profile,
    effective_quality_profile,
    playlist_quality_conflicts,
)
from core.library2.monitor_rules import PROVENANCE_PLAYLIST, record_rule
from core.library2.materialize import materialize_mirrored_playlist_intents
from core.library2.wanted import recompute_wanted
from core.library2.wishlist_mirror import track_wishlist_payload


def _track_chain(conn):
    row = conn.execute(
        """SELECT t.id AS track_id, t.album_id, al.primary_artist_id AS artist_id
             FROM lib2_tracks t
             JOIN lib2_albums al ON al.id=t.album_id
            WHERE t.title='One Dance' AND al.album_type='album'"""
    ).fetchone()
    conn.execute(
        "UPDATE lib2_tracks SET quality_profile_explicit=0 WHERE id=?",
        (row["track_id"],),
    )
    conn.execute(
        "UPDATE lib2_albums SET quality_profile_explicit=0 WHERE id=?",
        (row["album_id"],),
    )
    conn.execute(
        "UPDATE lib2_artists SET quality_profile_explicit=0 WHERE id=?",
        (row["artist_id"],),
    )
    return int(row["track_id"])


def _ensure_playlist_tables(conn):
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mirrored_playlists(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               source TEXT NOT NULL,
               source_playlist_id TEXT NOT NULL,
               name TEXT NOT NULL,
               profile_id INTEGER DEFAULT 1,
               quality_profile_id INTEGER DEFAULT NULL)"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS mirrored_playlist_tracks(
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               playlist_id INTEGER NOT NULL,
               position INTEGER NOT NULL,
               track_name TEXT NOT NULL,
               artist_name TEXT NOT NULL,
               album_name TEXT,
               source_track_id TEXT,
               lib2_track_id INTEGER DEFAULT NULL)"""
    )


def _playlist(conn, *, source_id, name, quality_profile_id, track_id):
    _ensure_playlist_tables(conn)
    cur = conn.execute(
        """INSERT INTO mirrored_playlists(
               source, source_playlist_id, name, profile_id, quality_profile_id)
           VALUES('spotify', ?, ?, 1, ?)""",
        (source_id, name, quality_profile_id),
    )
    playlist_id = cur.lastrowid
    conn.execute(
        """INSERT INTO mirrored_playlist_tracks(
               playlist_id, position, track_name, artist_name, album_name,
               source_track_id, lib2_track_id)
           VALUES(?, 1, 'One Dance', 'Drake', 'Views', 'sp-track', ?)""",
        (playlist_id, track_id),
    )
    return int(playlist_id)


def test_single_playlist_default_is_effective_after_entity_cascade(imported_conn):
    track_id = _track_chain(imported_conn)
    playlist_id = _playlist(
        imported_conn,
        source_id="playlist-one",
        name="One",
        quality_profile_id=2,
        track_id=track_id,
    )

    result = effective_quality_profile(imported_conn, "tracks", track_id)

    assert result["id"] == 2
    assert result["source"] == "playlist"
    assert result["source_id"] == playlist_id
    assert result["conflict"] is False


def test_same_playlist_profile_is_unambiguous_but_different_profiles_conflict(
    imported_conn,
):
    track_id = _track_chain(imported_conn)
    _playlist(
        imported_conn,
        source_id="playlist-one",
        name="One",
        quality_profile_id=2,
        track_id=track_id,
    )
    _playlist(
        imported_conn,
        source_id="playlist-two",
        name="Two",
        quality_profile_id=2,
        track_id=track_id,
    )
    assert effective_quality_profile(imported_conn, "tracks", track_id)["conflict"] is False

    third = _playlist(
        imported_conn,
        source_id="playlist-three",
        name="Three",
        quality_profile_id=1,
        track_id=track_id,
    )

    result = effective_quality_profile(imported_conn, "tracks", track_id)
    assert result["source"] == "playlist"
    assert result["conflict"] is True
    assert {item["profile_id"] for item in result["playlist_profiles"]} == {1, 2}
    conflicts = playlist_quality_conflicts(imported_conn, playlist_id=third)
    assert [item["track_id"] for item in conflicts] == [track_id]
    payload = track_wishlist_payload(imported_conn, track_id)
    assert payload["quality_profile_conflict"] is True
    assert payload["_should_queue"] is False


def test_explicit_track_choice_resolves_playlist_conflict(imported_conn):
    track_id = _track_chain(imported_conn)
    first = _playlist(
        imported_conn,
        source_id="playlist-one",
        name="One",
        quality_profile_id=1,
        track_id=track_id,
    )
    second = _playlist(
        imported_conn,
        source_id="playlist-two",
        name="Two",
        quality_profile_id=2,
        track_id=track_id,
    )
    assert effective_quality_profile(imported_conn, "tracks", track_id)["conflict"] is True

    resolved = assign_quality_profile(imported_conn, "tracks", track_id, 2)
    recompute_wanted(imported_conn, track_ids=[track_id])

    assert resolved["source"] == "track"
    assert resolved["id"] == 2
    assert playlist_quality_conflicts(imported_conn, playlist_id=first) == []
    assert playlist_quality_conflicts(imported_conn, playlist_id=second) == []
    projected = imported_conn.execute(
        "SELECT effective_profile_id FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=1",
        (track_id,),
    ).fetchone()
    assert projected["effective_profile_id"] == 2


def test_explicit_album_profile_outranks_conflicting_playlists(imported_conn):
    track_id = _track_chain(imported_conn)
    album_id = imported_conn.execute(
        "SELECT album_id FROM lib2_tracks WHERE id=?", (track_id,)
    ).fetchone()[0]
    _playlist(
        imported_conn,
        source_id="playlist-one",
        name="One",
        quality_profile_id=1,
        track_id=track_id,
    )
    _playlist(
        imported_conn,
        source_id="playlist-two",
        name="Two",
        quality_profile_id=2,
        track_id=track_id,
    )

    assign_quality_profile(imported_conn, "albums", album_id, 2)
    result = effective_quality_profile(imported_conn, "tracks", track_id)

    assert result["id"] == 2
    assert result["source"] == "album"
    assert result.get("conflict", False) is False


def test_playlist_materialization_provenance_is_wanted(imported_conn):
    track_id = _track_chain(imported_conn)
    record_rule(
        imported_conn,
        "track",
        track_id,
        True,
        PROVENANCE_PLAYLIST,
    )

    recompute_wanted(imported_conn, track_ids=[track_id])

    projected = imported_conn.execute(
        "SELECT wanted, reason FROM lib2_wanted_tracks WHERE track_id=? AND profile_id=1",
        (track_id,),
    ).fetchone()
    assert projected["wanted"] == 1
    assert projected["reason"] == "track_rule:playlist_intent"


class _PlaylistDatabase:
    def __init__(self, path):
        self.path = path
        self.removed = []

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_mirrored_playlist(self, playlist_id):
        with self._get_connection() as conn:
            row = conn.execute(
                "SELECT * FROM mirrored_playlists WHERE id=?", (playlist_id,)
            ).fetchone()
            return dict(row) if row else None

    def get_mirrored_playlist_tracks(self, playlist_id):
        with self._get_connection() as conn:
            return [
                dict(row) for row in conn.execute(
                    "SELECT * FROM mirrored_playlist_tracks WHERE playlist_id=?",
                    (playlist_id,),
                )
            ]

    def remove_from_wishlist(self, track_id, profile_id=1):
        self.removed.append((track_id, profile_id))
        return True


def test_pipeline_materialization_links_rows_and_detects_cross_playlist_conflict(
    imported_conn,
):
    track_id = _track_chain(imported_conn)
    first = _playlist(
        imported_conn,
        source_id="playlist-one",
        name="One",
        quality_profile_id=1,
        track_id=track_id,
    )
    second = _playlist(
        imported_conn,
        source_id="playlist-two",
        name="Two",
        quality_profile_id=2,
        track_id=track_id,
    )
    imported_conn.execute(
        "UPDATE mirrored_playlist_tracks SET lib2_track_id=NULL"
    )
    imported_conn.commit()
    database = _PlaylistDatabase(
        imported_conn.execute("PRAGMA database_list").fetchone()[2]
    )

    first_result = materialize_mirrored_playlist_intents(database, first)
    second_result = materialize_mirrored_playlist_intents(database, second)

    assert first_result["linked"] == 1
    assert first_result["conflicts"] == []
    assert second_result["linked"] == 1
    assert [item["track_id"] for item in second_result["conflicts"]] == [track_id]
    with database._get_connection() as conn:
        links = conn.execute(
            "SELECT DISTINCT lib2_track_id FROM mirrored_playlist_tracks"
        ).fetchall()
        rule = conn.execute(
            "SELECT provenance FROM lib2_monitor_rules "
            "WHERE entity_type='track' AND entity_id=? AND profile_id=1",
            (track_id,),
        ).fetchone()
    assert [row[0] for row in links] == [track_id]
    assert rule["provenance"] == PROVENANCE_PLAYLIST
    assert database.removed
