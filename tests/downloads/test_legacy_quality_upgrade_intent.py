"""a quality upgrade queued before Library v2 still imports as an upgrade.

the import only replaces a file when the task carries a sealed upgrade intent,
and that was only issued for Library v2's own wishlist rows. a 3.6.1 quality
upgrade row (job 'quality_upgrade', the file it replaces in
original_file_path) imported as a plain download: same format, the new file
was thrown away as already there; a new format, both copies were kept. the
file it replaces names the track, so the intent is issued for that.
"""

from __future__ import annotations

import sqlite3

from core.downloads.master import _lib2_track_for_path


class _Db:
    def __init__(self, conn):
        self._conn = conn

    def _get_connection(self):
        class _Keep:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def close(self):
                pass
        return _Keep(self._conn)


def test_the_replaced_files_track_is_found():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE lib2_track_files (id INTEGER PRIMARY KEY, track_id INTEGER, path TEXT, file_state TEXT)")
    conn.execute("INSERT INTO lib2_track_files (track_id, path, file_state) VALUES (42, '/music/a/low.mp3', 'active')")
    conn.execute("INSERT INTO lib2_track_files (track_id, path, file_state) VALUES (43, '/music/a/gone.mp3', 'deleted')")
    db = _Db(conn)
    assert _lib2_track_for_path(db, "/music/a/low.mp3") == 42
    assert _lib2_track_for_path(db, "/music/a/gone.mp3") is None
    assert _lib2_track_for_path(db, "/music/elsewhere.mp3") is None
