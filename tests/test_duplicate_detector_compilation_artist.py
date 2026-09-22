"""#1263: a compilation copy never matched the artist's own copy.

tracks.artist_id is the album artist, so the track on a Various Artists
compilation was compared as 'Various Artists' vs 'Dan Winter' and fell
under the artist threshold. the per-track credit lives in track_artist.
these run the real scan() query against a tmp db, since the bug was in
the SELECT, not in _scan_bucket.
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
        INSERT INTO artists VALUES (1, 'Dan Winter', NULL), (2, 'Various Artists', NULL);
        INSERT INTO albums VALUES (10, '100 Dance Club Hits Vol. 1', NULL),
                                  (20, '100 Dance Club Hits (Vol. 1)', NULL);
    """)
    conn.executemany(
        "INSERT INTO tracks VALUES (?, ?, ?, ?, ?, 320, 190.0, ?)", rows)
    conn.commit()
    conn.close()
    return _Db(path)


def _run(db):
    findings = []
    ctx = JobContext(db=db, transfer_folder='', config_manager=None,
                     create_finding=lambda **kw: findings.append(kw) or True)
    DuplicateDetectorJob().scan(ctx)
    return findings


_OWN = (1, 'Carry Your Heart (Radio Edit)', 1, 10,
        '/music/Dan Winter/Dan Winter - 100 Dance Club Hits Vol. 1/'
        '46 - Carry Your Heart (Radio Edit).mp3', None)


def test_compilation_copy_matches_the_artists_own_copy(tmp_path):
    comp = (2, 'Carry Your Heart - Radio Edit', 2, 20,
            '/music/Compilations/100 Dance Club Hits (Vol. 1)/'
            '04 - Dan Winter - Carry Your Heart - Radio Edit.mp3', 'Dan Winter')
    findings = _run(_make_db(tmp_path, [_OWN, comp]))
    assert len(findings) == 1
    members = findings[0]['details']['tracks']
    assert {t['id'] for t in members} == {1, 2}
    assert {t['artist'] for t in members} == {'Dan Winter'}


def test_blank_track_artist_falls_back_to_album_artist(tmp_path):
    # empty string, not NULL - the jellyfin/plex paths can leave either
    other = (2, 'Carry Your Heart (Radio Edit)', 1, 20,
             '/music/Dan Winter/Other/01 - Carry Your Heart (Radio Edit).mp3', '')
    findings = _run(_make_db(tmp_path, [_OWN, other]))
    assert len(findings) == 1


def test_different_performers_on_one_compilation_are_not_dupes(tmp_path):
    # both used to read as 'Various Artists' and score a perfect artist match
    a = (1, 'Hallelujah', 2, 20, '/music/Compilations/X/01 - Hallelujah.mp3',
         'Leonard Cohen')
    b = (2, 'Hallelujah', 2, 20, '/music/Compilations/X/02 - Hallelujah.mp3',
         'Jeff Buckley')
    assert _run(_make_db(tmp_path, [a, b])) == []


def test_feat_credit_on_one_copy_still_matches(tmp_path):
    # jellyfin keeps every ArtistItem as 'A; B'. whole-string that scores
    # ~0.5 against the plain 'Dan Winter' copy
    feat = (2, 'Carry Your Heart (Radio Edit)', 2, 20,
            '/music/Compilations/Y/07 - Carry Your Heart (Radio Edit).mp3',
            'Dan Winter; Some Singer')
    findings = _run(_make_db(tmp_path, [_OWN, feat]))
    assert len(findings) == 1
