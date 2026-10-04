"""A finding whose subject is a bare integer predates Library v2 — decline it.

Every repair job that has a catalogue subject now emits ``lib2:<row_id>``; a bare
integer is a legacy back-reference by contract (T-12). The ``_fix_*`` handlers
still carried a second, legacy implementation behind that check — a whole
``else`` branch per handler that read and mutated ``artists``/``albums``/
``tracks``. It is unreachable from any scan the code can still produce, which is
exactly the property that makes it dangerous: it is the version whose breakage
nobody notices, and it deletes rows.

What can still arrive at a handler with a bare id is a *stale* finding — one
persisted before the job moved to native subjects and never applied. It names a
row in a catalogue that is gone, so it is refused, and the startup prune drops
it so the next scan can raise it again against the right subject.

A finding with **no** entity id at all is a different thing and must keep
working: ``track_number_repair``'s folder scan raises findings about a file on
disk that the catalogue does not know, and its fix is a pure retag.
"""

from __future__ import annotations

from contextlib import closing
from pathlib import Path

import pytest

from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


def _db(tmp_path: Path) -> MusicDatabase:
    return MusicDatabase(str(tmp_path / 'm.db'))


def _worker(db: MusicDatabase, tmp_path: Path) -> RepairWorker:
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None
    db.add_to_wishlist = lambda *a, **kw: True
    return worker


# finding_type -> the details that would let the legacy branch do its work
STALE_SUBJECT_CASES = {
    'dead_file': {'_fix_action': 'remove'},
    'short_preview_track': {'expected_duration_s': 200.0},
    'corrupt_audio': {'_fix_action': 'delete'},
    'fake_lossless': {'_fix_action': 'delete'},
    'library_retag': {},
    'unwanted_content': {},
    'metadata_gap': {'found_fields': {'isrc': 'DEZZZ0000001'}},
    'bpm_backfill': {'found_fields': {'bpm': 120.0}},
    'album_release_year_mismatch': {'album_id': 1, 'canonical_year': '1978', 'tracks': []},
    'acoustid_mismatch': {'_fix_action': 'delete'},
    'missing_cover_art': {'found_artwork_url': 'https://cdn/art.jpg'},
    'genre_cleanup': {'kept_genres': ['Rock'], 'removed_genres': ['seen live']},
    'genre_enrichment': {'added_genres': ['Rock']},
    'comma_artist_split': {'split_artists': ['A', 'B'], 'combined_name': 'A, B'},
    'track_number_mismatch': {'correct_track_num': 3},
    # `path_mismatch` names a lib2 track. It used to be absent from the set,
    # so the handler read a bare id as a NATIVE id while the sync layer read
    # the same id as a legacy back-reference -- the file moved, one track's row
    # was re-pointed, and a different track's history recorded it.
    'path_mismatch': {'from': 'old/a.flac', 'to': 'new/a.flac',
                      'from_abs': '/m/old/a.flac', 'to_abs': '/m/new/a.flac'},
}

STALE_SUBJECT_ENTITY_TYPES = {
    'genre_cleanup': 'artist',
    'genre_enrichment': 'artist',
    'fake_lossless': 'file',
    'album_release_year_mismatch': 'album',
}


@pytest.mark.parametrize('finding_type', sorted(STALE_SUBJECT_CASES))
def test_a_bare_id_subject_is_declined_as_stale(finding_type, tmp_path: Path):
    db = _db(tmp_path)
    audio = tmp_path / 'a.flac'
    audio.write_bytes(b'fake audio bytes')

    result = _worker(db, tmp_path)._execute_fix(
        finding_type, STALE_SUBJECT_ENTITY_TYPES.get(finding_type, 'track'), '1',
        str(audio), dict(STALE_SUBJECT_CASES[finding_type]))

    assert result['success'] is False, finding_type
    assert result.get('stale_subject') is True, finding_type


def test_the_declining_types_are_declared(tmp_path: Path):
    """The prune and the guards must name the same set, or a stale finding is
    either refused forever or quietly deleted while still appliable."""
    from core.repair_worker import NATIVE_SUBJECT_FINDING_TYPES

    assert set(STALE_SUBJECT_CASES) == NATIVE_SUBJECT_FINDING_TYPES


def test_every_handler_that_declines_is_declared():
    """The declared set is derived knowledge: a handler that refuses a bare id
    must be in it, or its stale findings survive every start with a fix button
    that cannot work (genre_cleanup did, F06)."""
    import inspect
    import re

    from core.repair_worker import NATIVE_SUBJECT_FINDING_TYPES

    def declines(fn) -> bool:
        source = inspect.getsource(fn)
        if '_stale_legacy_subject(' in source:
            return True
        return any('_stale_legacy_subject(' in inspect.getsource(getattr(RepairWorker, name))
                   for name in set(re.findall(r'self\.(_[a-z_]+)\(', source))
                   if callable(getattr(RepairWorker, name, None)))

    worker = RepairWorker.__new__(RepairWorker)
    declining = {ftype for ftype, handler in worker._fix_handlers().items() if declines(handler)}
    assert declining == NATIVE_SUBJECT_FINDING_TYPES


def _pending(db: MusicDatabase, job_id, finding_type, entity_id, status='pending'):
    conn = db._get_connection()
    conn.execute(
        "INSERT INTO repair_findings (job_id, finding_type, severity, status, "
        "entity_type, entity_id, title) VALUES (?, ?, 'warning', ?, 'track', ?, 't')",
        (job_id, finding_type, status, entity_id))
    conn.commit()
    conn.close()


def _surviving(db: MusicDatabase) -> set:
    with closing(db._get_connection()) as conn:
        return {(r[0], r[1]) for r in conn.execute(
            "SELECT finding_type, COALESCE(entity_id,'') FROM repair_findings "
            "WHERE status='pending'")}


def test_startup_prunes_the_stale_findings_it_would_refuse(tmp_path: Path):
    db = _db(tmp_path)
    _pending(db, 'dead_file_cleaner', 'dead_file', '1')
    _pending(db, 'dead_file_cleaner', 'dead_file', 'lib2:1')
    _pending(db, 'track_number_repair', 'track_number_mismatch', None)
    _pending(db, 'empty_folder_cleaner', 'empty_folder', '/music/Some Folder')

    RepairWorker._prune_stale_legacy_findings(_worker(db, tmp_path))

    assert _surviving(db) == {
        ('dead_file', 'lib2:1'),          # native subject — the one that works
        ('track_number_mismatch', ''),    # folder scan — no catalogue subject
        ('empty_folder', '/music/Some Folder'),  # a path, not a legacy row id
    }


def test_the_prune_keeps_resolved_history(tmp_path: Path):
    """Pruning is "raise it again against the right subject", not "forget it
    happened" — the same line ``_prune_retired_job_findings`` holds."""
    db = _db(tmp_path)
    _pending(db, 'dead_file_cleaner', 'dead_file', '1', status='resolved')

    RepairWorker._prune_stale_legacy_findings(_worker(db, tmp_path))

    with closing(db._get_connection()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM repair_findings").fetchone()[0] == 1


def test_the_worker_no_longer_reads_the_legacy_catalogue():
    """The forks are gone, not merely unreachable.

    Every subject the worker fixes is a Library-v2 row, so there is nothing left
    to look up in ``artists``/``albums``/``tracks``. A read reappearing here
    means a handler grew a legacy fallback again.
    """
    import pathlib

    from tests.library2.legacy_usage import count_legacy_usage

    usage = count_legacy_usage(pathlib.Path('core/repair_worker.py').read_text())
    assert usage.reads == 0


def test_the_legacy_writes_left_are_only_path_write_throughs():
    """What survives is the file's *location*, and only until the readers move.

    ``tracks.file_path`` and ``tracks.track_number`` were the only columns a
    repair fix ever had to write on both sides, so that moving or renaming a
    file did not leave the legacy view stale.
    Nothing else may be written: a ``DELETE``, an ``INSERT``, or an update of any
    other column would be the legacy branch growing back.
    """
    import pathlib
    import re

    from tests.library2.legacy_usage import _WRITE

    allowed = re.compile(
        r"UPDATE tracks SET (file_path|track_number)\s*=", re.IGNORECASE)
    offenders = [
        (number, line.strip())
        for number, line in enumerate(
            pathlib.Path('core/repair_worker.py').read_text().splitlines(), 1)
        if _WRITE.search(line) and not allowed.search(line)
    ]
    assert not offenders, offenders


def test_a_finding_with_no_subject_at_all_still_retags_the_file(tmp_path: Path):
    """``track_number_repair``'s folder scan has no catalogue row to name, so it
    writes ``entity_id=None``. That is not a stale subject — the fix is the tag
    write, and refusing it would silently disable the whole folder-scan half of
    the job."""
    db = _db(tmp_path)
    audio = tmp_path / 'b.mp3'
    audio.write_bytes(b'ID3fake')
    written = {}

    worker = _worker(db, tmp_path)
    result = worker._fix_track_number(
        'track', None, str(audio),
        {'correct_track_num': 7, '_test_tag_writer': written.update})

    assert result.get('stale_subject') is not True
