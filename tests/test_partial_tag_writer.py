"""Real temporary audio exercises partial writes and aborted atomic saves."""
from types import SimpleNamespace
import shutil
import subprocess

import pytest
from mutagen import File

from core.repair_jobs.album_tag_consistency import _read_tag, _write_tag
from core.repair_jobs.track_number_repair import _fix_track_number_tag, _fix_disc_number_tag
from core.repair_worker import RepairWorker


@pytest.fixture(params=['flac', 'opus'])
def audio_path(request, tmp_path):
    if not shutil.which('ffmpeg'):
        pytest.skip('ffmpeg is needed to synthesize temporary audio')
    path = tmp_path / ('sample.' + request.param)
    subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                    'sine=frequency=440:duration=0.15', str(path)], check=True)
    audio = File(path)
    audio['album'] = ['Original']
    audio['title'] = ['Keep title']
    audio['tracknumber'] = ['2']
    audio.save()
    return str(path)


def test_consistency_reads_and_sets_opus_without_changing_other_fields(audio_path):
    audio = File(audio_path)
    assert _read_tag(audio, 'album') == 'Original'
    assert _write_tag(audio, 'album', 'Reviewed') is True
    audio.save()
    assert File(audio_path)['album'] == ['Reviewed']
    assert File(audio_path)['title'] == ['Keep title']


def test_number_repairs_reach_opus_and_preserve_text(audio_path):
    assert _fix_track_number_tag(audio_path, 7, 12) is True
    assert _fix_disc_number_tag(audio_path, 2, 3) is True
    audio = File(audio_path)
    from core.tag_writer import read_number_pair
    assert read_number_pair(audio, 'track') == (7, 12)
    assert read_number_pair(audio, 'disc') == (2, 3)
    assert audio['title'] == ['Keep title']
    assert audio['album'] == ['Original']


def test_consistency_apply_does_not_claim_aborted_save(audio_path, monkeypatch):
    monkeypatch.setattr('core.metadata.common.save_audio_file', lambda *_a: False)
    worker = object.__new__(RepairWorker)
    worker.db = SimpleNamespace(manual_path_keys=lambda: set())
    worker._config_manager = None
    worker.transfer_folder = None
    result = worker._fix_album_tag_inconsistency('album', None, None, {
        'inconsistencies': [{'field': 'album', 'canonical': 'Reviewed'}],
        'tracks': [{'file_path': audio_path}],
    })
    assert result['success'] is False
    assert File(audio_path)['album'] == ['Original']


def test_number_repairs_propagate_aborted_save(audio_path, monkeypatch):
    monkeypatch.setattr('core.metadata.common.save_audio_file', lambda *_a: False)
    assert _fix_track_number_tag(audio_path, 7, 12) is False
    assert _fix_disc_number_tag(audio_path, 2, 3) is False
    assert File(audio_path)['tracknumber'] == ['2']


def test_selected_field_writer_only_changes_requested_field(audio_path):
    from core import tag_writer
    assert hasattr(tag_writer, 'write_tag_fields'), 'shared partial writer is missing'
    result = tag_writer.write_tag_fields(audio_path, {'album': 'Reviewed'})
    assert result['success'] is True
    assert File(audio_path)['album'] == ['Reviewed']
    assert File(audio_path)['title'] == ['Keep title']
