"""music video downloads: one batch each, strict matching, every exit ends the card.

the job runs with fakes for search, download and history, on a tmp folder.
no network, no real yt-dlp, no real database. the runtime dicts are
process-global, so every test starts and ends with them empty.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, List

import pytest

from core.downloads import music_video as mv
from core.downloads.lifecycle import is_music_batch
from core.runtime_state import download_batches, download_tasks


@pytest.fixture(autouse=True)
def clean_state():
    for d in (download_tasks, download_batches, mv.state):
        d.clear()
    yield
    for d in (download_tasks, download_batches, mv.state):
        d.clear()


@dataclass
class Track:
    name: str
    artists: List[str]
    release_date: str = ''


@dataclass
class Fakes:
    results: List[Any] = field(default_factory=list)
    queries: List[str] = field(default_factory=list)
    downloads: List[str] = field(default_factory=list)
    history: List[dict] = field(default_factory=list)
    cleanups: List[Any] = field(default_factory=list)
    threads: List[Any] = field(default_factory=list)
    # what the fake download does: 'ok', 'none', 'raise', 'cancel'
    outcome: str = 'ok'
    template: str = ''


def make_deps(f: Fakes) -> mv.MusicVideoDeps:
    def search(query, limit):
        f.queries.append(query)
        return list(f.results)

    def download(url, stem, progress, should_cancel):
        f.downloads.append(stem)
        progress(50.0)
        if f.outcome == 'raise':
            raise RuntimeError('yt-dlp blew up')
        # a half-written file like yt-dlp leaves
        open(stem + '.mp4.part', 'wb').write(b'x')
        if f.outcome == 'cancel':
            task_id = next(iter(download_tasks))
            download_tasks[task_id]['cancel_requested'] = True
            download_tasks[task_id]['status'] = 'cancelled'
            assert should_cancel() is True
            return None
        if f.outcome == 'none':
            return None
        os.remove(stem + '.mp4.part')
        path = stem + '.mp4'
        open(path, 'wb').write(b'video')
        return path

    return mv.MusicVideoDeps(
        search_tracks=search,
        download=download,
        video_template=lambda: f.template,
        artist_letter=lambda name: name[:1].upper(),
        add_activity=lambda *a: None,
        record_history=lambda **fields: f.history.append(fields),
        # run the job inline and keep the cleanup for the test to fire
        start_thread=lambda fn, name: (f.threads.append(name), fn()),
        schedule_cleanup=lambda fn, delay: f.cleanups.append((fn, delay)),
    )


def video(**over):
    base = {
        'video_id': 'vid1',
        'url': 'https://youtube.com/watch?v=vid1',
        'title': 'Radiohead - Creep (Official Music Video)',
        'channel': 'RadioheadVEVO',
        'thumbnail': 'https://i.ytimg.com/vid1.jpg',
    }
    base.update(over)
    return base


def only_batch():
    assert len(download_batches) == 1
    batch_id, batch = next(iter(download_batches.items()))
    task = download_tasks[batch['queue'][0]]
    return batch_id, batch, task


# ── the batch ───────────────────────────────────────────────────────────────

def test_each_video_is_its_own_batch_the_music_engine_leaves_alone(tmp_path):
    f = Fakes(outcome='none')
    f_deps = make_deps(f)
    mv.start(video(), str(tmp_path), f_deps)
    mv.start(video(video_id='vid2', title='Muse - Uprising'), str(tmp_path), f_deps)
    assert len(download_batches) == 2
    for batch_id, batch in download_batches.items():
        assert batch_id.startswith('music_video_')
        assert batch['batch_type'] == 'music_video'
        assert is_music_batch(batch_id, batch) is False


def test_the_card_exists_before_matching_finishes(tmp_path):
    """it used to appear only once matching was done, so a slow metadata lookup
    looked like nothing happened"""
    f = Fakes()
    deps = make_deps(f)
    deps.start_thread = lambda fn, name: None       # never run the job
    result = mv.start(video(), str(tmp_path), deps)
    assert result['success'] is True
    batch_id, batch, task = only_batch()
    assert batch_id == result['batch_id']
    assert batch['phase'] == 'analysis'
    assert task['status'] == 'searching'
    assert batch['playlist_name'] == 'Radiohead - Creep'
    assert task['track_info']['artwork_url'] == 'https://i.ytimg.com/vid1.jpg'


def test_a_second_click_while_it_runs_is_refused(tmp_path):
    f = Fakes()
    deps = make_deps(f)
    deps.start_thread = lambda fn, name: None
    mv.start(video(), str(tmp_path), deps)
    again = mv.start(video(), str(tmp_path), deps)
    assert again['code'] == 409
    assert len(download_batches) == 1


def test_missing_id_or_url_is_refused(tmp_path):
    assert mv.start(video(video_id=''), str(tmp_path), make_deps(Fakes()))['code'] == 400
    assert mv.start(video(url=''), str(tmp_path), make_deps(Fakes()))['code'] == 400
    assert not download_batches


# ── the happy path ──────────────────────────────────────────────────────────

def test_a_matched_video_is_filed_tagged_recorded_and_the_batch_ends(tmp_path):
    f = Fakes(results=[Track('Creep', ['Radiohead'], '1992-09-21')])
    mv.start(video(), str(tmp_path), make_deps(f))

    batch_id, batch, task = only_batch()
    expected = tmp_path / 'Radiohead' / 'Creep-video.mp4'
    assert expected.exists()
    assert task['status'] == 'completed'
    assert task['final_file_path'] == str(expected)
    assert batch['phase'] == 'complete'
    assert 'completion_time' in batch
    assert mv.state['vid1']['status'] == 'completed'
    # searched with the parsed artist and title, not the raw noisy title
    assert f.queries == ['Radiohead Creep']
    assert f.history == [{
        'event_type': 'download', 'title': 'Creep', 'artist_name': 'Radiohead',
        'album_name': 'Music Video', 'file_path': str(expected),
        'thumb_url': 'https://i.ytimg.com/vid1.jpg', 'download_source': 'YouTube',
        'source_track_id': 'vid1',
        'source_track_title': 'Radiohead - Creep (Official Music Video)',
        'source_artist': 'RadioheadVEVO',
    }]


def test_the_batch_leaves_the_page_after_a_while(tmp_path):
    f = Fakes(results=[Track('Creep', ['Radiohead'])])
    mv.start(video(), str(tmp_path), make_deps(f))
    assert len(f.cleanups) == 1
    cleanup, delay = f.cleanups[0]
    assert delay == mv.CLEANUP_AFTER_SECONDS
    cleanup()
    assert not download_batches
    assert not download_tasks


def test_the_year_comes_from_the_match(tmp_path):
    f = Fakes(results=[Track('Creep', ['Radiohead'], '1992-09-21')], template='$artist/$title ($year)')
    mv.start(video(), str(tmp_path), make_deps(f))
    assert (tmp_path / 'Radiohead' / 'Creep (1992).mp4').exists()


def test_the_mp4_is_tagged(tmp_path, monkeypatch):
    tagged = []
    monkeypatch.setattr(mv, 'tag_video', lambda path, artist, title, year: tagged.append((artist, title, year)))
    f = Fakes(results=[Track('Creep', ['Radiohead'], '1992')])
    mv.start(video(), str(tmp_path), make_deps(f))
    assert tagged == [('Radiohead', 'Creep', '1992')]


# ── matching ────────────────────────────────────────────────────────────────

def test_a_title_match_by_another_artist_is_not_taken(tmp_path):
    """the old scorer weighted title 0.6 / channel 0.4 over 0.5: a same-named
    song by someone else won and the video filed under them"""
    f = Fakes(results=[Track('Creep', ['TLC'])])
    mv.start(video(), str(tmp_path), make_deps(f))
    assert (tmp_path / 'Radiohead' / 'Creep-video.mp4').exists()
    assert not (tmp_path / 'TLC').exists()


def test_a_live_video_never_takes_the_studio_name(tmp_path):
    f = Fakes(results=[Track('Creep', ['Radiohead'], '1992')])
    mv.start(video(title='Radiohead - Creep (Live at Glastonbury 1997)'), str(tmp_path), make_deps(f))
    assert (tmp_path / 'Radiohead' / 'Creep (Live at Glastonbury 1997)-video.mp4').exists()
    assert not (tmp_path / 'Radiohead' / 'Creep-video.mp4').exists()


def test_a_live_video_matches_a_live_release(tmp_path):
    f = Fakes(results=[Track('Creep', ['Radiohead']), Track('Creep (Live)', ['Radiohead'], '2008')],
              template='$artist/$title ($year)')
    mv.start(video(title='Radiohead - Creep (Live)'), str(tmp_path), make_deps(f))
    assert (tmp_path / 'Radiohead' / 'Creep (Live) (2008).mp4').exists()


def test_the_match_fixes_the_names_casing_and_featured_artists(tmp_path):
    f = Fakes(results=[Track('One More Time', ['Daft Punk', 'Romanthony'])])
    mv.start(video(title='daft punk - one more time (feat. Romanthony)'), str(tmp_path), make_deps(f))
    assert (tmp_path / 'Daft Punk' / 'One More Time-video.mp4').exists()


def test_no_results_files_under_the_parsed_name(tmp_path):
    """the empty-results path (a Spotify 403 gives []) must still parse the
    title, not fall back to the uploader's channel"""
    f = Fakes(results=[])
    mv.start(video(title='"Weird Al" Yankovic - Fat Official Music Video', channel='Bad Boy Edd'),
             str(tmp_path), make_deps(f))
    assert (tmp_path / "'Weird Al' Yankovic" / 'Fat-video.mp4').exists()


def test_a_crashing_lookup_still_files_the_video(tmp_path):
    f = Fakes()
    deps = make_deps(f)

    def boom(query, limit):
        raise RuntimeError('metadata down')
    deps.search_tracks = boom
    mv.start(video(), str(tmp_path), deps)
    assert (tmp_path / 'Radiohead' / 'Creep-video.mp4').exists()


def test_the_card_is_renamed_once_matched(tmp_path):
    f = Fakes(results=[Track('Creep', ['Radiohead'])])
    mv.start(video(title='radiohead - creep'), str(tmp_path), make_deps(f))
    _, batch, task = only_batch()
    assert task['track_info']['title'] == 'Creep'
    assert task['track_info']['artist'] == 'Radiohead'
    assert batch['playlist_name'] == 'Radiohead - Creep'


# ── the other exits ─────────────────────────────────────────────────────────

def test_a_video_already_on_disk_is_not_fetched_again(tmp_path):
    (tmp_path / 'Radiohead').mkdir()
    existing = tmp_path / 'Radiohead' / 'Creep-video.mkv'
    existing.write_bytes(b'old video')
    f = Fakes(results=[Track('Creep', ['Radiohead'])])
    mv.start(video(), str(tmp_path), make_deps(f))
    assert f.downloads == []
    _, batch, task = only_batch()
    assert task['status'] == 'completed'
    assert task['final_file_path'] == str(existing)
    assert batch['phase'] == 'complete'
    assert existing.read_bytes() == b'old video'


def test_a_cancel_stops_it_cleans_up_and_says_so(tmp_path):
    f = Fakes(results=[Track('Creep', ['Radiohead'])], outcome='cancel')
    mv.start(video(), str(tmp_path), make_deps(f))
    _, batch, task = only_batch()
    assert task['status'] == 'cancelled'
    assert batch['phase'] == 'cancelled'
    assert not list((tmp_path / 'Radiohead').iterdir()), 'partial file left behind'
    # the search page polls this and only understands completed / error
    assert mv.state['vid1']['status'] == 'error'
    assert mv.state['vid1']['error'] == 'Cancelled'
    assert f.history == []


def test_a_cancel_before_the_fetch_never_fetches(tmp_path):
    f = Fakes(results=[Track('Creep', ['Radiohead'])])
    deps = make_deps(f)
    real_search = deps.search_tracks

    def search_then_cancel(query, limit):
        task_id = next(iter(download_tasks))
        download_tasks[task_id]['cancel_requested'] = True
        return real_search(query, limit)
    deps.search_tracks = search_then_cancel
    mv.start(video(), str(tmp_path), deps)
    assert f.downloads == []
    assert only_batch()[1]['phase'] == 'cancelled'


def test_no_file_back_fails_the_card_and_the_batch(tmp_path):
    f = Fakes(results=[Track('Creep', ['Radiohead'])], outcome='none')
    mv.start(video(), str(tmp_path), make_deps(f))
    _, batch, task = only_batch()
    assert task['status'] == 'failed'
    assert task['error_message']
    assert batch['phase'] == 'error'
    assert mv.state['vid1']['status'] == 'error'
    assert not list((tmp_path / 'Radiohead').iterdir()), 'partial file left behind'
    assert len(f.cleanups) == 1


def test_a_dead_download_thread_never_leaves_a_running_card(tmp_path):
    f = Fakes(results=[Track('Creep', ['Radiohead'])], outcome='raise')
    mv.start(video(), str(tmp_path), make_deps(f))
    _, batch, task = only_batch()
    assert task['status'] == 'failed'
    assert 'yt-dlp blew up' in task['error_message']
    assert batch['phase'] == 'error'
    # and a retry is allowed afterwards
    assert mv.is_in_flight('vid1') is False
