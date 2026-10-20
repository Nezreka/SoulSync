"""the one-time purge of what beatport_chart_<position> ids left behind.

maV (discord oct 12 2026) synced the beatport top 10, then a trance chart.
the sync match cache had beatport_chart_0 -> Fancy Footwork, so the trance
chart's track 1 synced as Fancy Footwork. those rows have to go or the bleed
outlives the id fix.
"""

from database.music_database import MusicDatabase


def _db(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / 'music.db'))


def _seed(db):
    with db._get_connection() as conn:
        c = conn.cursor()
        c.execute("DROP TABLE IF EXISTS _beatport_positional_purged")
        for sid in ('beatport_chart_0', 'beatport_chart_9', 'beatport_30700667', '4uLU6hMCjMI75M1A2tKUQC', 'beatportXchartY0'):
            c.execute("INSERT INTO sync_match_cache (spotify_track_id, normalized_title, normalized_artist, "
                      "server_source, server_track_id, confidence) VALUES (?, 't', 'a', 'plex', 1, 0.9)", (sid,))
        c.execute("PRAGMA table_info(manual_library_track_matches)")
        cols = {r[1] for r in c.fetchall()}
        conn.commit()
        return cols


def _remaining(db, sql):
    with db._get_connection() as conn:
        return [r[0] for r in conn.execute(sql).fetchall()]


def test_purges_only_positional_beatport_rows(tmp_path):
    db = _db(tmp_path)
    _seed(db)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO metadata_cache_entities (source, entity_type, entity_id, name, raw_json) "
                     "VALUES ('beatport', 'track', 'https://www.beatport.com/track/nirvana/1', 'Nirvana', '{}')")
        conn.execute("INSERT INTO metadata_cache_entities (source, entity_type, entity_id, name, raw_json) "
                     "VALUES ('spotify', 'track', 'abc', 'Keep Me', '{}')")
        conn.commit()
        db._purge_beatport_positional_matches(conn.cursor())
        conn.commit()
    left = sorted(_remaining(db, "SELECT spotify_track_id FROM sync_match_cache"))
    assert left == ['4uLU6hMCjMI75M1A2tKUQC', 'beatportXchartY0', 'beatport_30700667']
    assert _remaining(db, "SELECT source FROM metadata_cache_entities") == ['spotify']
    assert 'beatport_positional_purged' in _remaining(db, "SELECT name FROM schema_migrations")


def test_runs_once(tmp_path):
    db = _db(tmp_path)
    with db._get_connection() as conn:
        conn.execute("INSERT INTO sync_match_cache (spotify_track_id, normalized_title, normalized_artist, "
                     "server_source, server_track_id, confidence) VALUES ('beatport_chart_0', 't', 'a', 'plex', 1, 0.9)")
        conn.commit()
        db._purge_beatport_positional_matches(conn.cursor())
        conn.commit()
    # a fresh db already ran it on init, so a later row survives
    assert _remaining(db, "SELECT spotify_track_id FROM sync_match_cache") == ['beatport_chart_0']


def test_manual_matches_under_positional_ids_go(tmp_path):
    db = _db(tmp_path)
    cols = _seed(db)
    assert 'source_track_id' in cols
    with db._get_connection() as conn:
        for source, sid in (('beatport', 'beatport_chart_3'), ('spotify', 'spotify123')):
            conn.execute("INSERT INTO manual_library_track_matches (profile_id, source, source_track_id, "
                         "server_source, library_track_id) VALUES (1, ?, ?, 'plex', 5)", (source, sid))
        conn.commit()
        db._purge_beatport_positional_matches(conn.cursor())
        conn.commit()
    assert _remaining(db, "SELECT source_track_id FROM manual_library_track_matches") == ['spotify123']
