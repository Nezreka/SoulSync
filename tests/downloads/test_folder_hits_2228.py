"""#2228: soulseek hits in a folder with 2+ matching files are still track
candidates.

the reporter's shape: a single shared next to its instrumental. soulseek
search groups any (user, folder) with 2+ hits into an AlbumResult and takes
those files out of the track list, and the per-track paths only read tracks,
so the single was never tried and the download fell back to youtube.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import Optional

import pytest

from core.download_engine.engine import DownloadEngine
from core.download_plugins.types import AlbumResult, TrackResult, track_candidates
from core.downloads import task_worker as tw
from core.downloads import validation
from core.downloads.validation import evaluate_candidates
from core.matching_engine import MusicMatchingEngine
from core.runtime_state import download_batches, download_tasks
from core.soulseek_client import SoulseekClient

FOLDER = '@@msr\\Music\\MSR\\Mystic Light Quest\\'
VOCAL = FOLDER + '01 Mystic Light Quest.flac'
INSTR = FOLDER + '02 Mystic Light Quest (Instrumental).flac'


def _file(path):
    return {'filename': path, 'size': 30_000_000, 'length': 200,
            'sampleRate': 48_000, 'bitDepth': 24}


def _responses():
    return [{'username': 'fastpeer', 'hasFreeUploadSlot': True, 'uploadSpeed': 6_000_000,
             'queueLength': 0, 'files': [_file(VOCAL), _file(INSTR)]}]


def _client():
    return SoulseekClient.__new__(SoulseekClient)


@dataclass
class _Track:
    name: str
    artists: tuple
    duration_ms: int = 200_000
    album: Optional[str] = None


class _Slsk:
    def filter_results_by_quality_preference(self, rows, profile_id=None):
        return list(rows)


class _Orch:
    def client(self, name):
        return _Slsk() if name == 'soulseek' else None


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setattr(validation, 'matching_engine', MusicMatchingEngine())
    monkeypatch.setattr(validation, 'download_orchestrator', _Orch())
    monkeypatch.setattr(validation, '_artist_aliases', lambda name: [])
    real_get = validation.config_manager.get
    monkeypatch.setattr(validation.config_manager, 'get',
                        lambda key, default=None: 0 if key == 'soulseek.max_peer_queue'
                        else real_get(key, default))


def test_reporter_shape_groups_both_files_out_of_the_track_list():
    tracks, albums = _client()._process_search_responses(_responses())
    assert tracks == []  # the bug: no loose track left to try
    assert len(albums) == 1 and albums[0].grouped_hits is True
    assert {t.filename for t in albums[0].tracks} == {VOCAL, INSTR}


def test_folder_files_become_track_candidates():
    tracks, albums = _client()._process_search_responses(_responses())
    cands = track_candidates(tracks, albums)
    assert {(c.username, c.filename) for c in cands} == {('fastpeer', VOCAL), ('fastpeer', INSTR)}
    # each candidate is the single file, so a pick downloads that file only
    assert all(isinstance(c, TrackResult) for c in cands)


def test_vocal_wins_and_instrumental_is_refused(env):
    tracks, albums = _client()._process_search_responses(_responses())
    want = _Track('Mystic Light Quest', ('MSR',), album='Mystic Light Quest')
    pairs = evaluate_candidates(track_candidates(tracks, albums), want, 'MSR Mystic Light Quest')
    accepted = [r.filename for r, d in pairs if d.accepted]
    assert accepted == [VOCAL]


def test_release_level_albums_are_not_flattened():
    loose = TrackResult(username='torrent', filename='tok||Some Release', size=1,
                        bitrate=None, duration=None, quality='flac',
                        free_upload_slots=1, upload_speed=0, queue_length=0)
    torrent_album = AlbumResult(username='torrent', album_path='torrent/x', album_title='Some Release',
                                artist=None, track_count=1, total_size=1,
                                tracks=[replace(loose, filename='album||Some Release')],
                                dominant_quality='flac')
    assert track_candidates([loose], [torrent_album]) == [loose]


def test_duplicates_kept_once_and_none_safe():
    tracks, albums = _client()._process_search_responses(_responses())
    dup = albums[0].tracks[0]
    out = track_candidates([dup], albums)
    assert [c.filename for c in out].count(dup.filename) == 1
    assert track_candidates(None, None) == []


class _Plugin:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    async def search(self, query, timeout=None, progress_callback=None):
        self.calls += 1
        return self.result


def test_hybrid_does_not_skip_soulseek_when_every_hit_is_grouped():
    tracks, albums = _client()._process_search_responses(_responses())
    slsk = _Plugin((tracks, albums))
    yt = _Plugin(([TrackResult(username='youtube', filename='id||x', size=0,
                               bitrate=None, duration=None, quality='opus',
                               free_upload_slots=1, upload_speed=0, queue_length=0)], []))
    engine = DownloadEngine()
    engine._plugins = {'soulseek': slsk, 'youtube': yt}
    got_tracks, got_albums = asyncio.run(engine.search_with_fallback('q', ['soulseek', 'youtube']))
    assert yt.calls == 0
    assert {c.filename for c in track_candidates(got_tracks, got_albums)} == {VOCAL, INSTR}


# --- the per-track worker sees the folder files -------------------------------

@pytest.fixture
def clean_state():
    download_tasks.clear()
    download_batches.clear()
    yield
    download_tasks.clear()
    download_batches.clear()


class _Orchestrator:
    mode = 'soulseek'

    def __init__(self, result):
        self.result = result

    def client(self, name):
        return None

    async def search(self, query, **kwargs):
        return self.result


class _Matching:
    def generate_download_queries(self, track):
        return ['MSR Mystic Light Quest']

    @staticmethod
    def _title_is_distinctive_enough_to_broadcast(title):
        return True


def test_task_worker_judges_folder_files(clean_state):
    tracks, albums = _client()._process_search_responses(_responses())
    seen = []

    def judge(results, track, query, profile_id=None):
        seen.extend(r.filename for r in results)
        return []

    download_tasks['t1'] = {'status': 'pending', 'track_info': {
        'id': 'x', 'name': 'Mystic Light Quest', 'artists': ['MSR'],
        'album': 'Mystic Light Quest', 'duration_ms': 200_000}}
    deps = tw.TaskWorkerDeps(
        download_orchestrator=_Orchestrator((tracks, albums)),
        matching_engine=_Matching(),
        run_async=lambda c: asyncio.run(c),
        try_source_reuse=lambda *a, **kw: False,
        store_batch_source=lambda *a, **kw: None,
        try_staging_match=lambda *a, **kw: False,
        get_valid_candidates=judge,
        attempt_download_with_candidates=lambda *a, **kw: False,
        on_download_completed=lambda *a, **kw: None,
        recover_worker_slot=lambda *a, **kw: None,
    )
    tw.download_track_worker('t1', 'b1', deps)
    assert VOCAL in seen and INSTR in seen
