"""the movers actually feed the manifest - end-to-end through the real
duplicate cleaner worker against a tmp transfer folder."""

import os
import sqlite3

import pytest

import core.library.duplicate_cleaner as dc
from core.library.deleted_quarantine import list_entries


class _FakeConfig:
    def __init__(self, transfer, *, lossy_enabled=False, lossy_codec='mp3'):
        self.transfer = transfer
        self.lossy_enabled = lossy_enabled
        self.lossy_codec = lossy_codec

    def get(self, key, default=None):
        if key == 'soulseek.transfer_path':
            return self.transfer
        if key == 'lossy_copy.enabled':
            return self.lossy_enabled
        if key == 'lossy_copy.codec':
            return self.lossy_codec
        return default


@pytest.fixture
def cleaner(tmp_path, monkeypatch):
    state = {"status": "idle"}
    import threading
    dc.init(state, threading.Lock(), lambda p: p, None)
    monkeypatch.setattr(dc, 'config_manager', _FakeConfig(str(tmp_path)))
    monkeypatch.setattr(dc, 'get_database', lambda: None)
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


def test_the_duplicate_cleaner_keeps_an_intentional_lossy_copy(cleaner, monkeypatch):
    state, transfer = cleaner
    monkeypatch.setattr(
        dc, 'config_manager',
        _FakeConfig(transfer, lossy_enabled=True, lossy_codec='mp3'),
    )
    album = os.path.join(transfer, 'Artist', 'Album')
    os.makedirs(album)
    flac = os.path.join(album, 'song.flac')
    mp3 = os.path.join(album, 'song.mp3')
    with open(flac, 'wb') as handle:
        handle.write(b'flac' * 100)
    with open(mp3, 'wb') as handle:
        handle.write(b'mp3')

    dc._run_duplicate_cleaner()

    assert os.path.isfile(flac)
    assert os.path.isfile(mp3)
    assert state['duplicates_found'] == 0
    assert state['deleted'] == 0
    assert list_entries(transfer)['count'] == 0


def test_a_quality_profile_also_protects_its_lossy_copy(cleaner, monkeypatch, tmp_path):
    state, transfer = cleaner
    monkeypatch.setattr(dc, 'config_manager', _FakeConfig(transfer))
    db_path = tmp_path / 'profiles.db'
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE quality_profiles ("
        "lossy_copy_enabled INTEGER, lossy_copy_codec TEXT)"
    )
    conn.execute("INSERT INTO quality_profiles VALUES (1, 'opus')")
    conn.commit()
    conn.close()

    class _Db:
        def _get_connection(self):
            return sqlite3.connect(db_path)

    monkeypatch.setattr(dc, 'get_database', _Db)
    album = os.path.join(transfer, 'Artist', 'Album')
    os.makedirs(album)
    flac = os.path.join(album, 'song.flac')
    opus = os.path.join(album, 'song.opus')
    with open(flac, 'wb') as handle:
        handle.write(b'flac' * 100)
    with open(opus, 'wb') as handle:
        handle.write(b'opus')

    dc._run_duplicate_cleaner()

    assert os.path.isfile(flac)
    assert os.path.isfile(opus)
    assert state['duplicates_found'] == 0
    assert state['deleted'] == 0


def test_alac_m4a_source_and_opus_companion_are_both_kept(cleaner, monkeypatch):
    state, transfer = cleaner
    monkeypatch.setattr(
        dc, 'config_manager',
        _FakeConfig(transfer, lossy_enabled=True, lossy_codec='opus'),
    )
    monkeypatch.setattr(
        'core.imports.file_ops.m4a_codec', lambda path: (
            'alac' if str(path).lower().endswith('.m4a') else None
        ),
    )
    album = os.path.join(transfer, 'Artist', 'Album')
    os.makedirs(album)
    alac = os.path.join(album, 'song.m4a')
    opus = os.path.join(album, 'song.opus')
    with open(alac, 'wb') as handle:
        handle.write(b'alac' * 100)
    with open(opus, 'wb') as handle:
        handle.write(b'opus')

    dc._run_duplicate_cleaner()

    # Opus sorts ahead of M4A in the cleaner. The codec probe must still
    # recognise the M4A as the lossless source and protect both directions.
    assert os.path.isfile(alac)
    assert os.path.isfile(opus)
    assert state['duplicates_found'] == 0
    assert state['deleted'] == 0


def test_the_duplicate_cleaner_still_removes_a_real_cross_format_duplicate(
        cleaner, monkeypatch):
    state, transfer = cleaner
    monkeypatch.setattr(
        dc, 'config_manager',
        _FakeConfig(transfer, lossy_enabled=True, lossy_codec='mp3'),
    )
    album = os.path.join(transfer, 'Artist', 'Album')
    os.makedirs(album)
    flac = os.path.join(album, 'song.flac')
    ogg = os.path.join(album, 'song.ogg')
    with open(flac, 'wb') as handle:
        handle.write(b'flac' * 100)
    with open(ogg, 'wb') as handle:
        handle.write(b'ogg')

    dc._run_duplicate_cleaner()

    assert os.path.isfile(flac)
    assert not os.path.isfile(ogg)
    assert state['duplicates_found'] == 1
    assert state['deleted'] == 1
