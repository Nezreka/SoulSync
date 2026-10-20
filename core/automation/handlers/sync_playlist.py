"""Automation handler: ``sync_playlist`` action.

Lifted from ``web_server._register_automation_handlers`` (the
``_auto_sync_playlist`` closure). Syncs a mirrored playlist to the
configured media server, using discovered metadata when available
and skipping undiscovered tracks. When triggered on a schedule with
no track changes since the last sync, short-circuits with
``status: skipped`` (saves Plex / Jellyfin / Navidrome from
needless rewrites)."""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from core.automation.deps import AutomationDeps

logger = logging.getLogger(__name__)


def auto_sync_playlist(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Sync a mirrored playlist (or all mirrored playlists) to the active media server.

    Behavior:
    - If ``all: True``: iterates through all mirrored playlists sequentially
      in a background thread, tracking aggregate progress across all playlists.
    - Otherwise syncs the single playlist indicated by ``playlist_id``.
    - Tracks with discovered metadata (extra_data.discovered + matched_data)
      are routed via the official metadata.
    - Tracks with a Spotify hint (real Spotify ID from the embed
      scraper) are included so they can still hit Soulseek + the
      wishlist.
    - Tracks with neither are counted as ``skipped_tracks``.
    - Empty result → ``status: skipped`` with the skipped count.
    - Same track set as last sync (matched_tracks unchanged) →
      ``status: skipped`` (no-op).
    - Otherwise spawns a daemon thread running ``run_sync_task`` and
      returns ``status: started`` with ``_manages_own_progress: True``.
    """
    if config.get('all'):
        return _auto_sync_all_playlists(config, deps)

    playlist_id = config.get('playlist_id')
    if not playlist_id:
        return {'status': 'error', 'reason': 'No playlist specified'}

    return _sync_single_mirrored_playlist(config, deps)


def _auto_sync_all_playlists(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Sync all mirrored playlists to the active media server."""
    db = deps.get_database()
    from core.profile_context import get_background_profile

    owner = config.get('profile_id') or get_background_profile()
    playlists = db.get_mirrored_playlists(int(owner)) if owner else db.get_mirrored_playlists()

    if not playlists:
        return {'status': 'error', 'reason': 'No playlists found'}

    threading.Thread(
        target=_run_all_playlists_sync_worker,
        args=(playlists, config, deps),
        daemon=True,
        name='auto-sync-all-playlists',
    ).start()

    names = ', '.join(p.get('name', '?') for p in playlists[:3])
    if len(playlists) > 3:
        names += f' (+{len(playlists) - 3} more)'

    return {
        'status': 'started',
        'playlist_count': str(len(playlists)),
        'playlists': names,
        '_manages_own_progress': True,
    }


def _run_all_playlists_sync_worker(
    playlists: List[Dict[str, Any]],
    config: Dict[str, Any],
    deps: AutomationDeps,
) -> None:
    auto_id = config.get('_automation_id')
    total = len(playlists)
    if auto_id and hasattr(deps, 'update_progress'):
        deps.update_progress(
            auto_id,
            progress=5,
            phase='Syncing playlists...',
            log_line=f'Starting sync for {total} mirrored playlist(s)',
            log_type='info',
        )

    total_synced = 0
    total_skipped = 0
    total_matched_tracks = 0
    errors: List[str] = []
    _SYNC_TIMEOUT = 600

    for idx, pl in enumerate(playlists):
        pl_id = pl.get('id')
        if not pl_id:
            continue
        pl_name = pl.get('name') or f'Playlist {pl_id}'

        sub_config = dict(config)
        sub_config['playlist_id'] = str(pl_id)
        sub_config['all'] = False
        sub_config['_automation_id'] = None

        if auto_id and hasattr(deps, 'update_progress'):
            pct = int(5 + (idx / total) * 90)
            deps.update_progress(
                auto_id,
                progress=pct,
                phase=f'Syncing ({idx + 1}/{total}): "{pl_name}"',
                log_line=f'Syncing playlist {idx + 1}/{total}: "{pl_name}"',
                log_type='info',
            )

        done_event = threading.Event()
        try:
            res = _sync_single_mirrored_playlist(sub_config, deps, pl=pl, done_event=done_event)
            status = res.get('status')
            if status == 'skipped':
                total_skipped += 1
                if auto_id and hasattr(deps, 'update_progress'):
                    deps.update_progress(
                        auto_id,
                        log_line=f'Skipped "{pl_name}": {res.get("reason", "unchanged")}',
                        log_type='skip',
                    )
            elif status == 'error':
                err_msg = res.get('reason') or 'unknown error'
                errors.append(f'"{pl_name}": {err_msg}')
                if auto_id and hasattr(deps, 'update_progress'):
                    deps.update_progress(
                        auto_id,
                        log_line=f'Error syncing "{pl_name}": {err_msg}',
                        log_type='error',
                    )
            elif status == 'started':
                start_t = time.time()
                timed_out = False
                while not done_event.wait(timeout=2):
                    elapsed = int(time.time() - start_t)
                    if elapsed >= _SYNC_TIMEOUT:
                        timed_out = True
                        break
                    if auto_id and hasattr(deps, 'update_progress'):
                        pl_pct = min(elapsed // 3, 80)
                        pct = int(5 + ((idx + pl_pct / 100) / total) * 90)
                        deps.update_progress(
                            auto_id,
                            progress=pct,
                            phase=f'Syncing ({idx + 1}/{total}): "{pl_name}" ({elapsed}s)',
                        )

                sync_id = f"auto_mirror_{pl_id}"
                sync_states = (
                    deps.get_sync_states()
                    if hasattr(deps, 'get_sync_states')
                    else getattr(deps, 'sync_states', {})
                )
                ss = (sync_states or {}).get(sync_id, {})
                if timed_out:
                    errors.append(f'"{pl_name}": timed out')
                    if auto_id and hasattr(deps, 'update_progress'):
                        deps.update_progress(
                            auto_id,
                            log_line=f'Sync timed out for "{pl_name}" after {_SYNC_TIMEOUT}s',
                            log_type='error',
                        )
                elif ss.get('status') in ('error', 'failed'):
                    err_reason = ss.get('error') or ss.get('reason') or 'sync failed'
                    errors.append(f'"{pl_name}": {err_reason}')
                    if auto_id and hasattr(deps, 'update_progress'):
                        deps.update_progress(
                            auto_id,
                            log_line=f'Sync error for "{pl_name}": {err_reason}',
                            log_type='error',
                        )
                else:
                    total_synced += 1
                    ss_result = ss.get('result', ss.get('progress', {}))
                    matched = ss_result.get('matched_tracks', 0) if isinstance(ss_result, dict) else 0
                    total_matched_tracks += int(matched or 0)
                    if auto_id and hasattr(deps, 'update_progress'):
                        deps.update_progress(
                            auto_id,
                            log_line=f'Synced "{pl_name}": {matched} tracks in library',
                            log_type='success',
                        )
        except Exception as exc:
            logger.exception("Sync all playlist %s error: %s", pl_id, exc)
            errors.append(f'"{pl_name}": {exc}')
            if auto_id and hasattr(deps, 'update_progress'):
                deps.update_progress(
                    auto_id,
                    log_line=f'Exception syncing "{pl_name}": {exc}',
                    log_type='error',
                )

    organize_playlists = [p for p in playlists if p.get('organize_by_playlist')]
    if organize_playlists and hasattr(deps, 'run_playlist_organize_download'):
        for org_pl in organize_playlists:
            org_id = org_pl.get('id')
            if not org_id:
                continue
            org_name = org_pl.get('name') or f'Playlist {org_id}'
            try:
                org_res = deps.run_playlist_organize_download(
                    mirrored_playlist_id=int(org_id),
                    automation_id=auto_id,
                )
                if auto_id and hasattr(deps, 'update_progress'):
                    if org_res.get('status') == 'started':
                        deps.update_progress(
                            auto_id,
                            log_line=f'Organize download started for "{org_name}"',
                            log_type='success',
                        )
            except Exception as org_err:
                logger.debug("Organize download dispatch failed for %s: %s", org_id, org_err)

    if auto_id and hasattr(deps, 'update_progress'):
        summary = f'Sync all finished: {total_synced}/{total} playlists synced ({total_matched_tracks} matched)'
        if total_skipped:
            summary += f', {total_skipped} skipped'
        if errors:
            summary += f', {len(errors)} error(s)'
        deps.update_progress(
            auto_id,
            status='finished',
            progress=100,
            phase='Sync complete',
            log_line=summary,
            log_type='success' if not errors else 'warning',
        )


def _sync_single_mirrored_playlist(
    config: Dict[str, Any],
    deps: AutomationDeps,
    *,
    pl: Optional[Dict[str, Any]] = None,
    done_event: Optional[threading.Event] = None,
) -> Dict[str, Any]:
    auto_id = config.get('_automation_id')
    playlist_id = config.get('playlist_id')

    db = deps.get_database()
    if pl is None:
        pl = db.get_mirrored_playlist(int(playlist_id))
    if not pl:
        return {'status': 'error', 'reason': 'Playlist not found'}

    tracks = db.get_mirrored_playlist_tracks(int(playlist_id))
    if not tracks:
        return {'status': 'error', 'reason': 'No tracks in playlist'}

    # Convert mirrored tracks to format expected by run_sync_task.
    # Use discovered metadata when available, fall back to Spotify
    # hint or raw playlist fields when not.
    tracks_json = []
    skipped_count = 0

    for t in tracks:
        # Parse extra_data for discovery info.
        extra = {}
        if t.get('extra_data'):
            try:
                extra = json.loads(t['extra_data']) if isinstance(t['extra_data'], str) else t['extra_data']
            except (json.JSONDecodeError, TypeError):
                pass

        if extra.get('discovered') and extra.get('matched_data'):
            # Use official discovered metadata.
            md = extra['matched_data']
            album_raw = md.get('album', '')
            album_obj = album_raw if isinstance(album_raw, dict) else {'name': album_raw or ''}
            _track_entry = {
                'name': md.get('name', ''),
                'artists': md.get('artists', [{'name': t.get('artist_name', '')}]),
                'album': album_obj,
                'duration_ms': md.get('duration_ms', 0),
                'id': md.get('id', ''),
                # Carried purely so the sync can write its per-track verdict back
                # onto this row. `id` above is the SOURCE id and cannot serve:
                # for a ListenBrainz playlist it is a 'file:...' reference, and
                # after discovery it is whichever provider matched (often Deezer).
                # SpotifyTrack reads named fields, so this rides along unused.
                'db_track_id': t.get('id'),
                # the playlist's own id, which `id` above is not: Find & Add
                # files a manual match under it, so the sync needs both (#1289)
                'source_track_id': t.get('source_track_id') or '',
            }
            if md.get('track_number'):
                _track_entry['track_number'] = md['track_number']
            if md.get('disc_number'):
                _track_entry['disc_number'] = md['disc_number']
            tracks_json.append(_track_entry)
        else:
            # NOT discovered — try to include using available metadata so
            # the track can still be searched on Soulseek and added to
            # wishlist. Without this, failed discovery blocks the entire
            # download pipeline.
            #
            # Priority: spotify_hint (has real Spotify ID from embed
            # scraper) > raw playlist fields (only if source_track_id
            # is valid).
            hint = extra.get('spotify_hint', {})
            # Build album object with cover art from the mirrored playlist track.
            track_image = (t.get('image_url') or '').strip()
            album_obj = {
                'name': (t.get('album_name') or '').strip(),
                'images': [{'url': track_image, 'height': 300, 'width': 300}] if track_image else [],
            }

            if hint.get('id') and hint.get('name'):
                # spotify_hint has proper Spotify track ID + metadata from embed scraper.
                hint_artists = hint.get('artists', [])
                if hint_artists and isinstance(hint_artists[0], str):
                    hint_artists = [{'name': a} for a in hint_artists]
                elif hint_artists and isinstance(hint_artists[0], dict):
                    pass  # Already in correct format
                else:
                    hint_artists = [{'name': t.get('artist_name', '')}]
                tracks_json.append({
                    'name': hint['name'],
                    'artists': hint_artists,
                    'album': album_obj,
                    'duration_ms': t.get('duration_ms', 0),
                    'id': hint['id'],
                    'db_track_id': t.get('id'),
                    'source_track_id': t.get('source_track_id') or '',
                })
            elif t.get('source_track_id') and (t.get('track_name') or '').strip():
                # Has a valid source ID and track name — usable for wishlist.
                tracks_json.append({
                    'name': t['track_name'].strip(),
                    'artists': [{'name': (t.get('artist_name') or '').strip() or 'Unknown Artist'}],
                    'album': album_obj,
                    'duration_ms': t.get('duration_ms', 0),
                    'id': t['source_track_id'],
                    'db_track_id': t.get('id'),
                    'source_track_id': t['source_track_id'],
                })
            else:
                skipped_count += 1  # No usable ID or name — truly can't process.

    if not tracks_json:
        deps.update_progress(
            auto_id,
            log_line=f'No identified tracks — {skipped_count} need identification first',
            log_type='skip',
        )
        return {
            'status': 'skipped',
            'reason': f'No identified tracks to sync ({skipped_count} tracks need identification first)',
            'skipped_tracks': str(skipped_count),
        }

    # The mirror owns this assignment.  Carry it on every track so both the
    # direct sync-time wishlist path and any later failed-download path use
    # the same profile, independent of Library v2.
    quality_profile_id = pl.get('quality_profile_id')
    if quality_profile_id is not None:
        for track in tracks_json:
            track['quality_profile_id'] = quality_profile_id

    # Preflight: hash the track list and compare against last sync.
    # Skip if the exact same set of tracks was already synced and
    # everything matched (no-op preserves Plex / Jellyfin / Navidrome
    # from needless rewrites).
    track_ids_str = ','.join(sorted(t.get('id', '') for t in tracks_json))
    tracks_hash = hashlib.md5(track_ids_str.encode()).hexdigest()

    sync_id_key = f"auto_mirror_{playlist_id}"
    # Full mirror identity (every source_track_id on the playlist). tracks_hash
    # only covers tracks_json — if a new mirror row is skipped (no discovery /
    # no source id), tracks_hash stays identical to the pre-add sync and we
    # used to no-op with "unchanged" while the new song never hit wishlist.
    mirror_ids_str = ','.join(
        sorted(t.get('source_track_id', '') or '' for t in tracks if t.get('source_track_id'))
    )
    mirror_tracks_hash = hashlib.md5(mirror_ids_str.encode()).hexdigest() if mirror_ids_str else ''

    # #1704: the mirror's own mode, then the pipeline's, else None so the
    # global setting decides. junk in either is skipped, not trusted
    from core.sync.playlist_edit import VALID_SYNC_MODES, resolve_sync_mode
    own_sync_mode = next(
        (m for m in (pl.get('sync_mode'), config.get('sync_mode')) if m in VALID_SYNC_MODES),
        None,
    )
    _cm_get = getattr(getattr(deps, 'config_manager', None), 'get', None)
    _global_mode = _cm_get('playlist_sync.mode', 'reconcile') if callable(_cm_get) else None
    sync_mode = resolve_sync_mode(own_sync_mode, _global_mode)

    event_data = config.get('_event_data') or {}
    try:
        tracks_added = int(event_data.get('added') or 0)
    except (TypeError, ValueError):
        tracks_added = 0
    force_sync = bool(config.get('force_sync')) or tracks_added > 0 or skipped_count > 0

    try:
        sync_statuses = deps.load_sync_status_file()
        last_status = sync_statuses.get(sync_id_key, {})
        last_hash = last_status.get('tracks_hash', '')
        last_mirror_hash = last_status.get('mirror_tracks_hash', '')
        last_matched = last_status.get('matched_tracks', -1)

        mirror_changed = bool(mirror_tracks_hash) and mirror_tracks_hash != last_mirror_hash
        # An authoritative Quality Profile change is a state change even when
        # not a single track id moved: the existing Wishlist rows still have to
        # be re-stamped. The fingerprint used to cover only the track list, so
        # a profile-only edit was silently skipped forever (P1-03).
        #
        # A status written before this key existed reads as None. After the
        # Foundation backfill every mirror HAS a profile, so an upgraded install
        # forces exactly one extra sync per mirror and is then self-consistent.
        last_quality_profile_id = last_status.get('quality_profile_id')
        quality_profile_changed = last_quality_profile_id != quality_profile_id
        # same for the mode (#1704): same tracks synced in a new mode is a
        # change, or switching a pipeline to replace never reached the server.
        # a status from before the key existed ran in the global mode
        last_sync_mode = last_status.get('sync_mode') or resolve_sync_mode(None, _global_mode)
        sync_mode_changed = last_sync_mode != sync_mode
        # unchanged tracks are not enough: a matched file deleted since the last
        # sync changes nothing above, and skipping then never re-matched or
        # wishlisted it (#1417). checked last, it is the only part that reads
        # the library.
        unchanged = (
            not force_sync
            and not mirror_changed
            and not quality_profile_changed
            and not sync_mode_changed
            and last_hash == tracks_hash
            and last_matched >= len(tracks_json)
        )
        library_lost = False
        if unchanged:
            from core.sync.library_presence import matched_tracks_still_present
            library_lost = not matched_tracks_still_present(db, tracks)
        if unchanged and not library_lost:
            # Exact same tracks, all matched last time — nothing to DOWNLOAD.
            # The run still happened, so it still gets recorded: skipping the
            # bookkeeping too is what left the dashboard card with no run to
            # show, saying "no runs yet" and refusing to open, for a playlist
            # whose pipeline had just completed (Boulder, Aug 2026).
            try:
                from core.downloads.history import record_sync_history_noop
                record_sync_history_noop(
                    db,
                    sync_id_key,
                    pl.get('name') or '',
                    tracks_json,
                    profile_id=pl.get('profile_id'),
                    quality_profile_id=quality_profile_id,
                )
            except Exception as hist_err:  # noqa: BLE001 - never fail a sync on bookkeeping
                deps.logger.debug("no-op sync history failed: %s", hist_err)
            deps.update_progress(
                auto_id,
                log_line=f'All {len(tracks_json)} tracks unchanged since last sync — skipping',
                log_type='skip',
            )
            return {
                'status': 'skipped',
                'reason': f'All {len(tracks_json)} tracks unchanged since last sync',
            }
        if force_sync and last_hash == tracks_hash and last_matched >= len(tracks_json):
            deps.update_progress(
                auto_id,
                log_line=(
                    f'Forcing sync: playlist changed ({tracks_added} added) or '
                    f'{skipped_count} track(s) need discovery'
                ),
                log_type='info',
            )
        elif library_lost:
            deps.update_progress(
                auto_id,
                log_line='A track from the last sync is no longer in the library, running sync',
                log_type='info',
            )
        elif mirror_changed:
            deps.update_progress(
                auto_id,
                log_line='Mirror track list changed — running sync',
                log_type='info',
            )
        elif quality_profile_changed:
            deps.update_progress(
                auto_id,
                log_line=(
                    f'Quality Profile changed ({last_quality_profile_id} → '
                    f'{quality_profile_id}) — re-running sync'
                ),
                log_type='info',
            )
    except Exception as e:
        deps.logger.debug("mirror sync last-status read: %s", e)

    # Sync under the user's custom alias when set, else the upstream name (#865
    # follow-up). The server-side playlist is named with this.
    # the server playlist is found by name, so a mirror that would land on
    # another mirror's playlist gets a distinct name (core/playlists/sync_names)
    from core.playlists.sync_names import sync_name_for
    try:
        _active_server = deps.config_manager.get_active_media_server()
    except Exception:
        _active_server = None
    sync_name = sync_name_for(db, _active_server, pl) or pl.get('name') or 'Playlist'

    deps.update_progress(
        auto_id,
        progress=50,
        phase=f'Syncing "{sync_name}"',
        log_line=f'{len(tracks_json)} identified, {skipped_count} skipped',
        log_type='info',
    )

    sync_id = f"auto_mirror_{playlist_id}"
    deps.update_progress(
        auto_id,
        progress=90,
        log_line=f'Starting sync: {len(tracks_json)} tracks',
        log_type='success',
    )
    # Organize-by-playlist skips sync-time wishlist adds (its batch failure
    # handling covers missing tracks instead). A pipeline run whose
    # resolved skip_wishlist is set — the automation's "Skip wishlist
    # processing" checkbox or the global wishlist_missing_tracks toggle
    # (#1455) — must skip the adds too (#1689): the wishlist-phase skip
    # alone still let every sync wishlist the tracks it was syncing.
    # a standalone "Sync Playlist" automation never sets the key: nobody chose
    # per click there, so the global toggle decides, as its help text says
    # ("turn it off and syncs skip the wishlist step").
    organize_by_playlist = bool(pl.get('organize_by_playlist'))
    skip_add = config.get('skip_wishlist_add')
    if skip_add is None and pl.get('is_request_list'):
        # wishlisting what's missing is the whole job of a request list, so
        # the global "skip the wishlist step" toggle doesn't switch it off.
        # an explicit skip (the post-download re-sync) still wins
        skip_add = False
    if skip_add is None:
        _cm_get = getattr(getattr(deps, 'config_manager', None), 'get', None)
        skip_add = callable(_cm_get) and not _cm_get('playlist_sync.wishlist_missing_tracks', True)
    skip_wishlist_add = organize_by_playlist or bool(skip_add)
    def _safe_run_sync_task(*args, **kwargs):
        try:
            deps.run_sync_task(*args, **kwargs)
        except Exception as exc:
            import logging
            logging.getLogger("soulsync.automation").exception("Background sync task %s failed", sync_id)
            sync_states = getattr(deps, 'sync_states', None)
            if sync_states is None and hasattr(deps, 'get_sync_states'):
                sync_states = deps.get_sync_states()
            if isinstance(sync_states, dict):
                sync_states[sync_id] = {
                    'status': 'error',
                    'playlist_name': sync_name,
                    'error': str(exc),
                }
            if auto_id and hasattr(deps, 'update_progress'):
                deps.update_progress(
                    auto_id,
                    status='error',
                    progress=100,
                    phase='Sync error',
                    log_line=f'Sync error: {exc}',
                    log_type='error',
                )
        finally:
            if done_event is not None:
                done_event.set()

    threading.Thread(
        target=_safe_run_sync_task,
        args=(
            sync_id, sync_name, tracks_json, auto_id,
            int(pl.get('profile_id') or 1), pl.get('image_url', ''),
        ),
        kwargs={
            'skip_wishlist_add': skip_wishlist_add,
            # Kept separate from skip_wishlist_add (#1689): the post-sync
            # followup starts an organize download batch only for a real
            # organize-by-playlist skip, never for a wishlist-toggle skip.
            'organize_by_playlist': organize_by_playlist,
            # a playlist's own Sync & download click, not a schedule (#1603)
            'user_initiated': bool(config.get('user_initiated')),
            'sync_mode': own_sync_mode,
            'is_request_list': bool(pl.get('is_request_list')),
            'auto_remove_fulfilled': bool(pl.get('auto_remove_fulfilled')),
        },
        daemon=True,
        name=f'auto-sync-{playlist_id}',
    ).start()
    return {
        'status': 'started',
        'playlist_name': sync_name,
        'discovered_tracks': str(len(tracks_json)),
        'skipped_tracks': str(skipped_count),
        '_manages_own_progress': True,
    }
