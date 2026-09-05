"""TorrentDownloadPlugin — composes Prowlarr search + torrent client
adapter + archive_pipeline into a uniform download source.

Two flows:

**Per-track flow** (basic search, single-track wishlist) —
1. ``search(query)`` calls ``ProwlarrClient.search`` filtered to
   ``protocol='torrent'`` results, projects releases into
   ``TrackResult`` / ``AlbumResult`` shaped objects the existing
   search UI already understands. Encodes the indexer's
   ``downloadUrl`` (or magnet URI) into the filename so
   ``download()`` can recover it.
2. ``download(username, filename, ...)`` decodes the URL, asks the
   active torrent adapter (qBittorrent, Transmission, or Deluge per
   user's settings) to add it, spawns a background thread that
   polls the adapter for completion.
3. On completion the thread walks the adapter-reported save path
   via ``archive_pipeline.collect_audio_after_extraction`` and
   exposes the full audio-file list. Post-processing can then pick
   the requested track from a completed release instead of importing
   the first file blindly.

**Album-bundle flow** (album-context batch downloads — wired in
``core/downloads/master.py``) —
4. ``download_album_to_staging(album, artist, staging_dir)`` does
   ONE Prowlarr search for the whole release, picks the best
   torrent (prefers FLAC, decent seeders, reasonable size),
   downloads it, extracts archives if needed, copies every audio
   file into the staging directory. The existing per-track
   ``try_staging_match`` flow then finds + imports each track by
   fuzzy title match against the staged files. Per-track Prowlarr
   queries never fire — track titles like "Luther (with SZA)"
   would match album torrents like "GNX (2024) [FLAC]" at near-
   zero confidence and break the per-track dispatch.

Limitations:
- ``save_path`` is the torrent client's view of the disk. If
  SoulSync runs on a different host than qBit / Trans / Deluge,
  the post-processing pipeline can't see those files. The plugin
  works fine for the all-on-one-box case (most users); remote
  setups will need a future sync step (rclone / SMB / Docker
  bind mount).
- Track-level metadata isn't available until after download.
  Search results carry only the release title + indexer metadata;
  individual track names are populated when the matching pipeline
  walks the extracted audio files.
"""

from __future__ import annotations

import asyncio
import re
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.settings import config_manager
from core.archive_pipeline import AUDIO_EXTENSIONS, collect_audio_after_extraction
from core.download_plugins.album_bundle import (
    TransientMissCounter,
    copy_audio_files_atomically,
    get_poll_interval,
    get_poll_timeout,
    pick_best_album_release,
    profile_allowed_formats,
    profile_quality_targets,
    poll_album_download,
    resolve_reported_save_path,
)
from core.download_plugins.base import DownloadSourcePlugin
from core.download_plugins.candidate_store import get_candidate_store
from core.download_plugins.torrent_stall import (
    StallTracker,
    get_min_seeders,
    get_stall_action,
    get_stall_timeout,
)
from core.download_plugins.types import AlbumResult, DownloadStatus, TrackResult
from core.download_plugins.query_variants import indexer_query_variants
from core.prowlarr_client import (
    DEFAULT_MUSIC_CATEGORIES,
    ProwlarrClient,
    ProwlarrSearchError,
    ProwlarrSearchResult,
    canonical_protocol,
)
from core.torrent_clients import get_active_adapter as get_active_torrent_adapter
from utils.async_helpers import run_async
from utils.logging_config import get_logger

from core.quality.release_format import (
    audio_quality_from_release,
    audio_quality_from_release_title,
    evaluate_release,
    is_sample_release,
)

logger = get_logger("download_plugins.torrent")


# Separator used to encode the download URL inside the filename
# field. Same convention Lidarr / YouTube use for embedding their
# own opaque identifiers — ``<download_url>||<display>``.
_FILENAME_SEP = '||'

# Separator for the (download_url, magnet) pair a candidate token stands for.
# The token is opaque and never leaves the server, so the shape is private —
# but both halves are needed at grab time: we hand the client the .torrent
# fetched from the URL, and fall back to the magnet only if that fetch fails
# (#1139). A control character no URL can contain keeps the split unambiguous.
_CANDIDATE_SEP = '\x1f'


def _encode_candidate(download_url: str, magnet: Optional[str]) -> str:
    """Pack the pair a candidate token resolves to. No magnet (or the same
    string twice) packs to the bare URL, so nothing changes for the common
    single-link case and old tokens still decode."""
    if magnet and magnet != download_url:
        return f'{download_url}{_CANDIDATE_SEP}{magnet}'
    return download_url


def _decode_candidate(value: str) -> Tuple[str, Optional[str]]:
    """Unpack ``(download_url, fallback_magnet)``. A value with no separator
    is the whole URL and no fallback — which is also what every token minted
    before this existed looks like."""
    url, sep, magnet = (value or '').partition(_CANDIDATE_SEP)
    return url, (magnet or None) if sep else None

# Adapter states that count as the download being on-disk and
# safe to walk. ``seeding`` and ``completed`` both mean the
# bits are there; the user can pause seeding manually if they
# don't want to keep sharing.
_COMPLETE_STATES = frozenset(['seeding', 'completed'])

# Poll cadence / timeout — both pull from config via the shared
# album_bundle helpers so users can extend the deadline for slow
# trackers without editing source. Kept as module aliases so the
# per-track flow at the bottom of this file can still import them
# under the legacy names without re-reading config every loop.
_POLL_TIMEOUT_SECONDS = get_poll_timeout()
_POLL_INTERVAL_SECONDS = get_poll_interval()


class TorrentDownloadPlugin(DownloadSourcePlugin):
    """Torrent download source backed by Prowlarr + an active
    torrent client adapter."""

    def __init__(self) -> None:
        self._prowlarr = ProwlarrClient()
        # Track every download we've kicked off. Keyed by our own
        # uuid — NOT the adapter's hash — because the orchestrator
        # owns the lifecycle and we need a stable id even before
        # the adapter has assigned one.
        self.active_downloads: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.shutdown_check = None

    def set_shutdown_check(self, check_callable):
        self.shutdown_check = check_callable

    def reload_settings(self) -> None:
        self._prowlarr.reload_settings()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def is_configured(self) -> bool:
        if not self._prowlarr.is_configured():
            return False
        adapter = get_active_torrent_adapter()
        return bool(adapter and adapter.is_configured())

    async def check_connection(self) -> bool:
        if not self._prowlarr.is_configured():
            return False
        adapter = get_active_torrent_adapter()
        if not adapter or not adapter.is_configured():
            return False
        # Probe both sides. A torrent download is useless if either
        # the indexer or the downloader is unreachable.
        prowlarr_ok = await self._prowlarr.check_connection()
        if not prowlarr_ok:
            return False
        return await adapter.check_connection()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        timeout: Optional[int] = None,
        progress_callback=None,
    ) -> Tuple[List[TrackResult], List[AlbumResult]]:
        if not self._prowlarr.is_configured():
            return ([], [])
        results = await prowlarr_search_with_variants(
            self._prowlarr, query, "torrent", timeout=timeout,
        )
        return self._project_results(results)

    def _project_results(
        self, results: List[ProwlarrSearchResult]
    ) -> Tuple[List[TrackResult], List[AlbumResult]]:
        """Turn Prowlarr releases into TrackResult / AlbumResult
        shaped objects. One TrackResult + one AlbumResult per
        release — Prowlarr search hits are at the release level,
        not the track level, so we can't synthesise track listings
        without downloading the actual torrent."""
        tracks: List[TrackResult] = []
        albums: List[AlbumResult] = []
        for result in results:
            if result.protocol != 'torrent':
                continue
            # Prefer the .torrent URL over the magnet (#1139). A magnet gives
            # the client nothing but an info-hash, so it must find the swarm
            # itself — and a client without working DHT/PEX (or a release with
            # no live peers) parks on "downloading metadata" indefinitely. The
            # http link lets SoulSync fetch the real .torrent server-side and
            # push the file, which is what Sonarr/Radarr do. The magnet rides
            # along as the fallback for when that fetch fails.
            download_url = result.download_url or result.magnet_uri
            if not download_url:
                continue
            if is_sample_release(result.title, result.size):
                continue
            # The filename crosses to the browser in search responses and
            # comes back on grab. Indexer URLs can carry API keys / signed
            # params, so only an opaque server-side token travels (P0-03).
            # Ours: a token is bound to the result KIND it was minted for, so
            # a track candidate cannot be grabbed as an album. Upstream's
            # delta ported onto both: the indexer's categories travel with the
            # candidate, because `evaluate_release` judges on that evidence
            # before it falls back to parsing the title.
            candidate_metadata = {'categories': list(result.categories or [])}
            track_token = get_candidate_store().put(
                _encode_candidate(download_url, result.magnet_uri),
                result_kind="track",
                metadata=candidate_metadata,
            )
            album_token = get_candidate_store().put(
                _encode_candidate(download_url, result.magnet_uri),
                result_kind="album",
                metadata=candidate_metadata,
            )
            filename = f"{track_token}{_FILENAME_SEP}{result.title}"
            audio_quality = audio_quality_from_release(
                result.title,
                result.categories,
            )
            quality = audio_quality.format
            parsed_artist, parsed_title = _parse_release_title(result.title)
            tr = TrackResult(
                username='torrent',
                filename=filename,
                size=result.size,
                bitrate=audio_quality.bitrate,
                duration=None,
                quality=quality,
                # Torrent results don't have per-uploader slot / queue
                # data the way Soulseek does. Fill with neutral values
                # so the quality_score doesn't punish them artificially.
                free_upload_slots=max(1, result.seeders or 0),
                upload_speed=0,
                queue_length=0,
                sample_rate=audio_quality.sample_rate,
                bit_depth=audio_quality.bit_depth,
                # Pre-fill artist + title so TrackResult.__post_init__
                # doesn't auto-parse the filename — our filename starts
                # with the indexer download URL, which would otherwise
                # show up as "by download?apikey=..." in the UI. The
                # indexer (e.g. "NZBGeek") is metadata about WHERE the
                # result came from, never a substitute artist name — it
                # only ever goes into _source_metadata below.
                artist=parsed_artist or 'Unknown Artist',
                title=parsed_title or result.title,
                album=parsed_title or None,
                track_number=None,
                _source_metadata={
                    'indexer': result.indexer_name,
                    'indexer_id': result.indexer_id,
                    'seeders': result.seeders,
                    'leechers': result.leechers,
                    'grabs': result.grabs,
                    'publish_date': result.publish_date,
                    'protocol': 'torrent',
                    'release_title': result.title,
                    'categories': list(result.categories or []),
                },
            )
            tracks.append(tr)
            album_track = replace(
                tr, filename=f"{album_token}{_FILENAME_SEP}{result.title}")
            albums.append(AlbumResult(
                username='torrent',
                album_path=f"torrent/{result.guid}",
                album_title=parsed_title or result.title,
                artist=parsed_artist or None,
                track_count=1,    # unknown until download finishes
                total_size=result.size,
                tracks=[album_track],
                dominant_quality=quality,
                year=None,
            ))
        return tracks, albums

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    async def download(
        self,
        username: str,
        filename: str,
        file_size: int = 0,
        *,
        quality_profile_id=None,
    ) -> Optional[str]:
        if not self.is_configured():
            return None
        token, display_name = _decode_filename(filename)
        if not token:
            logger.error("Torrent download missing candidate token in filename: %r", filename)
            return None
        # Only a token from OUR candidate store is accepted — a raw URL from
        # the client is a trust-boundary violation, not a fallback (P0-03).
        candidate, candidate_metadata = get_candidate_store().resolve_with_metadata(token)
        if not candidate:
            logger.error("Torrent download: unknown or expired candidate for %r "
                         "— re-run the search", display_name)
            return None
        download_url, fallback_magnet = _decode_candidate(candidate)

        # #1149 stage 1: refuse on the TITLE before spawning anything.
        # Returning None is the engine's "declined, try the next source"
        # contract (core/download_engine/engine.py), so a strict-lossless user
        # falls through to a source that can satisfy them instead of queueing
        # a release the import guard will throw away.
        #
        # Only ever fires for a profile that names formats AND disables
        # fallback. A user who allows lossy has allowed_formats=None here and
        # sees no change whatsoever.
        allowed_formats = profile_allowed_formats(quality_profile_id)
        if allowed_formats:
            ok, why = evaluate_release(
                allowed_formats,
                display_name,
                categories=candidate_metadata.get('categories'),
            )
            if not ok:
                logger.info("Torrent declined %r on the quality profile: %s",
                            display_name, why)
                return None

        download_id = str(uuid.uuid4())
        with self._lock:
            self.active_downloads[download_id] = {
                'id': download_id,
                'filename': filename,
                'username': 'torrent',
                'display_name': display_name,
                'state': 'Initializing',
                'progress': 0.0,
                'size': file_size,
                'transferred': 0,
                'speed': 0,
                'file_path': None,
                'audio_files': [],
                'torrent_hash': None,
                'error': None,
            }

        thread = threading.Thread(
            target=self._download_thread,
            args=(download_id, download_url, display_name, fallback_magnet,
                  allowed_formats, candidate_metadata.get('categories')),
            daemon=True,
            name=f'torrent-dl-{download_id[:8]}',
        )
        thread.start()
        return download_id

    def _download_thread(self, download_id: str, download_url: str, display_name: str,
                         fallback_magnet: Optional[str] = None,
                         allowed_formats=None, categories=None) -> None:
        """Background worker: hand the URL to the active adapter,
        poll until done, then walk the resulting directory."""
        adapter = get_active_torrent_adapter()
        if adapter is None or not adapter.is_configured():
            self._mark_error(download_id, "No torrent client configured")
            return

        try:
            from core.torrent_clients.base import ReleaseRejected, add_torrent_smart

            # #1149 stage 2: the title cleared, now check what is actually in
            # the release. A title saying FLAC over a folder of MP3s is the
            # case a title-only check cannot catch.
            def _verify(names):
                if not allowed_formats:
                    return True, ''
                return evaluate_release(
                    allowed_formats,
                    display_name,
                    file_names=names,
                    categories=categories,
                )

            torrent_hash = run_async(add_torrent_smart(
                adapter, download_url, fallback_magnet=fallback_magnet,
                verify_files=_verify))
        except ReleaseRejected as rejected:
            logger.info("Torrent refused %r after reading its file list: %s",
                        display_name, rejected.reason)
            self._mark_error(
                download_id,
                f"Does not match the quality profile: {rejected.reason}")
            return
        except Exception as e:
            self._mark_error(download_id, f"add_torrent failed: {e}")
            return
        if not torrent_hash:
            self._mark_error(download_id, "Torrent client refused the URL")
            return

        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is not None:
                row['torrent_hash'] = torrent_hash
                row['state'] = 'InProgress, Downloading'

        deadline = time.monotonic() + _POLL_TIMEOUT_SECONDS
        last_save_path: Optional[str] = None
        # Tolerate transient None reads — covers network blips. Torrent
        # adapters don't have an SAB-style queue→history transition,
        # but the same tolerance keeps a one-off connection failure
        # from killing an otherwise-healthy download.
        misses = TransientMissCounter()
        # Stalled-torrent handling (noldevin): give up early on a torrent
        # making zero progress (dead magnet stuck on metadata, no seeders)
        # instead of holding this worker for the full album deadline. Read
        # per-download so a settings change applies to in-flight torrents.
        stall = StallTracker(get_stall_timeout())
        while time.monotonic() < deadline:
            if self.shutdown_check and self.shutdown_check():
                return
            try:
                status = run_async(adapter.get_status(torrent_hash))
            except Exception as e:
                logger.warning("Torrent poll error for %s: %s", torrent_hash, e)
                status = None

            if status is None:
                if misses.record_miss():
                    self._mark_error(
                        download_id,
                        f"Torrent disappeared from client (no status after {misses.threshold} polls)",
                    )
                    return
                time.sleep(_POLL_INTERVAL_SECONDS)
                continue

            misses.reset()

            with self._lock:
                row = self.active_downloads.get(download_id)
                if row is not None:
                    row['progress'] = status.progress * 100.0
                    row['transferred'] = status.downloaded
                    row['speed'] = status.download_speed
                    row['size'] = status.size or row.get('size', 0)
                    row['state'] = _adapter_state_to_display(status.state)
                    row['error'] = status.error
            if status.save_path:
                last_save_path = status.save_path

            if status.state in _COMPLETE_STATES:
                self._finalize_download(download_id, last_save_path,
                                        torrent_name=status.name)
                return
            if status.state == 'error':
                # Clean the dead torrent out of the client, or it's left orphaned
                # (active in qbit, untracked here) and re-grabbed as a duplicate.
                self._cleanup_torrent(torrent_hash, get_stall_action())
                self._mark_error(download_id, status.error or "Torrent client reported error")
                return

            if stall.is_stalled(status.downloaded, status.state, time.monotonic(),
                                size=status.size):
                self._handle_stalled(download_id, torrent_hash, get_stall_action())
                return

            time.sleep(_POLL_INTERVAL_SECONDS)

        # Deadline reached. One last status check closes the race where the
        # torrent completed during the final poll interval — finalize it instead
        # of deleting a just-finished download's files. Otherwise clean it out of
        # the client, or it sits orphaned in qbit (e.g. a metadata-stuck magnet
        # that escaped the stall timer) and gets re-grabbed as a duplicate.
        try:
            final = run_async(adapter.get_status(torrent_hash))
        except Exception:
            final = None
        if final is not None and final.state in _COMPLETE_STATES:
            self._finalize_download(download_id, final.save_path or last_save_path,
                                    torrent_name=final.name)
            return
        self._cleanup_torrent(torrent_hash, get_stall_action())
        self._mark_error(download_id, "Torrent download timed out")

    def _cleanup_torrent(self, torrent_hash: str, action: str) -> None:
        """Remove (abandon) or pause a dead/stalled/timed-out torrent in the
        client so it isn't left ORPHANED — active in qbit but no longer tracked
        here, which makes SoulSync re-grab the same dead torrent as a duplicate
        on the next attempt (noldevin). Best-effort: a client error is logged,
        not raised, so the download still fails cleanly."""
        adapter = get_active_torrent_adapter()
        if adapter is None or not torrent_hash:
            return
        try:
            if action == "pause":
                run_async(adapter.pause(torrent_hash))
            else:
                # delete_files: a stalled/failed torrent's partial data is junk
                # (often just a metadata stub) — don't leave it on disk.
                run_async(adapter.remove(torrent_hash, delete_files=True))
        except Exception as e:
            logger.warning("Torrent cleanup (%s) on %s failed: %s",
                           action, torrent_hash[:8] if torrent_hash else "?", e)

    def _handle_stalled(self, download_id: str, torrent_hash: str, action: str) -> None:
        """A torrent made no progress past the stall timeout. Abandon it
        (remove from client + delete its partial data) or pause it for the
        user, then fail the download so the worker frees up."""
        timeout_min = round(get_stall_timeout() / 60, 1)
        self._cleanup_torrent(torrent_hash, action)
        verb = "paused" if action == "pause" else "removed"
        self._mark_error(
            download_id,
            f"Torrent stalled (no progress for {timeout_min} min) — {verb}",
        )

    def _finalize_download(self, download_id: str, save_path: Optional[str],
                           torrent_name: Optional[str] = None) -> None:
        """Adapter said complete. Walk the directory + pick the
        first audio file as the canonical ``file_path``."""
        if not save_path:
            self._mark_error(download_id, "Torrent completed but no save_path reported")
            return
        # Resolve the client-reported path to one this process can read
        # (the client may report its own container's mount). The torrent's
        # NAME is the content check: a same-named directory that doesn't
        # contain this torrent is the wrong mount, not a resolution
        # (TheHomeGuy's '/downloads'). See ``resolve_reported_save_path``.
        local_path = resolve_reported_save_path(save_path, expect_name=torrent_name)
        if local_path != save_path:
            logger.info("Torrent %s: resolved client path %r -> %r",
                        download_id[:8], save_path, local_path)
        # save_path is the torrent's save DIRECTORY; the content lives at
        # <save_path>/<name>. Walking just the release keeps a shared
        # download root from donating the "first audio file" of some OTHER
        # torrent that happens to live there.
        walk_root = Path(local_path)
        if torrent_name and (walk_root / torrent_name).is_dir():
            # is_dir, not exists: a single-FILE torrent's name points at the
            # file itself, and the audio walker only walks directories.
            walk_root = walk_root / torrent_name
        try:
            audio_files = collect_audio_after_extraction(walk_root)
        except Exception as e:
            self._mark_error(download_id, f"Post-extract walk failed: {e}")
            return
        if not audio_files:
            suffix = f" (resolved: {local_path})" if local_path != save_path else ""
            self._mark_error(download_id, f"No audio files found in {save_path}{suffix}")
            return
        primary = audio_files[0]
        completed_hash = None
        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is not None:
                row['state'] = 'Completed, Succeeded'
                row['progress'] = 100.0
                row['file_path'] = str(primary)
                row['audio_files'] = [str(path) for path in audio_files]
                completed_hash = row.get('torrent_hash')
        logger.info("Torrent download complete: %s -> %s (%d audio files)",
                    download_id[:8], primary.name, len(audio_files))
        # Durably record this completed grab so the seeding sweep can manage the
        # tail (seed until ratio/time goals, then remove from the client). The
        # torrent_hash only lives in the in-memory row for the transfer, so this
        # is the one point it can be persisted. Best-effort: a DB hiccup here
        # must never affect the (already complete) download.
        if completed_hash:
            self._apply_seed_policy(completed_hash, torrent_name)

    def _mark_error(self, download_id: str, message: str) -> None:
        logger.error("Torrent download %s failed: %s", download_id[:8], message)
        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is not None:
                row['state'] = 'Completed, Errored'
                row['error'] = message

    def _apply_seed_policy(self, torrent_hash: str, title: Optional[str]) -> None:
        """Route a completed grab per the seed-enforcement mode. 'client' writes
        the ratio/time limit into the torrent client so it enforces (arr-style);
        'soulsync' (default) records the grab for the seeding sweep. If a client
        push fails/isn't supported, fall back to recording so the goal still
        applies. Best-effort — never raises into the completion path."""
        try:
            from core.settings import config_manager
            mode = config_manager.get('torrent_client.seed_mode', 'soulsync')
            if mode == 'client':
                ratio_goal = config_manager.get('torrent_client.seed_ratio_goal', 0)
                time_goal = config_manager.get('torrent_client.seed_time_goal_hours', 0)
                if ratio_goal or time_goal:
                    from core.torrent_clients import get_active_adapter as _adapter
                    from core.torrent_clients.share_limits import push_seed_goal
                    if push_seed_goal(_adapter(), torrent_hash, ratio_goal, time_goal):
                        return  # the client now enforces the goal itself
                    # push failed/unsupported → fall through to the sweep
        except Exception as e:
            logger.warning("Seed policy check failed for %s (%s); recording for sweep",
                           torrent_hash[:8] if torrent_hash else "?", e)
        self._record_seed_grab(torrent_hash, title)

    def _record_seed_grab(self, torrent_hash: str, title: Optional[str]) -> None:
        """Persist a completed torrent grab for the seeding sweep. Best-effort:
        never raises into the completion path."""
        try:
            from database.music_database import get_database
            from core.settings import config_manager
            category = config_manager.get('torrent_client.category', 'soulsync') or 'soulsync'
            get_database().record_torrent_seed_grab(torrent_hash, title, category)
        except Exception as e:
            logger.warning("Could not record torrent seed grab %s: %s",
                           torrent_hash[:8] if torrent_hash else "?", e)

    # ------------------------------------------------------------------
    # Status / lifecycle
    # ------------------------------------------------------------------

    async def get_all_downloads(self) -> List[DownloadStatus]:
        with self._lock:
            rows = list(self.active_downloads.values())
        return [_row_to_status(r) for r in rows]

    async def get_download_status(self, download_id: str) -> Optional[DownloadStatus]:
        with self._lock:
            row = self.active_downloads.get(download_id)
            if row is None:
                return None
            return _row_to_status(row)

    async def cancel_download(
        self,
        download_id: str,
        username: Optional[str] = None,
        remove: bool = False,
    ) -> bool:
        adapter = get_active_torrent_adapter()
        with self._lock:
            row = self.active_downloads.get(download_id)
            torrent_hash = row.get('torrent_hash') if row else None
        if adapter and torrent_hash:
            try:
                await adapter.remove(torrent_hash, delete_files=remove)
            except Exception as e:
                logger.warning("Torrent cancel via adapter failed: %s", e)
        with self._lock:
            if remove:
                self.active_downloads.pop(download_id, None)
            else:
                row = self.active_downloads.get(download_id)
                if row is not None:
                    row['state'] = 'Cancelled'
        return True

    async def clear_all_completed_downloads(self) -> bool:
        with self._lock:
            for did in list(self.active_downloads.keys()):
                state = self.active_downloads[did].get('state', '')
                if state.startswith('Completed') or state == 'Cancelled':
                    self.active_downloads.pop(did, None)
        return True

    # ------------------------------------------------------------------
    # Album-bundle flow
    # ------------------------------------------------------------------

    def download_album_to_staging(
        self,
        album_name: str,
        artist_name: str,
        staging_dir: str,
        progress_callback=None,
        quality_profile_id=None,
        expected_duration_seconds=None,
    ) -> Dict[str, Any]:
        """One-shot album download: search Prowlarr for the whole
        release, pick the best torrent, fetch it, extract if needed,
        copy every audio file into ``staging_dir`` so the existing
        ``try_staging_match`` flow can hand each track off to the
        post-processing pipeline.

        ``progress_callback`` is called with a dict on each state
        change so the batch UI can show download progress without
        waiting for the whole thing.

        Returns ``{'success': bool, 'files': [paths], 'error': str|None}``.
        """
        result: Dict[str, Any] = {'success': False, 'files': [], 'error': None}
        if not self.is_configured():
            result['error'] = 'Torrent source not configured'
            return result

        adapter = get_active_torrent_adapter()
        if adapter is None or not adapter.is_configured():
            result['error'] = 'No active torrent client'
            return result

        def _emit(state: str, **extra) -> None:
            if progress_callback:
                payload = {'state': state, **extra}
                try:
                    progress_callback(payload)
                except Exception as cb_exc:
                    logger.debug("[Torrent album] progress callback failed: %s", cb_exc)

        # Phase 1: search Prowlarr for the album.
        query = f"{artist_name} {album_name}".strip()
        _emit('searching', query=query)
        try:
            search_results = run_async(prowlarr_search_with_variants(
                self._prowlarr, query, 'torrent',
            ))
        except Exception as e:
            result['error'] = f'Prowlarr search failed: {e}'
            return result

        candidates = [r for r in search_results
                      if r.protocol == 'torrent' and (r.magnet_uri or r.download_url)]
        if not candidates:
            # Album isn't available on this source. Mark the failure as
            # fallback-eligible so the dispatch returns to the per-track flow
            # instead of hard-failing the batch — in hybrid mode that lets the
            # next configured source take over. Without this flag a torrent-first
            # hybrid would get stuck at "searching" forever when Prowlarr
            # returns nothing, never trying the other sources.
            result['error'] = f'No torrent results found for "{query}"'
            result['fallback'] = True
            return result

        # narrate the selection step (#1156) — the gap between 'searching' and
        # 'queued' used to be silent for however long scoring took
        _emit('selecting', count=len(candidates), query=query)

        # min_seeders keeps a provably-dead swarm out of the queue entirely
        # (#1139) — picking the "most seeded" of a field where everything is on
        # zero still queues something nobody is serving.
        # #1149: the profile's formats are a VETO here, not a ranking nudge.
        # Quality was the second sort key after seeders, so a well-seeded MP3
        # rip beat a FLAC rip with fewer seeders however the profile was set.
        # No new setting: a ladder of FLAC targets with fallback disabled
        # already means "FLAC only" to the import guard; this side just never
        # asked.
        allowed_formats = profile_allowed_formats(quality_profile_id)
        quality_targets, fallback_enabled = profile_quality_targets(quality_profile_id)
        picked = pick_best_album_release(
            candidates, _guess_quality_from_title, album_name=album_name,
            min_seeders=get_min_seeders(),
            allowed_formats=allowed_formats,
            quality_targets=quality_targets,
            fallback_enabled=fallback_enabled,
            expected_duration_seconds=expected_duration_seconds,
        )
        if picked is None:
            # No candidate matched the requested album, or none had a live
            # swarm. Fall back to the per-track flow rather than downloading a
            # wrong album (#730) or queueing a dead one (#1139) — per-track
            # searches each track individually.
            # Say WHICH gate refused, so the user can act on it rather than
            # re-running the same search and getting the same silence.
            if allowed_formats:
                result['error'] = (
                    'No torrent candidate matched the requested album in '
                    f"{'/'.join(sorted(allowed_formats)).upper()} "
                    '(quality profile allows no other format)')
            else:
                result['error'] = 'No torrent candidate matched the requested album'
            result['fallback'] = True
            return result

        # Prefer the .torrent URL over the magnet (#1139): a magnet hands the
        # client only an info-hash and leaves it to find the swarm, which is
        # exactly the state that parks on "downloading metadata" forever. The
        # http link lets us fetch the real .torrent here and push the file,
        # with the magnet kept as the fallback if that fetch fails.
        download_url = picked.download_url or picked.magnet_uri
        logger.info("[Torrent album] Picked '%s' (size=%.1fMB seeders=%s indexer=%s)",
                    picked.title, picked.size / 1_048_576, picked.seeders, picked.indexer_name)
        _emit('queued', release=picked.title, size=picked.size, seeders=picked.seeders)

        # Phase 2: hand to adapter. Fetch the .torrent server-side first —
        # the client often can't reach Prowlarr itself (split containers).
        try:
            from core.torrent_clients.base import ReleaseRejected, add_torrent_smart

            # #1149: the title got us this far; the FILE LIST is the evidence.
            # This runs inside add_torrent_smart because that is where the
            # fetched payload already lives, so verification costs no extra
            # request and happens strictly before the client is handed
            # anything.
            def _verify(names):
                if not allowed_formats:
                    return True, ''
                return evaluate_release(
                    allowed_formats,
                    picked.title,
                    file_names=names,
                    categories=getattr(picked, 'categories', None),
                )

            torrent_id = run_async(add_torrent_smart(
                adapter, download_url, fallback_magnet=picked.magnet_uri,
                verify_files=_verify))
        except ReleaseRejected as rejected:
            logger.info("[Torrent album] Refused '%s' after reading its file list: %s",
                        picked.title, rejected.reason)
            result['error'] = f'Release does not match the quality profile: {rejected.reason}'
            # Fallback-eligible: the next source may have a release that does.
            result['fallback'] = True
            return result
        except Exception as e:
            result['error'] = f'Torrent client refused the release: {e}'
            return result
        if not torrent_id:
            result['error'] = 'Torrent client refused the release'
            return result

        # Phase 3: poll until complete. The lifted helper handles
        # transient missing windows (uncommon for torrents — adapters
        # don't have a queue→history transition like SAB — but the
        # same path also catches network blips that would otherwise
        # take down the whole download) and always emits a terminal
        # 'failed' state on failure paths so the UI doesn't freeze on
        # the last 'downloading' emit.
        _emit('downloading', release=picked.title)
        # The album flow had no stall detection at all (#1139): the per-track
        # path tracked it, this one just rode the full poll deadline, so a
        # magnet stuck fetching metadata held the batch for hours across
        # thousands of polls while torrent_stall_timeout_seconds sat unread.
        # Same tracker, same settings, injected into the shared loop.
        stall = StallTracker(get_stall_timeout())

        def _stalled(status, now) -> bool:
            return stall.is_stalled(
                getattr(status, 'downloaded', 0), getattr(status, 'state', ''),
                now, size=getattr(status, 'size', None),
            )

        cleaned_up = False

        def _on_stall() -> str:
            nonlocal cleaned_up
            action = get_stall_action()
            self._cleanup_torrent(torrent_id, action)
            cleaned_up = True
            verb = 'paused' if action == 'pause' else 'removed'
            return (f'Torrent stalled (no progress for '
                    f'{round(get_stall_timeout() / 60, 1)} min) — {verb}')

        save_path = poll_album_download(
            get_status=lambda: run_async(adapter.get_status(torrent_id)),
            title=picked.title,
            emit=_emit,
            # Torrent adapters flip to 'seeding' on completion (files
            # on disk, share-ratio progress) — both states count as
            # terminal success.
            complete_states=frozenset(['seeding', 'completed']),
            # qBit / Transmission / Deluge surface a real 'error'
            # state when the torrent itself errors (tracker, missing
            # files, etc.). That's distinct from the unmapped-state
            # default-'error' fallback the helper treats as transient.
            failed_states=frozenset(['error']),
            is_shutdown=self.shutdown_check,
            # P2-21: remap the client-container path before the incomplete_path
            # stability check, not just on the final save_path — otherwise a
            # split-container mount never resolves to a readable local path and
            # the fallback can't stabilize. expect_name isn't known this early
            # (fetched below only after the poll returns), so this is the
            # weaker no-expect_name resolution; the final walk_root re-resolves
            # with expect_name for the stricter content-checked path.
            resolve_path=resolve_reported_save_path,
            stall_check=_stalled,
            on_stall=_on_stall,
            log_prefix='[Torrent album]',
        )
        if save_path is None:
            # poll_album_download already emitted terminal 'failed'.
            #
            # Clean the dead grab out of the client. The per-track path has
            # always done this; the album path did not, so a stalled or
            # timed-out album torrent stayed active in qBittorrent — untracked
            # here, and re-grabbed as a duplicate on the next attempt. _on_stall
            # already handled the stall case, so this covers timeout/vanished/
            # client-error — and the flag keeps a stall from being cleaned twice
            # (harmless, but the second remove logs a warning about a hash the
            # client has correctly forgotten).
            if not cleaned_up:
                self._cleanup_torrent(torrent_id, get_stall_action())
            # Fallback-eligible: the album source could not deliver, so the
            # batch should return to the per-track flow (and, in hybrid mode,
            # the next configured source) instead of hard-failing. Without
            # this a single dead swarm killed the whole batch.
            result['error'] = 'Torrent download failed or timed out'
            result['fallback'] = True
            return result

        # Phase 4: extract + walk + copy to staging.
        _emit('staging', release=picked.title)
        # Resolve the client-reported path to one this process can read,
        # content-checked against the torrent's on-disk NAME (the release
        # folder) so an existing-but-wrong mount can't win, and walk just
        # that release so a shared root can't donate another torrent's files.
        torrent_name = None
        content_path = None
        try:
            _final_status = run_async(adapter.get_status(torrent_id))
            if _final_status is not None:
                torrent_name = _final_status.name
                content_path = getattr(_final_status, 'content_path', None)
        except Exception:   # noqa: BLE001 - the name is an assist, not a requirement
            torrent_name = None

        # content_path is qBittorrent's absolute path to THIS torrent's own
        # file or folder, and it is the reliable answer to "which of the
        # things in the shared download dir is mine" — the release's on-disk
        # folder often differs from the torrent's display NAME, which is what
        # the save_path + name walk below assumes. The video side has resolved
        # completed torrents this way for a while; the music album flow never
        # adopted it, and that is the "completed and seeding in qBittorrent,
        # but SoulSync says No audio files found" half of #1139.
        walk_root = None
        single_file = None
        if content_path:
            resolved_content = resolve_reported_save_path(content_path)
            candidate = Path(resolved_content)
            if candidate.is_dir():
                walk_root = candidate
                logger.info("[Torrent album] Using client content_path %r -> %r",
                            content_path, str(walk_root))
            elif candidate.is_file() and candidate.suffix.lower() in AUDIO_EXTENSIONS:
                # A single-FILE torrent. Deliberately NOT walking its parent:
                # for these the parent is usually the shared download root, and
                # walking it would stage every other torrent's audio too. We
                # already know exactly which file is ours.
                single_file = candidate
                logger.info("[Torrent album] Single-file torrent via content_path -> %r",
                            str(candidate))
            # A single non-audio file (an archive) falls through to the
            # save_path walk below, which extracts before collecting.

        local_path = resolve_reported_save_path(save_path, expect_name=torrent_name)
        if local_path != save_path:
            logger.info("[Torrent album] Resolved client path %r -> %r", save_path, local_path)
        if walk_root is None:
            walk_root = Path(local_path)
            if torrent_name and (walk_root / torrent_name).is_dir():
                # is_dir, not exists: a single-FILE torrent's name points at the
                # file itself, and the audio walker only walks directories.
                walk_root = walk_root / torrent_name
        try:
            # single_file is set only when content_path named ONE audio file:
            # we already know exactly which file is ours, and walking its
            # parent (usually the shared download root) would stage every
            # other torrent's audio with it.
            audio_files = ([single_file] if single_file
                           else collect_audio_after_extraction(walk_root))
        except Exception as e:
            result['error'] = f'Failed to walk audio files: {e}'
            result['fallback'] = True
            return result
        if not audio_files:
            # Say WHICH of the two failures this is. "No audio files found"
            # reads identically whether the release genuinely has none or the
            # path simply isn't reachable from this process — and the second is
            # a remote-path-mapping problem the user can actually fix.
            result['error'] = _no_audio_diagnosis(save_path, walk_root)
            # The bits may well be on disk, so per-track can still succeed
            # where this bundle could not.
            result['fallback'] = True
            return result

        copied = copy_audio_files_atomically(audio_files, Path(staging_dir))
        if not copied:
            result['error'] = 'No audio files copied to staging'
            result['fallback'] = True
            return result
        logger.info("[Torrent album] Staged %d audio files for '%s'", len(copied), album_name)
        _emit('staged', count=len(copied))
        result['success'] = True
        result['files'] = copied
        return result



# ---------------------------------------------------------------------------
# Module-level helpers (pure functions — easy to unit-test)
# ---------------------------------------------------------------------------


def _no_audio_diagnosis(reported_path: str, walk_root) -> str:
    """Explain WHY an apparently-successful torrent staged nothing (#1139).

    Both failures used to print the same "No audio files found in <path>":

    - the path is not reachable from this process — the classic arr-stack
      remote-path mismatch, where the client reports its own container's
      mount and SoulSync sees the same files somewhere else (or not at all).
      Fixable by the user, via ``download_source.path_mappings``.
    - the path IS readable and simply holds no audio — a video/scene release,
      a still-extracting archive, a mis-picked candidate. Nothing to map.

    Naming which one it is turns an unactionable message into an instruction.
    Both paths are the user's own, on their own machine, in their own log.
    """
    root = Path(walk_root)
    try:
        reachable = root.is_dir()
    except OSError:
        reachable = False
    where = f'{reported_path}' + (f' (resolved: {root})' if str(root) != reported_path else '')
    if not reachable:
        return (
            f'Torrent finished but SoulSync cannot read {where} — that path exists on the '
            f'torrent client, not here. Add a mapping under Settings → Downloads '
            f'(download_source.path_mappings) pointing the client\'s completed-download '
            f'directory at the one SoulSync sees.'
        )
    return f'No audio files found in {where} (the folder is readable but holds no audio)'


def _decode_filename(filename: str) -> Tuple[Optional[str], str]:
    """Pull the encoded download URL out of the ``filename`` string.
    Returns ``(url, display_name)``. ``url`` is None when the string
    has no separator."""
    if not filename or _FILENAME_SEP not in filename:
        return (None, filename or '')
    url, display = filename.split(_FILENAME_SEP, 1)
    return (url, display)


def _parse_release_title(title: str) -> Tuple[str, str]:
    """Split a release title into ``(artist, title)`` using the
    ``Artist - Title`` / ``Artist - Album`` convention almost every
    indexer follows. Returns ``('', title)`` when no dash is found.

    Without this, ``TrackResult.__post_init__`` runs the bare
    filename through ``parse_filename_metadata`` — and our filename
    starts with the indexer's download URL, so the auto-parser
    extracts garbage like ``download?apikey=...`` as the artist
    and shows it in the search-result UI's "by" line. Pre-filling
    the artist field short-circuits the auto-parse.
    """
    if not title:
        return ('', '')
    # Strip common quality / format tags so the dash split doesn't
    # eat them — "Artist - Album [FLAC] (2020)" → "Artist", "Album".
    cleaned = re.sub(r'\s*[\[\(][^\]\)]*[\]\)]\s*$', '', title.strip())
    # Look for the FIRST " - " (or "-" surrounded by content). Some
    # release titles have multiple dashes (subtitle dashes); the
    # first split is the artist/work boundary.
    parts = re.split(r'\s+-\s+|\s+-(?=\S)|(?<=\S)-\s+', cleaned, maxsplit=1)
    if len(parts) == 2:
        artist = parts[0].strip()
        rest = parts[1].strip()
        # Reject obvious non-artist prefixes (URLs, hashes, single
        # punctuation) so we don't propagate garbage.
        if artist and not re.match(r'^https?:|^[a-f0-9]{32,}$', artist):
            return (artist, rest or cleaned)
    return ('', cleaned)


def _guess_quality_from_title(title: str) -> str:
    """Compatibility wrapper around the shared rich title parser.

    Unknown titles stay ``unknown``.  Calling them MP3 made the UI and quality
    profile believe Prowlarr supplied information that its API never sent.
    """
    return audio_quality_from_release_title(title).format


async def prowlarr_search_with_variants(
    prowlarr: ProwlarrClient,
    query: str,
    protocol: str,
    *,
    timeout: Optional[int] = None,
    categories=DEFAULT_MUSIC_CATEGORIES,
) -> List[ProwlarrSearchResult]:
    """One Prowlarr search per plugin, with the dd28-02/05/07/37 handling.

    * the caller's ``timeout`` actually reaches Prowlarr (dd28-05) and falls
      back to the user setting rather than a hard 15s constant (dd28-02);
    * a failed search raises instead of masquerading as zero hits (dd28-02),
      so the per-source UI can report it — the hybrid chain already swallows
      per-source exceptions, so its behaviour is unchanged;
    * a query that yields nothing is retried with progressively relaxed
      variants before giving up (dd28-07);
    * the shared indexer allowlist is narrowed to this protocol (dd28-37).
    """
    variants = indexer_query_variants(query)
    if not variants:
        return []
    # Canonical spelling, matching what the client stores on every parsed
    # result — this is the value the `usable` comparison below is made against
    # (`indexer_ids_for_protocol` normalizes its own argument).
    protocol = canonical_protocol(protocol)
    indexer_ids = prowlarr.indexer_ids_for_protocol(
        _parse_indexer_id_filter(), protocol,
    )
    # #1151: fan the search out per indexer. Prowlarr answers ONE aggregated
    # request for every indexer at once, so a single slow or unreachable one
    # holds the response past the read timeout — and a timed-out request
    # returns nothing, throwing away results the responsive indexers had
    # already produced. Resolving the concrete ids lets each be its own
    # request, so a failure costs only its own slot.
    #
    # An empty list means the ids could not be resolved (Prowlarr unreachable,
    # or an older version); that falls through to the single aggregated
    # request, which is exactly today's behaviour.
    try:
        fan_out_ids = await prowlarr.resolve_search_indexers(indexer_ids, protocol)
        # ``search_each_indexer`` dedupes too, but the failure accounting below
        # compares counts. Keep both sides on the same concrete set so an
        # allowlist like ``1,1`` cannot disguise that indexer 1 was the only
        # indexer and it failed.
        fan_out_ids = list(dict.fromkeys(int(value) for value in fan_out_ids))
    except Exception as exc:                            # noqa: BLE001
        # Resolving the ids is an optimisation, never a precondition. A failure
        # here must not become a failure to search at all.
        logger.debug("Prowlarr indexer resolution failed, using one request: %s", exc)
        fan_out_ids = []

    first_error: Optional[Exception] = None
    for attempt, variant in enumerate(variants):
        try:
            if fan_out_ids:
                results, failures = await prowlarr.search_each_indexer(
                    variant,
                    fan_out_ids,
                    categories=categories,
                    timeout=timeout,
                )
                if failures and len(failures) >= len(fan_out_ids) and not results:
                    # EVERY indexer failed. Raising preserves dd28-02: a
                    # transport failure must not masquerade as zero hits. A
                    # responsive indexer returning zero is still a successful
                    # search; one other broken indexer must not turn that
                    # honest empty answer into a failure for the whole wave.
                    raise ProwlarrSearchError('; '.join(failures))
                if failures:
                    # Some worked. Name the ones that did not — the aggregated
                    # request could never tell you which indexer was the
                    # problem, which the report asks for.
                    logger.warning(
                        "Prowlarr %s search: %d/%d indexer(s) failed; responsive "
                        "indexers returned %d result(s) — %s",
                        protocol, len(failures), len(fan_out_ids), len(results),
                        '; '.join(failures),
                    )
            else:
                results = await prowlarr.search(
                    variant,
                    categories=categories,
                    indexer_ids=indexer_ids,
                    timeout=timeout,
                )
        except ProwlarrSearchError as e:
            # A transport failure is not evidence that the relaxed variants
            # would fail too, but it IS the thing the user needs told if
            # nothing works out. Keep trying, remember the first cause.
            logger.warning("Prowlarr %s search failed for %r: %s", protocol, variant, e)
            first_error = first_error or e
            continue
        except Exception as e:  # noqa: BLE001
            logger.error("Prowlarr %s search errored for %r: %s", protocol, variant, e)
            first_error = first_error or e
            continue
        usable = [r for r in results if r.protocol == protocol]
        if usable:
            if attempt:
                logger.info(
                    "Prowlarr %s search matched on relaxed query %r (original %r)",
                    protocol, variant, variants[0],
                )
            # Only this protocol's releases: a mixed answer's foreign entries
            # can never be grabbed by the caller (every one re-filters before
            # projecting), and returning them made the retry decision and the
            # return value disagree about what "usable" meant. Both sides of
            # this comparison are canonical (`canonical_protocol` on the way
            # in, `_parse_result` on every release), so what survives here also
            # survives the callers' case-sensitive filters.
            return usable
    if first_error is not None:
        raise ProwlarrSearchError(str(first_error)) from first_error
    return []


def _parse_indexer_id_filter() -> List[int]:
    """Read the comma-separated indexer-ID allowlist from config.
    Empty list = search every enabled indexer."""
    raw = (config_manager.get('prowlarr.indexer_ids', '') or '').strip()
    if not raw:
        return []
    out: List[int] = []
    for chunk in raw.split(','):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.append(int(chunk))
        except ValueError:
            continue
    return out


def _adapter_state_to_display(state: str) -> str:
    """Translate the adapter-uniform state strings into the
    ``'InProgress, Downloading'`` / ``'Completed, Succeeded'``
    style the existing UI expects (matches Soulseek + Lidarr)."""
    mapping = {
        'queued':      'Queued',
        'downloading': 'InProgress, Downloading',
        'stalled':     'InProgress, Stalled',
        'seeding':     'Completed, Succeeded',
        'completed':   'Completed, Succeeded',
        'paused':      'Paused',
        'error':       'Completed, Errored',
    }
    return mapping.get(state, state.title())


def _row_to_status(row: Dict[str, Any]) -> DownloadStatus:
    return DownloadStatus(
        id=row['id'],
        filename=row['filename'],
        username=row['username'],
        state=row.get('state', 'Unknown'),
        progress=float(row.get('progress', 0.0)),
        size=int(row.get('size', 0)),
        transferred=int(row.get('transferred', 0)),
        speed=int(row.get('speed', 0)),
        time_remaining=None,
        file_path=row.get('file_path'),
        audio_files=row.get('audio_files') or None,
    )
