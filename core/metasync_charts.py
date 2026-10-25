"""MetaSync's SIN charts, shown in SoulSync as playlists you can open.

Once a day the "Refresh MetaSync Charts" system automation asks the MetaSync
node for the network's charts (Trending, Most Wanted, ...) over the node's
local API (GET /local/charts, with the local key SoulSync got at pairing).
They are OFFERS: once every track is matched they show up as a rail on the
dashboard and a shelf on Discover, and nothing is synced to a media server,
wishlisted or downloaded unless somebody opens one and presses Sync or
Download themselves.

How a chart becomes a playlist without a discovery wait:

* every chart is kept here (``metasync_charts``) and as a hidden mirrored
  playlist owned by TEMPLATE_PROFILE (0, no real profile), which identifies
  itself in the background the moment it arrives — the same
  core.playlists.auto_identify pass My Playlists use;
* opening a chart gives the active profile its OWN mirrored playlist
  (source 'metasync'), copied from the template with every match already in
  it, so it opens ready to Sync / Download;
* each weekly handoff updates the template and every profile's copy in place
  (mirror_playlist keeps each track's match by its id), so an Auto-Sync
  schedule on your copy always follows the current chart.

Profile 0 owns no UI and is never anyone's active profile, so the templates
never appear in a playlist list and no automation (they default to profile 1)
ever touches them. A chart that leaves the network loses its template; a
profile's own copy stays theirs.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, List, Optional

import requests

from utils.logging_config import get_logger

logger = get_logger("metasync_charts")

SOURCE = 'metasync'
TEMPLATE_PROFILE = 0

NODE_TIMEOUT_SECONDS = 20

MAX_CHARTS = 100
MAX_TRACKS_PER_CHART = 500
_KEY = re.compile(r'^[a-z0-9][a-z0-9-]{0,63}$')


class ChartsError(ValueError):
    """Charts from the node that do not have the expected shape."""


def ensure_schema(db: Any) -> None:
    with db._get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS metasync_charts (
                key TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                week TEXT,
                position INTEGER NOT NULL DEFAULT 0,
                image_url TEXT,
                tracks_json TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ''


def chart_track_to_mirror(track: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A chart track (the Spotify-shaped subset SIN publishes: id = the
    MusicBrainz recording id, name, artists, album, duration_ms, image_url)
    as a mirrored-playlist track. None when it has no name."""
    if not isinstance(track, dict):
        return None
    name = _text(track.get('name'), 512)
    if not name:
        return None
    artists = track.get('artists') if isinstance(track.get('artists'), list) else []
    artist = ', '.join(
        _text(a.get('name') if isinstance(a, dict) else a, 512) for a in artists
    ).strip(', ')
    album = track.get('album')
    album_name = _text(album.get('name') if isinstance(album, dict) else album, 512)
    image = _text(track.get('image_url'), 512)
    if not image and isinstance(album, dict):
        images = album.get('images') if isinstance(album.get('images'), list) else []
        image = _text((images[0] or {}).get('url') if images and isinstance(images[0], dict) else '', 512)
    duration = track.get('duration_ms')
    return {
        'track_name': name,
        'artist_name': artist,
        'album_name': album_name,
        'duration_ms': duration if isinstance(duration, int) and duration > 0 else 0,
        'image_url': image or None,
        'source_track_id': _text(track.get('id'), 128) or None,
    }


def validate(payload: Any) -> List[Dict[str, Any]]:
    """The node's charts as a list of {key, name, week, position, image_url,
    tracks}. Raises ChartsError on anything that is not the charts shape."""
    if not isinstance(payload, dict):
        raise ChartsError('body must be a JSON object')
    week = _text(payload.get('week'), 16)
    playlists = payload.get('playlists')
    if not isinstance(playlists, list) or len(playlists) > MAX_CHARTS:
        raise ChartsError(f'playlists must be a list of at most {MAX_CHARTS}')
    out: List[Dict[str, Any]] = []
    seen = set()
    for position, raw in enumerate(playlists):
        if not isinstance(raw, dict):
            raise ChartsError(f'playlists[{position}] must be an object')
        key = _text(raw.get('key'), 64)
        name = _text(raw.get('name'), 200)
        if not _KEY.match(key) or key in seen:
            raise ChartsError(f'playlists[{position}].key must be a unique lowercase slug')
        if not name:
            raise ChartsError(f'playlists[{position}].name is required')
        raw_tracks = raw.get('tracks')
        if not isinstance(raw_tracks, list) or len(raw_tracks) > MAX_TRACKS_PER_CHART:
            raise ChartsError(f'playlists[{position}].tracks must be a list of at most {MAX_TRACKS_PER_CHART}')
        tracks = [t for t in (chart_track_to_mirror(r) for r in raw_tracks) if t]
        seen.add(key)
        if not tracks:
            continue  # an empty chart is not offered at all
        out.append({
            'key': key,
            'name': name,
            'week': week,
            'position': position,
            'image_url': next((t['image_url'] for t in tracks if t.get('image_url')), None),
            'tracks': tracks,
        })
    return out


def _mirror_id(db: Any, key: str, profile_id: int) -> Optional[int]:
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT id FROM mirrored_playlists WHERE source = ? AND source_playlist_id = ? AND profile_id = ?",
            (SOURCE, key, int(profile_id)),
        ).fetchone()
    return int(row[0]) if row else None


def _stored_tracks(db: Any, playlist_id: int) -> List[Dict[str, Any]]:
    """A mirror's rows in mirror_playlist's input shape, matches kept."""
    out = []
    for row in db.get_mirrored_playlist_tracks(playlist_id):
        extra = row.get('extra_data')
        if isinstance(extra, str):
            try:
                extra = json.loads(extra)
            except (json.JSONDecodeError, TypeError):
                extra = None
        out.append({
            'track_name': row.get('track_name') or '',
            'artist_name': row.get('artist_name') or '',
            'album_name': row.get('album_name') or '',
            'duration_ms': row.get('duration_ms') or 0,
            'image_url': row.get('image_url'),
            'source_track_id': row.get('source_track_id'),
            'extra_data': extra or None,
        })
    return out


def store_charts(db: Any, payload: Any, identify: Callable[[int], Any]) -> Dict[str, Any]:
    """Take the charts the MetaSync node offers: keep them, refresh the
    hidden templates (and queue their identify), update every profile's
    open copy, and drop the template of any chart no longer offered.
    Never syncs, wishlists or downloads anything."""
    charts = validate(payload)
    ensure_schema(db)
    keys = [c['key'] for c in charts]
    with db._get_connection() as conn:
        for chart in charts:
            conn.execute("""
                INSERT INTO metasync_charts (key, name, week, position, image_url, tracks_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    name = excluded.name, week = excluded.week, position = excluded.position,
                    image_url = excluded.image_url, tracks_json = excluded.tracks_json,
                    updated_at = CURRENT_TIMESTAMP
            """, (chart['key'], chart['name'], chart['week'], chart['position'],
                  chart['image_url'], json.dumps(chart['tracks'])))
        placeholders = ','.join('?' for _ in keys) or "''"
        removed = [r[0] for r in conn.execute(
            f"SELECT key FROM metasync_charts WHERE key NOT IN ({placeholders})", keys).fetchall()]
        conn.execute(f"DELETE FROM metasync_charts WHERE key NOT IN ({placeholders})", keys)
        copies = conn.execute(
            "SELECT id, source_playlist_id, profile_id FROM mirrored_playlists WHERE source = ?",
            (SOURCE,),
        ).fetchall()
        conn.commit()

    by_key = {c['key']: c for c in charts}
    for chart in charts:
        template_id = db.mirror_playlist(
            SOURCE, chart['key'], chart['name'], chart['tracks'],
            profile_id=TEMPLATE_PROFILE, image_url=chart['image_url'], owner='MetaSync',
        )
        if template_id:
            identify(int(template_id))
    for playlist_id, key, profile_id in copies:
        if int(profile_id) == TEMPLATE_PROFILE:
            if key not in by_key:
                db.delete_mirrored_playlist(int(playlist_id))
            continue
        chart = by_key.get(key)
        if not chart:
            continue  # their copy of a chart that left the network stays theirs
        db.mirror_playlist(
            SOURCE, key, chart['name'], chart['tracks'],
            profile_id=int(profile_id), image_url=chart['image_url'], owner='MetaSync',
        )
        identify(int(playlist_id))
    logger.info("MetaSync charts stored: %s offered, %s removed", len(charts), len(removed))
    return {'stored': len(charts), 'removed': len(removed)}


def list_charts(db: Any, profile_id: Optional[int]) -> List[Dict[str, Any]]:
    """The offered charts in the network's order, with the active profile's
    own copy (when they opened one) and how far identify has got."""
    ensure_schema(db)
    with db._get_connection() as conn:
        rows = conn.execute(
            "SELECT key, name, week, image_url, tracks_json, updated_at FROM metasync_charts "
            "ORDER BY position, key").fetchall()
    out = []
    for key, name, week, image_url, tracks_json, updated_at in rows:
        try:
            tracks = json.loads(tracks_json or '[]')
        except (json.JSONDecodeError, TypeError):
            tracks = []
        template_id = _mirror_id(db, key, TEMPLATE_PROFILE)
        matched, total = (0, len(tracks))
        if template_id:
            try:
                matched, total = db.get_mirrored_playlist_discovery_counts(template_id)
            except Exception as e:  # noqa: BLE001 - a count is decoration
                logger.debug("chart %s: discovery counts unreadable: %s", key, e)
        cover_tiles = []
        for t in tracks:
            url = t.get('image_url')
            if url and url not in cover_tiles:
                cover_tiles.append(url)
            if len(cover_tiles) == 4:
                break
        own_id = _mirror_id(db, key, profile_id) if profile_id is not None else None
        # a cover they generated for their copy beats the network's chart art
        own = db.get_mirrored_playlist(own_id) if own_id else None
        if own and own.get('custom_cover') and own.get('image_url'):
            image_url = own['image_url']
        out.append({
            'key': key,
            'name': name,
            'week': week,
            'image_url': image_url,
            'cover_tiles': cover_tiles,
            'track_count': len(tracks),
            'matched': matched,
            'ready': bool(total) and matched >= total,
            'playlist_id': own_id,
            'updated_at': updated_at,
        })
    return out


def open_chart(db: Any, key: str, profile_id: int, identify: Callable[[int], Any]) -> Optional[int]:
    """The active profile's own playlist for a chart, creating it from the
    template (matches included) the first time. None for an unknown key."""
    ensure_schema(db)
    existing = _mirror_id(db, key, profile_id)
    if existing:
        return existing
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT name, image_url, tracks_json FROM metasync_charts WHERE key = ?", (key,),
        ).fetchone()
    if not row:
        return None
    name, image_url, tracks_json = row
    template_id = _mirror_id(db, key, TEMPLATE_PROFILE)
    tracks = _stored_tracks(db, template_id) if template_id else json.loads(tracks_json or '[]')
    playlist_id = db.mirror_playlist(
        SOURCE, key, name, tracks, profile_id=int(profile_id), image_url=image_url, owner='MetaSync',
    )
    if playlist_id:
        identify(int(playlist_id))  # only what the template had not matched yet
    return int(playlist_id) if playlist_id else None


def refresh_from_node(db: Any, config_get: Callable[..., Any], identify: Callable[[int], Any]) -> Dict[str, Any]:
    """The daily pull: ask the connected MetaSync node for its charts and
    store them (store_charts). Not connected is a quiet skip, never an
    error — most installs never connect MetaSync. Returns the automation's
    result dict."""
    node_url = str(config_get('metasync.node_url', '') or '').strip().rstrip('/')
    local_key = str(config_get('metasync.local_api_key', '') or '').strip()
    if not node_url or not local_key:
        return {'status': 'skipped', 'reason': 'MetaSync is not connected'}
    try:
        resp = requests.get(
            f"{node_url}/local/charts",
            headers={'Authorization': f'Bearer {local_key}', 'Accept': 'application/json'},
            timeout=NODE_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        return {'status': 'error', 'error': f'MetaSync is unreachable at {node_url}: {e}'}
    if resp.status_code == 401:
        return {'status': 'error', 'error': 'MetaSync rejected the stored local key. Reconnect MetaSync.'}
    if resp.status_code == 404:
        return {'status': 'error', 'error': 'This MetaSync is too old to offer charts. Update MetaSync.'}
    if resp.status_code != 200:
        return {'status': 'error', 'error': f'MetaSync answered HTTP {resp.status_code}'}
    try:
        payload = resp.json()
    except ValueError:
        return {'status': 'error', 'error': 'MetaSync answered with something that is not JSON'}
    try:
        result = store_charts(db, payload, identify)
    except ChartsError as e:
        return {'status': 'error', 'error': f'MetaSync sent charts in a shape SoulSync does not accept: {e}'}
    return {'status': 'completed', **result}
