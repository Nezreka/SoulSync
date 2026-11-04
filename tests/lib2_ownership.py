"""Test helper: give a synthetic Library-v2 database its ownership evidence.

Library-v2 ownership is a live file row, and the enrichment queue offers only
what the user owns. The worker suites seed catalogue rows directly, so without
this an artist they created is correctly invisible — which is the behaviour
under test everywhere *else*. A trigger rather than a loop, so rows a test
inserts after the fixture are covered too.
"""

from __future__ import annotations


def own_every_track(conn):
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS test_own_every_track "
        "AFTER INSERT ON lib2_tracks BEGIN "
        "  INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
        "  VALUES(NEW.id, '/music/' || NEW.id || '.flac', 1, 'active'); "
        "END")
    conn.execute(
        "INSERT INTO lib2_track_files(track_id,path,is_primary,file_state) "
        "SELECT id, '/music/' || id || '.flac', 1, 'active' FROM lib2_tracks t "
        "WHERE NOT EXISTS (SELECT 1 FROM lib2_track_files f WHERE f.track_id=t.id)")
    return conn
