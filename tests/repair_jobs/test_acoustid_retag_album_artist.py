"""#1289: an AcoustID retag rewrote album artist to the track artist.

the writer puts `artist_name` into album artist, and the retag passed the
AcoustID artist there. the retagged track stays on its album, so a compilation
track ("Various Artists") split off into its own album on the next scan.
"""

from __future__ import annotations

import struct


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


def test_retag_keeps_the_album_artist(tmp_path):
    from mutagen.flac import FLAC
    from database.music_database import MusicDatabase
    from core.repair_worker import RepairWorker

    db = MusicDatabase(str(tmp_path / 'm.db'))
    f = tmp_path / 'music' / 'Various Artists' / 'Trail Songs' / '04 - wrong.flac'
    f.parent.mkdir(parents=True)
    _make_flac(f)
    from tests.lib2_seed import track
    with db._get_connection() as conn:
        tid = track(conn, 'Various Artists', 'Trail Songs', 'Wrong Title',
                    path=str(f), track_number=4, duration=100)
        conn.commit()

    worker = RepairWorker(db)
    res = worker._fix_acoustid_mismatch(
        'track', f'lib2:{tid}', str(f),
        {'_fix_action': 'retag', 'acoustid_title': 'Such Great Heights', 'acoustid_artist': 'Iron & Wine'})

    assert res['success'] is True
    tags = FLAC(str(f))
    assert tags['title'] == ['Such Great Heights']
    assert tags['artist'] == ['Iron & Wine']
    assert tags['albumartist'] == ['Various Artists']
    assert tags['album'] == ['Trail Songs']
