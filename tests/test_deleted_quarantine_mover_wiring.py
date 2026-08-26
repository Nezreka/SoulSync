"""the movers actually feed the manifest - end-to-end through the real
duplicate cleaner worker against a tmp transfer folder."""

import os

import pytest

import core.library.duplicate_cleaner as dc
from core.library.deleted_quarantine import list_entries


class _FakeConfig:
    def __init__(self, transfer):
        self.transfer = transfer

    def get(self, key, default=None):
        if key == 'soulseek.transfer_path':
            return self.transfer
        return default


@pytest.fixture
def cleaner(tmp_path, monkeypatch):
    state = {"status": "idle"}
    import threading
    dc.init(state, threading.Lock(), lambda p: p, None)
    monkeypatch.setattr(dc, 'config_manager', _FakeConfig(str(tmp_path)))
    monkeypatch.setattr(dc, 'add_activity_item', lambda *a, **k: None)
    return state, str(tmp_path)


def test_the_duplicate_cleaner_records_what_it_quarantines(cleaner):
    state, transfer = cleaner
    album = os.path.join(transfer, 'Artist', 'Album')
    os.makedirs(album)
    # same stem, two formats -> the mp3 loses to the flac and gets quarantined
    with open(os.path.join(album, 'song.flac'), 'wb') as f:
        f.write(b'flac' * 100)
    with open(os.path.join(album, 'song.mp3'), 'wb') as f:
        f.write(b'mp3')

    dc._run_duplicate_cleaner()

    assert state['status'] == 'finished'
    assert state['deleted'] == 1
    # the flac survived in place
    assert os.path.isfile(os.path.join(album, 'song.flac'))
    # the mp3 is in the bin WITH provenance - restorable and ageable
    result = list_entries(transfer)
    assert result['count'] == 1
    entry = result['entries'][0]
    assert entry['id'] == 'deleted:Artist/Album/song.mp3'
    assert entry['source'] == 'duplicate-cleaner'
    assert entry['deleted_at'] is not None
    assert entry['original_path'] == os.path.join(transfer, 'Artist', 'Album', 'song.mp3')
