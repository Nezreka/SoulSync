"""A failed sync drops the smart-skip fingerprint of the last good run.

A scheduled run skips a playlist whose track hash is unchanged and was fully
matched last time. When a later write fails, keeping that fingerprint made
every following run skip the write too, long after the server recovered.
"""

from __future__ import annotations

import json
import os
import tempfile

import pytest

_TMP = tempfile.mkdtemp(prefix='soulsync-testdb-fingerprint-')
os.environ.setdefault('DATABASE_PATH', os.path.join(_TMP, 's.db'))
os.environ.setdefault('SOULSYNC_TEST_DB_READY', '1')

web_server = pytest.importorskip('web_server')


class _Prefs:
    def __init__(self, statuses):
        self.raw = json.dumps(statuses)

    def get_preference(self, key):
        return self.raw if key == 'sync_statuses' else None

    def set_preference(self, key, value):
        assert key == 'sync_statuses'
        self.raw = value


def test_failed_sync_clears_only_the_fingerprint(monkeypatch):
    prefs = _Prefs({
        'auto_mirror_1': {
            'name': 'Liked Songs', 'snapshot_id': 's1', 'last_synced': '2026-10-14T10:00:00',
            'tracks_hash': 'h', 'mirror_tracks_hash': 'm', 'matched_tracks': 3230,
            'total_tracks': 3230, 'sync_mode': 'replace',
        },
        'other': {'name': 'Other', 'tracks_hash': 'keep', 'matched_tracks': 5},
    })
    monkeypatch.setattr(web_server, 'get_database', lambda: prefs)

    web_server._invalidate_sync_fingerprint('auto_mirror_1')

    statuses = json.loads(prefs.raw)
    assert statuses['auto_mirror_1'] == {
        'name': 'Liked Songs', 'snapshot_id': 's1', 'last_synced': '2026-10-14T10:00:00',
        'total_tracks': 3230, 'sync_mode': 'replace',
    }
    assert statuses['other'] == {'name': 'Other', 'tracks_hash': 'keep', 'matched_tracks': 5}
