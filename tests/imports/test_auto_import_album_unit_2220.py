"""#2220: an album folder imports as one unit.

Ericsson imports his collection through the Import folder. some albums moved
only partly to the music folder; the rest stayed in Import, and once imported
they sat on a different release of the same album in the library.

three causes, each pinned here:
- the release the search picked lacked some of the folder's songs, and no
  other edition was tried, so those files stayed behind;
- the matcher dropped its quality-duplicate list, so a lower-quality copy of
  a track counted as an unclaimed song and a healthy album came out partial;
- the leftovers were identified again on their own. a few files score the
  release search differently (the track count is part of the score), so they
  went to another edition: a second copy of the album in the library.
"""

from __future__ import annotations

import json
import os
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core import auto_import_worker as aiw
from core.auto_import_worker import AutoImportWorker, FolderCandidate
from core.imports.album_unit import (
    edition_alternates,
    edition_key,
    sibling_release,
    tags_agree_with_album,
)
from core.imports.inbox import _parse_match, derive_status

ALBUM = 'T.R.U. (The Real University)'
ARTIST = '2 Chainz'


class _DB:
    def __init__(self, path):
        self.path = path
        with self._get_connection() as conn:
            conn.execute("""
                CREATE TABLE auto_import_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    folder_name TEXT NOT NULL, folder_path TEXT NOT NULL,
                    folder_hash TEXT, status TEXT NOT NULL,
                    confidence REAL DEFAULT 0, album_id TEXT, album_name TEXT,
                    artist_name TEXT, image_url TEXT, total_files INTEGER DEFAULT 0,
                    matched_files INTEGER DEFAULT 0, match_data TEXT,
                    identification_method TEXT, error_message TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                )
            """)

    def _get_connection(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn


class _Config:
    def get(self, key, default=None):
        if key == 'import.folder_artist_override':
            return False
        return default


def _track(n):
    return {'id': f't{n}', 'name': f'Song {n}', 'track_number': n, 'disc_number': 1,
            'duration_ms': 200_000 + n * 7_000, 'artists': [{'name': ARTIST}]}


def _tags(n, album=ALBUM):
    return {'title': f'Song {n}', 'artist': ARTIST, 'albumartist': ARTIST, 'album': album,
            'track_number': n, 'disc_number': 1, 'duration_ms': 200_000 + n * 7_000,
            'year': '2014', 'genres': []}


RELEASES = {
    # the standard upload: 15 of the folder's 17 songs
    'std': [_track(n) for n in range(1, 16)],
    # the full mixtape, same name
    'dlx': [_track(n) for n in range(1, 18)],
    # what a search on a couple of leftovers alone turns up
    'other': [_track(n) for n in range(1, 18)],
}


@pytest.fixture
def env(tmp_path, monkeypatch):
    folder = tmp_path / '2 Chainz - TRU'
    folder.mkdir()
    files = []
    tags = {}
    for n in range(1, 18):
        path = folder / f'{n:02d} - Song {n}.flac'
        path.write_bytes(b'audio')
        files.append(str(path))
        tags[str(path)] = _tags(n)
    monkeypatch.setattr(aiw, '_read_file_tags', lambda f: tags.get(f, {}))

    client = MagicMock(spec=['get_album'])
    client.get_album.side_effect = lambda album_id: {
        'id': album_id, 'name': ALBUM, 'tracks': {'items': RELEASES[album_id]},
        'total_tracks': len(RELEASES[album_id]),
    }
    import core.metadata_service as ms
    monkeypatch.setattr(ms, 'get_client_for_source', lambda _source: client)

    imported = []

    def callback(_key, context, path):
        imported.append((os.path.basename(path), context['spotify_album']['id']))
        context['_final_processed_path'] = path
        context['_pipeline_import_succeeded'] = True

    worker = AutoImportWorker(_DB(tmp_path / 'history.db'), staging_path=str(tmp_path),
                              process_callback=callback, config_manager=_Config())
    monkeypatch.setattr(worker, '_resolve_rematch_hint', lambda _c: (None, None))
    monkeypatch.setattr(worker, '_gate_quality_profile', lambda _p: {})
    import core.imports.library_check as lc
    monkeypatch.setattr(lc, 'check_track_library_status',
                        lambda *a, **k: {'decision': 'import', 'reason': 'new'})
    return SimpleNamespace(worker=worker, folder=str(folder), files=files, tags=tags,
                           imported=imported, client=client)


def _identification(album_id, editions=()):
    return {
        'source': 'deezer', 'album_id': album_id, 'album_name': ALBUM,
        'artist_name': ARTIST, 'artist_id': 'a1', 'image_url': '', 'method': 'tags',
        'identification_confidence': 0.95,
        'edition_candidates': [{'album_id': e, 'album_name': ALBUM} for e in editions],
    }


def _history(worker):
    with worker.database._get_connection() as conn:
        return [dict(r) for r in conn.execute('SELECT * FROM auto_import_history ORDER BY id')]


def _seed_run(worker, folder, *, album_id, status, statuses, unmatched=()):
    """a finished run of the folder: ``statuses`` maps track number to what
    the import did with it."""
    data = {
        'source': 'deezer', 'total_tracks': len(RELEASES[album_id]),
        'matches': [{'track_name': f'Song {n}', 'track_number': n,
                     'file': f'{n:02d} - Song {n}.flac', 'confidence': 1.0,
                     'import_status': st} for n, st in statuses.items()],
        'unmatched_files': [f'{n:02d} - Song {n}.flac' for n in unmatched],
    }
    with worker.database._get_connection() as conn:
        return conn.execute(
            "INSERT INTO auto_import_history (folder_name, folder_path, folder_hash, status,"
            " album_id, album_name, artist_name, match_data, identification_method)"
            " VALUES (?, ?, 'hash-before', ?, ?, ?, ?, ?, 'tags')",
            (os.path.basename(folder), folder, status, album_id, ALBUM, ARTIST,
             json.dumps(data))).lastrowid


def _leftovers(env, numbers):
    files = [f for f in env.files if int(os.path.basename(f)[:2]) in numbers]
    for f in env.files:
        if f not in files:
            os.remove(f)
    return FolderCandidate(path=env.folder, name=os.path.basename(env.folder),
                           audio_files=files, folder_hash=aiw._compute_folder_hash(files))


# ── the first pass: the folder goes to the edition that holds all of it ──


def test_folder_goes_to_the_edition_that_covers_it_not_the_one_missing_tracks(env, monkeypatch):
    """the search picked the 15-track upload for a 17-song folder; two songs
    stayed in Import. the same album's other edition holds all 17."""
    monkeypatch.setattr(env.worker, '_identify_folder', lambda _c: _identification('std', ['dlx']))
    candidate = FolderCandidate(path=env.folder, name='2 Chainz - TRU', audio_files=env.files,
                                folder_hash=aiw._compute_folder_hash(env.files))

    env.worker._process_one_candidate(candidate)

    assert len(env.imported) == 17
    assert {album for _f, album in env.imported} == {'dlx'}
    row = _history(env.worker)[-1]
    assert row['status'] == 'completed'
    assert row['album_id'] == 'dlx'


def test_a_tie_keeps_the_picked_release(env, monkeypatch):
    """another edition that takes no more of the folder never replaces the pick."""
    files = env.files[:15]
    for f in env.files[15:]:
        os.remove(f)
    monkeypatch.setattr(env.worker, '_identify_folder', lambda _c: _identification('std', ['dlx']))
    candidate = FolderCandidate(path=env.folder, name='x', audio_files=files,
                                folder_hash=aiw._compute_folder_hash(files))

    env.worker._process_one_candidate(candidate)

    assert {album for _f, album in env.imported} == {'std'}
    # every song fit the pick: no other edition was even fetched
    assert [c.args[0] for c in env.client.get_album.call_args_list] == ['std']


def test_quality_duplicates_do_not_make_a_healthy_album_partial(env, monkeypatch):
    """an mp3 copy of a track next to its flac: the matcher kept the flac and
    knew the mp3 was a duplicate, but dropped that list, so the album came
    out 'partial' with the mp3 listed as an unmatched song."""
    files = env.files[:15]
    for f in env.files[15:]:
        os.remove(f)
    dupe = os.path.join(env.folder, '01 - Song 1.mp3')
    with open(dupe, 'wb') as handle:
        handle.write(b'lossy')
    env.tags[dupe] = _tags(1)
    files = files + [dupe]
    monkeypatch.setattr(env.worker, '_identify_folder', lambda _c: _identification('std'))
    candidate = FolderCandidate(path=env.folder, name='x', audio_files=files,
                                folder_hash=aiw._compute_folder_hash(files))

    env.worker._process_one_candidate(candidate)

    row = _history(env.worker)[-1]
    assert row['status'] == 'completed', row['error_message']
    left = json.loads(row['match_data'])['left_behind']
    assert left == [{'file': '01 - Song 1.mp3',
                     'why': 'a lower quality copy of a track that is imported'}]


def test_files_no_edition_holds_are_left_with_the_reason(env, monkeypatch):
    """nothing is left in Import without saying why."""
    monkeypatch.setattr(env.worker, '_identify_folder', lambda _c: _identification('std'))
    candidate = FolderCandidate(path=env.folder, name='x', audio_files=env.files,
                                folder_hash=aiw._compute_folder_hash(env.files))

    env.worker._process_one_candidate(candidate)

    row = _history(env.worker)[-1]
    assert row['status'] == 'partial'
    assert f'Unmatched file: 16 - Song 16.flac (not on {ALBUM} (15 tracks))' in row['error_message']
    parsed = _parse_match(row['match_data'])
    assert parsed['source'] == 'deezer'
    assert [e['file'] for e in parsed['left_behind']] == ['16 - Song 16.flac', '17 - Song 17.flac']
    assert all(e['why'] == f'not on {ALBUM} (15 tracks)' for e in parsed['left_behind'])


# ── the leftovers: they go where the rest of the folder went ──


def test_failed_leftovers_rerun_on_the_release_their_siblings_went_to(env, monkeypatch):
    """two tracks failed in the pipeline and stayed in Import. the next scan
    saw a folder it had never hashed and identified the two on their own: a
    search on two files found another upload of the album, and the library
    got a second T.R.U."""
    _seed_run(env.worker, env.folder, album_id='std', status='partial',
              statuses={**{n: 'completed' for n in range(1, 14)}, 14: 'failed', 15: 'failed'},
              unmatched=(16, 17))
    candidate = _leftovers(env, {14, 15})
    monkeypatch.setattr(env.worker, '_identify_folder', lambda _c: _identification('other'))

    assert env.worker._is_already_processed(candidate) is False
    env.worker._process_one_candidate(candidate)

    assert env.imported == [('14 - Song 14.flac', 'std'), ('15 - Song 15.flac', 'std')]
    row = _history(env.worker)[-1]
    assert row['album_id'] == 'std'
    assert row['identification_method'] == 'sibling_release'


def test_retrying_a_partial_import_keeps_the_leftovers_on_the_same_release(env, monkeypatch):
    """Retry used to delete the partial row, which was the only record of the
    release the rest of the folder went to."""
    row_id = _seed_run(env.worker, env.folder, album_id='std', status='partial',
                       statuses={n: 'completed' for n in range(1, 14)},
                       unmatched=(14, 15))
    candidate = _leftovers(env, {14, 15})
    assert env.worker._is_already_processed(candidate) is True

    assert env.worker.retry_item(row_id)['success'] is True
    assert env.worker.retry_item(row_id)['success'] is False
    assert env.worker._is_already_processed(candidate) is False
    assert derive_status('retried', None, True) == 'queued'

    monkeypatch.setattr(env.worker, '_identify_folder', lambda _c: _identification('other'))
    env.worker._process_one_candidate(candidate)

    assert {album for _f, album in env.imported} == {'std'}


def test_leftovers_not_on_the_siblings_release_wait_for_the_user_with_the_reason(env, monkeypatch):
    """two songs the folder's release doesn't have are not sent off to
    another edition on their own: the row names the release the rest went to
    (Fix match opens on it) and says why they're still here."""
    row_id = _seed_run(env.worker, env.folder, album_id='std', status='partial',
                       statuses={n: 'completed' for n in range(1, 16)}, unmatched=(16, 17))
    candidate = _leftovers(env, {16, 17})
    env.worker.retry_item(row_id)
    monkeypatch.setattr(env.worker, '_identify_folder', lambda _c: _identification('other'))

    env.worker._process_one_candidate(candidate)

    assert env.imported == []
    row = _history(env.worker)[-1]
    assert row['status'] == 'needs_identification'
    assert row['album_id'] == 'std'
    assert row['error_message'].startswith(f'Not on {ALBUM}, where the rest of this folder went')
    assert json.loads(row['match_data'])['source'] == 'deezer'


def test_leftovers_tagged_as_another_album_are_identified_on_their_own(env, monkeypatch):
    _seed_run(env.worker, env.folder, album_id='std', status='partial',
              statuses={**{n: 'completed' for n in range(1, 14)}, 14: 'failed', 15: 'failed'})
    candidate = _leftovers(env, {14, 15})
    for f in candidate.audio_files:
        env.tags[f] = {**env.tags[f], 'album': 'Based on a T.R.U. Story'}
    assert env.worker._sibling_identification(candidate) is None


def test_a_new_drop_in_the_same_folder_is_not_a_leftover(env):
    _seed_run(env.worker, env.folder, album_id='std', status='completed',
              statuses={n: 'completed' for n in range(1, 4)})
    candidate = _leftovers(env, {14, 15})
    assert env.worker._sibling_identification(candidate) is None


# ── the pure helpers ──


def test_edition_key_drops_the_edition_note():
    assert edition_key(ALBUM) == edition_key(f'{ALBUM} (Deluxe Edition)')
    assert edition_key('Based on a T.R.U. Story') != edition_key(ALBUM)


def test_edition_alternates_are_the_same_album_by_the_same_artist():
    def hit(i, name, artist=ARTIST):
        return SimpleNamespace(id=i, name=name, artists=[{'name': artist}], total_tracks=17,
                               image_url='', release_date='2014')
    best = hit('a', ALBUM)
    alts = edition_alternates(best, [best, hit('b', f'{ALBUM} [Deluxe]'),
                                     hit('c', 'Based on a T.R.U. Story'),
                                     hit('d', ALBUM, artist='Someone Else'), hit('b', ALBUM)])
    assert [a['album_id'] for a in alts] == ['b']


def test_sibling_release_needs_an_import_and_every_file_from_that_run():
    row = {'album_id': 'std', 'album_name': ALBUM, 'artist_name': ARTIST,
           'match_data': json.dumps({'source': 'deezer', 'matches': [
               {'file': '01.flac', 'import_status': 'completed'},
               {'file': '02.flac', 'import_status': 'failed'}],
               'unmatched_files': ['03.flac']})}
    assert sibling_release([row], ['/s/02.flac', '/s/03.flac'])['album_id'] == 'std'
    assert sibling_release([row], ['/s/02.flac', '/s/09.flac']) is None
    nothing_imported = {**row, 'match_data': json.dumps({'source': 'deezer', 'matches': [
        {'file': '02.flac', 'import_status': 'failed'}]})}
    assert sibling_release([nothing_imported], ['/s/02.flac']) is None
    assert sibling_release([{**row, 'album_id': None}], ['/s/02.flac']) is None


def test_tags_agree_with_album():
    assert tags_agree_with_album([ALBUM, None, ''], ALBUM)
    assert tags_agree_with_album([], ALBUM)
    assert not tags_agree_with_album(['Something Else', 'Something Else'], ALBUM)
