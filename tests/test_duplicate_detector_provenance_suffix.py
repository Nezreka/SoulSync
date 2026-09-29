"""#1315 follow-up: dash-form provenance tails defeat duplicate detection.

kevin2xk's 8 Mile pair — "Rabbit Run" vs 'Rabbit Run - From "8 Mile"
Soundtrack' — is the same recording (MusicBrainz confirms file 1's recording
id 427ee9b2-1e5d-4ccb-91d6-bbd3badac80a sits on file 2's release id
bd85aed8-626c-4ce9-9f54-010d7526c92b), but the detector never flagged it:
the provenance tail dragged title similarity to 0.45, below the 0.85
threshold.

The detector's _normalize now strips dash-form " - from ..." tails (mirroring
audio_verification's long-standing rule), so the pair scores 1.0. These run
the real scan() against a tmp db.
"""

import sqlite3

from core.repair_jobs.base import JobContext
from core.repair_jobs.duplicate_detector import DuplicateDetectorJob, _normalize


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
        INSERT INTO artists VALUES (1, 'Eminem', NULL), (2, 'Various Artists', NULL);
        INSERT INTO albums VALUES (10, '8 Mile', NULL),
                                  (20, '8 Mile (Music From And Inspired By The Motion Picture)', NULL);
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


# The reporter's pair, verbatim tags (track_artist set so the #1315 healing
# path — which needs real files — is not involved).
_FILE1 = (1, 'Rabbit Run', 1, 10,
          '/music/Compilations/8 Mile/16 - Eminem - Rabbit Run.mp3',
          'Eminem')
_FILE2 = (2, 'Rabbit Run - From "8 Mile" Soundtrack', 1, 20,
          '/music/Compilations/8 Mile (Music From And Inspired By The Motion Picture)/'
          '16 - Eminem - Rabbit Run (From 8 Mile Soundtrack).mp3',
          'Eminem')


def test_provenance_tail_stripped_from_title():
    assert _normalize('Rabbit Run - From "8 Mile" Soundtrack') == 'rabbit run'
    assert _normalize('Rabbit Run') == 'rabbit run'


def test_parenthesized_from_still_distinguishes():
    # Dash-form is provenance; parenthesized content is identity — the
    # detector must keep telling "title" from "title (from the vault)" apart.
    assert _normalize('Rabbit Run (From the Vault)') == 'rabbit run (from the vault)'


def test_8_mile_pair_is_flagged(tmp_path):
    findings = _run(_make_db(tmp_path, [_FILE1, _FILE2]))
    assert len(findings) == 1
    titles = {t['title'] for t in findings[0]['details']['tracks']}
    assert titles == {'Rabbit Run', 'Rabbit Run - From "8 Mile" Soundtrack'}


def test_plain_titles_still_flagged(tmp_path):
    # The strip must not break the ordinary case.
    other = (2, 'Rabbit Run', 1, 20,
             '/music/Eminem/8 Mile/16 - Eminem - Rabbit Run.mp3',
             'Eminem')
    findings = _run(_make_db(tmp_path, [_FILE1, other]))
    assert len(findings) == 1


def test_different_songs_not_flagged(tmp_path):
    other = (2, 'Lose Yourself - From "8 Mile" Soundtrack', 1, 20,
             '/music/Compilations/8 Mile (Music From And Inspired By The Motion Picture)/'
             '01 - Eminem - Lose Yourself.mp3',
             'Eminem')
    findings = _run(_make_db(tmp_path, [_FILE1, other]))
    assert findings == []
