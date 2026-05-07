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
