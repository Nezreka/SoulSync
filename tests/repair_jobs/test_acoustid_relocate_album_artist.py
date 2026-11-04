"""#1289: relocate branch of AcoustID mismatch must not rewrite album artist."""
from __future__ import annotations
import struct
from unittest.mock import patch


def _make_flac(path):
    from mutagen.flac import FLAC
    streaminfo = bytearray(34)
    streaminfo[0:2] = struct.pack('>H', 4096)
    streaminfo[2:4] = struct.pack('>H', 4096)
    streaminfo[10] = 0x0A
    streaminfo[12] = 0x70
    path.write_bytes(b'fLaC' + bytes([0x80, 0x00, 0x00, 0x22]) + bytes(streaminfo))
    audio = FLAC(str(path))
    audio['title'] = ['Wrong Title']
    audio['artist'] = ['Wrong Artist']
    audio['albumartist'] = ['Various Artists']
    audio['album'] = ['Trail Songs']
    audio.save()


def test_relocate_keeps_the_album_artist(tmp_path):
    from mutagen.flac import FLAC
    from database.music_database import MusicDatabase
    from core.repair_worker import RepairWorker

    db = MusicDatabase(str(tmp_path / 'm.db'))
    f = tmp_path / 'music' / '04 - wrong.flac'
    f.parent.mkdir(parents=True)
    _make_flac(f)
    from tests import lib2_seed
    with db._get_connection() as conn:
        track_id = lib2_seed.track(conn, 'Various Artists', 'Trail Songs', 'Wrong Title',
                                   path=str(f), track_number=4, duration=100)
        conn.commit()

    captured = {}
    def fake_relocate(resolved, staging, tag_updates, **kwargs):
        captured.update(tag_updates or {})
        return '/fake/staging/04 - wrong.flac'

    worker = RepairWorker(db)
    with patch('core.repair_jobs.relocate.relocate_mismatch_to_staging', fake_relocate):
        # bypass path resolution: the catalogue path is the file itself
        with patch.object(worker, '_resolve_path', return_value=str(tmp_path / 'staging')):
            with patch('core.library2.paths.resolve_lib2_path', return_value=str(f)):
                res = worker._fix_acoustid_mismatch(
                    'track', f'lib2:{track_id}', str(f),
                    {'_fix_action': 'relocate', 'acoustid_title': 'Such Great Heights',
                     'acoustid_artist': 'Iron & Wine'})

    assert res['success'] is True
    # track_artist, not artist_name — the writer puts artist_name into album artist
    assert captured.get('track_artist') == 'Iron & Wine'
    assert 'artist_name' not in captured
