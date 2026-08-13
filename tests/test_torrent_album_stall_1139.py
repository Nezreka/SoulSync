"""#1139 — the album-bundle torrent flow could stall for hours and then lose a
finished download.

Zombiehamser's report, reproduced as six independent failures. Each one alone
is survivable; together they are the reported experience — a batch pinned for
hours on a release nobody is serving, and then, when a torrent DID complete,
"No audio files found".

1. The album flow preferred the magnet when the indexer offered both. A magnet
   is an info-hash: the client must find the swarm itself, and one that can't
   sits on "downloading metadata" with zero size and zero peers forever. The
   .torrent URL lets SoulSync fetch the real metadata server-side and push the
   file — what Sonarr/Radarr do, and what ``add_torrent_smart`` was already
   built for but never reached.
2. ``torrent_stall_timeout_seconds`` was wired into the per-TRACK poll loop and
   not the album one, so the setting was configured and simply never consulted
   for album bundles.
3. Nothing enforced a minimum seeder count. Sorting by seeders still returns a
   release when the whole field is on zero.
4. A stalled/timed-out album torrent was never removed from the client, so it
   stayed active in qBittorrent — untracked here, and re-grabbed as a duplicate
   next time.
5. Every one of those failures was terminal for the batch. None set
   ``fallback``, so the per-track flow (and, in hybrid mode, the next source)
   never got a turn.
6. Staging walked ``save_path`` + the torrent's display NAME, ignoring the
   client's ``content_path``. When the on-disk folder differs from the display
   name — routine — the walk finds nothing and reports "No audio files found"
   about a download that completed and is seeding.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from core.download_plugins.album_bundle import pick_best_album_release, poll_album_download
from core.download_plugins.torrent import _no_audio_diagnosis


def _status(**over):
    base = dict(id='h', name='rel', state='downloading', progress=0.0, size=0,
                downloaded=0, download_speed=0, upload_speed=0, seeders=0, peers=0,
                save_path=None)
    base.update(over)
    return SimpleNamespace(**base)


def _release(**over):
    base = dict(title='Artist - Album [FLAC]', size=300 * 1024 * 1024, seeders=10,
                grabs=None, magnet_uri='magnet:?xt=urn:btih:a',
                download_url='https://indexer/dl.torrent',
                protocol='torrent', indexer_name='idx', indexer_id=1, guid='g')
    base.update(over)
    return SimpleNamespace(**base)


# ── 2 + 4: the album poll loop can now see a stall, and clean up after it ─────

class _Clock:
    """Monotonic clock the poll loop drives forward through its own sleeps."""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def test_the_album_poll_loop_gives_up_on_a_stalled_torrent():
    from core.download_plugins.torrent_stall import StallTracker

    clock = _Clock()
    emitted = []
    # A magnet parked on metadata: state 'downloading', size 0 forever, and a
    # byte counter that ticks up from protocol overhead — the exact shape that
    # used to look like progress.
    polls = {'n': 0}

    def get_status():
        polls['n'] += 1
        return _status(downloaded=polls['n'] * 512, size=0)

    stall = StallTracker(60.0)
    cleaned = []

    out = poll_album_download(
        get_status=get_status,
        title='rel',
        emit=lambda state, **f: emitted.append((state, f)),
        complete_states=frozenset(['seeding', 'completed']),
        failed_states=frozenset(['error']),
        poll_interval=10.0,
        timeout=3600.0,
        sleep=clock.sleep,
        monotonic=clock,
        stall_check=lambda s, now: stall.is_stalled(s.downloaded, s.state, now, size=s.size),
        on_stall=lambda: (cleaned.append('removed'), 'Torrent stalled — removed')[1],
    )

    assert out is None
    # Gave up on the stall timeout, NOT after riding the hour-long deadline.
    assert clock.t < 300.0
    assert cleaned == ['removed']
    assert emitted[-1][0] == 'failed'
    assert 'stalled' in emitted[-1][1]['error'].lower()


def test_a_healthy_download_is_never_killed_by_the_stall_gate():
    from core.download_plugins.torrent_stall import StallTracker

    clock = _Clock()
    polls = {'n': 0}

    def get_status():
        polls['n'] += 1
        if polls['n'] >= 5:
            return _status(state='seeding', size=1000, downloaded=1000,
                           save_path='/downloads')
        # Real payload progress, metadata present.
        return _status(size=1000, downloaded=polls['n'] * 100)

    stall = StallTracker(60.0)
    out = poll_album_download(
        get_status=get_status,
        title='rel',
        emit=lambda state, **f: None,
        complete_states=frozenset(['seeding', 'completed']),
        poll_interval=30.0,          # longer than the stall timeout on purpose
        timeout=3600.0,
        sleep=clock.sleep,
        monotonic=clock,
        stall_check=lambda s, now: stall.is_stalled(s.downloaded, s.state, now, size=s.size),
        on_stall=lambda: 'should not happen',
    )
    assert out == '/downloads'


def test_a_seeding_torrent_is_not_a_stall_even_with_no_byte_movement():
    """Belt and braces on the ordering: terminal states return before the gate
    is consulted, so a completed torrent can never be removed for idling."""
    from core.download_plugins.torrent_stall import StallTracker

    clock = _Clock()
    stall = StallTracker(1.0)          # trips almost immediately
    killed = []

    out = poll_album_download(
        get_status=lambda: _status(state='seeding', size=1000, downloaded=1000,
                                   save_path='/downloads'),
        title='rel',
        emit=lambda state, **f: None,
        complete_states=frozenset(['seeding', 'completed']),
        poll_interval=10.0,
        timeout=600.0,
        sleep=clock.sleep,
        monotonic=clock,
        stall_check=lambda s, now: stall.is_stalled(s.downloaded, s.state, now, size=s.size),
        on_stall=lambda: (killed.append(1), 'killed')[1],
    )
    assert out == '/downloads'
    assert killed == []


def test_the_usenet_caller_that_passes_no_stall_hook_is_unchanged():
    clock = _Clock()
    polls = {'n': 0}

    def get_status():
        polls['n'] += 1
        return (_status(state='completed', save_path='/dl') if polls['n'] >= 3
                else _status(size=0, downloaded=0))

    out = poll_album_download(
        get_status=get_status,
        title='rel',
        emit=lambda state, **f: None,
        complete_states=frozenset(['completed']),
        poll_interval=5.0,
        timeout=600.0,
        sleep=clock.sleep,
        monotonic=clock,
    )
    assert out == '/dl'


# ── 3: the availability gate ─────────────────────────────────────────────────

def test_a_field_of_zero_seeder_releases_is_refused_outright():
    dead = [_release(seeders=0), _release(seeders=0, title='Artist - Album [MP3]')]
    assert pick_best_album_release(dead, lambda t: 'flac', min_seeders=1) is None
    # Without the gate the old behaviour stands: it still hands one back.
    assert pick_best_album_release(dead, lambda t: 'flac', min_seeders=0) is not None


def test_the_gate_drops_only_the_dead_candidates():
    live = _release(seeders=5, title='Artist - Album [FLAC] live')
    pool = [_release(seeders=0), live]
    assert pick_best_album_release(pool, lambda t: 'flac', min_seeders=1) is live


def test_candidates_that_report_no_seeder_count_are_exempt():
    """Usenet releases carry ``seeders=None`` (they have no swarm), as do some
    torrent indexers that omit the field. Gating on an unknown would silently
    refuse every usenet bundle."""
    usenet = _release(seeders=None, grabs=40, magnet_uri=None, download_url=None)
    assert pick_best_album_release([usenet], lambda t: 'flac', min_seeders=5) is usenet


def test_the_gate_is_off_by_default_so_callers_opt_in():
    assert pick_best_album_release([_release(seeders=0)], lambda t: 'flac') is not None


# ── 1: .torrent preferred, magnet kept as the fallback ───────────────────────

class _Adapter:
    def __init__(self):
        self.added = []

    async def add_torrent(self, url, category='soulsync', save_path=None):
        self.added.append(('url', url))
        return 'hash'

    async def add_torrent_file(self, blob, category='soulsync', save_path=None):
        self.added.append(('file', blob))
        return 'hash'


def test_a_failed_server_side_fetch_falls_back_to_the_magnet(monkeypatch):
    """The whole point of preferring the URL is that it can be fetched here.
    When it can't, handing the client that same unreachable URL is strictly
    worse than the magnet we were holding all along."""
    import core.torrent_clients.base as base

    async def _fetch_fails(url):
        return None, None

    monkeypatch.setattr(base, '_fetch_torrent_payload_async', _fetch_fails)
    adapter = _Adapter()
    asyncio.run(base.add_torrent_smart(adapter, 'https://indexer/dl.torrent',
                                       fallback_magnet='magnet:?xt=urn:btih:a'))
    assert adapter.added == [('url', 'magnet:?xt=urn:btih:a')]


def test_without_a_fallback_magnet_the_legacy_url_handoff_still_happens(monkeypatch):
    """Setups where the CLIENT can reach the indexer but SoulSync cannot are
    real, and they worked before this change. They still do."""
    import core.torrent_clients.base as base

    async def _fetch_fails(url):
        return None, None

    monkeypatch.setattr(base, '_fetch_torrent_payload_async', _fetch_fails)
    adapter = _Adapter()
    asyncio.run(base.add_torrent_smart(adapter, 'https://indexer/dl.torrent'))
    assert adapter.added == [('url', 'https://indexer/dl.torrent')]


def test_a_successful_fetch_pushes_the_file_and_ignores_the_magnet(monkeypatch):
    import core.torrent_clients.base as base

    async def _fetch_ok(url):
        return b'd4:infod4:name5:aatee', None

    monkeypatch.setattr(base, '_fetch_torrent_payload_async', _fetch_ok)
    adapter = _Adapter()
    asyncio.run(base.add_torrent_smart(adapter, 'https://indexer/dl.torrent',
                                       fallback_magnet='magnet:?xt=urn:btih:a'))
    assert adapter.added == [('file', b'd4:infod4:name5:aatee')]


# ── 6: naming the failure instead of blaming the audio ───────────────────────

def test_an_unreachable_path_is_reported_as_a_mapping_problem(tmp_path):
    message = _no_audio_diagnosis('/data/downloads/rel', tmp_path / 'nope')
    assert 'path_mappings' in message
    assert 'cannot read' in message


def test_a_readable_but_empty_folder_is_not_blamed_on_mapping(tmp_path):
    message = _no_audio_diagnosis(str(tmp_path), tmp_path)
    assert 'path_mappings' not in message
    assert 'no audio' in message.lower()


def test_the_diagnosis_names_both_paths_when_they_differ(tmp_path):
    message = _no_audio_diagnosis('/data/downloads/rel', tmp_path)
    assert '/data/downloads/rel' in message
    assert str(tmp_path) in message


# ── 5: the album flow's failures hand back to per-track ──────────────────────

def _plugin_with(monkeypatch, *, poll_result, status_after=None, results=None):
    """A TorrentDownloadPlugin with Prowlarr, the adapter and the poll loop
    stubbed, so download_album_to_staging can be driven end to end."""
    import core.download_plugins.torrent as mod

    plugin = mod.TorrentDownloadPlugin()
    monkeypatch.setattr(plugin, 'is_configured', lambda: True)

    removed = []

    class _Ad:
        def is_configured(self):
            return True

        async def get_status(self, tid):
            return status_after

        async def remove(self, tid, delete_files=False):
            removed.append(tid)
            return True

        async def pause(self, tid):
            removed.append(('pause', tid))
            return True

    monkeypatch.setattr(mod, 'get_active_torrent_adapter', lambda: _Ad())
    monkeypatch.setattr(mod, 'run_async', lambda coro: _drain(coro))
    monkeypatch.setattr(
        mod, 'prowlarr_search_with_variants',
        lambda *a, **k: _wrap(results if results is not None else [_release()]),
    )
    monkeypatch.setattr(mod, 'poll_album_download', lambda **kw: poll_result)
    return plugin, removed


def _drain(coro):
    """Run a coroutine to completion on a throwaway loop (the plugin calls
    run_async, which the fixture points here)."""
    import asyncio
    if asyncio.iscoroutine(coro):
        return asyncio.run(coro)
    return coro


def _wrap(value):
    async def _c():
        return value
    return _c()


def test_a_stalled_album_download_hands_back_to_per_track(monkeypatch, tmp_path):
    import core.torrent_clients.base as base

    plugin, removed = _plugin_with(monkeypatch, poll_result=None)
    # download_album_to_staging imports this inside the function, so the module
    # attribute is what it resolves at call time.
    monkeypatch.setattr(base, 'add_torrent_smart', _async_return('hash'))

    out = plugin.download_album_to_staging('Album', 'Artist', str(tmp_path))
    assert out['success'] is False
    # THE fix: without this the whole batch died on one dead swarm.
    assert out['fallback'] is True
    # And the dead grab does not stay in the client waiting to be re-picked —
    # exactly once, not once per failure path that noticed.
    assert removed == ['hash']


def test_a_completed_torrent_with_no_readable_audio_hands_back_too(monkeypatch, tmp_path):
    import core.torrent_clients.base as base

    empty = tmp_path / 'client-path'
    empty.mkdir()
    plugin, _removed = _plugin_with(
        monkeypatch, poll_result=str(empty),
        status_after=SimpleNamespace(name='rel', content_path=None),
    )
    monkeypatch.setattr(base, 'add_torrent_smart', _async_return('hash'))

    out = plugin.download_album_to_staging('Album', 'Artist', str(tmp_path / 'staging'))
    assert out['success'] is False
    assert out['fallback'] is True


def _async_return(value):
    async def _f(*a, **k):
        return value
    return _f


# ── 6 (positive): content_path is what identifies THIS torrent's files ───────

def test_the_album_flow_stages_from_the_clients_content_path(monkeypatch, tmp_path):
    """The release folder on disk routinely differs from the torrent's display
    NAME, and the save_path is shared with every other concurrent grab. qBit
    answers the question directly with content_path; the video side has used it
    for a while and this is the music album flow catching up."""
    import core.torrent_clients.base as base

    # Shared download root, with the real release under a name that is NOT the
    # torrent's display name — so a save_path + name walk finds nothing.
    root = tmp_path / 'downloads'
    release = root / 'artist.album.2024.flac-grp'
    release.mkdir(parents=True)
    (release / '01 - track.flac').write_bytes(b'x')

    status = SimpleNamespace(name='Artist - Album (2024) [FLAC]',
                             content_path=str(release))
    plugin, _removed = _plugin_with(monkeypatch, poll_result=str(root),
                                    status_after=status)
    monkeypatch.setattr(base, 'add_torrent_smart', _async_return('hash'))

    staging = tmp_path / 'staging'
    out = plugin.download_album_to_staging('Album', 'Artist', str(staging))
    assert out['success'] is True, out['error']
    assert len(out['files']) == 1


def test_a_single_file_torrent_stages_only_its_own_file(monkeypatch, tmp_path):
    """content_path points at the FILE for a single-file torrent, and that file
    usually sits directly in the SHARED download root. Walking its parent would
    stage every other torrent's audio along with it, so we use the one file we
    already know is ours."""
    import core.torrent_clients.base as base

    root = tmp_path / 'downloads'
    root.mkdir(parents=True)
    single = root / 'one-track.flac'
    single.write_bytes(b'x')
    (root / 'someone-elses-download.flac').write_bytes(b'y')

    status = SimpleNamespace(name='one-track.flac', content_path=str(single))
    plugin, _removed = _plugin_with(monkeypatch, poll_result=str(root),
                                    status_after=status)
    monkeypatch.setattr(base, 'add_torrent_smart', _async_return('hash'))

    out = plugin.download_album_to_staging('Album', 'Artist', str(tmp_path / 'staging'))
    assert out['success'] is True, out['error']
    assert len(out['files']) == 1
    assert Path(out['files'][0]).name == 'one-track.flac'


def test_clients_without_content_path_still_use_save_path_and_name(monkeypatch, tmp_path):
    """Transmission and Deluge leave content_path unset — the old resolution
    has to keep working for them."""
    import core.torrent_clients.base as base

    root = tmp_path / 'downloads'
    release = root / 'Artist - Album'
    release.mkdir(parents=True)
    (release / '01.flac').write_bytes(b'x')

    status = SimpleNamespace(name='Artist - Album', content_path=None)
    plugin, _removed = _plugin_with(monkeypatch, poll_result=str(root),
                                    status_after=status)
    monkeypatch.setattr(base, 'add_torrent_smart', _async_return('hash'))

    out = plugin.download_album_to_staging('Album', 'Artist', str(tmp_path / 'staging'))
    assert out['success'] is True, out['error']
