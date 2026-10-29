"""Real two-track scan fixtures shared across behavior suites."""
from __future__ import annotations

import json

from core.repair_jobs.base import JobContext
from core.repair_worker import RepairWorker
from database.music_database import MusicDatabase
from tests.reconciliation.support.audio import _Acoustid, _minimal_flac


def _album(db, path):
    with db._get_connection() as conn:
        conn.execute("INSERT INTO artists (id, name, server_source) VALUES (1, 'Lamb of God', 'test')")
        conn.execute("""INSERT INTO albums
            (id, title, artist_id, server_source, canonical_source, canonical_album_id)
            VALUES (10, 'Ashes of the Wake', 1, 'test', 'spotify', 'ashes-release')""")
        conn.execute("""INSERT INTO tracks
            (id, album_id, artist_id, title, track_number, file_path)
            VALUES (1, 10, 1, 'Laid to Rest', 1, ?)""", (str(path),))
        conn.commit()


def _metadata_recommendation_album(tmp_path, monkeypatch, *, mixed=False, recordings=None):
    import core.repair_jobs.library_reconciliation as module
    root = tmp_path / 'music'
    folder = root / 'Artist' / 'Album'
    folder.mkdir(parents=True)
    db = MusicDatabase(str(tmp_path / 'music.db'))
    paths = [folder / '01 - Laid to Rest.flac', folder / '02 - Hourglass.flac']
    _album(db, paths[0])
    titles = ['Laid to Rest', 'Hourglass']
    mbids = ['80951433-fbbf-45e8-b906-e820652b9ca5', 'e2bd3843-a9f1-447f-b733-f5f3d5dd3d33']
    with db._get_connection() as conn:
        conn.execute("UPDATE albums SET canonical_source='itunes', title='Ashes of the Wake (Deluxe)'")
        conn.execute("UPDATE artists SET aliases=?", (json.dumps(['ラム・オブ・ゴッド']),))
        conn.execute("INSERT INTO tracks (id, album_id, artist_id, title, track_number, file_path) VALUES (2,10,1,'Hourglass',2,?)", (str(paths[1]),))
        conn.commit()
    for index, path in enumerate(paths):
        _minimal_flac(path, {'title': titles[index], 'artist': 'Lamb of God',
            'albumartist': 'Lamb of God', 'tracknumber': str(index + 1), 'discnumber': '1',
            'album': 'Ashes of the Wake' if mixed and index == 0 else 'Ashes of the Wake (Deluxe)',
            'musicbrainz_trackid': 'feb76071-8125-4e98-8841-7b45a8d37feb',
            'musicbrainz_albumid': 'first-release' if mixed and index == 0 else 'second-release'})
    client = _Acoustid({path.name: (recordings if recordings is not None else
        {'mbid': mbids[index], 'title': titles[index], 'artist': 'Lamb of God', 'score': .99})
        for index, path in enumerate(paths)})
    worker = RepairWorker(database=db, transfer_folder=str(root))
    monkeypatch.setattr(RepairWorker, 'acoustid_client', property(lambda _self: client))
    monkeypatch.setattr(module, 'all_library_roots', lambda _context: [str(root)])
    read_tags = module._read_file_tags
    monkeypatch.setattr(module, '_read_file_tags', lambda path: {**read_tags(path), 'duration_ms': 200000})
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *_args, **_kwargs: [
        {'id': str(index + 1), 'name': title, 'artists': [{'name': 'Lamb of God'}],
         'track_number': index + 1, 'disc_number': 1, 'duration_ms': 200000}
        for index, title in enumerate(titles)])
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *_args, **_kwargs: {
        'name': 'Ashes of the Wake (Deluxe)', 'artists': [{'name': 'Lamb of God'}]})
    context = JobContext(db=db, transfer_folder=str(root), config_manager=None,
                         acoustid_client=client, create_finding=worker._create_finding)
    return db, worker, context, paths, mbids
