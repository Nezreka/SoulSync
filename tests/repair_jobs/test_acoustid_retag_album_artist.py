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
    with db._get_connection() as conn:
        conn.execute("INSERT OR REPLACE INTO artists (id, name, server_source) "
                     "VALUES ('va','Various Artists','plex')")
        conn.execute("INSERT OR REPLACE INTO albums (id, title, artist_id) VALUES (10,'Trail Songs','va')")
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, duration, "
                     "file_path, server_source) VALUES ('t1',10,'va','Wrong Title',4,100,?, 'plex')",
                     (str(f),))
        conn.commit()

    worker = RepairWorker(db)
    res = worker._fix_acoustid_mismatch(
        'track', 't1', str(f),
        {'_fix_action': 'retag', 'acoustid_title': 'Such Great Heights', 'acoustid_artist': 'Iron & Wine'})

    assert res['success'] is True
    tags = FLAC(str(f))
    assert tags['title'] == ['Such Great Heights']
    assert tags['artist'] == ['Iron & Wine']
    assert tags['albumartist'] == ['Various Artists']
    assert tags['album'] == ['Trail Songs']
