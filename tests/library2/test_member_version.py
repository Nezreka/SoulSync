"""The artist grid keeps its library membership between pages until a table
it comes from changes (core.library2.member_version): ~1s a page on a big
library before. Never stale: every write that can change membership bumps the
counter in the same transaction."""

from __future__ import annotations

from core.library2 import member_version
from core.library2 import queries as Q


def _names(conn):
    return {a["name"] for a in Q.list_artists(conn)[0]}


def test_membership_is_reused_until_something_changes(imported_conn, monkeypatch):
    computed = []
    real = member_version.cached_member_ids

    def spy(conn, sql):
        before = dict(member_version._cache)
        ids = real(conn, sql)
        if member_version._cache != before:
            computed.append(1)
        return ids
    monkeypatch.setattr(member_version, "cached_member_ids", spy)
    import core.library2.artist_reader  # noqa: F401 - the reader imports it lazily

    first = _names(imported_conn)
    assert first and len(computed) == 1
    assert _names(imported_conn) == first and len(computed) == 1, "nothing changed, nothing recomputed"

    drake = imported_conn.execute("SELECT id FROM lib2_artists WHERE name='Drake'").fetchone()[0]
    assert "Drake" in first
    # every file of Drake's goes: off the grid at once
    imported_conn.execute(
        "DELETE FROM lib2_track_files WHERE track_id IN ("
        " SELECT t.id FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id"
        "  WHERE al.primary_artist_id=?"
        " UNION SELECT track_id FROM lib2_track_artists WHERE artist_id=?)", (drake, drake))
    imported_conn.execute("UPDATE lib2_artists SET monitored=0 WHERE id=?", (drake,))
    imported_conn.execute("UPDATE lib2_tracks SET monitored=0")
    imported_conn.execute("DELETE FROM lib2_wanted_tracks")
    imported_conn.execute("DELETE FROM lib2_monitor_rules")
    imported_conn.execute("UPDATE lib2_albums SET monitored=0")
    assert "Drake" not in _names(imported_conn)
    assert len(computed) == 2


def test_the_counter_exists_and_moves(imported_conn):
    before = member_version.current_version(imported_conn)
    assert before is not None
    imported_conn.execute("INSERT INTO lib2_artists(name) VALUES('Someone New')")
    assert member_version.current_version(imported_conn) == before + 1
