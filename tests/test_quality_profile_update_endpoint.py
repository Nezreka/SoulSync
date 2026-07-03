"""POST /api/quality-profile/custom/<id>/update — editing the ACTIVE DEFAULT
profile must also push the new values into config.json.

Regression: the endpoint only wrote the `quality_profiles` row
(`MusicDatabase.update_quality_profile`), never config. Every profile-owned
setting the rest of the app reads directly from config (AcoustID, lossy-copy,
deep-verify, replace-lower-quality) then went stale — and the next unrelated
Settings save (which mirrors config -> the default row via
`sync_default_quality_profile_from_config`) would silently revert this edit
back to the old config values.
"""

from __future__ import annotations

import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-qp-update-')
os.environ['DATABASE_PATH'] = os.path.join(_TMP, 'a.db')
os.environ['SOULSYNC_TEST_DB_READY'] = '1'

web_server = pytest.importorskip('web_server')


@pytest.fixture
def client():
    return web_server.app.test_client()


def _default_profile_id(client):
    r = client.get('/api/quality-profile/custom')
    body = r.get_json()
    assert body['success'] is True
    default = next(p for p in body['profiles'] if p['is_default'])
    return default['id']


def test_updating_the_default_profile_pushes_config(client):
    from config.settings import config_manager

    default_id = _default_profile_id(client)
    assert config_manager.get('acoustid.require_verified') is not True

    r = client.post(
        f'/api/quality-profile/custom/{default_id}/update',
        json={
            'ranked_targets': [{'label': 'FLAC', 'format': 'flac'}],
            'acoustid_required': True,
            'deep_audio_verify': True,
            'replace_lower_quality': True,
        },
    )
    body = r.get_json()
    assert body['success'] is True

    # The row itself was updated...
    updated = next(p for p in body['profiles'] if p['id'] == default_id)
    assert updated['acoustid_required'] is True

    # ...and so was config.json, for every profile-owned key the rest of the
    # app reads directly (not just via the profile row).
    assert config_manager.get('acoustid.require_verified') is True
    assert config_manager.get('post_processing.audio_completeness_check') is True
    assert config_manager.get('import.replace_lower_quality') is True


def test_updating_a_non_default_profile_does_not_touch_config(client):
    """Editing a profile that ISN'T the active default must not touch
    config.json at all — only the active default's settings are mirrored
    into the live global config. (Toggles `deep_audio_verify` — a key the
    other test in this file leaves untouched — so this assertion is valid
    regardless of test execution order within the shared app/config.)"""
    from config.settings import config_manager

    r = client.post(
        '/api/quality-profile/custom',
        json={'name': 'Not Default', 'ranked_targets': [{'label': 'FLAC', 'format': 'flac'}]},
    )
    new_id = r.get_json()['id']
    before = config_manager.get('post_processing.audio_completeness_check')

    r = client.post(
        f'/api/quality-profile/custom/{new_id}/update',
        json={'ranked_targets': [{'label': 'FLAC', 'format': 'flac'}], 'deep_audio_verify': not before},
    )
    assert r.get_json()['success'] is True

    assert config_manager.get('post_processing.audio_completeness_check') == before
