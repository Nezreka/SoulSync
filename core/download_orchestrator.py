"""
Download Orchestrator
Routes downloads between Soulseek, YouTube, Tidal, Qobuz, HiFi, Deezer, and SoundCloud based on configuration.

Supports eight modes:
- Soulseek Only: Traditional behavior
- YouTube Only: YouTube-exclusive downloads
- Tidal Only: Tidal-exclusive downloads
- Qobuz Only: Qobuz-exclusive downloads
- HiFi Only: Free lossless downloads via public hifi-api instances
- Deezer Only: Deezer downloads via ARL authentication
- SoundCloud Only: Anonymous SoundCloud downloads (DJ mixes, removed/exclusive tracks)
- Hybrid: Try primary source first, fallback to others

The orchestrator dispatches through ``core.download_plugins.registry``
instead of hardcoded per-source ``[self.soulseek, self.youtube, ...]``
lists. External callers reach individual clients via the generic
``orchestrator.client('<name>')`` accessor (alias-aware), not direct
attribute access.
"""

import asyncio
from typing import List, Optional, Tuple
from pathlib import Path

from utils.logging_config import get_logger
from config.settings import config_manager
from core.download_engine import DownloadEngine
from core.download_plugins.registry import DownloadPluginRegistry, build_default_registry
from core.download_plugins.types import TrackResult, AlbumResult, DownloadStatus

logger = get_logger("download_orchestrator")


class DownloadOrchestrator:
    """
    Orchestrates downloads between Soulseek, YouTube, Tidal, Qobuz, and HiFi based on user preferences.

    Acts as a drop-in replacement for SoulseekClient by exposing the same async interface.
    Routes requests to the appropriate client(s) based on configured mode.
    """

    def __init__(self, registry: Optional[DownloadPluginRegistry] = None,
                 engine: Optional[DownloadEngine] = None):
        """Initialize orchestrator with a plugin registry. Each plugin
        is built and registered independently — one failing plugin
        doesn't prevent others from working. The ``registry`` arg
        exists so tests can inject a registry with mock plugins; in
        production callers leave it None and get the default.

        ``engine`` is the cross-source state owner. Phase B introduces
        it as a held reference; it isn't on any code path yet — Phase
        C/D/E/F migrate behavior into it incrementally.
        """
        self.registry = registry if registry is not None else build_default_registry()
        self.registry.initialize()
        self._init_failures = self.registry.init_failures

        # Engine — owns cross-source state, threading, search retry,
        # rate-limits, fallback. Built in subsequent phases. For Phase
        # B it's just an empty registry of plugins so future phases
        # can route through it without further orchestrator changes.
        self.engine = engine if engine is not None else DownloadEngine()
        for source_name, plugin in self.registry.all_plugins():
            spec = self.registry.get_spec(source_name)
            aliases = spec.aliases if spec else ()
            self.engine.register_plugin(source_name, plugin, aliases=aliases)

        if self._init_failures:
            logger.warning(f"Download clients failed to initialize: {', '.join(self._init_failures)}")

        # Load mode from config
        self.mode = config_manager.get('download_source.mode', 'soulseek')
        self.hybrid_primary = config_manager.get('download_source.hybrid_primary', 'soulseek')
        self.hybrid_secondary = config_manager.get('download_source.hybrid_secondary', 'youtube')
        self.hybrid_order = config_manager.get('download_source.hybrid_order', ['hifi', 'youtube', 'soulseek'])

        logger.info(f"Download Orchestrator initialized - Mode: {self.mode}")
        if self.mode == 'hybrid':
            logger.info(
                "Hybrid source order: order=%s primary=%s secondary=%s",
                " → ".join(self.hybrid_order) if self.hybrid_order else "default",
                self.hybrid_primary,
                self.hybrid_secondary,
            )

    def reload_settings(self):
        """Reload settings from config (call after settings change)"""
        self.mode = config_manager.get('download_source.mode', 'soulseek')
        self.hybrid_primary = config_manager.get('download_source.hybrid_primary', 'soulseek')
        self.hybrid_secondary = config_manager.get('download_source.hybrid_secondary', 'youtube')
        self.hybrid_order = config_manager.get('download_source.hybrid_order', ['hifi', 'youtube', 'soulseek'])

        # Reload underlying client configs (SLSKD URL, API key, etc.)
        soulseek = self.client('soulseek')
        if soulseek:
            soulseek._setup_client()
            logger.info("Soulseek client config reloaded")

        # Reconnect Deezer if ARL changed
        deezer_arl = config_manager.get('deezer_download.arl', '')
        deezer_dl = self.client('deezer_dl')
        if deezer_arl and deezer_dl:
            deezer_dl.reconnect(deezer_arl)
            deezer_dl._quality = config_manager.get('deezer_download.quality', 'flac')

        # Reload download path for all clients that cache it.
        # Soulseek owns the path config and is reloaded above; every
        # other source mirrors that path so files all land in one
        # tree. Sources without a `download_path` attribute (e.g.
        # Lidarr — pulls into Lidarr's own tree) silently skip.
        new_path = Path(config_manager.get('soulseek.download_path', './downloads'))
        for name, client in self.registry.all_plugins():
            if name == 'soulseek':
                continue
            if hasattr(client, 'download_path') and client.download_path != new_path:
                client.download_path = new_path
                client.download_path.mkdir(parents=True, exist_ok=True)
                # YouTube also caches path in yt-dlp opts
                if hasattr(client, 'download_opts') and 'outtmpl' in client.download_opts:
                    client.download_opts['outtmpl'] = str(new_path / '%(title)s.%(ext)s')
                logger.info(f"{type(client).__name__} download path updated to: {new_path}")

        logger.info(f"Download Orchestrator settings reloaded - Mode: {self.mode}")

    def client(self, name):
        """Generic accessor for a download source client by name.

        Cin's review feedback: external callers should reach into
        per-source clients via this method (``orch.client('hifi')``)
        instead of attribute access (``orch.hifi``). Resolves both
        canonical names (``deezer``) and legacy aliases (``deezer_dl``)
        via the registry. Returns None if the source isn't registered
        or failed to initialize.
        """
        return self.registry.get(name)

    # Internal alias kept for legacy callers inside this file.
    _client = client

    def configured_clients(self) -> dict:
        """Return ``{source_name: client}`` for every download source
        that's both initialized AND reports is_configured() == True.

        Replaces the legacy per-source iteration pattern Cin called
        out — `if hasattr(orch, 'soulseek') and orch.soulseek and
        orch.soulseek.is_configured(): download_clients['soulseek']
        = orch.soulseek` repeated for each source.
        """
        result = {}
        for name, client in self.registry.all_plugins():
            try:
                if not hasattr(client, 'is_configured') or client.is_configured():
                    result[name] = client
            except Exception as exc:
                logger.debug("%s is_configured raised: %s", name, exc)
        return result

    def reload_instances(self, source: str = None) -> bool:
        """Reload a source's instance config (e.g. HiFi instance list,
        Qobuz session restore). Generic dispatch — caller passes the
        source name instead of reaching for ``orch.hifi.reload_instances()``.

        When ``source`` is None, reloads every source that has a
        ``reload_instances`` method.
        """
        sources = [source] if source else list(self.registry.names())
        ok = True
        for name in sources:
            client = self.client(name)
            if client is None:
                continue
            if not hasattr(client, 'reload_instances'):
                continue
            try:
                client.reload_instances()
            except Exception as exc:
                logger.warning("%s reload_instances failed: %s", name, exc)
                ok = False
        return ok

    def is_configured(self) -> bool:
        """
        Check if orchestrator is configured and ready to use.

        Returns True if at least one download source is configured.
        """
        client = self._client(self.mode)
        if client:
            return client.is_configured()
        elif self.mode == 'hybrid':
            sources = self.hybrid_order if self.hybrid_order else [self.hybrid_primary, self.hybrid_secondary]
            return any(c.is_configured() for s in sources if (c := self._client(s)))
        return False

    def get_source_status(self) -> dict:
        """Return configured status for each download source.

        Keys preserve the legacy ``deezer_dl`` alias used by the
        frontend status indicators and per-source dispatch strings,
        so callers reading specific keys keep working unchanged.
        """
        status = {}
        for name in self.registry.names():
            client = self.registry.get(name)
            key = 'deezer_dl' if name == 'deezer' else name
            status[key] = client.is_configured() if client else False
        return status

    async def check_connection(self) -> bool:
        """
        Test if download sources are accessible.

        Returns True if the configured source(s) are reachable.
        """
        client = self._client(self.mode)
        if client and self.mode != 'hybrid':
            return await client.check_connection()
        elif self.mode == 'hybrid':
            sources_to_check = self.hybrid_order if self.hybrid_order else self.registry.names()
            results = {}
            for source in sources_to_check:
                client = self._client(source)
                if client:
                    try:
                        results[source] = await client.check_connection()
                    except Exception:
                        results[source] = False

            logger.info(
                "Hybrid connection check: %s",
                " | ".join(f"{source}={'ok' if ok else 'fail'}" for source, ok in results.items()),
            )

            return any(results.values())

        return False

    def _normalize_source_name(self, name: str) -> Optional[str]:
        """Convert a possibly-aliased source name (e.g. legacy
        ``'deezer_dl'``) to the canonical registry name (``'deezer'``).
        Returns None if the input matches neither a canonical name
        nor an alias.

        Cin's review caught a bug where legacy alias values from
        config (hybrid_order containing ``'deezer_dl'``) silently
        dropped Deezer from hybrid mode because the canonical-name
        membership check rejected the alias.
        """
        spec = self.registry.get_spec(name) if name else None
        return spec.name if spec else None

    def _resolve_source_chain(self) -> List[str]:
        """Order the configured sources for hybrid mode. Prefers
        ``hybrid_order`` config; falls back to legacy
        primary/secondary pair when no order set. Normalizes alias
        names through the registry so legacy ``deezer_dl`` config
        values resolve correctly to the canonical ``deezer`` plugin."""
        if self.hybrid_order:
            chain = []
            seen = set()
            for raw in self.hybrid_order:
                canonical = self._normalize_source_name(raw)
                if canonical and canonical not in seen:
                    chain.append(canonical)
                    seen.add(canonical)
            return chain
        primary = self._normalize_source_name(self.hybrid_primary) or 'soulseek'
        secondary = self._normalize_source_name(self.hybrid_secondary) or 'soulseek'
        if secondary == primary:
            secondary = next(
                (name for name in self.registry.names() if name != primary),
                'soulseek',
            )
        chain = [primary, secondary]
        if not chain:
            chain = ['soulseek']
        return chain

    async def search(self, query: str, timeout: int = None, progress_callback=None) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """Search for tracks using configured source(s). Single-source
        modes route directly; hybrid mode delegates to
        ``engine.search_with_fallback`` which tries the chain in order."""
        if self.mode != 'hybrid':
            client = self._client(self.mode)
            if not client:
                logger.error(f"{self.registry.display_name(self.mode)} client not available (failed to initialize)")
                return [], []
            logger.info(f"Searching {self.registry.display_name(self.mode)}: {query}")
            return await client.search(query, timeout, progress_callback)

        chain = self._resolve_source_chain()
        logger.info(f"Hybrid search ({' → '.join(chain)}): {query}")
        return await self.engine.search_with_fallback(query, chain, timeout, progress_callback)

    async def search_and_download_best(self, query: str, expected_track=None) -> Optional[str]:
        """
        Search and automatically download the best result.
        Supports Hybrid mode (uses configured source priority).

        Args:
            query: Search query string
            expected_track: Optional SpotifyTrack for match validation (title/artist/duration)

        Returns:
            Download ID (str) or None if failed
        """
        # 1. Search using configured mode/hybrid logic
        results = await self.search(query)

        # Unpack tuple (tracks, albums) - defensive handling
        if isinstance(results, tuple):
            tracks = results[0]
        else:
            tracks = results # Should not happen based on search() return type, but safe

        if not tracks:
            logger.warning(f"No results found for: {query}")
            return None

        # 2. Filter and validate results
        _streaming_sources = ('youtube', 'tidal', 'qobuz', 'hifi', 'deezer_dl', 'lidarr', 'soundcloud')
        is_streaming = tracks[0].username in _streaming_sources if tracks else False

        if is_streaming and expected_track:
            # Score streaming results against expected track metadata
            from core.matching_engine import MusicMatchingEngine
            me = MusicMatchingEngine()

            expected_title = expected_track.name if hasattr(expected_track, 'name') else ''
            expected_artists = expected_track.artists if hasattr(expected_track, 'artists') else []
            expected_duration = expected_track.duration_ms if hasattr(expected_track, 'duration_ms') else 0

            expected_title_lower = (expected_title or '').lower()
            _version_kw = ['remix', 'live', 'acoustic', 'instrumental', 'radio edit',
                           'extended', 'slowed', 'sped up', 'reverb', 'karaoke']
            expected_is_version = any(kw in expected_title_lower for kw in _version_kw)

            scored = []
            for r in tracks:
                confidence, _ = me.score_track_match(
                    source_title=expected_title,
                    source_artists=expected_artists,
                    source_duration_ms=expected_duration,
                    candidate_title=r.title or '',
                    candidate_artists=[r.artist] if r.artist else [],
                    candidate_duration_ms=r.duration or 0,
                )
                # Version penalty
                r_title_lower = (r.title or '').lower()
                if not expected_is_version:
                    for kw in _version_kw:
                        if kw in r_title_lower and kw not in expected_title_lower:
                            confidence *= 0.4
                            break

                if confidence >= 0.55:
                    r._match_confidence = confidence
                    scored.append(r)

            if scored:
                scored.sort(key=lambda x: x._match_confidence, reverse=True)
                filtered_results = scored
                logger.info(f"Streaming validation: {len(scored)}/{len(tracks)} passed "
                            f"(best: {scored[0]._match_confidence:.2f})")
            else:
                logger.warning(f"No streaming results passed validation for: {query}")
                return None
        elif is_streaming:
            filtered_results = tracks
        else:
            soulseek = self.client('soulseek')
            filtered_results = soulseek.filter_results_by_quality_preference(tracks) if soulseek else tracks

        if not filtered_results:
            logger.warning(f"No suitable quality results found for: {query}")
            return None

        # 3. Download the best match
        best_result = filtered_results[0]

        quality_info = f"{best_result.quality.upper()}"
        if best_result.bitrate:
            quality_info += f" {best_result.bitrate}kbps"

        logger.info(f"Downloading best match: {best_result.filename} ({quality_info}) from {best_result.username}")

        # Use orchestrator's download method to route correctly
        return await self.download(best_result.username, best_result.filename, best_result.size)

    async def download(self, username: str, filename: str, file_size: int = 0) -> Optional[str]:
        """
        Download a track using the appropriate client.

        Args:
            username: Source-name string for streaming sources
                (e.g. ``'youtube'``, ``'tidal'``, ``'deezer_dl'``)
                OR the actual slskd peer username for Soulseek.
            filename: Filename / video ID / track ID encoding (source-specific)
            file_size: File size estimate

        Returns:
            download_id: Unique download ID for tracking
        """
        # Streaming sources are dispatched by name match; anything
        # unrecognized falls through to Soulseek (peer username case).
        spec = self.registry.get_spec(username) if username else None
        if spec is not None and spec.name != 'soulseek':
            client = self.registry.get(spec.name)
            if not client:
                raise RuntimeError(f"{spec.display_name} download client not available (failed to initialize)")
            logger.info(f"Downloading from {spec.display_name}: {filename}")
            return await client.download(username, filename, file_size)

        soulseek = self.registry.get('soulseek')
        if not soulseek:
            raise RuntimeError("Soulseek client not available (failed to initialize)")
        logger.info(f"Downloading from Soulseek: {filename}")
        return await soulseek.download(username, filename, file_size)

    async def get_all_downloads(self) -> List[DownloadStatus]:
        """Aggregated view across every source. Delegates to the
        engine, which iterates registered plugins."""
        return await self.engine.get_all_downloads()

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        """Find a download by id across every source. Delegates to
        the engine."""
        return await self.engine.get_download_status(download_id)

    async def cancel_download(self, download_id: str, username: str = None, remove: bool = False) -> bool:
        """Cancel an active download. Delegates to the engine, which
        handles source-hint routing (streaming source name → direct
        plugin, unknown name → Soulseek as peer username, no hint →
        try every plugin)."""
        return await self.engine.cancel_download(download_id, username, remove)

    async def signal_download_completion(self, download_id: str, username: str, remove: bool = True) -> bool:
        """
        Signal that a download has completed (Soulseek only).

        Args:
            download_id: Download ID
            username: Username
            remove: Whether to remove from active downloads

        Returns:
            True if successful
        """
        # This is Soulseek-specific, so only call on Soulseek client
        soulseek = self.client('soulseek')
        if not soulseek:
            return False
        return await soulseek.signal_download_completion(download_id, username, remove)

    async def clear_all_completed_downloads(self) -> bool:
        """Clear completed downloads from every source. Delegates
        to the engine, which skips unconfigured plugins and treats
        per-plugin failures as False (not an exception)."""
        return await self.engine.clear_all_completed_downloads()

    # ===== Soulseek-specific methods (for backwards compatibility) =====
    # These are internal methods that some parts of the codebase use directly

    async def _make_request(self, method: str, endpoint: str, **kwargs):
        """
        Proxy to SoulseekClient._make_request for backwards compatibility.
        This is a Soulseek-specific internal method.

        Args:
            method: HTTP method
            endpoint: API endpoint
            **kwargs: Additional request parameters

        Returns:
            API response
        """
        soulseek = self.client('soulseek')
        if not soulseek:
            raise RuntimeError("Soulseek client not available (failed to initialize)")
        return await soulseek._make_request(method, endpoint, **kwargs)

    async def _make_direct_request(self, method: str, endpoint: str, **kwargs):
        """
        Proxy to SoulseekClient._make_direct_request for backwards compatibility.
        This is a Soulseek-specific internal method.

        Args:
            method: HTTP method
            endpoint: API endpoint
            **kwargs: Additional request parameters

        Returns:
            API response
        """
        soulseek = self.client('soulseek')
        if not soulseek:
            raise RuntimeError("Soulseek client not available (failed to initialize)")
        return await soulseek._make_direct_request(method, endpoint, **kwargs)

    async def clear_all_searches(self) -> bool:
        """
        Clear all searches (Soulseek-specific).

        Returns:
            True if successful
        """
        soulseek = self.client('soulseek')
        return await soulseek.clear_all_searches() if soulseek else True

    async def maintain_search_history_with_buffer(self, keep_searches: int = 50, trigger_threshold: int = 200) -> bool:
        """
        Maintain search history (Soulseek-specific).

        Args:
            keep_searches: Number of searches to keep
            trigger_threshold: Threshold to trigger cleanup

        Returns:
            True if successful
        """
        soulseek = self.client('soulseek')
        return await soulseek.maintain_search_history_with_buffer(keep_searches, trigger_threshold) if soulseek else True

    async def cancel_all_downloads(self) -> bool:
        """Cancel and remove all downloads from all sources.

        Note: YouTube is intentionally excluded from this loop in the
        legacy implementation — preserved here. (yt-dlp downloads
        run as detached subprocesses and don't share the
        ``cancel_all_downloads`` semantics the streaming sources
        use.) Sources without ``cancel_all_downloads`` fall back to
        ``clear_all_completed_downloads``.
        """
        ok = True
        for name, client in self.registry.all_plugins():
            if name == 'youtube':
                continue
            try:
                await client.cancel_all_downloads() if hasattr(client, 'cancel_all_downloads') else await client.clear_all_completed_downloads()
            except Exception:
                ok = False
        return ok


# ---------------------------------------------------------------------------
# Singleton accessor — mirrors Cin's metadata engine pattern
# (``get_metadata_engine()``). Callers that don't need a custom
# registry use this instead of instantiating DownloadOrchestrator
# directly. web_server.py constructs the singleton at startup and
# exposes it via the ``download_orchestrator`` global.
# ---------------------------------------------------------------------------

_default_orchestrator: Optional['DownloadOrchestrator'] = None


def get_download_orchestrator() -> 'DownloadOrchestrator':
    """Return (lazily creating) the process-wide DownloadOrchestrator
    singleton. Mirrors the ``get_metadata_engine()`` pattern Cin used
    for the metadata engine refactor."""
    global _default_orchestrator
    if _default_orchestrator is None:
        _default_orchestrator = DownloadOrchestrator()
    return _default_orchestrator


def set_download_orchestrator(orchestrator: 'DownloadOrchestrator') -> None:
    """Set the process-wide singleton. Used by web_server.py at boot
    to install the orchestrator it constructs as the default for
    callers that grab via ``get_download_orchestrator()``."""
    global _default_orchestrator
    _default_orchestrator = orchestrator
