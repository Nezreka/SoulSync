"""Pure matcher that re-links a moved file to its download-history row (#934)."""

from core.downloads.history_match import pick_history_row, like_filename_filter


# (id, file_path, title, download_source)
def _row(i, path, title='', source='soulseek'):
    return (i, path, title, source)


def test_exact_current_path_wins():
    cands = [_row(1, '/old/song.flac', 'Song'), _row(2, '/lib/song.flac', 'Song')]
    assert pick_history_row(cands, current_paths=('/lib/song.flac', None),
                            basename='song.flac', title='Song') == 2


def test_falls_back_to_filename_when_path_drifted():
    # history has the OLD import path; scanner only knows the NEW library path.
    cands = [_row(7, '/downloads/transfer/Artist/01 - Song.flac', 'Song')]
    assert pick_history_row(cands, current_paths=('/music/Artist/Album/01 - Song.flac', None),
                            basename='01 - Song.flac', title='Song') == 7


def test_title_guard_blocks_shared_filename_collision():
    # two different songs both named "01 - Intro.flac" — must NOT heal the wrong one.
    cands = [_row(1, '/a/01 - Intro.flac', 'Album A Intro'),
             _row(2, '/b/01 - Intro.flac', 'Album B Intro')]
    got = pick_history_row(cands, current_paths=('/c/01 - Intro.flac', None),
                           basename='01 - Intro.flac', title='Album B Intro')
    assert got == 2


def test_title_drift_still_matches_after_normalization():
    cands = [_row(5, '/old/track.flac', 'Song (Remastered)')]
    assert pick_history_row(cands, current_paths=('/new/track.flac', None),
                            basename='track.flac', title='song remastered') == 5


def test_prefers_real_download_row_over_synthetic_scan_row():
    # the #934 collapse: a real download row (drifted path) + a synthetic scan dup at the
    # exact current path. The REAL row must win, so the synthetic one can be deleted.
    cands = [_row(10, '/old/song.flac', 'Song', source='soulseek'),
             _row(11, '/lib/song.flac', 'Song', source='acoustid_scan')]
    assert pick_history_row(cands, current_paths=('/lib/song.flac', None),
                            basename='song.flac', title='Song') == 10


def test_no_basename_match_returns_none():
    cands = [_row(1, '/x/other.flac', 'Other')]
    assert pick_history_row(cands, current_paths=('/x/wanted.flac', None),
                            basename='wanted.flac', title='Wanted') is None


def test_filename_only_substring_is_not_a_match():
    # '/x/mysong.flac' must NOT satisfy basename 'song.flac'
    cands = [_row(1, '/x/mysong.flac', 'My Song')]
    assert pick_history_row(cands, current_paths=('/x/song.flac', None),
                            basename='song.flac', title='Song') is None


def test_like_filter_escapes_metacharacters():
    # underscores/percents in filenames must not become LIKE wildcards
    assert like_filename_filter('a_b%c.flac') == r'%a\_b\%c.flac'
