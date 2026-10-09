"""#1568: a feat credit in the title hid a duplicate.

kevin2xk had two copies of Crack a Bottle on Relapse: Refill, one tagged by
Deezer ('Crack A Bottle', artist 'Eminem; Dr. Dre; 50 Cent') and one by
MusicBrainz ('Crack a Bottle (feat. Dr. Dre & 50 Cent)', artist 'Eminem').
The feat tail dragged title similarity to ~0.54, so the detector never
flagged them and reorganize kept failing on 'destination already exists'.
These run the real scan() against a tmp db.
"""

import sqlite3

from core.repair_jobs.base import JobContext
from core.repair_jobs.duplicate_detector import DuplicateDetectorJob


class _Db:
    def __init__(self, path):
        self.path = path

    def _get_connection(self):
        return sqlite3.connect(self.path)


def _make_db(tmp_path, rows):
    path = str(tmp_path / 'lib.db')
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE artists (id INTEGER PRIMARY KEY, name TEXT, thumb_url TEXT);
        CREATE TABLE albums (id INTEGER PRIMARY KEY, title TEXT, thumb_url TEXT);
        CREATE TABLE tracks (id INTEGER PRIMARY KEY, title TEXT, artist_id INTEGER,
            album_id INTEGER, file_path TEXT, bitrate INTEGER, duration REAL,
            track_artist TEXT);
        INSERT INTO artists VALUES (1, 'Eminem', NULL);
        INSERT INTO albums VALUES (10, 'Relapse: Refill', NULL);
    """)
    conn.executemany(
        "INSERT INTO tracks VALUES (?, ?, 1, 10, ?, 320, 297.0, ?)", rows)
    conn.commit()
    conn.close()
    return _Db(path)


def _run(db):
    findings = []
    ctx = JobContext(db=db, transfer_folder='', config_manager=None,
                     create_finding=lambda **kw: findings.append(kw) or True)
    DuplicateDetectorJob().scan(ctx)
    return findings


_DEEZER = (1, 'Crack A Bottle',
           '/music/Eminem/Eminem - Relapse_ Refill/Disc 1/18 - Crack A Bottle.mp3',
           'Eminem; Dr. Dre; 50 Cent')
_MB = (2, 'Crack a Bottle (feat. Dr. Dre & 50 Cent)',
       '/music/Eminem/Eminem - Relapse_ Refill/18 - Crack a Bottle (feat. Dr. Dre & 50 Cent).mp3',
       'Eminem')


def test_feat_tagged_copy_is_flagged(tmp_path):
    findings = _run(_make_db(tmp_path, [_DEEZER, _MB]))
    assert len(findings) == 1
    assert findings[0]['details']['count'] == 2


def test_other_parentheticals_still_distinguish(tmp_path):
    remix = (2, 'Crack a Bottle (Remix)',
             '/music/Eminem/Eminem - Relapse_ Refill/19 - Crack a Bottle (Remix).mp3',
             'Eminem')
    assert _run(_make_db(tmp_path, [_DEEZER, remix])) == []
