"""Download dispatch belongs to the registry-owning engine (P2-23)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from core.download_engine import DownloadEngine
from core.download_orchestrator import DownloadOrchestrator


class _Plugin:
    def __init__(self, result: str):
        self.result = result
        self.calls = []

    async def download(self, username, filename, file_size=0):
        self.calls.append((username, filename, file_size))
        return self.result


def _engine() -> tuple[DownloadEngine, _Plugin, _Plugin]:
    engine = DownloadEngine()
    soulseek = _Plugin("soulseek-id")
    deezer = _Plugin("deezer-id")
    engine.register_plugin("soulseek", soulseek)
    engine.register_plugin("deezer", deezer, aliases=("deezer_dl",))
    return engine, soulseek, deezer


def test_dispatch_routes_canonical_streaming_source() -> None:
    engine, soulseek, deezer = _engine()

    result = asyncio.run(engine.dispatch_download("deezer", "track-1", 42))

    assert result == "deezer-id"
    assert deezer.calls == [("deezer", "track-1", 42)]
    assert soulseek.calls == []


def test_dispatch_resolves_legacy_source_alias_without_rewriting_payload() -> None:
    engine, soulseek, deezer = _engine()

    result = asyncio.run(engine.dispatch_download("deezer_dl", "track-2", 7))

    assert result == "deezer-id"
    assert deezer.calls == [("deezer_dl", "track-2", 7)]
    assert soulseek.calls == []


def test_dispatch_preserves_unknown_username_as_soulseek_peer() -> None:
    engine, soulseek, deezer = _engine()

    result = asyncio.run(engine.dispatch_download("peer-user", "music/file.flac", 99))

    assert result == "soulseek-id"
    assert soulseek.calls == [("peer-user", "music/file.flac", 99)]
    assert deezer.calls == []


def test_dispatch_fails_loudly_when_default_plugin_is_unavailable() -> None:
    engine = DownloadEngine()

    with pytest.raises(RuntimeError, match="soulseek download client not available"):
        asyncio.run(engine.dispatch_download("peer-user", "file.flac"))


def test_dispatch_does_not_treat_unavailable_known_source_as_soulseek_peer() -> None:
    engine, soulseek, _ = _engine()
    engine.register_plugin("youtube", None, aliases=("yt",))

    with pytest.raises(RuntimeError, match="youtube download client not available"):
        asyncio.run(engine.dispatch_download("yt", "video-id"))

    assert soulseek.calls == []


def test_orchestrator_download_delegates_to_engine() -> None:
    """The public facade no longer owns a second routing implementation."""
    orchestrator = object.__new__(DownloadOrchestrator)
    orchestrator.engine = AsyncMock()
    orchestrator.engine.dispatch_download = AsyncMock(return_value="engine-id")

    result = asyncio.run(orchestrator.download("deezer_dl", "track-3", 123))

    assert result == "engine-id"
    orchestrator.engine.dispatch_download.assert_awaited_once_with(
        "deezer_dl", "track-3", 123
    )
