"""T-11 — the last two jobs that declared ``lib2`` but scanned legacy tables.

Genre Tag Cleanup saw 9 of 273 albums in the user's real library and Comma
Artist Splitter 156 of 2,048 tracks, because both read ``artists``/``albums``/
``tracks`` while the catalogue moved to ``lib2_*`` at the P3 cutover. Both also
minted findings with bare legacy entity ids, which is the T-01 dead end.

These tests pin the native boundary: native rows are the scan subjects, the
findings carry a resolvable ``lib2:`` identity, and the fixes write back to the
native tables/files.
"""

from __future__ import annotations

import json
import sqlite3
import struct
from pathlib import Path

import pytest

from core.repair_jobs import get_all_jobs
from core.repair_jobs.base import JobContext


class _Cfg:
    """Strict genre mode on, tiny whitelist, Library v2 live."""

    def __init__(self, enabled=True, genres=None):
        self._d = {
            'genre_whitelist.enabled': enabled,
            'genre_whitelist.genres': genres or ['Rock', 'Jazz'],
            'features.library_v2': True,
        }

    def get(self, key, default=None):
        return self._d.get(key, default)


def _make_db(tmp_path: Path):
    db_path = str(tmp_path / 'lib2.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    from core.library2.schema import ensure_library_v2_schema
    ensure_library_v2_schema(conn)

    # A legacy catalogue that still carries the same dirty data. Nothing native
    # points at it — a native scan must not report it.
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS artists (id TEXT PRIMARY KEY, name TEXT,
                                            genres TEXT, thumb_url TEXT);
        CREATE TABLE IF NOT EXISTS albums (id TEXT PRIMARY KEY, artist_id TEXT,
                                           title TEXT, genres TEXT, thumb_url TEXT);
        CREATE TABLE IF NOT EXISTS tracks (id TEXT PRIMARY KEY, artist_id TEXT,
                                           album_id TEXT, title TEXT,
                                           track_number INTEGER, file_path TEXT);
        """
    )
    conn.commit()

    class _DB:
        database_path = db_path

        def _get_connection(self):
            opened = sqlite3.connect(db_path)
            opened.row_factory = sqlite3.Row
            return opened

    return _DB(), conn


def _ctx(db, cfg, findings):
    return JobContext(
        db=db, transfer_folder='/tmp', config_manager=cfg,
        create_finding=lambda **kw: findings.append(kw) or True,
        should_stop=lambda: False, is_paused=lambda: False,
    )


# ── Genre Tag Cleanup ────────────────────────────────────────────────────────

@pytest.fixture
def genre_db(tmp_path: Path):
    db, conn = _make_db(tmp_path)
    conn.execute(
        "INSERT INTO lib2_artists(id, name, genres, image_url) VALUES(1, ?, ?, ?)",
        ('Dirty Artist', json.dumps(['Rock', 'seen live']), 'art.jpg'),
    )
    conn.execute(
        "INSERT INTO lib2_artists(id, name, genres) VALUES(2, 'Clean Artist', ?)",
        (json.dumps(['Jazz']),),
    )
    conn.execute(
        "INSERT INTO lib2_albums(id, primary_artist_id, title, genres, image_url) "
        "VALUES(10, 1, 'Dirty Album', ?, 'alb.jpg')",
        (json.dumps(['favorites', 'Rock']),),
    )
    # Legacy-only dirt: invisible to a native scan.
    conn.execute(
        "INSERT INTO artists(id, name, genres) VALUES('LEG1', 'Legacy Only', ?)",
        (json.dumps(['seen live']),),
    )
    conn.commit()
    conn.close()
    return db


def test_native_genre_cleanup_scans_lib2_rows_and_ignores_legacy_only_dirt(genre_db):
    findings = []
    job = get_all_jobs()['genre_cleanup']()

    result = job.scan(_ctx(genre_db, _Cfg(), findings))

    names = sorted(f['details']['name'] for f in findings)
    assert names == ['Dirty Album', 'Dirty Artist']
    assert 'Legacy Only' not in names
    # Two artists + one album — the legacy rows are not part of the scope.
    assert result.scanned == 3


def test_native_genre_findings_carry_a_resolvable_native_identity(genre_db):
    findings = []

    get_all_jobs()['genre_cleanup']().scan(_ctx(genre_db, _Cfg(), findings))

    by_name = {f['details']['name']: f for f in findings}
    assert by_name['Dirty Artist']['entity_id'] == 'lib2:1'
    assert by_name['Dirty Album']['entity_id'] == 'lib2:10'
    # T-01: the details block alone is enough for _resolve_links, too.
    assert by_name['Dirty Album']['details']['library_v2']['album_ids'] == [10]
    assert by_name['Dirty Artist']['details']['library_v2']['artist_ids'] == [1]


def test_native_genre_scan_is_a_no_op_when_strict_mode_is_off(genre_db):
    findings = []

    result = get_all_jobs()['genre_cleanup']().scan(
        _ctx(genre_db, _Cfg(enabled=False), findings))

    assert findings == []
    assert result.scanned == 0


def test_genre_fix_rewrites_the_native_row(genre_db, monkeypatch):
    from core.repair_worker import RepairWorker

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = genre_db
    worker._config_manager = _Cfg()

    out = worker._fix_genre_cleanup('artist', 'lib2:1', None, {'kept_genres': ['Rock']})

    assert out['success'] is True
    conn = genre_db._get_connection()
    try:
        stored = conn.execute(
            "SELECT genres FROM lib2_artists WHERE id=1").fetchone()['genres']
        legacy_untouched = conn.execute(
            "SELECT genres FROM artists WHERE id='LEG1'").fetchone()['genres']
    finally:
        conn.close()
    assert json.loads(stored) == ['Rock']
    assert json.loads(legacy_untouched) == ['seen live']


def test_genre_fix_on_a_missing_native_row_reports_failure(genre_db):
    from core.repair_worker import RepairWorker

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = genre_db
    worker._config_manager = _Cfg()

    out = worker._fix_genre_cleanup('album', 'lib2:999', None, {'kept_genres': []})

    assert out['success'] is False


# ── Comma Artist Splitter ────────────────────────────────────────────────────

@pytest.fixture
def comma_db(tmp_path: Path):
    db, conn = _make_db(tmp_path)
    music = tmp_path / 'music'
    music.mkdir()
    from mutagen.flac import FLAC

    for index, name in enumerate(('01.flac', '02.flac'), start=1):
        path = music / name
        stream_info = bytearray(34)
        stream_info[0:2] = struct.pack(">H", 4096)
        stream_info[2:4] = struct.pack(">H", 4096)
        stream_info[10] = 0x0A
        stream_info[12] = 0x70
        path.write_bytes(
            b"fLaC" + bytes([0x80, 0x00, 0x00, 0x22])
            + bytes(stream_info) + bytes(range(256)) * 8
        )
        audio = FLAC(str(path))
        audio['artist'] = ['Camellia, Toby Fox']
        audio['title'] = [f'Track {index}']
        audio.save()

    conn.execute("INSERT INTO lib2_artists(id, name) VALUES(1, 'Camellia, Toby Fox')")
    conn.execute("INSERT INTO lib2_artists(id, name) VALUES(2, 'Camellia')")
    conn.execute("INSERT INTO lib2_artists(id, name) VALUES(3, 'Toby Fox')")
    conn.execute("INSERT INTO lib2_albums(id, primary_artist_id, title) "
                 "VALUES(10, 1, 'Collab EP')")
    conn.execute("INSERT INTO lib2_tracks(id, album_id, title, track_number) "
                 "VALUES(100, 10, 'One', 1)")
    conn.execute("INSERT INTO lib2_tracks(id, album_id, title, track_number) "
                 "VALUES(101, 10, 'Two', 2)")
    conn.execute("INSERT INTO lib2_track_files(id, track_id, path) VALUES(1000, 100, ?)",
                 (str(music / '01.flac'),))
    conn.execute("INSERT INTO lib2_track_files(id, track_id, path) VALUES(1001, 101, ?)",
                 (str(music / '02.flac'),))
    # Legacy-only comma artist: not a native subject.
    conn.execute("INSERT INTO artists(id, name) VALUES('LEG9', 'Legacy, Comma')")
    conn.execute("INSERT INTO tracks(id, artist_id, title, file_path) "
                 "VALUES('T9', 'LEG9', 'Legacy Track', '/legacy/x.flac')")
    conn.commit()
    conn.close()
    return db, music


def test_native_comma_splitter_flags_the_native_artist(comma_db, monkeypatch):
    db, _ = comma_db
    findings = []
    job = get_all_jobs()['comma_artist_splitter']()

    # Both checks answer from "the API": the full string is unknown, each part
    # is a real artist. Parts also resolve from the native library.
    monkeypatch.setattr(
        job, '_search_artist_names',
        lambda source, query, memo, symbols: {'camellia', 'toby fox'},
    )

    result = job.scan(_ctx(db, _Cfg(), findings))

    assert result.findings_created == 1
    finding = findings[0]
    assert finding['entity_id'] == 'lib2:1'
    assert finding['details']['split_artists'] == ['Camellia', 'Toby Fox']
    assert finding['details']['library_v2']['artist_ids'] == [1]
    assert finding['details']['track_count'] == 2
    assert 'Legacy, Comma' not in [f['details']['artist_name'] for f in findings]


def test_native_comma_splitter_resolves_parts_from_the_native_library(comma_db,
                                                                     monkeypatch):
    db, _ = comma_db
    findings = []
    job = get_all_jobs()['comma_artist_splitter']()
    queries = []

    def _search(source, query, memo, symbols):
        queries.append(query)
        return set()  # the API knows nobody; only the library can vouch

    monkeypatch.setattr(job, '_search_artist_names', _search)

    job.scan(_ctx(db, _Cfg(), findings))

    assert findings, 'both parts exist as native artists — the split is verified'
    resolution = findings[0]['details']['parts_resolution']
    assert [entry['verified_via'] for entry in resolution] == ['library', 'library']
    assert [entry['library_artist_id'] for entry in resolution] == [2, 3]


def test_comma_split_fix_retags_the_native_files(comma_db, monkeypatch):
    pytest.importorskip('mutagen')
    from mutagen.flac import FLAC

    db, music = comma_db
    # Real FLACs so the fix's mutagen path is exercised end to end.
    import subprocess
    for name in ('01.flac', '02.flac'):
        subprocess.run(
            ['ffmpeg', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=mono',
             '-t', '0.2', '-c:a', 'flac', str(music / name)],
            check=True, capture_output=True,
        )
        audio = FLAC(str(music / name))
        audio['artist'] = ['Camellia, Toby Fox']
        audio.save()

    from core.repair_worker import RepairWorker
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker._config_manager = _Cfg()
    worker.transfer_folder = '/tmp'

    out = worker._fix_comma_artist_split('artist', 'lib2:1', None, {
        'combined_name': 'Camellia, Toby Fox',
        'split_artists': ['Camellia', 'Toby Fox'],
    })

    assert out['success'] is True, out
    for name in ('01.flac', '02.flac'):
        audio = FLAC(str(music / name))
        assert audio['artist'] == ['Camellia; Toby Fox']
        assert list(audio['artists']) == ['Camellia', 'Toby Fox']
