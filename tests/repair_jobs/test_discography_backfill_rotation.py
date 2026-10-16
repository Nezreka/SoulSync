"""#1709: Discography Backfill rescanned the same first artists every run.

``max_artists_per_run`` sliced ``artists[:max]`` off a name-ordered list, so
with a limit of 2 in a 5-artist library, artists 3-5 were NEVER scanned.
Runs now rotate: each run starts after the last artist the previous run
finished (checkpoint on the config manager, the AcoustID scanner's pattern),
wraps to the top at the end of the list, and a stopped run resumes after
the last artist it completed.
"""
import types

import core.repair_jobs.discography_backfill as backfill_module
from core.repair_jobs.discography_backfill import DiscographyBackfillJob


class _Config:
    """Dict-backed config manager (get/set like ConfigManager)."""

    def __init__(self, max_artists=2):
        self.store = {
            'repair.jobs.discography_backfill.settings': {
                'max_artists_per_run': max_artists,
                'min_album_tracks_owned': 0,
            },
        }

    def get(self, key, default=None):
        return self.store.get(key, default)

    def set(self, key, value):
        self.store[key] = value

    def get_active_media_server(self):
        return None


class _Context:
    def __init__(self, config):
        self.db = None
        self.config_manager = config
        self.update_progress = None
        self.report_progress = None
        self.stop = False
        self.stop_after_sleeps = None
        self.rate_limited = False
        self.pause_on_wait_calls = set()
        self._sleeps = 0
        self._waits = 0

    def check_stop(self):
        return self.stop

    def wait_if_paused(self):
        self._waits += 1
        if self._waits in self.pause_on_wait_calls:
            return True
        return self.stop

    def is_spotify_rate_limited(self):
        return self.rate_limited

    def sleep_or_stop(self, _seconds):
        self._sleeps += 1
        if self.stop_after_sleeps is not None and self._sleeps >= self.stop_after_sleeps:
            self.stop = True
            return True
        return self.stop


def _artists(*names):
    return [{'id': i + 1, 'name': name} for i, name in enumerate(names)]


def _run(monkeypatch, config, artists, scanned, fail_on=(), stop_during=(),
         empty_on=(), rate_limited=False, configure_context=None,
         primary_source='spotify'):
    """One scan() with the library and per-artist work stubbed out.

    ``empty_on`` artists return None from _scan_artist (no discography
    came back — the rate-limit / nothing-on-file case)."""
    job = DiscographyBackfillJob()
    monkeypatch.setattr(backfill_module, 'get_primary_source', lambda: primary_source)
    monkeypatch.setattr(job, '_get_library_artists',
                        lambda context, min_album_tracks=0: list(artists))

    def _scan_artist(context, artist, settings, primary_source, result):
        scanned.append(artist['name'])
        if artist['name'] in stop_during:
            context.stop = True
        if artist['name'] in fail_on:
            raise RuntimeError('boom')
        result.scanned += 1
        if artist['name'] in empty_on:
            result.skipped += 1
            return None
        return 0

    monkeypatch.setattr(job, '_scan_artist', _scan_artist)
    context = _Context(config)
    context.rate_limited = rate_limited
    if configure_context:
        configure_context(context)
    result = job.scan(context)
    return job, context, result


def test_runs_rotate_through_the_whole_library(monkeypatch):
    config = _Config(max_artists=2)
    artists = _artists('A', 'B', 'C', 'D', 'E')
    scanned = []
    for _ in range(3):
        _run(monkeypatch, config, artists, scanned)
    # 2 + 2 + a full wrapped window of 2: no artist is starved, none is
    # rescanned until the pass has covered everyone.
    assert scanned == ['A', 'B', 'C', 'D', 'E', 'A']


def test_window_wraps_and_refills_at_the_end(monkeypatch):
    config = _Config(max_artists=3)
    artists = _artists('A', 'B', 'C', 'D')
    scanned = []
    _run(monkeypatch, config, artists, scanned)
    _run(monkeypatch, config, artists, scanned)
    assert scanned == ['A', 'B', 'C', 'D', 'A', 'B']


def test_library_smaller_than_limit_scans_everyone_every_run(monkeypatch):
    config = _Config(max_artists=50)
    artists = _artists('A', 'B')
    scanned = []
    _run(monkeypatch, config, artists, scanned)
    _run(monkeypatch, config, artists, scanned)
    assert scanned == ['A', 'B', 'A', 'B']


def test_stopped_run_resumes_after_last_finished_artist(monkeypatch):
    # A run stopped (via the inter-artist sleep) after artist A.
    config = _Config(max_artists=50)
    artists = _artists('A', 'B', 'C', 'D')
    job = DiscographyBackfillJob()
    monkeypatch.setattr(backfill_module, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(job, '_get_library_artists',
                        lambda context, min_album_tracks=0: list(artists))
    scanned = []
    monkeypatch.setattr(job, '_scan_artist',
                        lambda context, artist, settings, src, result:
                        (scanned.append(artist['name']), 0)[1])
    context = _Context(config)
    context.stop_after_sleeps = 1
    job.scan(context)
    assert scanned == ['A']
    # The next run picks up at B, not back at A.
    scanned2 = []
    monkeypatch.setattr(job, '_scan_artist',
                        lambda context, artist, settings, src, result:
                        (scanned2.append(artist['name']), 0)[1])
    job.scan(_Context(config))
    assert scanned2 == ['B', 'C', 'D', 'A']


def test_stop_during_an_artist_does_not_skip_it(monkeypatch):
    config = _Config(max_artists=50)
    artists = _artists('A', 'B', 'C')
    scanned = []
    _run(monkeypatch, config, artists, scanned, stop_during=('B',))
    assert scanned == ['A', 'B']
    # B never finished, so the next run resumes AT B.
    scanned2 = []
    _run(monkeypatch, config, artists, scanned2)
    assert scanned2 == ['B', 'C', 'A']


def test_errored_artist_still_advances_the_rotation(monkeypatch):
    config = _Config(max_artists=2)
    artists = _artists('A', 'B', 'C')
    scanned = []
    _, _, result = _run(monkeypatch, config, artists, scanned, fail_on=('B',))
    assert scanned == ['A', 'B']
    assert result.errors == 1
    # A permanently failing B must not starve C.
    scanned2 = []
    _run(monkeypatch, config, artists, scanned2)
    assert scanned2 == ['C', 'A']


def test_checkpoint_artist_removed_resumes_at_its_old_neighbours(monkeypatch):
    config = _Config(max_artists=50)
    artists = _artists('A', 'B', 'C', 'D')
    scanned = []
    _run(monkeypatch, config, artists, scanned)
    config.set('repair.jobs.discography_backfill.scan_checkpoint',
               {'id': '2', 'name': 'B'})
    remaining = _artists('A', 'C', 'D')
    remaining[1]['id'] = 3
    remaining[2]['id'] = 4
    scanned2 = []
    _run(monkeypatch, config, remaining, scanned2)
    assert scanned2 == ['C', 'D', 'A']


def test_paused_run_resumes_after_last_finished_artist(monkeypatch):
    # wait_if_paused is consulted at i=0, 5, 10... — pausing on the second
    # consultation stops the run after 5 artists with 7 in the window.
    config = _Config(max_artists=50)
    artists = _artists('A', 'B', 'C', 'D', 'E', 'F', 'G')
    scanned = []
    _run(monkeypatch, config, artists, scanned,
         configure_context=lambda ctx: ctx.pause_on_wait_calls.add(2))
    assert scanned == ['A', 'B', 'C', 'D', 'E']
    scanned2 = []
    _run(monkeypatch, config, artists, scanned2)
    assert scanned2 == ['F', 'G', 'A', 'B', 'C', 'D', 'E']


def test_checkpoint_on_last_artist_wraps_to_the_top(monkeypatch):
    config = _Config(max_artists=2)
    config.set('repair.jobs.discography_backfill.scan_checkpoint',
               {'id': '3', 'name': 'C'})
    artists = _artists('A', 'B', 'C')
    scanned = []
    _run(monkeypatch, config, artists, scanned)
    assert scanned == ['A', 'B']
    scanned2 = []
    _run(monkeypatch, config, artists, scanned2)
    assert scanned2 == ['C', 'A']


def test_corrupt_checkpoint_values_start_at_the_top(monkeypatch):
    artists = _artists('A', 'B', 'C')
    for bad in ('garbage', ['id', 'name'], {'name': 'B'}, {'id': None, 'name': 'B'},
                {'id': {'x': 1}, 'name': 'B'}, {'id': True, 'name': 'B'}, 42):
        config = _Config(max_artists=2)
        config.set('repair.jobs.discography_backfill.scan_checkpoint', bad)
        scanned = []
        _run(monkeypatch, config, artists, scanned)
        assert scanned == ['A', 'B'], f'checkpoint {bad!r} should be ignored'


def test_removed_checkpoint_with_same_named_survivor_scans_the_survivor(monkeypatch):
    # Checkpoint was the SECOND 'B' (id 3), since removed. The surviving
    # 'B' (id 2) must be scanned, not skipped as already-passed.
    config = _Config(max_artists=50)
    config.set('repair.jobs.discography_backfill.scan_checkpoint',
               {'id': '3', 'name': 'B'})
    artists = [{'id': 1, 'name': 'A'}, {'id': 2, 'name': 'B'}, {'id': 4, 'name': 'C'}]
    scanned = []
    _run(monkeypatch, config, artists, scanned)
    assert scanned == ['B', 'C', 'A']


def test_artist_inserted_before_checkpoint_joins_on_the_wrap(monkeypatch):
    config = _Config(max_artists=2)
    artists = _artists('B', 'C')
    scanned = []
    _run(monkeypatch, config, artists, scanned)
    assert scanned == ['B', 'C']
    grown = [{'id': 3, 'name': 'A'}, {'id': 1, 'name': 'B'}, {'id': 2, 'name': 'C'}]
    scanned2 = []
    _run(monkeypatch, config, grown, scanned2)
    assert scanned2 == ['A', 'B']


def test_empty_discography_under_rate_limit_stops_without_burning_the_window(monkeypatch):
    config = _Config(max_artists=50)
    artists = _artists('A', 'B', 'C')
    scanned = []
    _, _, result = _run(monkeypatch, config, artists, scanned,
                        empty_on=('B',), rate_limited=True)
    assert scanned == ['A', 'B']
    assert result.stopped_early  # recorded as stopped, not completed
    # B was NOT checkpointed: the next run resumes at B.
    scanned2 = []
    _run(monkeypatch, config, artists, scanned2)
    assert scanned2 == ['B', 'C', 'A']


def test_rate_limit_guard_only_applies_to_a_spotify_primary(monkeypatch):
    # With a Deezer primary, an empty discography is Deezer's answer, not
    # the Spotify ban's — rotation advances and no early stop is recorded.
    config = _Config(max_artists=50)
    artists = _artists('A', 'B', 'C')
    scanned = []
    _, _, result = _run(monkeypatch, config, artists, scanned,
                        empty_on=('B',), rate_limited=True, primary_source='deezer')
    assert scanned == ['A', 'B', 'C']
    assert not result.stopped_early


def test_empty_discography_without_rate_limit_still_rotates(monkeypatch):
    # An artist with genuinely nothing on file must not block the library.
    config = _Config(max_artists=50)
    artists = _artists('A', 'B', 'C')
    scanned = []
    _run(monkeypatch, config, artists, scanned, empty_on=('B',))
    assert scanned == ['A', 'B', 'C']


def test_checkpoint_save_failure_warns_but_never_fails_the_scan(monkeypatch, caplog):
    import logging
    class _BrokenConfig(_Config):
        def set(self, key, value):
            raise RuntimeError('store is read-only')

    config = _BrokenConfig(max_artists=2)
    artists = _artists('A', 'B', 'C')
    scanned = []
    with caplog.at_level(logging.WARNING):
        _run(monkeypatch, config, artists, scanned)
    assert scanned == ['A', 'B']
    assert 'Could not save backfill checkpoint' in caplog.text
    # Nothing persisted, so the next run starts at the top again.
    scanned2 = []
    _run(monkeypatch, config, artists, scanned2)
    assert scanned2 == ['A', 'B']


def test_string_and_zero_limits(monkeypatch):
    artists = _artists('A', 'B', 'C')
    config = _Config(max_artists='2')
    scanned = []
    _run(monkeypatch, config, artists, scanned)
    assert scanned == ['A', 'B']

    config0 = _Config(max_artists=0)
    scanned0 = []
    _run(monkeypatch, config0, artists, scanned0)
    assert scanned0 == []


def test_estimate_scope_uses_the_same_limit_coercion(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / 'music.db'))
    with db._get_connection() as conn:
        for i, name in enumerate(('A', 'B', 'C'), start=1):
            conn.execute("INSERT INTO artists (id, name) VALUES (?, ?)", (i, name))
        conn.commit()
    job = DiscographyBackfillJob()
    ctx = types.SimpleNamespace(db=db, config_manager=_Config(max_artists='2'))
    assert job.estimate_scope(ctx) == 2
    ctx2 = types.SimpleNamespace(db=db, config_manager=_Config(max_artists=0))
    assert job.estimate_scope(ctx2) == 0
    # Garbage falls back to the default (50) — same as the scan — and a
    # negative limit estimates an empty window, never a negative scope.
    ctx3 = types.SimpleNamespace(db=db, config_manager=_Config(max_artists='abc'))
    assert job.estimate_scope(ctx3) == 3
    ctx4 = types.SimpleNamespace(db=db, config_manager=_Config(max_artists=-5))
    assert job.estimate_scope(ctx4) == 0


class _ArtistDB:
    """Minimal db for the real _scan_artist: no candidates, tracks owned."""

    def get_candidate_albums_for_artist(self, name, server_source=None):
        return []

    def check_track_exists(self, *args, **kwargs):
        return (object(), 0.95)


def _real_scan_artist(monkeypatch, discography, track_fetches):
    """Call the REAL _scan_artist with only the fetch layer stubbed."""
    from core.repair_jobs.base import JobResult
    job = DiscographyBackfillJob()
    monkeypatch.setattr(backfill_module, 'get_artist_discography',
                        lambda **kwargs: discography)
    monkeypatch.setattr(backfill_module, 'get_album_tracks_for_source',
                        lambda source, release_id: track_fetches.get(release_id))
    context = types.SimpleNamespace(
        db=_ArtistDB(), config_manager=_Config(),
        check_stop=lambda: False, create_finding=None)
    result = JobResult()
    out = job._scan_artist(context, {'id': 1, 'name': 'A'}, {}, 'spotify', result)
    return out, result


_ALBUM = {'id': 'r1', 'name': 'Alb', 'total_tracks': 10, 'album_type': 'album'}
_ALBUM2 = {'id': 'r2', 'name': 'Alb2', 'total_tracks': 8, 'album_type': 'album'}
_TRACKS = {'items': [{'name': 'Song', 'artists': [{'name': 'A'}],
                      'id': 't1', 'duration_ms': 1000}]}


def test_real_scan_artist_empty_discography_is_none_and_skips(monkeypatch):
    out, result = _real_scan_artist(monkeypatch, {'albums': [], 'singles': []}, {})
    assert out is None
    assert result.skipped == 1


def test_real_scan_artist_falsy_discography_is_none_and_skips(monkeypatch):
    out, result = _real_scan_artist(monkeypatch, None, {})
    assert out is None
    assert result.skipped == 1


def test_real_scan_artist_all_track_fetches_failed_is_none(monkeypatch):
    # A cached discography behind a provider outage: releases exist but
    # every track list fails — the artist was NOT scanned.
    out, result = _real_scan_artist(
        monkeypatch, {'albums': [_ALBUM], 'singles': [], 'source': 'spotify'},
        {'r1': None})
    assert out is None
    assert result.skipped == 0


def test_real_scan_artist_partial_track_fetch_failure_still_counts(monkeypatch):
    out, result = _real_scan_artist(
        monkeypatch, {'albums': [_ALBUM, _ALBUM2], 'singles': [], 'source': 'spotify'},
        {'r1': _TRACKS, 'r2': None})
    assert out == 0  # one release fetched fine (track owned) — an attempt


def test_real_scan_artist_owned_library_returns_zero(monkeypatch):
    out, result = _real_scan_artist(
        monkeypatch, {'albums': [_ALBUM], 'singles': [], 'source': 'spotify'},
        {'r1': _TRACKS})
    assert out == 0
    assert result.skipped == 0


def test_checkpoint_round_trips_through_the_real_config_manager(monkeypatch, tmp_path):
    # The fake config in the other tests is dict-backed; the real
    # ConfigManager splits the key on dots into nested dicts and persists
    # to disk — the checkpoint dict must survive both.
    monkeypatch.setenv('SOULSYNC_CONFIG_PATH', str(tmp_path / 'config.json'))
    monkeypatch.setenv('DATABASE_PATH', str(tmp_path / 'music_library.db'))
    from core.settings import ConfigManager
    job = DiscographyBackfillJob()
    ctx = types.SimpleNamespace(config_manager=ConfigManager(str(tmp_path / 'config.json')))
    job._save_checkpoint(ctx, {'id': 42, 'name': 'Some Artist'})
    fresh = types.SimpleNamespace(config_manager=ConfigManager(str(tmp_path / 'config.json')))
    assert job._get_checkpoint(fresh) == {'id': '42', 'name': 'Some Artist'}


def test_no_config_manager_starts_at_the_top_like_before(monkeypatch):
    artists = _artists('A', 'B', 'C')
    scanned = []
    job = DiscographyBackfillJob()
    monkeypatch.setattr(backfill_module, 'get_primary_source', lambda: 'spotify')
    monkeypatch.setattr(job, '_get_library_artists',
                        lambda context, min_album_tracks=0: list(artists))
    monkeypatch.setattr(job, '_scan_artist',
                        lambda context, artist, settings, src, result:
                        (scanned.append(artist['name']), 0)[1])
    context = types.SimpleNamespace(
        db=None, config_manager=None, update_progress=None, report_progress=None,
        check_stop=lambda: False, wait_if_paused=lambda: False,
        is_spotify_rate_limited=lambda: False,
        sleep_or_stop=lambda s: False)
    job.scan(context)
    job.scan(context)
    assert scanned == ['A', 'B', 'C', 'A', 'B', 'C']
