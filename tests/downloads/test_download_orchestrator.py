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
    orch.soulseek = registry.get('soulseek')
    orch.youtube = registry.get('youtube')
    orch.tidal = registry.get('tidal')
    orch.qobuz = registry.get('qobuz')
    orch.hifi = registry.get('hifi')
    orch.deezer_dl = registry.get('deezer')
    orch.lidarr = registry.get('lidarr')
    orch.soundcloud = registry.get('soundcloud')
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
    assert orch.soulseek.clear_calls == 1
    assert orch.youtube.clear_calls == 0


def test_clear_all_completed_downloads_propagates_configured_failures():
    orch = _build_orchestrator(
        soulseek=_FakeClient(configured=True, clear_result=False),
    )

    result = _run_async(orch.clear_all_completed_downloads())

    assert result is False
    assert orch.soulseek.clear_calls == 1
