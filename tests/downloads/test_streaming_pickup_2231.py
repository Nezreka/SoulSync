"""#2231: a finished streaming download is found by the path the source
reported, and a file that isn't there yet is retried, never just dropped."""

import re
from pathlib import Path
from types import SimpleNamespace

from core.downloads import status as st
from core.downloads.file_finder import find_completed_audio_file
from core.downloads.streaming_pickup import (
    PickupMisses, check_streaming_pickup, resolve_streaming_file,
)

TITLE_KEY = 'eksy1uWA60g||魔法少女A - Magical GirlA'


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _download(file_path=None, filename=TITLE_KEY):
    return SimpleNamespace(id='dl-1', username='youtube', filename=filename,
                           file_path=file_path, state='Completed, Succeeded')


def test_reporter_case_name_match_alone_misses(tmp_path):
    saved = tmp_path / '魔法少女A.opus'
    saved.write_bytes(b'x')
    # the old pickup: the result title never matches yt-dlp's saved name
    found, _ = find_completed_audio_file(str(tmp_path), TITLE_KEY)
    assert found is None


def test_reporter_case_found_by_reported_path(tmp_path):
    saved = tmp_path / '魔法少女A.opus'
    saved.write_bytes(b'x')
    path = resolve_streaming_file(_download(str(saved)), str(tmp_path), find_completed_audio_file)
    assert path == str(saved)
    outcome, value = check_streaming_pickup(
        _download(str(saved)), 'youtube::k', str(tmp_path), find_completed_audio_file, PickupMisses())
    assert (outcome, value) == ('found', str(saved))


def test_name_match_still_used_when_no_path_reported(tmp_path):
    saved = tmp_path / 'Song Title.flac'
    saved.write_bytes(b'x')
    path = resolve_streaming_file(_download(None, 'id||Song Title'), str(tmp_path),
                                  find_completed_audio_file)
    assert path == str(saved)


def test_not_found_waits_and_is_never_marked_processed_early(tmp_path):
    clock = Clock()
    misses = PickupMisses(give_up_after=300, clock=clock)
    dl = _download(None)
    for _ in range(40):
        outcome, _ = check_streaming_pickup(dl, 'k', str(tmp_path), find_completed_audio_file, misses)
        assert outcome == 'wait'
        clock.now += 5
        if clock.now - 1000 >= 290:
            break
    assert misses.failure('k') is None


def test_backoff_skips_lookups_between_tries(tmp_path):
    clock = Clock()
    misses = PickupMisses(clock=clock)
    calls = []

    def finder(*a):
        calls.append(a)
        return None, None

    check_streaming_pickup(_download(None), 'k', str(tmp_path), finder, misses)
    check_streaming_pickup(_download(None), 'k', str(tmp_path), finder, misses)
    assert len(calls) == 1  # second poll a moment later doesn't walk the folder again
    clock.now += 2.1
    check_streaming_pickup(_download(None), 'k', str(tmp_path), finder, misses)
    assert len(calls) == 2


def test_file_turning_up_late_is_picked_up(tmp_path):
    clock = Clock()
    misses = PickupMisses(clock=clock)
    target = tmp_path / '魔法少女A.opus'
    dl = _download(str(target))
    assert check_streaming_pickup(dl, 'k', str(tmp_path), find_completed_audio_file, misses)[0] == 'wait'
    target.write_bytes(b'x')
    clock.now += 60
    assert check_streaming_pickup(dl, 'k', str(tmp_path), find_completed_audio_file, misses) == ('found', str(target))
    assert misses.attempts('k') == 0


def test_gives_up_with_a_reason_after_the_window(tmp_path):
    clock = Clock()
    misses = PickupMisses(give_up_after=300, clock=clock)
    dl = _download(str(tmp_path / 'gone.opus'))
    outcome = None
    for _ in range(100):
        outcome, value = check_streaming_pickup(dl, 'k', str(tmp_path), find_completed_audio_file, misses)
        if outcome == 'gave_up':
            break
        clock.now += 31
    assert outcome == 'gave_up'
    assert 'gone.opus' in value
    assert misses.failure('k') == value


def test_task_handled_elsewhere_is_not_a_failure(tmp_path):
    misses = PickupMisses()
    outcome, _ = check_streaming_pickup(_download(None), 'k', str(tmp_path), find_completed_audio_file,
                                        misses, task_status='post_processing')
    assert outcome == 'handled'
    outcome, _ = check_streaming_pickup(_download(None), 'k2', str(tmp_path), find_completed_audio_file,
                                        misses, task_status='downloading')
    assert outcome == 'wait'


def test_web_server_streaming_branch_uses_the_pickup_helper():
    # web_server can't be imported in tests (it starts workers), so pin the wiring
    src = Path(__file__).resolve().parents[2].joinpath('web_server.py').read_text(encoding='utf-8')
    start = src.index("Also include YouTube/Tidal downloads in the response")
    end = src.index("Could not fetch YouTube/Tidal downloads for status")
    branch = src[start:end]
    assert 'check_streaming_pickup(' in branch
    assert '_find_completed_file_robust(download_dir, download.filename)' not in branch
    assert 'likely already processed' not in branch
    # a miss ('wait') must not add to processed_download_ids
    adds = [m.start() for m in re.finditer(r'processed_download_ids\.add\(context_key\)', branch)]
    for pos in adds:
        prior = branch[:pos].rsplit('elif _st_outcome', 1)[-1]
        assert prior.lstrip().startswith(("== 'handled'", "== 'gave_up'"))


def test_safety_valve_recovery_uses_the_reported_path(tmp_path, monkeypatch):
    saved = tmp_path / '魔法少女A.opus'
    saved.write_bytes(b'x')
    monkeypatch.setattr(st, '_recovery_pool', SimpleNamespace(submit=lambda fn: fn()))
    submitted = []

    class Orch:
        async def get_download_status(self, download_id):
            assert download_id == 'dl-1'
            return SimpleNamespace(file_path=str(saved))

    import asyncio
    deps = st.StatusDeps(SimpleNamespace(get=lambda k, d=None: d), lambda p: p,
                         lambda *a: (None, None), lambda u, f: f,
                         lambda *a: submitted.append(a), lambda: {},
                         download_orchestrator=Orch(), run_async=lambda c: asyncio.run(c))
    task = dict(status='downloading', filename=TITLE_KEY, username='youtube',
                download_id='dl-1', status_change_time=0)
    st.download_tasks['rec-2231'] = task
    try:
        st._schedule_file_recovery('rec-2231', 'batch', task, deps)
        assert task['status'] == 'post_processing'
        assert submitted == [('rec-2231', 'batch')]
    finally:
        st.download_tasks.pop('rec-2231', None)
