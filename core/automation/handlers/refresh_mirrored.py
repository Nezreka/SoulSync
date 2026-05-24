"""Automation handler: ``refresh_mirrored`` action.

Lifted from ``web_server._register_automation_handlers`` (the
``_auto_refresh_mirrored`` closure). Re-pulls track lists from each
mirrored playlist's source (Spotify / Tidal / Deezer / YouTube),
updates the local mirror DB, and emits a ``playlist_changed``
automation event when the track set actually shifts.

Source-specific branches (Spotify auth + public-embed fallback,
``spotify_public`` URL→ID resolution, Deezer / Tidal / YouTube)
remain identical to the pre-extraction closure — this is a
mechanical lift, not a redesign.
"""

from __future__ import annotations

import json
from typing import Any, Dict

from core.automation.deps import AutomationDeps


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
    errors = []
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
            tracks = None

            if source == 'spotify':
                # Try authenticated API first, fall back to public embed scraper.
                if deps.spotify_client and deps.spotify_client.is_spotify_authenticated():
                    playlist_obj = deps.spotify_client.get_playlist_by_id(source_id)
                    if playlist_obj and playlist_obj.tracks:
                        tracks = []
                        for t in playlist_obj.tracks:
                            artist_name = t.artists[0] if t.artists else ''
                            track_dict = {
                                'track_name': t.name or '',
                                'artist_name': str(artist_name),
                                'album_name': t.album or '',
                                'duration_ms': t.duration_ms or 0,
                                'source_track_id': t.id or '',
                            }
                            # Spotify data IS official — auto-mark as discovered.
                            if t.id:
                                _album_obj = {'name': t.album or ''}
                                if getattr(t, 'image_url', None):
                                    _album_obj['images'] = [{'url': t.image_url, 'height': 600, 'width': 600}]
                                track_dict['extra_data'] = json.dumps({
                                    'discovered': True,
                                    'provider': 'spotify',
                                    'confidence': 1.0,
                                    'matched_data': {
                                        'id': t.id,
                                        'name': t.name or '',
                                        'artists': [{'name': str(a)} for a in (t.artists or [])],
                                        'album': _album_obj,
                                        'duration_ms': t.duration_ms or 0,
                                        'image_url': getattr(t, 'image_url', None),
                                    }
                                })
                            tracks.append(track_dict)

                # Fallback: public embed scraper (no auth needed).
                if tracks is None:
                    try:
                        from core.spotify_public_scraper import scrape_spotify_embed
                        embed_data = scrape_spotify_embed('playlist', source_id)
                        if embed_data and not embed_data.get('error') and embed_data.get('tracks'):
                            embed_album = embed_data.get('name', '') if embed_data.get('type') == 'album' else ''
                            tracks = []
                            for t in embed_data['tracks']:
                                artist_names = [a['name'] for a in t.get('artists', [])]
                                artist_name = artist_names[0] if artist_names else ''
                                track_dict = {
                                    'track_name': t.get('name', ''),
                                    'artist_name': artist_name,
                                    'album_name': embed_album,
                                    'duration_ms': t.get('duration_ms', 0),
                                    'source_track_id': t.get('id', ''),
                                }
                                # Store Spotify track ID hint but don't mark discovered —
                                # Discover step needs to run for proper album art.
                                if t.get('id'):
                                    track_dict['extra_data'] = json.dumps({
                                        'discovered': False,
                                        'spotify_hint': {
                                            'id': t['id'],
                                            'name': t.get('name', ''),
                                            'artists': t.get('artists', []),
                                        }
                                    })
                                tracks.append(track_dict)
                    except Exception as e:
                        deps.logger.warning(f"Spotify public scraper fallback failed for {source_id}: {e}")

            elif source == 'spotify_public':
                # source_playlist_id is an MD5 hash; extract actual Spotify ID from stored description (URL).
                try:
                    from core.spotify_public_scraper import parse_spotify_url, scrape_spotify_embed
                    spotify_url = pl.get('description', '')
                    parsed = parse_spotify_url(spotify_url) if spotify_url else None

                    # If Spotify is authenticated, use the full API (auto-discovers with album art).
                    if (parsed and parsed.get('type') == 'playlist'
                            and deps.spotify_client and deps.spotify_client.is_spotify_authenticated()):
                        playlist_obj = deps.spotify_client.get_playlist_by_id(parsed['id'])
                        if playlist_obj and playlist_obj.tracks:
                            tracks = []
                            for t in playlist_obj.tracks:
                                artist_name = t.artists[0] if t.artists else ''
                                track_dict = {
                                    'track_name': t.name or '',
                                    'artist_name': str(artist_name),
                                    'album_name': t.album or '',
                                    'duration_ms': t.duration_ms or 0,
                                    'source_track_id': t.id or '',
                                }
                                if t.id:
                                    _album_obj = {'name': t.album or ''}
                                    if getattr(t, 'image_url', None):
                                        _album_obj['images'] = [{'url': t.image_url, 'height': 600, 'width': 600}]
                                    track_dict['extra_data'] = json.dumps({
                                        'discovered': True,
                                        'provider': 'spotify',
                                        'confidence': 1.0,
                                        'matched_data': {
                                            'id': t.id,
                                            'name': t.name or '',
                                            'artists': [{'name': str(a)} for a in (t.artists or [])],
                                            'album': _album_obj,
                                            'duration_ms': t.duration_ms or 0,
                                            'image_url': getattr(t, 'image_url', None),
                                        }
                                    })
                                tracks.append(track_dict)

                    # Fallback: public embed scraper (no auth or album-type URL).
                    if tracks is None and parsed:
                        embed_data = scrape_spotify_embed(parsed['type'], parsed['id'])
                        if embed_data and not embed_data.get('error') and embed_data.get('tracks'):
                            embed_album = embed_data.get('name', '') if embed_data.get('type') == 'album' else ''
                            tracks = []
                            for t in embed_data['tracks']:
                                artist_names = [a['name'] for a in t.get('artists', [])]
                                artist_name = artist_names[0] if artist_names else ''
                                tracks.append({
                                    'track_name': t.get('name', ''),
                                    'artist_name': artist_name,
                                    'album_name': embed_album,
                                    'duration_ms': t.get('duration_ms', 0),
                                    'source_track_id': t.get('id', ''),
                                })
                                # No extra_data — let preservation code keep existing discovery data.
                except Exception as e:
                    deps.logger.warning(f"Spotify public playlist refresh failed for {source_id}: {e}")

            elif source == 'deezer':
                try:
                    deezer = deps.get_deezer_client()
                    playlist_data = deezer.get_playlist(source_id)
                    if playlist_data and playlist_data.get('tracks'):
                        tracks = []
                        for t in playlist_data['tracks']:
                            artist_name = t['artists'][0] if t.get('artists') else ''
                            tracks.append({
                                'track_name': t.get('name', ''),
                                'artist_name': str(artist_name),
                                'album_name': t.get('album', ''),
                                'duration_ms': t.get('duration_ms', 0),
                                'source_track_id': str(t.get('id', '')),
                            })
                except Exception as e:
                    deps.logger.warning(f"Deezer playlist refresh failed for {source_id}: {e}")

            elif source == 'tidal':
                if not deps.tidal_client or not deps.tidal_client.is_authenticated():
                    deps.logger.warning(f"Tidal not authenticated — skipping refresh for '{pl.get('name', '')}'")
                    deps.update_progress(
                        auto_id,
                        log_line=f'Skipped "{pl.get("name", "")}" — Tidal not authenticated',
                        log_type='skip',
                    )
                    continue
                full_playlist = deps.tidal_client.get_playlist(source_id)
                if full_playlist and full_playlist.tracks:
                    tracks = []
                    for t in full_playlist.tracks:
                        artist_name = t.artists[0] if t.artists else ''
                        tracks.append({
                            'track_name': t.name or '',
                            'artist_name': str(artist_name),
                            'album_name': t.album or '',
                            'duration_ms': t.duration_ms or 0,
                            'source_track_id': t.id or '',
                        })

            elif source == 'youtube':
                # source_playlist_id is now a deterministic hash; use stored description (original URL) for refresh.
                yt_url = pl.get('description', '') or f"https://www.youtube.com/playlist?list={source_id}"
                playlist_data = deps.parse_youtube_playlist(yt_url)
                if playlist_data and playlist_data.get('tracks'):
                    tracks = []
                    for t in playlist_data['tracks']:
                        artist_name = t['artists'][0] if t.get('artists') else ''
                        tracks.append({
                            'track_name': t.get('name', ''),
                            'artist_name': str(artist_name),
                            'album_name': '',
                            'duration_ms': t.get('duration_ms', 0),
                            'source_track_id': t.get('id', ''),
                        })

            if tracks is not None:
                # Compare old vs new track IDs to detect changes.
                old_tracks = db.get_mirrored_playlist_tracks(pl['id']) if pl.get('id') else []
                old_ids = {t.get('source_track_id') for t in old_tracks if t.get('source_track_id')}
                new_ids = {t.get('source_track_id') for t in tracks if t.get('source_track_id')}

                # Preserve existing discovery extra_data for tracks that still exist.
                old_extra_map = db.get_mirrored_tracks_extra_data_map(pl['id']) if pl.get('id') else {}
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
                    owner=pl.get('owner'),
                    image_url=pl.get('image_url'),
                )
                refreshed += 1

                # Emit playlist_changed if tracks actually changed.
                if old_ids != new_ids:
                    added_count = len(new_ids - old_ids)
                    removed_count = len(old_ids - new_ids)
                    deps.logger.info(
                        f"[AUTOMATION] Playlist changed: '{pl.get('name', '')}' — "
                        f"{added_count} added, {removed_count} removed (old={len(old_ids)}, new={len(new_ids)})"
                    )
                    deps.update_progress(
                        auto_id,
                        log_line=f'"{pl.get("name", "")}" — {added_count} added, {removed_count} removed',
                        log_type='success',
                    )
                    try:
                        if deps.engine:
                            deps.engine.emit('playlist_changed', {
                                'playlist_name': pl.get('name', ''),
                                'playlist_id': str(pl.get('id', '')),
                                'old_count': str(len(old_ids)),
                                'new_count': str(len(new_ids)),
                                'added': str(added_count),
                                'removed': str(removed_count),
                            })
                    except Exception as e:
                        deps.logger.debug("playlist_synced automation emit failed: %s", e)
                else:
                    deps.logger.warning(f"[AUTOMATION] No changes: '{pl.get('name', '')}' (tracks={len(old_ids)})")
                    deps.update_progress(
                        auto_id,
                        log_line=f'No changes: "{pl.get("name", "")}"',
                        log_type='skip',
                    )
        except Exception as e:
            errors.append(f"{pl.get('name', '?')}: {str(e)}")
            deps.update_progress(
                auto_id,
                log_line=f'Error: {pl.get("name", "?")} — {str(e)}',
                log_type='error',
            )
    return {'status': 'completed', 'refreshed': str(refreshed), 'errors': str(len(errors))}
