"""Canonical contract every download source plugin must satisfy.

`DownloadSourcePlugin` is a structural Protocol — any class that
implements these methods with matching signatures is automatically
treated as a download source. No inheritance required, no manual
registration required beyond the registry entry.

The protocol is intentionally narrow — only the methods the
orchestrator dispatches generically across all sources. Source-
specific extras (Soulseek's slskd internals, Lidarr's album-only
flow, etc.) stay on the underlying client and are accessed through
the registry's typed accessor.

This file is the FOUNDATION step. Existing client classes
(SoulseekClient, YouTubeClient, TidalDownloadClient, etc.) already
conform structurally — they grew the same shape independently
because every consumer site needed the same calls. This file just
makes the implicit contract explicit so:

- Type checkers can flag drift if a new source forgets a method.
- The orchestrator can iterate plugins generically instead of
  hardcoding `[self.soulseek, self.youtube, ...]` everywhere.
- Future PRs can move shared logic INTO the contract (a base
  class with default implementations) without changing the
  signature surface every consumer already depends on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional, Protocol, Tuple, runtime_checkable

# Soulseek client owns the canonical TrackResult / AlbumResult /
# DownloadStatus dataclasses — every other source already imports
# from there. We only need them for type annotations on this
# protocol; using TYPE_CHECKING avoids a circular import once the
# clients themselves inherit from DownloadSourcePlugin (Cin's
# review feedback — clients explicitly declare conformance instead
# of relying on structural typing).
if TYPE_CHECKING:
    from core.soulseek_client import AlbumResult, DownloadStatus, TrackResult


@runtime_checkable
class DownloadSourcePlugin(Protocol):
    """Structural contract for a download source.

    `runtime_checkable` lets `isinstance(client, DownloadSourcePlugin)`
    work for the conformance test, but it ONLY checks method names —
    not signatures or async-ness. The conformance test in
    ``tests/test_download_plugin_conformance.py`` does the deeper
    signature check.
    """

    # ------------------------------------------------------------------
    # Configuration / lifecycle
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        """Return True iff this source has the credentials / settings
        it needs to function. Used by the orchestrator to skip
        unconfigured sources during hybrid fallback."""
        ...

    async def check_connection(self) -> bool:
        """Probe the source's API / endpoint. Return True if the
        source is reachable. May make a live network call."""
        ...

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        timeout: Optional[int] = None,
        progress_callback=None,
    ) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """Search the source for tracks (and albums where supported).

        Returns a tuple of (track_results, album_results). Either
        list may be empty. Sources that don't expose album-level
        search return ``[]`` as the second element.
        """
        ...

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    async def download(
        self,
        username: str,
        filename: str,
        file_size: int = 0,
    ) -> Optional[str]:
        """Kick off a download. Returns a download_id string the
        caller can poll via ``get_download_status``. Returns ``None``
        if the source can't / won't handle this download.

        ``username`` is the source-name string for streaming sources
        (e.g. ``'youtube'``, ``'tidal'``) and the actual slskd peer
        username for Soulseek. ``filename`` is source-specific —
        Soulseek file path, YouTube ``video_id||title``, Tidal /
        Qobuz ``track_id||display``, etc.
        """
        ...

    async def get_all_downloads(self) -> List[DownloadStatus]:
        """Return live status of all downloads currently tracked by
        this source. The orchestrator concatenates results from
        every plugin to build the global download list."""
        ...

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        """Return status for a single download or ``None`` if this
        source doesn't know about that download_id."""
        ...

    async def cancel_download(
        self,
        download_id: str,
        username: Optional[str] = None,
        remove: bool = False,
    ) -> bool:
        """Cancel an active download. ``remove=True`` also drops
        the row from the source's active-downloads tracking."""
        ...

    async def clear_all_completed_downloads(self) -> bool:
        """Drop completed downloads from active tracking. Sources
        that don't keep completed history return True with no-op."""
        ...
