"""Personalized Playlist Pipeline automation handler.

Sibling to ``auto_playlist_pipeline`` (mirrored). Where the mirrored
pipeline runs REFRESH external sources → DISCOVER metadata → SYNC →
WISHLIST, the personalized pipeline is simpler:

    SNAPSHOT → SYNC → WISHLIST

SNAPSHOT reads the persisted track list from
``PersonalizedPlaylistManager``. When ``refresh_first=True`` (config),
each playlist is refreshed BEFORE syncing — useful when the user
wants the cron to capture a fresh-each-run view (e.g. "give me a new
Hidden Gems set every night"). Default is to sync the existing
snapshot, on the assumption the user / a separate cron has already
refreshed when they wanted to.

Config schema:
    {
        'kinds': [
            {'kind': 'hidden_gems'},
            {'kind': 'time_machine', 'variant': '1980s'},
            {'kind': 'seasonal_mix', 'variant': 'halloween'},
            ...
        ],
        'refresh_first': bool,    # default false
        'skip_wishlist': bool,    # default false
    }

Each kind dict has at minimum ``kind``; ``variant`` is required for
kinds that need it (time_machine, genre_playlist, daily_mix,
seasonal_mix). Singleton kinds (hidden_gems, discovery_shuffle,
popular_picks, fresh_tape, archives) ignore variant.

Pipeline-running flag (``deps.state.pipeline_running``) is shared
with the mirrored pipeline so the two can't overlap. (One sync
queue, one wishlist worker — overlapping triggers would step on
each other.)"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Dict, List, Optional

from core.automation.deps import AutomationDeps
from core.automation.handlers._pipeline_shared import run_sync_and_wishlist


# Sync state key prefix so personalized syncs don't collide with
# mirrored ones (`auto_mirror_<id>`).
_SYNC_ID_PREFIX = 'auto_personalized'


def auto_personalized_pipeline(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Run SNAPSHOT → SYNC → WISHLIST for selected personalized playlists."""
    deps.state.set_pipeline_running(True)
    automation_id = config.get('_automation_id')
    pipeline_start = time.time()

    try:
        kinds_config = config.get('kinds') or []
        if not isinstance(kinds_config, list) or not kinds_config:
            deps.state.set_pipeline_running(False)
            return {
                'status': 'error',
                'error': 'No personalized playlist kinds selected',
            }

        refresh_first = bool(config.get('refresh_first', False))
        skip_wishlist = bool(config.get('skip_wishlist', False))

        manager = deps.build_personalized_manager()

        deps.update_progress(
            automation_id,
            progress=2,
            phase=f'Personalized pipeline: {len(kinds_config)} playlist(s)',
            log_line=f'Starting pipeline for {len(kinds_config)} playlist(s)',
            log_type='info',
        )

        # ── PHASE 1: SNAPSHOT (optionally refresh) ──────────────────
        deps.update_progress(
            automation_id,
            progress=3,
            phase='Phase 1/2: Loading snapshots...' if not refresh_first
                  else 'Phase 1/2: Refreshing snapshots...',
            log_line='Phase 1: Snapshot' + (' (with refresh)' if refresh_first else ''),
            log_type='info',
        )

        profile_id = deps.get_current_profile_id()
        playload_payloads = _build_payloads_for_kinds(
            deps, manager, kinds_config, profile_id,
            automation_id=automation_id,
            refresh_first=refresh_first,
        )

        if not playload_payloads:
            deps.state.set_pipeline_running(False)
            deps.update_progress(
                automation_id,
                status='finished', progress=100,
                phase='No playlists to sync',
                log_line='No personalized playlists had tracks to sync',
                log_type='warning',
            )
            return {
                'status': 'completed',
                '_manages_own_progress': True,
                'playlists_synced': '0',
                'tracks_synced': '0',
                'duration_seconds': str(int(time.time() - pipeline_start)),
            }

        deps.update_progress(
            automation_id,
            progress=50,
            phase='Phase 1/2: Snapshot complete',
            log_line=f'Phase 1 done: {len(playload_payloads)} playlist(s) ready to sync',
            log_type='success',
        )

        # ── PHASE 2: SYNC + WISHLIST (shared helper) ────────────────
        sync_summary = run_sync_and_wishlist(
            deps,
            automation_id,
            playload_payloads,
            sync_one_fn=lambda pl: _sync_personalized_playlist(deps, pl),
            sync_id_for_fn=lambda pl: pl['sync_id'],
            skip_wishlist=skip_wishlist,
            progress_start=51,
            progress_end=90,
            sync_phase_label='Phase 2/2: Syncing to server...',
            sync_phase_start_log='Phase 2: Sync',
            wishlist_phase_label='Phase 2/2: Processing wishlist...',
            wishlist_phase_start_log='Wishlist',
        )

        # ── COMPLETE ────────────────────────────────────────────────
        duration = int(time.time() - pipeline_start)
        deps.update_progress(
            automation_id,
            status='finished', progress=100,
            phase='Pipeline complete',
            log_line=f'Personalized pipeline finished in {duration // 60}m {duration % 60}s',
            log_type='success',
        )

        deps.state.set_pipeline_running(False)
        return {
            'status': 'completed',
            '_manages_own_progress': True,
            'playlists_synced': str(len(playload_payloads)),
            'tracks_synced': str(sync_summary['synced']),
            'sync_skipped': str(sync_summary['skipped']),
            'wishlist_queued': str(sync_summary['wishlist_queued']),
            'duration_seconds': str(duration),
        }

    except Exception as e:  # noqa: BLE001 — automation handlers must never raise into engine
        deps.state.set_pipeline_running(False)
        deps.update_progress(
            automation_id,
            status='error', progress=100,
            phase='Pipeline error',
            log_line=f'Personalized pipeline failed: {e}',
            log_type='error',
        )
        return {'status': 'error', 'error': str(e), '_manages_own_progress': True}


def _build_payloads_for_kinds(
    deps: AutomationDeps,
    manager: Any,
    kinds_config: List[Dict[str, Any]],
    profile_id: int,
    *,
    automation_id: Optional[str],
    refresh_first: bool,
) -> List[Dict[str, Any]]:
    """Resolve each requested kind+variant into a sync-payload dict.

    Each payload has: ``{'name', 'kind', 'variant', 'tracks_json',
    'image_url', 'sync_id'}``. Playlists with no tracks (e.g. a
    seasonal mix that hasn't been populated yet) are omitted from
    the result so the sync loop doesn't waste time on empty pushes.
    """
    payloads: List[Dict[str, Any]] = []
    for entry in kinds_config:
        if not isinstance(entry, dict):
            continue
        kind = entry.get('kind')
        variant = entry.get('variant') or ''
        if not kind:
            continue

        try:
            # Refresh when ANY of:
            #   - explicit user flag (cron use case: regenerate each run)
            #   - snapshot marked stale by upstream data refresher
            #   - playlist was never generated yet (auto-created by
            #     ensure_playlist; track_count=0, last_generated_at=NULL).
            #     Without this branch, a first-run pipeline reads the
            #     empty snapshot and silently skips — user picks a kind,
            #     hits run, gets "No tracks to sync" with no clue why.
            if refresh_first:
                record = manager.refresh_playlist(kind, variant, profile_id)
            else:
                existing = manager.ensure_playlist(kind, variant, profile_id)
                needs_first_gen = existing.last_generated_at is None
                if existing.is_stale or needs_first_gen:
                    record = manager.refresh_playlist(kind, variant, profile_id)
                else:
                    record = existing
        except Exception as exc:  # noqa: BLE001 — log + continue with next kind
            deps.update_progress(
                automation_id,
                log_line=f'Skipping {kind}{("/" + variant) if variant else ""}: {exc}',
                log_type='warning',
            )
            continue

        tracks = manager.get_playlist_tracks(record.id)
        if not tracks:
            deps.update_progress(
                automation_id,
                log_line=f'No tracks in {record.name} — skipping sync',
                log_type='skip',
            )
            continue

        tracks_json = [_track_to_sync_shape(t) for t in tracks]
        payloads.append({
            'name': record.name,
            'kind': record.kind,
            'variant': record.variant,
            'tracks_json': tracks_json,
            'image_url': '',  # personalized playlists don't have a cover image yet
            'sync_id': f'{_SYNC_ID_PREFIX}_{record.kind}_{record.variant or "_"}',
        })
    return payloads


def _track_to_sync_shape(track: Any) -> Dict[str, Any]:
    """Convert a personalized.types.Track into the dict shape
    `_run_sync_task` expects. Mirrors what the mirrored pipeline
    builds from extra_data.matched_data, preserving enriched metadata
    from personalized snapshots when available."""
    primary_id = track.spotify_track_id or track.itunes_track_id or track.deezer_track_id or ''
    rich_data = _coerce_track_data_json(getattr(track, 'track_data_json', None))

    if not rich_data:
        album = {'name': track.album_name or ''}
        cover_url = getattr(track, 'album_cover_url', None)
        if cover_url:
            album['images'] = [{'url': cover_url}]
        return {
            'name': track.track_name,
            'artists': [{'name': track.artist_name}],
            'album': album,
            'duration_ms': int(track.duration_ms or 0),
            'id': primary_id,
        }

    payload = dict(rich_data)
    cover_url = (
        getattr(track, 'album_cover_url', None)
        or payload.get('album_cover_url')
        or payload.get('image_url')
    )
    payload['id'] = payload.get('id') or primary_id
    payload['name'] = payload.get('name') or track.track_name
    payload['artists'] = _normalize_artists(payload.get('artists'), track.artist_name)
    payload['album'] = _normalize_album(payload.get('album'), track, cover_url=cover_url)
    payload['duration_ms'] = int(payload.get('duration_ms') or track.duration_ms or 0)
    if 'popularity' not in payload and getattr(track, 'popularity', None) is not None:
        payload['popularity'] = int(track.popularity or 0)

    if cover_url and not payload.get('image_url'):
        payload['image_url'] = cover_url

    return payload


def _coerce_track_data_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            loaded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}
    return {}


def _normalize_artists(artists: Any, fallback_artist: str) -> List[Dict[str, Any]]:
    if not artists:
        return [{'name': fallback_artist or 'Unknown Artist'}]
    if isinstance(artists, list):
        normalized = []
        for artist in artists:
            if isinstance(artist, dict):
                normalized.append(artist if artist.get('name') else {'name': str(artist)})
            elif isinstance(artist, str):
                normalized.append({'name': artist})
            else:
                normalized.append({'name': str(artist)})
        return normalized or [{'name': fallback_artist or 'Unknown Artist'}]
    if isinstance(artists, dict):
        return [artists if artists.get('name') else {'name': str(artists)}]
    return [{'name': str(artists)}]


def _normalize_album(album: Any, track: Any, cover_url: Optional[str] = None) -> Dict[str, Any]:
    if isinstance(album, dict):
        normalized = dict(album)
    else:
        normalized = {'name': str(album) if album else (track.album_name or '')}

    normalized['name'] = normalized.get('name') or track.album_name or ''
    images = normalized.get('images')
    if not isinstance(images, list):
        images = []
    if cover_url and not images:
        images = [{'url': cover_url}]
    if images:
        normalized['images'] = images
    return normalized


def _sync_personalized_playlist(deps: AutomationDeps, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Launch a personalized playlist sync via _run_sync_task on a
    daemon thread + return immediately with status='started'.

    Mirrors the mirrored ``auto_sync_playlist`` return contract so the
    shared helper can poll on ``sync_states[sync_id]`` and aggregate
    results identically."""
    sync_id = payload['sync_id']
    name = payload['name']
    tracks_json = payload['tracks_json']
    profile_id = deps.get_current_profile_id()

    threading.Thread(
        target=deps.run_sync_task,
        args=(sync_id, name, tracks_json, None, profile_id, payload.get('image_url', '')),
        daemon=True,
        name=f'auto-personalized-{sync_id}',
    ).start()
    return {
        'status': 'started',
        'playlist_name': name,
        '_manages_own_progress': True,
    }
