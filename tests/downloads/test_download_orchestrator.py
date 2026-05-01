from core.download_orchestrator import DownloadOrchestrator


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
    orch = DownloadOrchestrator.__new__(DownloadOrchestrator)
    orch.soulseek = clients.get("soulseek")
    orch.youtube = clients.get("youtube")
    orch.tidal = clients.get("tidal")
    orch.qobuz = clients.get("qobuz")
    orch.hifi = clients.get("hifi")
    orch.deezer_dl = clients.get("deezer_dl")
    orch.lidarr = clients.get("lidarr")
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
