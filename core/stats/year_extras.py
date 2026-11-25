"""the extras around Your Year in Listening.

the story itself is MusicDatabase.get_year_in_listening. this holds what was
added around it: the longest streak and top genres it reports, the "save your
year as a playlist" playlist, the soundtrack it plays, and the season the
dashboard announces it in.

everything here reads the same twelve-month window and the same listening
pile as the story, so a private session or a pile the profile keeps out of its
stats stays out of all of it the same way.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("stats.year_extras")

PLAYLIST_SIZES = (50, 100)
SOUNDTRACK_TRACKS = 3
BANNER_KEY = 'year_banner_dismissed'
NOTIFIED_KEY = 'year_banner_notified'


# ── the story's new numbers ──────────────────────────────────────────────────

def longest_streak(days: Iterable[Any]) -> Dict[str, Any]:
    """the longest run of consecutive days with music, from ISO dates in any
    order. ties keep the earlier run. {'days': 0, ...} with none."""
    parsed = sorted({d for d in (_date(x) for x in days) if d})
    best = {'days': 0, 'start': None, 'end': None}
    run_start = prev = None
    run = 0
    for day in parsed:
        if prev is not None and day == prev + timedelta(days=1):
            run += 1
        else:
            run_start, run = day, 1
        if run > best['days']:
            best = {'days': run, 'start': run_start.isoformat(), 'end': day.isoformat()}
        prev = day
    return best


def _date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def top_genres(genre_counts: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
    """[{genre, plays, share}] most played first, share of the plays the
    library could put a genre on. names break ties so the order is stable."""
    clean = {str(g).strip(): float(n or 0) for g, n in (genre_counts or {}).items()
             if str(g).strip() and float(n or 0) > 0}
    total = sum(clean.values())
    if total <= 0:
        return []
    ranked = sorted(clean.items(), key=lambda kv: (-kv[1], kv[0].casefold()))[:limit]
    return [{'genre': g, 'plays': int(n), 'share': round(n / total, 3)} for g, n in ranked]


# ── which year, and when to say so ───────────────────────────────────────────

def story_year(period_end: Any) -> int:
    """the year the story is about. the window is the twelve months up to
    today, so in early January it is still mostly last year."""
    day = _date(period_end) or date.today()
    return day.year - 1 if day.month == 1 else day.year


def season_year(today: Optional[date] = None) -> Optional[int]:
    """the year being announced, Dec 1 through Jan 15, else None."""
    today = today or date.today()
    if today.month == 12:
        return today.year
    if today.month == 1 and today.day <= 15:
        return today.year - 1
    return None


def playlist_name(year: int) -> str:
    return f"Your {int(year)}"


def playlist_source_id(year: int) -> str:
    """the fixed id that makes saving twice update one playlist."""
    return f"your-year-{int(year)}"


def banner_state(database, profile_id, *, today: Optional[date] = None,
                 has_plays: Optional[bool] = None) -> Dict[str, Any]:
    """{show, year} for the dashboard's "Your <year> is ready". shown in the
    season, once there is something to show, until this profile dismisses
    that year's banner."""
    year = season_year(today)
    if year is None:
        return {'show': False, 'year': None}
    dismissed = database.get_metadata(_key(BANNER_KEY, profile_id))
    if dismissed and str(dismissed) == str(year):
        return {'show': False, 'year': year}
    if has_plays is False:
        return {'show': False, 'year': year}
    return {'show': True, 'year': year}


def dismiss_banner(database, profile_id, year: int) -> None:
    database.set_metadata(_key(BANNER_KEY, profile_id), str(int(year)))


def notify_once(database, profile_id, year: int, notify) -> bool:
    """one bell note per profile per season, so someone who never opens the
    dashboard still hears about it. True when it sent one."""
    key = _key(NOTIFIED_KEY, profile_id)
    if str(database.get_metadata(key) or '') == str(year):
        return False
    database.set_metadata(key, str(int(year)))
    try:
        notify(int(profile_id), f"Your {year} in listening is ready", 'info', '/stats?story=year')
    except Exception as e:  # noqa: BLE001 - a note is a courtesy
        logger.debug("year notification failed: %s", e)
    return True


def _key(base: str, profile_id: Any) -> str:
    try:
        return f"{base}:{int(profile_id)}"
    except (TypeError, ValueError):
        return f"{base}:1"


# ── save your year as a playlist ─────────────────────────────────────────────

def clamp_size(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return PLAYLIST_SIZES[0]
    return n if n in PLAYLIST_SIZES else PLAYLIST_SIZES[0]


def save_year_playlist(database, profile_id, year: int, tracks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """write the year's top songs into this profile's "Your <year>" user
    playlist, making it the first time and replacing its songs after that, so
    saving again never makes a second one. a song's match carries over."""
    from core.playlists import user_playlists as up

    source_id = playlist_source_id(year)
    rows = [t for t in (up.clean_track({
        'track_name': t.get('title'), 'artist_name': t.get('artist'),
        'album_name': t.get('album'), 'duration_ms': t.get('duration_ms')}) for t in tracks) if t]
    if not rows:
        raise up.UserPlaylistError('Nothing played this year to save')

    existing = next((p for p in database.get_mirrored_playlists(profile_id=int(profile_id)) or []
                     if up.is_user_playlist(p) and p.get('source_playlist_id') == source_id), None)
    # keep each song's match: rewrite from the stored rows where the song is the same
    kept = {}
    if existing:
        for row in up._stored_tracks(database, existing['id']):
            kept.setdefault(up.track_key(row), row)
    merged = [kept.get(up.track_key(r), r) for r in rows]
    playlist_id = database.mirror_playlist(
        source=up.USER_PLAYLIST_SOURCE,
        source_playlist_id=source_id,
        name=existing.get('name') if existing else playlist_name(year),
        tracks=merged,
        profile_id=int(profile_id),
        description=None if existing else f"Your most played songs of {year}, from Your Year in Listening.",
    )
    if playlist_id is None:
        raise RuntimeError('could not save the playlist')
    logger.info("saved %s (%s songs) for profile %s", playlist_name(year), len(merged), profile_id)
    return {'id': int(playlist_id), 'name': existing.get('name') if existing else playlist_name(year),
            'track_count': len(merged), 'created': existing is None}


# ── the soundtrack ───────────────────────────────────────────────────────────

def soundtrack_start_ms(duration_ms: Any) -> int:
    """where to drop the needle: about a third in, where most songs have
    reached a chorus, leaving at least 45 seconds to play. short songs start
    at the top."""
    try:
        total = int(duration_ms or 0)
    except (TypeError, ValueError):
        return 0
    if total < 90_000:
        return 0
    start = int(total * 0.3)
    return max(0, min(start, total - 45_000))


def soundtrack_rows(database, tracks: List[Dict[str, Any]], image_url_fixer,
                    limit: int = SOUNDTRACK_TRACKS) -> List[Dict[str, Any]]:
    """the year's top songs that are in the library with a live file, in the
    player's queue shape (see core/stats/queries.get_album_play_tracks), with
    where to start each one."""
    out: List[Dict[str, Any]] = []
    ids = [t.get('lib2_track_id') for t in tracks if t.get('lib2_track_id')]
    if not ids:
        return out
    conn = None
    try:
        conn = database._get_connection()
        for track in tracks:
            if len(out) >= limit:
                break
            tid = track.get('lib2_track_id')
            if not tid:
                continue
            row = conn.execute(
                """
                SELECT t.id, t.title, COALESCE(NULLIF(t.track_artist, ''), ar.name), al.title,
                       f.path, f.bitrate, COALESCE(ar.canonical_artist_id, ar.id), t.album_id,
                       al.image_url, t.duration
                FROM lib2_tracks t
                JOIN lib2_albums al ON al.id = t.album_id
                JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                JOIN lib2_track_files f ON f.id = (
                    SELECT preferred.id FROM lib2_track_files preferred
                    WHERE preferred.track_id = t.id
                      AND preferred.path IS NOT NULL AND TRIM(preferred.path) != ''
                      AND COALESCE(preferred.file_state, 'active') = 'active'
                    ORDER BY preferred.is_primary DESC, preferred.id LIMIT 1)
                WHERE t.id = ?
                """, (int(tid),)).fetchone()
            if not row:
                continue
            duration = row[9] or track.get('duration_ms') or 0
            out.append({
                'id': row[0], 'title': row[1], 'artist': row[2], 'album': row[3],
                'file_path': row[4], 'bitrate': row[5], 'artist_id': row[6], 'album_id': row[7],
                'image_url': image_url_fixer(row[8]) if row[8] else None,
                'duration_ms': duration, 'plays': track.get('plays', 0),
                'start_ms': soundtrack_start_ms(duration),
            })
    except Exception as e:  # noqa: BLE001 - no soundtrack is a quiet story, not an error
        logger.debug("year soundtrack lookup failed: %s", e)
    finally:
        if conn:
            conn.close()
    return out


def today_local() -> date:
    return datetime.now().date()
