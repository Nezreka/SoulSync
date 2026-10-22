"""A terminal album-bundle failure must start the wishlist retry backoff.

When the album-bundle flow fails terminally (a Usenet release SABnzbd aborts, a
dead torrent), ``try_dispatch`` marks the batch failed and the master worker
returns before any per-track task exists. ``add_failed_tracks_to_wishlist``
therefore never ran for these tracks, ``record_failed_attempt`` never stamped
them, and they kept ``retry_count = 0`` / ``last_attempted = NULL``. The backoff
reads that as "never tried", so the same dead release was fetched again on every
30-minute wishlist cycle.
"""
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


class _ForwardingService:
    """mark_track_download_result -> the real DB method (hermetic, no singleton)."""

    def __init__(self, db):
        self.db = db

    def mark_track_download_result(self, spotify_track_id, success,
                                   error_message=None, profile_id=1, **kwargs):
        return self.db.update_wishlist_retry(spotify_track_id, success,
                                             error_message, profile_id=profile_id,
                                             **kwargs)


def _payload(sp_id):
    return {
        'id': sp_id, 'name': f'Track {sp_id}', 'artists': [{'name': 'Some Artist'}],
        'album': {'id': 'alb', 'name': 'Some Album', 'artists': [{'name': 'Some Artist'}],
                  'images': [], 'album_type': 'album', 'release_date': '2020-01-01',
                  'total_tracks': 3},
        'duration_ms': 1000, 'track_number': 1, 'disc_number': 1,
    }


def _wishlist(db, ids):
    for sp_id in ids:
        assert db.add_to_wishlist(spotify_track_data=_payload(sp_id),
                                  failure_reason='Missing from media server after sync',
                                  source_type='album', source_info='{}', profile_id=1)


def test_terminal_bundle_failure_starts_backoff(tmp_path):
    from core.wishlist.processing import record_failed_attempts_for_tracks
    from core.wishlist.retry_backoff import split_due_for_retry
    from database.music_database import MusicDatabase

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    _wishlist(db, ['t1', 't2', 't3'])
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # before: three fresh rows are all due, which is the re-grab loop
    due, cooling = split_due_for_retry(db.get_wishlist_tracks(), now)
    assert len(due) == 3 and not cooling

    svc = _ForwardingService(db)
    tracks = [_payload('t1'), _payload('t2'), {'spotify_track_id': 't3'}]
    assert record_failed_attempts_for_tracks(
        svc, tracks, 'Usenet download failed or timed out', 1) == 3

    rows = db.get_wishlist_tracks()
    assert {r['retry_count'] for r in rows} == {1}
    assert all(r['last_attempted'] for r in rows)
    assert all(r['failure_reason'] == 'Usenet download failed or timed out' for r in rows)

    # the standard ladder gives the first two failures no wait (attempts 0-1) ...
    due, cooling = split_due_for_retry(rows, now)
    assert len(due) == 3 and not cooling

    # ... and from the second failed cycle on, the backoff holds them back
    assert record_failed_attempts_for_tracks(
        svc, tracks, 'Usenet download failed or timed out', 1) == 3
    rows = db.get_wishlist_tracks()
    assert {r['retry_count'] for r in rows} == {2}
    due, cooling = split_due_for_retry(rows, now)
    assert not due and len(cooling) == 3


def test_stamp_guards(tmp_path):
    from core.wishlist.processing import record_failed_attempts_for_tracks
    from database.music_database import MusicDatabase

    db = MusicDatabase(database_path=str(tmp_path / 'm.db'))
    svc = _ForwardingService(db)
    # not on the wishlist, no id, wrong shape, empty: nothing stamped, nothing raised
    assert record_failed_attempts_for_tracks(svc, [{'id': 'nope'}, {}, None, 'x'], 'e', 1) == 0
    assert record_failed_attempts_for_tracks(svc, None, 'e', 1) == 0
    assert record_failed_attempts_for_tracks(svc, [], 'e', 1) == 0

    class _Boom:
        def mark_track_download_result(self, *a, **k):
            raise RuntimeError('db locked')
    assert record_failed_attempts_for_tracks(_Boom(), [{'id': 'x'}], 'e', 1) == 0


def test_both_dispatch_sites_stamp_before_returning():
    src = (_ROOT / "core" / "downloads" / "master.py").read_text(encoding="utf-8")
    assert "def _stamp_terminal_bundle_failure" in src
    # the two try_dispatch call sites (hybrid prefix/suffix and soulseek) both stamp
    assert src.count("_album_bundle_dispatch.try_dispatch(") == 2
    assert src.count("_stamp_terminal_bundle_failure()\n") == 2
    assert "record_failed_attempts_for_tracks" in src


def test_an_unfinished_search_is_not_an_attempt():
    """an indexer that Prowlarr holds back answers 400, which the plugins report
    as 'Prowlarr search failed: ...'. no release was tried, so it must not push
    the batch's tracks into the backoff."""
    from core.wishlist.processing import bundle_failure_counts_as_attempt

    assert not bundle_failure_counts_as_attempt('Prowlarr search failed: indexer 1: Prowlarr returned HTTP 400')
    assert not bundle_failure_counts_as_attempt('Prowlarr search failed: Prowlarr did not answer within 30s')
    assert bundle_failure_counts_as_attempt('Usenet download failed or timed out')
    assert bundle_failure_counts_as_attempt('')
    assert bundle_failure_counts_as_attempt(None)


def test_plugins_report_search_failures_with_the_shared_prefix():
    from core.prowlarr_client import SEARCH_FAILED_PREFIX
    for name in ('torrent', 'usenet'):
        src = (_ROOT / "core" / "download_plugins" / f"{name}.py").read_text(encoding="utf-8")
        assert "f'{SEARCH_FAILED_PREFIX}: {e}'" in src, name
