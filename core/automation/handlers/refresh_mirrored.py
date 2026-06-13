"""Automation handler: ``refresh_mirrored`` action.

Re-pulls track lists from each mirrored playlist's source via the
unified ``PlaylistSourceRegistry`` (Phase 1 of the Discover-to-Sync
unification). The pre-extraction handler had ~190 lines of per-source
if/elif branches; this version delegates to the adapter for each
source, leaving the handler responsible only for:

- filtering sources that can't be refreshed (``file``, ``beatport``),
- extracting upstream URLs from the stored ``description`` for URL-
  backed sources (``spotify_public``, ``youtube``),
- the Spotify-public → authenticated-Spotify fallback (uses the
  ``spotify`` adapter when the user is signed in so the mirror keeps
  album art),
- the Tidal-not-authenticated skip log type (vs error),
- preserving existing per-track ``extra_data`` on tracks that survive
  the refresh, and
- emitting the ``playlist_changed`` automation event when the track
  set actually shifts.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.automation.deps import AutomationDeps
from core.playlists.source_refs import require_refresh_url
from core.playlists.sources import PlaylistDetail, to_mirror_track_dict
from core.playlists.sources.base import (
    SOURCE_SPOTIFY,
    SOURCE_SPOTIFY_PUBLIC,
    SOURCE_TIDAL,
    SOURCE_YOUTUBE,
)


# Sources that store the upstream URL in ``description`` (because their
# ``source_playlist_id`` is a deterministic hash, not the native ID).
# The refresh path has to recover the URL before calling the adapter.
_URL_BACKED_SOURCES = {SOURCE_SPOTIFY_PUBLIC, SOURCE_YOUTUBE}


def auto_refresh_mirrored(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Refresh mirrored playlist(s) from source.

    Returns ``{'status': 'completed', 'refreshed': '<int>',
    'errors': '<int>'}`` on success (counts stringified to match the
    automation engine's stat-rendering convention).
    """
    db = deps.get_database()
    playlist_id = config.get('playlist_id')
    refresh_all = config.get('all', False)
    auto_id = config.get('_automation_id')
    # Pipeline runs (``run_mirrored_playlist_pipeline``) set this flag
    # because Phase 2 of the pipeline already runs the playlist
    # discovery worker — the same matching engine ``_maybe_discover``
    # would call here. Running both means LB tracks discover twice
    # AND the refresh-side discovery blocks 5+ minutes with no
    # progress emission, leaving the UI stuck on "Refreshing:" until
    # the loop returns. Standalone callers (Sync page, registration
    # action) leave it False so LB tracks still get matched_data on
    # refresh.
    skip_discovery = bool(config.get('skip_discovery', False))

    if refresh_all:
        playlists = db.get_mirrored_playlists()
    elif playlist_id:
        p = db.get_mirrored_playlist(int(playlist_id))
        playlists = [p] if p else []
    else:
        return {'status': 'error', 'reason': 'No playlist specified'}

    # Filter out sources that can't be refreshed (no external API).
    playlists = [pl for pl in playlists if pl.get('source', '') not in ('file', 'beatport')]

    refreshed = 0
    errors: List[str] = []
    for idx, pl in enumerate(playlists):
        try:
            source = pl.get('source', '')
            source_id = pl.get('source_playlist_id', '')
            deps.update_progress(
                auto_id,
                progress=(idx / max(1, len(playlists))) * 100,
                phase=f'Refreshing: "{pl.get("name", "")}"',
                current_item=pl.get('name', ''),
            )

            detail = _fetch_detail(source, source_id, pl, deps, auto_id)
            if detail is None:
                # _fetch_detail already logged the specific failure;
                # mark the playlist as a generic refresh error so the
                # automation result tally matches the legacy handler.
                errors.append(f"{pl.get('name', '?')}: no tracks returned from source")
                deps.update_progress(
                    auto_id,
                    log_line=f'Refresh failed: "{pl.get("name", "")}" - no tracks returned from source',
                    log_type='error',
                )
                continue

            # Sources that return MB-metadata-only tracks (LB, Last.fm)
            # mark them ``needs_discovery=True``. Hand them to the
            # adapter's matcher so the resulting mirror rows carry
            # provider IDs + matched_data, ready for the sync pipeline.
            #
            # Pipeline runs skip this because Phase 2's discovery
            # worker handles it with proper progress emission — see
            # ``skip_discovery`` resolution at the top of this fn.
            detail_tracks = (
                detail.tracks if skip_discovery
                else _maybe_discover(detail.tracks, source, deps)
            )

            tracks = [to_mirror_track_dict(t) for t in detail_tracks]
            refreshed += _commit_refresh(pl, source, source_id, tracks, db, deps, auto_id)
        except _SkipPlaylist:
            # Source-specific soft-skip (e.g. Tidal not authenticated).
            # Logging was already emitted; do not count as error.
            continue
        except Exception as e:
            errors.append(f"{pl.get('name', '?')}: {str(e)}")
            deps.update_progress(
                auto_id,
                log_line=f'Error: {pl.get("name", "?")} — {str(e)}',
                log_type='error',
            )
    return {'status': 'completed', 'refreshed': str(refreshed), 'errors': str(len(errors))}


def _maybe_discover(
    tracks: List[Any],
    source: str,
    deps: AutomationDeps,
) -> List[Any]:
    """Run the adapter's ``discover_tracks`` when any track needs it.

    Most sources are no-ops here (their tracks have provider IDs
    already). LB / Last.fm are the ones that actually do work."""
    if not tracks:
        return tracks
    if not any(getattr(t, "needs_discovery", False) for t in tracks):
        return tracks
    registry = deps.playlist_source_registry
    if registry is None:
        return tracks
    adapter = registry.get_source(source)
    if adapter is None:
        return tracks
    try:
        return adapter.discover_tracks(tracks)
    except Exception as exc:
        deps.logger.warning(f"{source} discover_tracks failed: {exc}")
        return tracks


class _SkipPlaylist(Exception):
    """Internal sentinel: source-specific soft-skip (e.g. not authed).

    The per-playlist loop catches it specifically so the skip isn't
    counted in the error tally — matches the pre-extraction behavior
    where ``continue`` was used inline."""


def _fetch_detail(
    source: str,
    source_id: str,
    pl: Dict[str, Any],
    deps: AutomationDeps,
    auto_id: Optional[str],
) -> Optional[PlaylistDetail]:
    """Resolve the playlist's tracks through the registry.

    Handler-level branches (URL extraction, Spotify-public→authed
    fallback, Tidal not-authed skip) live here; everything else
    delegates to the adapter."""
    registry = deps.playlist_source_registry
    if registry is None:
        return None

    # URL-backed sources: pull the upstream URL out of `description`.
    playlist_input = source_id
    if source in _URL_BACKED_SOURCES:
        # ``require_refresh_url`` raises ValueError on missing URL.
        # The outer try/except in the loop catches it and reports as
        # an error — matching the pre-extraction behavior.
        playlist_input = require_refresh_url(
            source, pl.get('description', ''), pl.get('name', '')
        )

    # Spotify-public refresh: prefer the authenticated Spotify API
    # when the user is signed in. Better album art, matches the
    # pre-extraction handler. Falls through to the public scraper on
    # auth failure or non-playlist URL types (e.g. album URLs).
    if source == SOURCE_SPOTIFY_PUBLIC:
        detail = _try_spotify_authed_for_public(playlist_input, deps)
        if detail is not None:
            return detail

    # Tidal not-authed: soft-skip with a 'skip' log line, not an error.
    if source == SOURCE_TIDAL:
        tidal_source = registry.get_source(SOURCE_TIDAL)
        if tidal_source is None or not tidal_source.is_authenticated():
            deps.logger.warning(
                f"Tidal not authenticated — skipping refresh for '{pl.get('name', '')}'"
            )
            deps.update_progress(
                auto_id,
                log_line=f'Skipped "{pl.get("name", "")}" — Tidal not authenticated',
                log_type='skip',
            )
            raise _SkipPlaylist

    adapter = registry.get_source(source)
    if adapter is None:
        return None
    try:
        return adapter.refresh_playlist(playlist_input)
    except Exception as exc:
        deps.logger.warning(
            f"{source} playlist refresh failed for {playlist_input}: {exc}"
        )
        return None


def _try_spotify_authed_for_public(
    spotify_url: str, deps: AutomationDeps
) -> Optional[PlaylistDetail]:
    """Best-effort: use the authenticated Spotify adapter on a public URL.

    Returns ``None`` to signal "fall through to the public-scraper
    adapter" — never raises. Only applies to ``playlist``-type URLs;
    album URLs fall through unconditionally."""
    if not spotify_url:
        return None
    spotify_client = deps.spotify_client
    if spotify_client is None or not spotify_client.is_spotify_authenticated():
        return None
    try:
        from core.spotify_public_scraper import parse_spotify_url
        parsed = parse_spotify_url(spotify_url)
    except Exception:
        return None
    if not parsed or parsed.get('type') != 'playlist':
        return None
    adapter = deps.playlist_source_registry.get_source(SOURCE_SPOTIFY)
    if adapter is None:
        return None
    try:
        return adapter.refresh_playlist(parsed['id'])
    except Exception as exc:
        deps.logger.debug(f"Spotify authed fallback for public mirror failed: {exc}")
        return None


def _commit_refresh(
    pl: Dict[str, Any],
    source: str,
    source_id: str,
    tracks: List[Dict[str, Any]],
    db: Any,
    deps: AutomationDeps,
    auto_id: Optional[str],
) -> int:
    """Persist the refreshed track list + emit playlist_changed when delta.

    Returns 1 when a refresh successfully landed, 0 otherwise. The
    caller is responsible for incrementing the running tally."""
    old_tracks = db.get_mirrored_playlist_tracks(pl['id']) if pl.get('id') else []
    old_ids = {t.get('source_track_id') for t in old_tracks if t.get('source_track_id')}
    new_ids = {t.get('source_track_id') for t in tracks if t.get('source_track_id')}

    # Preserve existing extra_data (matched_data + discovery state)
    # for tracks that still exist in the refreshed snapshot, unless
    # the adapter already provided fresh extra_data for that track.
    old_extra_map = (
        db.get_mirrored_tracks_extra_data_map(pl['id']) if pl.get('id') else {}
    )
    for t in tracks:
        sid = t.get('source_track_id', '')
        if sid and sid in old_extra_map and 'extra_data' not in t:
            t['extra_data'] = old_extra_map[sid]

    db.mirror_playlist(
        source=source,
        source_playlist_id=source_id,
        name=pl['name'],
        tracks=tracks,
        profile_id=pl.get('profile_id', 1),
        description=pl.get('description'),
        owner=pl.get('owner'),
        image_url=pl.get('image_url'),
    )

    # Membership just changed — if this playlist is organize-by-playlist, rebuild
    # its folder (with prune) so a track that LEFT the playlist has its symlink
    # cleaned up now. Gated to organized playlists, non-fatal — never disturbs
    # the refresh. (Additions are handled by the post-download reconcile.)
    try:
        from core.playlists.materialize_service import rebuild_mirrored_playlist_if_organized
        rebuild_mirrored_playlist_if_organized(
            db, deps.config_manager, pl.get('id'), profile_id=pl.get('profile_id', 1)
        )
    except Exception as _mat_err:
        deps.logger.debug(f"[Playlist Folder] mirror-refresh cleanup skipped: {_mat_err}")

    if old_ids != new_ids:
        added = len(new_ids - old_ids)
        removed = len(old_ids - new_ids)
        deps.logger.info(
            f"[AUTOMATION] Playlist changed: '{pl.get('name', '')}' — "
            f"{added} added, {removed} removed (old={len(old_ids)}, new={len(new_ids)})"
        )
        deps.update_progress(
            auto_id,
            log_line=f'"{pl.get("name", "")}" — {added} added, {removed} removed',
            log_type='success',
        )
        try:
            if deps.engine:
                deps.engine.emit('playlist_changed', {
                    'playlist_name': pl.get('name', ''),
                    'playlist_id': str(pl.get('id', '')),
                    'old_count': str(len(old_ids)),
                    'new_count': str(len(new_ids)),
                    'added': str(added),
                    'removed': str(removed),
                })
        except Exception as e:
            deps.logger.debug("playlist_synced automation emit failed: %s", e)
    else:
        deps.logger.warning(
            f"[AUTOMATION] No changes: '{pl.get('name', '')}' (tracks={len(old_ids)})"
        )
        deps.update_progress(
            auto_id,
            log_line=f'No changes: "{pl.get("name", "")}"',
            log_type='skip',
        )

    return 1
