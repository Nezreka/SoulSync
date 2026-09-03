"""#1208: deleting a whole group of quarantined candidates in one request.

One rejected track can pile up dozens of failed candidates, all folded behind a
single row in the review UI. The row's own Delete only removes the candidate it
sits on, so clearing the pile meant one confirm per file. DELETE with
``?siblings=1`` takes the group - resolved from the same group key the UI folds
rows by, and the same one approve already uses for its sibling cleanup.

Blueprint on a bare Flask app with a fake config; no web_server, no real db.
"""

import json
import os

import pytest
from flask import Flask

import api.quarantine as q_api


class _FakeConfig:
    def __init__(self, download_path):
        self.values = {'soulseek.download_path': download_path}

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def client(tmp_path):
    q_api.configure(
        config_manager_=_FakeConfig(str(tmp_path)),
        docker_resolve_path_=lambda p: p,
        serve_audio_file_with_range=None,
        audio_mime_types={},
        post_process_matched_download=None,
        post_process_matched_download_with_verification=None,
        download_orchestrator_getter=lambda: None,
        matching_engine_getter=lambda: None,
        # This branch's quarantine module also reaches the catalogue and the
        # scan/automation surfaces; the group delete needs none of them, so
        # they are stubbed rather than built.
        get_database_=lambda: None,
        automation_engine_getter=lambda: None,
        web_scan_manager_getter=lambda: None,
    )
    app = Flask(__name__)
    app.register_blueprint(q_api.bp)
    app.config['TESTING'] = True
    c = app.test_client()
    c.quarantine_dir = os.path.join(str(tmp_path), 'ss_quarantine')
    os.makedirs(c.quarantine_dir, exist_ok=True)
    return c


def _entry(quarantine_dir, stem, artist, track):
    """One quarantined file + sidecar, the shape move_to_quarantine writes."""
    path = os.path.join(quarantine_dir, f"{stem}.flac.quarantined")
    with open(path, 'wb') as f:
        f.write(b'audio')
    with open(os.path.join(quarantine_dir, f"{stem}.json"), 'w', encoding='utf-8') as f:
        json.dump({
            'original_filename': f'{stem}.flac',
            'expected_artist': artist,
            'expected_track': track,
            'quarantine_reason': 'duration mismatch',
            'trigger': 'integrity',
            'timestamp': '2026-08-30 10:00:00',
        }, f)
    return stem


def _ids(client):
    from core.imports.quarantine import list_quarantine_entries
    return {e['id'] for e in list_quarantine_entries(client.quarantine_dir)}


def test_siblings_deletes_the_whole_group(client):
    for i in range(4):
        _entry(client.quarantine_dir, f'2026_c{i}', 'The Troggs', 'Wild Thing')
    keep = _entry(client.quarantine_dir, '2026_other', 'Sly', 'Everyday People')

    r = client.delete('/api/quarantine/2026_c0?siblings=1')
    assert r.status_code == 200
    body = r.get_json()
    assert body['success'] and body['deleted'] == 4
    # The group is gone, the unrelated track is untouched.
    assert _ids(client) == {keep}
    assert not os.path.exists(os.path.join(client.quarantine_dir, '2026_c3.json'))


def test_without_the_flag_only_one_goes(client):
    """The per-row Delete must keep its old scope - it is a different button."""
    for i in range(3):
        _entry(client.quarantine_dir, f'2026_c{i}', 'The Troggs', 'Wild Thing')

    body = client.delete('/api/quarantine/2026_c0').get_json()
    assert body['success'] and body['deleted'] == 1
    assert _ids(client) == {'2026_c1', '2026_c2'}


def test_a_lone_entry_reports_one(client):
    _entry(client.quarantine_dir, '2026_solo', 'Sly', 'Everyday People')
    body = client.delete('/api/quarantine/2026_solo?siblings=1').get_json()
    assert body['success'] and body['deleted'] == 1
    assert _ids(client) == set()


def test_ungroupable_entries_do_not_take_each_other_down(client):
    """No expected artist/track means no group key, so nothing is a sibling -
    a blank key must not sweep up every other blank-key entry."""
    _entry(client.quarantine_dir, '2026_a', '', '')
    _entry(client.quarantine_dir, '2026_b', '', '')

    body = client.delete('/api/quarantine/2026_a?siblings=1').get_json()
    assert body['deleted'] == 1
    assert _ids(client) == {'2026_b'}


def test_missing_entry_is_a_404(client):
    r = client.delete('/api/quarantine/nope?siblings=1')
    assert r.status_code == 404
    assert r.get_json()['success'] is False
