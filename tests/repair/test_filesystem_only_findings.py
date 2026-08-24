"""A finding about a file the catalogue never knew must still be fixable.

The corruption detector walks the library folders as well as the catalogue, so
it raises findings with ``entity_type='file'`` and **no** ``entity_id`` — audio
sitting in the transfer tree that no ``lib2_tracks`` row points at. Both
delete-and-re-download handlers opened with

    if not entity_id:
        return {'success': False, 'error': 'No track ID associated ...'}

which made those rows unworkable in three ways:

* the fix could only fail, so the finding stayed ``pending`` and every later
  "fix all" tried it again;
* the failure was not ``stale``, so the #1143 retire-on-vanished path — the one
  thing that WOULD have closed a finding naming a file that is long gone — was
  unreachable;
* the row promised "approve to delete it and re-download the real version",
  and there is no track to re-download.

Deleting the damaged file IS the whole fix for a file nothing references. The
promise is what has to go, not the button.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase


def _worker(tmp_path: Path) -> RepairWorker:
    db = MusicDatabase(str(tmp_path / 'm.db'))
    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    worker.transfer_folder = str(tmp_path)
    worker._config_manager = None
    db.add_to_wishlist = lambda *a, **kw: True
    return worker


FILE_ONLY_TYPES = ('corrupt_audio', 'short_preview_track')


@pytest.mark.parametrize('finding_type', FILE_ONLY_TYPES)
def test_a_file_only_finding_deletes_the_file(finding_type, tmp_path: Path):
    audio = tmp_path / 'Artist' / 'Album' / '01 - Bad.flac'
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b'not really flac')

    result = _worker(tmp_path)._execute_fix(
        finding_type, 'file', None, str(audio),
        {'reason': 'FLAC__STREAM_DECODER_ERROR_STATUS_LOST_SYNC'})

    assert result['success'] is True, result
    assert result['action'] == 'deleted_file'
    assert not audio.exists()


@pytest.mark.parametrize('finding_type', FILE_ONLY_TYPES)
def test_a_file_only_fix_never_promises_a_re_download(finding_type, tmp_path: Path):
    """No catalogue row means no wishlist entry — so say so, and do not set the
    redownload markers the track-backed path uses."""
    audio = tmp_path / 'Artist' / 'Album' / '01 - Bad.flac'
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b'not really flac')

    result = _worker(tmp_path)._execute_fix(
        finding_type, 'file', None, str(audio), {})

    assert result.get('repair_intent') != 'redownload'
    assert result.get('library_v2_file_deleted') is not True
    assert 'download' not in result['message'].lower()


@pytest.mark.parametrize('finding_type', FILE_ONLY_TYPES)
def test_a_file_only_finding_whose_file_is_gone_is_retired(finding_type, tmp_path: Path):
    """The reported symptom: findings naming paths that no longer exist. They
    can never be fixed, so they must be closed rather than retried forever."""
    folder = tmp_path / 'Artist' / 'Album'
    folder.mkdir(parents=True)
    vanished = folder / '01 - Gone.flac'

    result = _worker(tmp_path)._execute_fix(
        finding_type, 'file', None, str(vanished), {})

    assert result['success'] is False
    assert result['stale'] is True, result


@pytest.mark.parametrize('finding_type', FILE_ONLY_TYPES)
def test_an_unreachable_library_is_not_mistaken_for_a_vanished_file(
        finding_type, tmp_path: Path):
    """A whole folder that is not there is a mount that is not there. Retiring
    the finding would throw away the only record of the problem."""
    unreachable = tmp_path / 'nas' / 'Artist' / 'Album' / '01 - Gone.flac'

    result = _worker(tmp_path)._execute_fix(
        finding_type, 'file', None, str(unreachable), {})

    assert result['success'] is False
    assert result.get('stale') is not True, result


@pytest.mark.parametrize('finding_type', FILE_ONLY_TYPES)
def test_no_subject_and_no_path_is_still_refused(finding_type, tmp_path: Path):
    result = _worker(tmp_path)._execute_fix(finding_type, 'file', None, None, {})

    assert result['success'] is False
    assert result.get('stale') is not True
