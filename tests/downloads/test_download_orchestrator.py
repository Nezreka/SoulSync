from core.download_engine import DownloadEngine
from core.download_orchestrator import DownloadOrchestrator
from core.download_plugins.registry import DownloadPluginRegistry, PluginSpec


class _FakeClient:
    def __init__(self, configured=True, clear_result=True):
        self.configured = configured
        self.clear_result = clear_result
        self.clear_calls = 0

    def is_configured(self):
        return self.configured

    async def clear_all_completed_downloads(self):
        self.clear_calls += 1
        return self.clear_result


def _build_orchestrator(**clients):
    """Build an orchestrator with mock clients via the registry.

    The orchestrator iterates `self.registry.all_plugins()` to drive
    every per-source operation, so the test must set up a real
    registry with mock plugins (not just stuff attributes on the
    orchestrator). Source slots not provided in `clients` are
    skipped — registry only holds the ones the test cares about.
    """
    registry = DownloadPluginRegistry()
    name_to_display = {
        'soulseek': 'Soulseek', 'youtube': 'YouTube', 'tidal': 'Tidal',
        'qobuz': 'Qobuz', 'hifi': 'HiFi', 'deezer_dl': 'Deezer',
        'lidarr': 'Lidarr', 'soundcloud': 'SoundCloud',
    }
    # 'deezer_dl' is the legacy attr name; canonical registry name is 'deezer'.
    aliases_for = {'deezer_dl': ('deezer_dl',)}
    canonical_for = {'deezer_dl': 'deezer'}

    for slot, client in clients.items():
        if client is None:
            continue
        canonical_name = canonical_for.get(slot, slot)
        registry.register(PluginSpec(
            name=canonical_name,
            factory=lambda c=client: c,
            display_name=name_to_display.get(slot, slot),
            aliases=aliases_for.get(slot, ()),
        ))
    registry.initialize()

    orch = DownloadOrchestrator.__new__(DownloadOrchestrator)
    orch.registry = registry
    orch._init_failures = registry.init_failures
    # Engine — orchestrator delegates per-source query/cancel
    # methods to it, so the test fixture must build one and
    # register every mock plugin under its canonical name.
    orch.engine = DownloadEngine()
    for source_name, plugin in registry.all_plugins():
        orch.engine.register_plugin(source_name, plugin)
    return orch


def _run_async(coro):
    import asyncio

    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_clear_all_completed_downloads_ignores_unconfigured_clients():
    orch = _build_orchestrator(
        soulseek=_FakeClient(configured=True, clear_result=True),
        youtube=_FakeClient(configured=False, clear_result=False),
    )

    result = _run_async(orch.clear_all_completed_downloads())

    assert result is True
    assert orch.client('soulseek').clear_calls == 1
    assert orch.client('youtube').clear_calls == 0


def test_clear_all_completed_downloads_propagates_configured_failures():
    orch = _build_orchestrator(
        soulseek=_FakeClient(configured=True, clear_result=False),
    )

    result = _run_async(orch.clear_all_completed_downloads())

    assert result is False
    assert orch.client('soulseek').clear_calls == 1


# ---------------------------------------------------------------------------
# Cin-2 generic accessors
# ---------------------------------------------------------------------------


def test_client_returns_registered_client_by_name():
    """Cin's review feedback: orch.client('hifi') is the canonical
    way to reach a per-source client, replacing orch.hifi attribute
    access."""
    soulseek = _FakeClient()
    youtube = _FakeClient()
    orch = _build_orchestrator(soulseek=soulseek, youtube=youtube)

    assert orch.client('soulseek') is soulseek
    assert orch.client('youtube') is youtube
    assert orch.client('made_up') is None


def test_configured_clients_excludes_unconfigured_sources():
    """Replaces the legacy iteration pattern: 6+ if/hasattr/is_configured
    checks per source. Single call returns dict of configured clients."""
    configured = _FakeClient(configured=True)
    unconfigured = _FakeClient(configured=False)
    orch = _build_orchestrator(
        soulseek=configured,
        youtube=unconfigured,
    )
    result = orch.configured_clients()
    assert 'soulseek' in result
    assert 'youtube' not in result
    assert result['soulseek'] is configured


def test_configured_clients_skips_clients_whose_is_configured_raises():
    """Per JohnBaumb: configured_clients() has a try/except so a single
    broken is_configured() call doesn't crash the whole iteration —
    pin it so a future refactor can't quietly drop the guard. The
    broken plugin is skipped; the rest still come back."""

    class _BrokenIsConfigured(_FakeClient):
        def is_configured(self):
            raise RuntimeError("is_configured blew up")

    broken = _BrokenIsConfigured()
    healthy = _FakeClient(configured=True)
    orch = _build_orchestrator(soulseek=healthy, youtube=broken)

    result = orch.configured_clients()
    # Healthy plugin still surfaces; broken one is silently skipped.
    assert 'soulseek' in result
    assert result['soulseek'] is healthy
    assert 'youtube' not in result


def test_reload_instances_dispatches_to_named_source():
    """Generic dispatch — caller passes source name instead of
    reaching for orch.hifi.reload_instances() directly."""

    class _ReloadableClient(_FakeClient):
        def __init__(self):
            super().__init__(configured=True)
            self.reload_called = False

        def reload_instances(self):
            self.reload_called = True

    hifi = _ReloadableClient()
    soulseek = _FakeClient()  # No reload_instances method
    orch = _build_orchestrator(soulseek=soulseek, hifi=hifi)

    assert orch.reload_instances('hifi') is True
    assert hifi.reload_called is True


def test_reload_instances_skips_clients_without_method():
    """Sources that don't expose reload_instances are skipped, not
    treated as failures."""
    soulseek = _FakeClient()  # No reload_instances method
    orch = _build_orchestrator(soulseek=soulseek)
    # Calling on a source without the method = silent no-op
    assert orch.reload_instances('soulseek') is True


def test_reload_instances_with_no_args_reloads_every_source():
    """When called with no source argument, hits every registered
    source that exposes reload_instances."""

    class _ReloadableClient(_FakeClient):
        def __init__(self):
            super().__init__()
            self.reload_called = False

        def reload_instances(self):
            self.reload_called = True

    a = _ReloadableClient()
    b = _ReloadableClient()
    orch = _build_orchestrator(soulseek=a, hifi=b)

    orch.reload_instances()
    assert a.reload_called is True
    assert b.reload_called is True


def test_reload_settings_refreshes_registry_plugins(monkeypatch):
    """Settings saves should refresh plugins that cache config at init.

    Prowlarr-backed torrent / usenet clients keep a ProwlarrClient
    instance, so without this hook newly-saved indexer settings only
    took effect after process restart.
    """

    class _ReloadSettingsClient(_FakeClient):
        def __init__(self):
            super().__init__()
            self.reload_calls = 0

        def reload_settings(self):
            self.reload_calls += 1

    torrent = _ReloadSettingsClient()
    usenet = _ReloadSettingsClient()
    orch = _build_orchestrator(torrent=torrent, usenet=usenet)

    monkeypatch.setattr(
        'core.download_orchestrator.config_manager.get',
        lambda _key, default=None: default,
    )

    orch.reload_settings()

    assert torrent.reload_calls == 1
    assert usenet.reload_calls == 1


def test_search_and_download_best_does_not_fetch_when_profile_rejects_youtube(monkeypatch):
    """Fallback off + no matching target must not fall through to `or scored`."""
    import asyncio
    from types import SimpleNamespace

    from core.download_plugins.types import TrackResult
    from core.quality.model import AudioQuality

    hit = TrackResult(
        username='youtube',
        filename='vid1||Song',
        size=0,
        bitrate=160,
        duration=180_000,
        quality='opus',
        free_upload_slots=1,
        upload_speed=1,
        queue_length=0,
        artist='Artist',
        title='Song',
    )
    hit.set_quality(AudioQuality(format='opus', bitrate=160))

    class _YT(_FakeClient):
        def refresh_claimed_quality(self, candidates, **kwargs):
            return None

    youtube = _YT()
    orch = _build_orchestrator(youtube=youtube)
    orch.mode = 'youtube'

    async def _search(*_a, **_k):
        return [hit], []

    downloaded = []

    async def _download(*a, **_k):
        downloaded.append(a)
        return 'dl-id'

    orch.search = _search
    orch.download = _download
    monkeypatch.setattr(
        'core.quality.selection.rank_for_profile',
        lambda _cands: ([], False),
    )
    monkeypatch.setattr(
        'core.matching_engine.MusicMatchingEngine.score_track_match',
        lambda self, **_kw: (0.99, 'ok'),
    )

    expected = SimpleNamespace(name='Song', artists=['Artist'], duration_ms=180_000)
    result = asyncio.run(orch.search_and_download_best('Artist Song', expected))
    assert result is None
    assert downloaded == []


def test_search_and_download_best_ranks_only_close_youtube_matches(monkeypatch):
    """Distant YouTube hits must not enter rank_for_profile."""
    import asyncio
    from types import SimpleNamespace

    from core.download_plugins.types import TrackResult
    from core.quality.model import AudioQuality

    def _hit(filename, title, bitrate):
        row = TrackResult(
            username='youtube',
            filename=filename,
            size=0,
            bitrate=bitrate,
            duration=180_000,
            quality='opus',
            free_upload_slots=1,
            upload_speed=1,
            queue_length=0,
            artist='Artist',
            title=title,
        )
        row.set_quality(AudioQuality(format='opus', bitrate=bitrate))
        return row

    top = _hit('aaaaaaaaaaa||Song', 'Song', 160)
    far = _hit('bbbbbbbbbbb||Other', 'Other', 256)
    probed = []

    class _YT(_FakeClient):
        def refresh_claimed_quality(self, candidates, **kwargs):
            probed.extend(candidates)

    youtube = _YT()
    orch = _build_orchestrator(youtube=youtube)
    orch.mode = 'youtube'

    async def _search(*_a, **_k):
        return [top, far], []

    downloaded = []

    async def _download(*a, **_k):
        downloaded.append(a)
        return 'dl-id'

    orch.search = _search
    orch.download = _download
    ranked_args = []
    monkeypatch.setattr(
        'core.quality.selection.rank_for_profile',
        lambda cands: (ranked_args.append(list(cands)) or cands, True),
    )
    monkeypatch.setattr(
        'core.matching_engine.MusicMatchingEngine.score_track_match',
        lambda self, **kw: (0.62, 'ok') if kw.get('candidate_title') == 'Other' else (0.92, 'ok'),
    )
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([], True),
    )

    expected = SimpleNamespace(name='Song', artists=['Artist'], duration_ms=180_000)
    result = asyncio.run(orch.search_and_download_best('Artist Song', expected))
    assert result == 'dl-id'
    assert ranked_args and ranked_args[0] == [top]
    assert probed == [top]
    assert downloaded and downloaded[0][1] == top.filename


def test_search_and_download_best_does_not_band_non_youtube_in_hybrid(monkeypatch):
    """A closer YouTube hit must not drop a Tidal match in best-quality hybrid."""
    import asyncio
    from types import SimpleNamespace

    from core.download_plugins.types import TrackResult
    from core.quality.model import AudioQuality

    def _hit(username, filename, title, quality, bitrate, duration=180_000):
        row = TrackResult(
            username=username,
            filename=filename,
            size=0,
            bitrate=bitrate,
            duration=duration,
            quality=quality,
            free_upload_slots=1,
            upload_speed=1,
            queue_length=0,
            artist='Artist',
            title=title,
        )
        row.set_quality(AudioQuality(format=quality, bitrate=bitrate))
        return row

    yt_top = _hit('youtube', 'aaaaaaaaaaa||Song', 'Song', 'opus', 160)
    yt_far = _hit('youtube', 'bbbbbbbbbbb||Other', 'Other', 'opus', 256)
    tidal = _hit('tidal', 'tid||Song', 'Song', 'flac', 1411, duration=181_000)
    probed = []

    class _YT(_FakeClient):
        def refresh_claimed_quality(self, candidates, **kwargs):
            probed.extend(candidates)

    youtube = _YT()
    orch = _build_orchestrator(youtube=youtube, tidal=_FakeClient())
    orch.mode = 'hybrid'

    async def _search(*_a, **_k):
        return [yt_top, yt_far, tidal], []

    async def _download(*a, **_k):
        return 'dl-id'

    orch.search = _search
    orch.download = _download
    ranked_args = []
    monkeypatch.setattr(
        'core.quality.selection.rank_for_profile',
        lambda cands: (ranked_args.append(list(cands)) or cands, True),
    )

    def _score(self, **kw):
        if kw.get('candidate_title') == 'Other':
            return (0.62, 'ok')
        if kw.get('candidate_duration_ms') == 181_000:
            return (0.70, 'ok')
        return (0.92, 'ok')

    monkeypatch.setattr(
        'core.matching_engine.MusicMatchingEngine.score_track_match',
        _score,
    )
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([], True),
    )

    expected = SimpleNamespace(name='Song', artists=['Artist'], duration_ms=180_000)
    result = asyncio.run(orch.search_and_download_best('Artist Song', expected))
    assert result == 'dl-id'
    ranked = ranked_args[0]
    assert tidal in ranked
    assert yt_top in ranked
    assert yt_far not in ranked
    assert probed == [yt_top]


def test_search_and_download_best_scores_each_source_when_soulseek_is_first(monkeypatch):
    """A Soulseek peer first must not send Tidal/YouTube through the P2P quality filter."""
    import asyncio
    from types import SimpleNamespace

    from core.download_plugins.types import TrackResult
    from core.quality.model import AudioQuality

    def _hit(username, filename, title, quality, bitrate):
        row = TrackResult(
            username=username,
            filename=filename,
            size=0,
            bitrate=bitrate,
            duration=180_000,
            quality=quality,
            free_upload_slots=1,
            upload_speed=1,
            queue_length=0,
            artist='Artist',
            title=title,
        )
        row.set_quality(AudioQuality(format=quality, bitrate=bitrate))
        return row

    peer = _hit('alice', 'Artist/Album/01 - Song.flac', 'Song', 'flac', 1411)
    yt = _hit('youtube', 'aaaaaaaaaaa||Song', 'Song', 'opus', 160)
    tidal = _hit('tidal', 'tid||Song', 'Song', 'flac', 1411)
    slsk_filtered = []
    probed = []

    class _Slsk(_FakeClient):
        def filter_results_by_quality_preference(self, tracks):
            slsk_filtered.extend(tracks)
            return list(tracks)

    class _YT(_FakeClient):
        def refresh_claimed_quality(self, candidates, **kwargs):
            probed.extend(candidates)

    orch = _build_orchestrator(soulseek=_Slsk(), youtube=_YT(), tidal=_FakeClient())
    orch.mode = 'hybrid'

    async def _search(*_a, **_k):
        return [peer, yt, tidal], []

    async def _download(*a, **_k):
        return 'dl-id'

    orch.search = _search
    orch.download = _download
    ranked_args = []
    monkeypatch.setattr(
        'core.quality.selection.rank_for_profile',
        lambda cands: (ranked_args.append(list(cands)) or cands, True),
    )
    monkeypatch.setattr(
        'core.matching_engine.MusicMatchingEngine.score_track_match',
        lambda self, **_kw: (0.92, 'ok'),
    )
    monkeypatch.setattr(
        'core.quality.selection.load_profile_targets',
        lambda: ([], True),
    )

    expected = SimpleNamespace(name='Song', artists=['Artist'], duration_ms=180_000)
    result = asyncio.run(orch.search_and_download_best('Artist Song', expected))
    assert result == 'dl-id'
    assert slsk_filtered == [peer]
    assert yt in (ranked_args[0] if ranked_args else [])
    assert tidal in (ranked_args[0] if ranked_args else [])
    assert peer in (ranked_args[0] if ranked_args else [])
    assert probed == [yt]


# ---------------------------------------------------------------------------
# Singleton factory (matches Cin's get_metadata_engine pattern)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Cin bug 2: hybrid_order alias normalization
# ---------------------------------------------------------------------------


def test_resolve_source_chain_normalizes_legacy_aliases():
    """Cin's bug 2: hybrid_order config containing the legacy alias
    'deezer_dl' was silently dropped because the canonical-name
    membership check rejected it. Orchestrator must normalize via
    the registry alias map first."""
    orch = _build_orchestrator(
        soulseek=_FakeClient(),
        deezer_dl=_FakeClient(),
        youtube=_FakeClient(),
    )
    orch.hybrid_order = ['deezer_dl', 'soulseek', 'youtube']
    orch.hybrid_primary = None
    orch.hybrid_secondary = None

    chain = orch._resolve_source_chain()
    assert chain == ['deezer', 'soulseek', 'youtube']


def test_resolve_source_chain_dedupes_alias_and_canonical():
    """If both 'deezer' and 'deezer_dl' appear, dedupe to single entry."""
    orch = _build_orchestrator(
        soulseek=_FakeClient(),
        deezer_dl=_FakeClient(),
    )
    orch.hybrid_order = ['deezer_dl', 'deezer', 'soulseek']
    orch.hybrid_primary = None
    orch.hybrid_secondary = None

    chain = orch._resolve_source_chain()
    assert chain == ['deezer', 'soulseek']


def test_resolve_source_chain_drops_unknown_names():
    orch = _build_orchestrator(soulseek=_FakeClient(), youtube=_FakeClient())
    orch.hybrid_order = ['nonsense', 'soulseek', 'also_fake', 'youtube']
    orch.hybrid_primary = None
    orch.hybrid_secondary = None

    chain = orch._resolve_source_chain()
    assert chain == ['soulseek', 'youtube']


def test_get_download_orchestrator_returns_set_singleton():
    """When set_download_orchestrator has been called (web_server.py
    does this at boot), get_download_orchestrator returns the
    installed instance instead of building a fresh one."""
    from core.download_orchestrator import (
        get_download_orchestrator,
        set_download_orchestrator,
    )

    orch = _build_orchestrator(soulseek=_FakeClient())
    set_download_orchestrator(orch)
    try:
        assert get_download_orchestrator() is orch
    finally:
        set_download_orchestrator(None)
