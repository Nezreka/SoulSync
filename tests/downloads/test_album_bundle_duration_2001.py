"""#2001: a folder held the single edit and the 13 minute extended mix of the
same song. with no track number to tell them apart the bundle took whichever
file the peer listed first, got quarantined, and the task ended as "no match
found" with the file sitting in quarantine."""

from core.downloads.soulseek_identity import assign_album_tracks

WANTED = {'name': 'DoYaThing', 'artists': [{'name': 'Gorillaz'}], 'duration_ms': 266_000}
EXTENDED = {'filename': 'Lib\\Gorillaz\\DoYaThing\\1-02 DoYaThing.mp3', 'duration': 784_000}
EDIT = {'filename': 'Lib\\Gorillaz\\DoYaThing\\1-01 DoYaThing.mp3', 'duration': 266_000}


def _picked(expected, files):
    pairs = assign_album_tracks(expected, files, album='DoYaThing').pairs
    return [files[f]['filename'].rsplit('\\', 1)[-1] for _, f in pairs]


def test_the_file_whose_length_fits_wins_a_tie():
    assert _picked([WANTED], [EXTENDED, EDIT]) == ['1-01 DoYaThing.mp3']


def test_an_unknown_length_beats_a_known_mismatch():
    unknown = {'filename': EDIT['filename']}
    assert _picked([WANTED], [EXTENDED, unknown]) == ['1-01 DoYaThing.mp3']


def test_a_matching_track_number_still_comes_first():
    # identity evidence outranks length: a numbered match is the right slot
    numbered = dict(WANTED, track_number=2, disc_number=1)
    assert _picked([numbered], [EDIT, EXTENDED]) == ['1-02 DoYaThing.mp3']


def test_the_retry_remembers_why_the_file_was_quarantined(monkeypatch):
    from core.imports import pipeline
    from core.runtime_state import download_tasks, tasks_lock

    with tasks_lock:
        download_tasks['t-2001'] = {'status': 'post_processing'}
    try:
        pipeline._remember_quarantine('t-2001', 'integrity check failed: file is 784s, expected 266s')
        assert 'expected 266s' in download_tasks['t-2001']['last_quarantine_reason']
    finally:
        download_tasks.pop('t-2001', None)


def test_a_quarantine_retry_starts_from_what_the_album_search_found():
    """the retry searched from scratch (and found nothing, the artist was on the
    server's banned phrase list) while the right cut sat in the folder it came
    from"""
    from types import SimpleNamespace
    from core.downloads.task_worker import _seed_from_preflight_pool
    from core.runtime_state import download_batches, download_tasks, tasks_lock

    f = lambda name, ms, user='peer-a': SimpleNamespace(  # noqa: E731
        username=user, filename=f'Lib\\Gorillaz\\DoYaThing\\{name}', duration=ms)
    pool = [f('1-02 DoYaThing.mp3', 784_000), f('1-01 DoYaThing.mp3', 266_000),
            f('01 Feel Good Inc.mp3', 222_000), f('DoYaThing.flac', 265_000, 'peer-b')]
    track = SimpleNamespace(name='DoYaThing', artists=['Gorillaz'], duration_ms=266_000)
    with tasks_lock:
        download_batches['b-2001'] = {'preflight_pool': pool}
        download_tasks['t-seed'] = {'status': 'searching'}
        download_tasks['t-own'] = {'status': 'searching', 'cached_candidates': ['mine']}
    try:
        _seed_from_preflight_pool('t-seed', 'b-2001', track)
        _seed_from_preflight_pool('t-own', 'b-2001', track)
        names = [c.filename.rsplit('\\', 1)[-1] for c in download_tasks['t-seed']['cached_candidates']]
        assert names[:2] == ['1-01 DoYaThing.mp3', 'DoYaThing.flac'], "the right length first"
        assert names[-1] == '1-02 DoYaThing.mp3' and '01 Feel Good Inc.mp3' not in names
        assert download_tasks['t-own']['cached_candidates'] == ['mine'], "its own search wins"
    finally:
        download_batches.pop('b-2001', None)
        download_tasks.pop('t-seed', None)
        download_tasks.pop('t-own', None)


def test_a_seeded_retry_candidate_carries_a_confidence():
    """the album search's results were never scored, and the download path
    logs candidate.confidence: every seeded retry died on "'TrackResult' object
    has no attribute 'confidence'" (PROBLEMS IN MY HEAD, LFTBU, BAD GIRLS)"""
    from types import SimpleNamespace
    from core.download_plugins.types import TrackResult
    from core.downloads.task_worker import _seed_from_preflight_pool
    from core.runtime_state import download_batches, download_tasks, tasks_lock

    raw = TrackResult(username='peer-a', filename='Lib\\Gorillaz\\DoYaThing\\1-01 DoYaThing.mp3',
                      size=8_000_000, bitrate=320, duration=266_000, quality='mp3',
                      free_upload_slots=1, upload_speed=500, queue_length=0)
    assert getattr(raw, 'confidence', None) is None
    track = SimpleNamespace(name='DoYaThing', artists=['Gorillaz'], duration_ms=266_000)
    with tasks_lock:
        download_batches['b-conf'] = {'preflight_pool': [raw]}
        download_tasks['t-conf'] = {'status': 'searching'}
    try:
        _seed_from_preflight_pool('t-conf', 'b-conf', track)
        seeded = download_tasks['t-conf']['cached_candidates']
        assert seeded and all(isinstance(c.confidence, float) for c in seeded)
    finally:
        download_batches.pop('b-conf', None)
        download_tasks.pop('t-conf', None)
