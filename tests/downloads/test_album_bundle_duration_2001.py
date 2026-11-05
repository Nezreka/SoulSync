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
