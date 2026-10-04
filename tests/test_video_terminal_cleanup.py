"""Fix 12: 'completed' is dead — nothing ever writes it into shows.status."""
from core.automation.handlers.video_auto_wishlist_airing import _TERMINAL, _is_terminal_status


def test_completed_not_in_terminal():
    # TMDB's raw statuses: Returning Series/Planned/In Production/Ended/Canceled/Pilot.
    # 'completed' was never written; keeping it in _TERMINAL was dead.
    assert 'completed' not in _TERMINAL
    assert _TERMINAL == ('ended', 'canceled', 'cancelled')


def test_is_terminal_still_correct():
    assert _is_terminal_status('Ended') is True
    assert _is_terminal_status('Canceled') is True
    assert _is_terminal_status('Returning Series') is False
    # 'completed' is no longer terminal (was dead anyway)
    assert _is_terminal_status('completed') is False


def test_collections_terminal_status_clean():
    from core.video.collections.sync import _TERMINAL_STATUS
    assert 'completed' not in _TERMINAL_STATUS
    assert _TERMINAL_STATUS == {"ended", "canceled", "cancelled"}


def test_all_show_status_terminal_lists_clean():
    # every show-status terminal list in the repo must exclude 'completed'
    import pathlib
    text = pathlib.Path('database/video_database.py').read_text()
    for line in text.split('\n'):
        if 'ended' in line and 'canceled' in line and 'cancelled' in line:
            assert 'completed' not in line.lower(), f"dead 'completed': {line.strip()[:100]}"
