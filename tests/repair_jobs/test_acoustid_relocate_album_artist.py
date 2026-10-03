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
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO artists (id, name, server_source) "
                     "VALUES ('va','Various Artists','plex')")
        conn.execute("INSERT OR REPLACE INTO albums (id, title, artist_id) VALUES (10,'Trail Songs','va')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, "
                     "file_path, server_source) VALUES ('t1',10,'va','Wrong Title',4,100,?, 'plex')",
                     (str(f),))
        conn.commit()

    captured = {}
    def fake_relocate(resolved, staging, tag_updates, **kwargs):
        captured.update(tag_updates or {})
        return '/fake/staging/04 - wrong.flac'

    worker = RepairWorker(db)
    with patch('core.repair_jobs.relocate.relocate_mismatch_to_staging', fake_relocate):
        # bypass path resolution by patching _resolve_file_path and _resolve_path
        with patch.object(worker, '_resolve_path', return_value=str(tmp_path / 'staging')):
            from core import repair_worker as rw_mod
            orig_resolve = rw_mod._resolve_file_path
            with patch.object(rw_mod, '_resolve_file_path', return_value=str(f)):
                res = worker._fix_acoustid_mismatch(
                    'track', 't1', str(f),
                    {'_fix_action': 'relocate', 'acoustid_title': 'Such Great Heights',
                     'acoustid_artist': 'Iron & Wine'})

    assert res['success'] is True
    # track_artist, not artist_name — the writer puts artist_name into album artist
    assert captured.get('track_artist') == 'Iron & Wine'
    assert 'artist_name' not in captured
