"""issues reported before the upgrade point at the catalogue rows again.

found in the second #1062 review: an issue stores the id of what it is
about, and 3.6.1 stored legacy (media-server) ids. read as catalogue ids
they named nothing (the issue said the item was gone, its fix buttons were
dead) or, with Plex numbers, another row.
"""

from __future__ import annotations

import json

from core.library2.legacy_issues import carry_legacy_issue_ids


def _issues_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS library_issues (
        id INTEGER PRIMARY KEY AUTOINCREMENT, profile_id INTEGER DEFAULT 1,
        entity_type TEXT NOT NULL, entity_id TEXT NOT NULL, category TEXT DEFAULT 'x',
        title TEXT DEFAULT 'x', snapshot_data TEXT DEFAULT '{}')""")


def _issue(conn, kind, entity_id, snapshot):
    return conn.execute("INSERT INTO library_issues(entity_type, entity_id, snapshot_data)"
                        " VALUES(?,?,?)", (kind, str(entity_id), json.dumps(snapshot))).lastrowid


def _get(conn, issue_id):
    row = conn.execute("SELECT entity_id, snapshot_data FROM library_issues WHERE id=?",
                       (issue_id,)).fetchone()
    return row[0], json.loads(row[1])


def _a_track(conn):
    """one imported track with its album and artist, and their legacy ids"""
    return conn.execute(
        """SELECT t.id, t.title, t.legacy_track_id, al.id, al.title, al.legacy_album_id,
                  ar.id, ar.name, ar.legacy_artist_id
             FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id
             JOIN lib2_artists ar ON ar.id=al.primary_artist_id
            WHERE t.legacy_track_id IS NOT NULL ORDER BY t.id LIMIT 1""").fetchone()


def test_a_3_6_1_track_issue_points_at_its_catalogue_rows(imported_conn):
    conn = imported_conn
    _issues_table(conn)
    (tid, title, old_tid, aid, album, old_aid, arid, artist, old_arid) = _a_track(conn)
    old = _issue(conn, "track", old_tid, {"id": old_tid, "title": title, "artist_id": old_arid,
                                         "artist_name": artist, "album_id": old_aid,
                                         "album_title": album})
    conn.commit()

    stats = carry_legacy_issue_ids(conn)

    entity, snap = _get(conn, old)
    assert stats["issues"] == 1
    assert entity == str(tid) and snap["id"] == tid
    assert (snap["artist_id"], snap["album_id"]) == (arid, aid)
    assert snap["_ids_carried_from_3_6_1"] is True


def test_an_issue_reported_on_library_v2_is_left_alone_and_it_runs_once(imported_conn):
    conn = imported_conn
    _issues_table(conn)
    (tid, title, _old_tid, aid, album, _old_aid, arid, artist, _old_arid) = _a_track(conn)
    new = _issue(conn, "track", tid, {"id": tid, "title": title, "artist_id": arid,
                                     "artist_name": artist, "album_id": aid, "album_title": album})
    unknown = _issue(conn, "track", "no-such-id", {"title": "Ghost"})
    conn.commit()
    before = (_get(conn, new), _get(conn, unknown))

    assert carry_legacy_issue_ids(conn)["issues"] == 0
    assert (_get(conn, new), _get(conn, unknown)) == before
    assert carry_legacy_issue_ids(conn) == {"issues": 0, "snapshot_ids": 0}


def test_a_name_that_does_not_match_is_never_guessed(imported_conn):
    """a legacy id whose catalogue row is now something else stays as it was"""
    conn = imported_conn
    _issues_table(conn)
    (_tid, _title, old_tid, *_rest) = _a_track(conn)
    issue = _issue(conn, "track", old_tid, {"title": "A Different Song"})
    conn.commit()
    carry_legacy_issue_ids(conn)
    assert _get(conn, issue)[0] == str(old_tid)


def test_the_deferred_convergence_runs_it():
    import inspect
    from core.library2 import bootstrap
    assert "carry_legacy_issue_ids(conn)" in inspect.getsource(bootstrap.run_deferred_backfills)


def test_every_lookup_uses_an_index(imported_conn):
    """the lookups were CAST(column AS TEXT)=?, which no index serves: every
    one read the whole table, per issue and per album track, inside the
    start-up convergence that holds the migration gate (discord, Nov 3: a
    big library's automation sat paused for hours on every start)"""
    conn = imported_conn
    _issues_table(conn)
    (tid, title, old_tid, aid, album, old_aid, arid, artist, old_arid) = _a_track(conn)
    _issue(conn, "track", old_tid, {"id": old_tid, "title": title, "artist_id": old_arid,
                                    "artist_name": artist, "album_id": old_aid, "album_title": album})
    _issue(conn, "album", old_aid, {"id": old_aid, "title": album,
                                    "tracks": [{"id": old_tid, "title": title}]})
    conn.commit()
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        carry_legacy_issue_ids(conn)
    finally:
        conn.set_trace_callback(None)

    lookups = [s for s in statements
               if s.lstrip().upper().startswith("SELECT") and " FROM lib2_" in s]
    assert lookups, "the carry looked nothing up"
    for sql in lookups:
        plan = " ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql))
        assert "USING" in plan and "SCAN lib2_" not in plan, (sql, plan)


def test_a_text_legacy_id_still_translates(imported_conn):
    """jellyfin/navidrome ids are text; the lookup asks for both spellings"""
    conn = imported_conn
    _issues_table(conn)
    (tid, title, *_rest) = _a_track(conn)
    conn.execute("UPDATE lib2_tracks SET legacy_track_id='a1b2c3' WHERE id=?", (tid,))
    issue = _issue(conn, "track", "a1b2c3", {"id": "a1b2c3", "title": title})
    conn.commit()

    assert carry_legacy_issue_ids(conn)["issues"] == 1
    assert _get(conn, issue)[0] == str(tid)
