"""#1315: the #1263 fix needs track_artist, which is NULL for every row
that hasn't synced since 3.4.6 (the column didn't exist before). those
rows fall back to the album artist, so a compilation copy still reads
as 'Various Artists' and never matches the performer's own copy — the
exact #1263 miss, surviving the #1263 fix.

the detector heals those rows from the file's own artist tag and
persists the credit so the read happens once. these run the real
scan() query against a tmp db, since the gap was in scan()'s data,
not in _scan_bucket.
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


def _track_artist(db, track_id):
    conn = sqlite3.connect(db.path)
    try:
        row = conn.execute(
            "SELECT track_artist FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


_OWN = (1, 'Carry Your Heart (Radio Edit)', 1, 10,
        '/music/Dan Winter/Dan Winter - 100 Dance Club Hits Vol. 1/'
        '46 - Carry Your Heart (Radio Edit).mp3', None)

# the reporter's compilation copy in its post-3.4.6-upgrade state:
# track_artist is NULL because the row never synced after the upgrade
_COMP_NULL = (2, 'Carry Your Heart - Radio Edit', 2, 20,
              '/music/Compilations/100 Dance Club Hits (Vol. 1)/'
              '04 - Dan Winter - Carry Your Heart - Radio Edit.mp3', None)


def _fake_tags(monkeypatch, artist):
    def _read(file_path):
        return {'available': True, 'tags': {'artist': artist}} if artist else \
               {'available': False, 'reason': 'no tags'}
    monkeypatch.setattr(
        'core.repair_jobs.duplicate_detector.read_embedded_tags', _read)


def test_null_track_artist_healed_from_file_tag(tmp_path, monkeypatch):
    _fake_tags(monkeypatch, 'Dan Winter')
    findings = _run(_make_db(tmp_path, [_OWN, _COMP_NULL]))
    assert len(findings) == 1
    members = findings[0]['details']['tracks']
    assert {t['id'] for t in members} == {1, 2}
    assert {t['artist'] for t in members} == {'Dan Winter'}


def test_healed_credit_is_persisted(tmp_path, monkeypatch):
    db = _make_db(tmp_path, [_OWN, _COMP_NULL])
    _fake_tags(monkeypatch, 'Dan Winter')
    assert len(_run(db)) == 1
    # the credit lands in the db, exactly as a sync would have written it
    assert _track_artist(db, 2) == 'Dan Winter'
    # a second scan with real tag reads (files don't exist) still finds it
    monkeypatch.undo()
    assert len(_run(db)) == 1


def test_no_file_read_when_track_artist_already_set(tmp_path, monkeypatch):
    comp = _COMP_NULL[:5] + ('Dan Winter',)
    def _boom(file_path):
        raise AssertionError('file tags must not be read')
    monkeypatch.setattr(
        'core.repair_jobs.duplicate_detector.read_embedded_tags', _boom)
    assert len(_run(_make_db(tmp_path, [_OWN, comp]))) == 1


def test_no_file_read_on_non_compilation_album(tmp_path, monkeypatch):
    # NULL track_artist on the artist's own album: the fallback is already
    # right, so no tag read is needed
    other = (2, 'Carry Your Heart (Radio Edit)', 1, 10,
             '/music/Dan Winter/Other/01 - Carry Your Heart (Radio Edit).mp3',
             None)
    def _boom(file_path):
        raise AssertionError('file tags must not be read')
    monkeypatch.setattr(
        'core.repair_jobs.duplicate_detector.read_embedded_tags', _boom)
    assert len(_run(_make_db(tmp_path, [_OWN, other]))) == 1


def test_unreadable_file_keeps_fallback_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        'core.repair_jobs.duplicate_detector.read_embedded_tags',
        lambda fp: {'available': False, 'reason': 'missing file'})
    db = _make_db(tmp_path, [_OWN, _COMP_NULL])
    # no crash, no finding (still the pre-fix miss), nothing written
    assert _run(db) == []
    assert _track_artist(db, 2) is None


def test_various_artists_tag_is_not_stored(tmp_path, monkeypatch):
    # the file agreeing with the album artist teaches nothing
    _fake_tags(monkeypatch, 'Various Artists')
    db = _make_db(tmp_path, [_OWN, _COMP_NULL])
    assert _run(db) == []
    assert _track_artist(db, 2) is None
