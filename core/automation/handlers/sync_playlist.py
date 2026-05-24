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
import threading
from typing import Any, Dict

from core.automation.deps import AutomationDeps


def auto_sync_playlist(config: Dict[str, Any], deps: AutomationDeps) -> Dict[str, Any]:
    """Sync a mirrored playlist to the active media server.

    Behavior:
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
    auto_id = config.get('_automation_id')
    playlist_id = config.get('playlist_id')
    if not playlist_id:
        return {'status': 'error', 'reason': 'No playlist specified'}

    db = deps.get_database()
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
                })
            elif t.get('source_track_id') and (t.get('track_name') or '').strip():
                # Has a valid source ID and track name — usable for wishlist.
                tracks_json.append({
                    'name': t['track_name'].strip(),
                    'artists': [{'name': (t.get('artist_name') or '').strip() or 'Unknown Artist'}],
                    'album': album_obj,
                    'duration_ms': t.get('duration_ms', 0),
                    'id': t['source_track_id'],
                })
            else:
                skipped_count += 1  # No usable ID or name — truly can't process.

    if not tracks_json:
        deps.update_progress(
            auto_id,
            log_line=f'No discovered tracks — {skipped_count} need discovery first',
            log_type='skip',
        )
        return {
            'status': 'skipped',
            'reason': f'No discovered tracks to sync ({skipped_count} tracks need discovery first)',
            'skipped_tracks': str(skipped_count),
        }

    # Preflight: hash the track list and compare against last sync.
    # Skip if the exact same set of tracks was already synced and
    # everything matched (no-op preserves Plex / Jellyfin / Navidrome
    # from needless rewrites).
    track_ids_str = ','.join(sorted(t.get('id', '') for t in tracks_json))
    tracks_hash = hashlib.md5(track_ids_str.encode()).hexdigest()

    sync_id_key = f"auto_mirror_{playlist_id}"
    try:
        sync_statuses = deps.load_sync_status_file()
        last_status = sync_statuses.get(sync_id_key, {})
        last_hash = last_status.get('tracks_hash', '')
        last_matched = last_status.get('matched_tracks', -1)

        if last_hash == tracks_hash and last_matched >= len(tracks_json):
            # Exact same tracks, all matched last time — nothing to do.
            deps.update_progress(
                auto_id,
                log_line=f'All {len(tracks_json)} tracks unchanged since last sync — skipping',
                log_type='skip',
            )
            return {
                'status': 'skipped',
                'reason': f'All {len(tracks_json)} tracks unchanged since last sync',
            }
    except Exception as e:
        deps.logger.debug("mirror sync last-status read: %s", e)

    deps.update_progress(
        auto_id,
        progress=50,
        phase=f'Syncing "{pl["name"]}"',
        log_line=f'{len(tracks_json)} discovered, {skipped_count} skipped',
        log_type='info',
    )

    sync_id = f"auto_mirror_{playlist_id}"
    deps.update_progress(
        auto_id,
        progress=90,
        log_line=f'Starting sync: {len(tracks_json)} tracks',
        log_type='success',
    )
    threading.Thread(
        target=deps.run_sync_task,
        args=(sync_id, pl['name'], tracks_json, auto_id, 1, pl.get('image_url', '')),
        daemon=True,
        name=f'auto-sync-{playlist_id}',
    ).start()
    return {
        'status': 'started',
        'playlist_name': pl['name'],
        'discovered_tracks': str(len(tracks_json)),
        'skipped_tracks': str(skipped_count),
        '_manages_own_progress': True,
    }
