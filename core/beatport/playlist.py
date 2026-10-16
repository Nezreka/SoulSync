"""a beatport list as a playlist: the track shape discovery reads, and the
mirror it lands in.

every beatport list goes the way every other playlist source goes: identify
each track on the metadata source (isrc first), then sync or download the
identified tracks. albums come from the metadata source, never from the chart
name. the mirror is keyed by the list's ref, so it can refresh like any other.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.beatport.refs import parse_ref
from utils.logging_config import get_logger

logger = get_logger("beatport.playlist")

SOURCE = 'beatport'


def display_name(track: Dict[str, Any]) -> str:
    """'Title (Mix)', leaving out "original mix" the way beatport's own
    store titles do."""
    title = str(track.get('title') or track.get('name') or '').strip()
    mix = str(track.get('mix_name') or '').strip()
    if mix and mix.lower() != 'original mix' and f"({mix.lower()})" not in title.lower():
        return f"{title} ({mix})"
    return title


def _artist_names(track: Dict[str, Any]) -> List[str]:
    names = [a.get('name') for a in track.get('artists') or [] if isinstance(a, dict) and a.get('name')]
    if names:
        return names
    raw = track.get('artist') or ''
    return [p.strip() for p in str(raw).split(',') if p.strip()]


def chart_track(track: Dict[str, Any]) -> Dict[str, Any]:
    """one normalised beatport track in the chart_data shape the discovery
    worker and modal read (name, artists as names, isrc, id)."""
    return {
        'id': track.get('id') or '',
        'name': display_name(track),
        'title': track.get('title') or '',
        'mix_name': track.get('mix_name') or '',
        'artists': _artist_names(track) or ['Unknown Artist'],
        'isrc': track.get('isrc') or '',
        'duration_ms': int(track.get('duration_ms') or 0),
        'album': track.get('release_name') or '',
        'image_url': track.get('release_image') or '',
        'url': track.get('url') or '',
        'label': track.get('label') or '',
        'bpm': track.get('bpm'),
        'key': track.get('key'),
        'genre': track.get('genre'),
        'release_date': track.get('release_date') or '',
        'position': track.get('position'),
    }


def chart_data(listed: Dict[str, Any]) -> Dict[str, Any]:
    """fetch_list's answer as the chart_data body discovery/start takes."""
    tracks = [chart_track(t) for t in listed.get('tracks') or []]
    return {
        'hash': listed['ref'],
        'ref': listed['ref'],
        'name': listed['name'],
        'image': listed.get('image') or '',
        'url': listed.get('url') or '',
        'creator': listed.get('creator') or 'Beatport',
        'refreshable': bool(listed.get('refreshable', True)),
        'track_count': len(tracks),
        'tracks': tracks,
    }


def mirror_track(track: Dict[str, Any]) -> Dict[str, Any]:
    """a chart_data (or normalised) track as a mirror row. no extra_data on
    purpose: a refresh keeps the discovery results stored on rows whose
    source_track_id survives, and any extra_data here would replace them."""
    names = track.get('artists') if isinstance(track.get('artists'), list) else _artist_names(track)
    names = [n.get('name') if isinstance(n, dict) else n for n in names or []]
    return {
        'track_name': display_name(track),
        'artist_name': ', '.join(n for n in names if n) or 'Unknown Artist',
        'album_name': track.get('album') or track.get('release_name') or '',
        'duration_ms': int(track.get('duration_ms') or 0),
        'source_track_id': track.get('id') or '',
        'image_url': track.get('image_url') or track.get('release_image') or None,
    }


def mirror_chart(db: Any, ref: str, chart: Dict[str, Any], profile_id: int) -> Optional[int]:
    """upsert the mirror for a beatport list. only a real ref gets one: an old
    random chart hash could never be refreshed, so it would just be clutter."""
    parsed = parse_ref(ref)
    tracks = chart.get('tracks') or []
    if parsed is None or not tracks:
        return None
    try:
        return db.mirror_playlist(
            source=SOURCE,
            source_playlist_id=str(parsed),
            name=chart.get('name') or parsed.default_name(),
            tracks=[mirror_track(t) for t in tracks],
            profile_id=profile_id,
            description=parsed.url,
            owner=chart.get('creator') or 'Beatport',
            image_url=chart.get('image') or '',
        )
    except Exception as e:  # noqa: BLE001 - a failed mirror must not stop discovery
        logger.warning("could not mirror beatport list %s: %s", ref, e)
        return None


def save_snapshot(db: Any, ref: str, profile_id: int, today: Optional[str] = None,
                  fetch=None) -> Optional[Dict[str, Any]]:
    """freeze a beatport list as it is today: a dated mirror ("Hype Top 10 ·
    2026-10-16") that never refreshes, next to the live one that does. taking
    it twice on one day updates that day's copy. returns {playlist_id, name,
    track_count} or None when the ref is junk or beatport didn't answer."""
    import datetime as _dt
    from core.beatport.refs import parse_ref, snapshot_id

    parsed = parse_ref(ref)
    if parsed is None:
        return None
    day = today or _dt.date.today().isoformat()
    if fetch is None:
        from core.beatport.fetch import fetch_list
        listed = fetch_list(parsed, use_cache=False)
    else:
        listed = fetch(str(parsed))
    if not listed or not listed.get('tracks'):
        return None
    chart = chart_data(listed)
    name = f"{chart['name']} · {day}"
    playlist_id = db.mirror_playlist(
        source=SOURCE,
        source_playlist_id=snapshot_id(parsed, day),
        name=name,
        tracks=[mirror_track(t) for t in chart['tracks']],
        profile_id=profile_id,
        description=f"{parsed.url} (snapshot {day})",
        owner=chart.get('creator') or 'Beatport',
        image_url=chart.get('image') or '',
    )
    if playlist_id is None:
        return None
    return {'playlist_id': playlist_id, 'name': name, 'track_count': len(chart['tracks'])}
