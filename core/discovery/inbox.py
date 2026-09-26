"""The discovery inbox (plan phase 6): one place for things worth coming back to.

Sources, each independent (one failing never blanks the rest):

- new_release: a watchlist artist's release from the last few weeks
  (discovery_recent_albums, which the watchlist scan fills per profile).
- upcoming: the same, dated in the future. The scan already caches these;
  the radar skips them, the inbox is where they belong.
- saved_rec: a recommendation saved from its ⋯ menu.
- concert: a watchlist artist playing soon, when Ticketmaster is set up.

States per profile: unread, saved, dismissed, added. A refresh adds new items
as unread and never resets a state someone chose. "Added" is observed, not
clicked: the release is in the library, the saved artist is on the watchlist.

Blocked (and not-now) artists never show, the same filter as every discover
surface (core/discovery/blocked.py).
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from utils.logging_config import get_logger

logger = get_logger("discovery.inbox")

KINDS = ('new_release', 'upcoming', 'saved_rec', 'concert')
STATES = ('unread', 'saved', 'dismissed', 'added')
SOURCES = ('releases', 'concerts')

NEW_RELEASE_DAYS = 30        # how far back a release still counts as news
CONCERT_ARTISTS = 25         # watchlist artists asked about per refresh
STALE_AFTER = timedelta(hours=6)
KEEP_DISMISSED = timedelta(days=90)


def _norm(text: Any) -> str:
    return str(text or '').strip().casefold()


def item_key(*parts: Any) -> str:
    return '\x1f'.join(_norm(p) for p in parts)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(dt: datetime) -> str:
    return dt.strftime('%Y-%m-%d %H:%M:%S')


# ---- storage ----------------------------------------------------------------

def upsert(database, profile_id: int, kind: str, key: str, *, title: str,
           artist_name: str = '', image_url: str = '', item_date: str = '',
           payload: Optional[Dict[str, Any]] = None, state: str = 'unread') -> Optional[int]:
    """Add an item, or refresh what we know about it. A state someone chose
    (saved, dismissed) is never reset by a refresh; ``state`` is only the
    starting one."""
    if kind not in KINDS or state not in STATES or not key or not title:
        return None
    body = json.dumps(payload or {})
    with database._get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO discovery_inbox (profile_id, kind, entity_key, title, artist_name, "
            "image_url, item_date, payload_json, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(profile_id, kind, entity_key) DO UPDATE SET "
            "title = excluded.title, artist_name = excluded.artist_name, "
            "image_url = COALESCE(NULLIF(excluded.image_url, ''), discovery_inbox.image_url), "
            "item_date = excluded.item_date, payload_json = excluded.payload_json, "
            "updated_at = CURRENT_TIMESTAMP",
            (profile_id, kind, key, title, artist_name, image_url, item_date, body, state))
        cur.execute("SELECT id FROM discovery_inbox WHERE profile_id = ? AND kind = ? AND entity_key = ?",
                    (profile_id, kind, key))
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None


def set_state(database, profile_id: int, item_id: int, state: str) -> bool:
    if state not in STATES:
        return False
    with database._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE discovery_inbox SET state = ?, updated_at = CURRENT_TIMESTAMP "
                    "WHERE id = ? AND profile_id = ?", (state, int(item_id), profile_id))
        conn.commit()
        return cur.rowcount > 0


def dismiss_all_unread(database, profile_id: int) -> int:
    with database._get_connection() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE discovery_inbox SET state = 'dismissed', updated_at = CURRENT_TIMESTAMP "
                    "WHERE profile_id = ? AND state = 'unread'", (profile_id,))
        conn.commit()
        return cur.rowcount


def _rows(database, profile_id: int, states: Iterable[str]) -> List[Dict[str, Any]]:
    states = [s for s in states if s in STATES]
    if not states:
        return []
    marks = ','.join('?' * len(states))
    with database._get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            f"SELECT id, kind, entity_key, title, artist_name, image_url, item_date, payload_json, "
            f"state, created_at, updated_at FROM discovery_inbox "
            f"WHERE profile_id = ? AND state IN ({marks})", (profile_id, *states))
        out = []
        for r in cur.fetchall():
            row = dict(r)
            try:
                row['payload'] = json.loads(row.pop('payload_json') or '{}')
            except (TypeError, ValueError):
                row['payload'] = {}
            out.append(row)
        return out


def _visible(database, profile_id: int, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    from core.discovery.blocked import BlockedArtists
    hidden = BlockedArtists.load(database, profile_id)
    if hidden.is_empty:
        return rows
    return [r for r in rows if not hidden.blocks_work(
        {'artist_name': r.get('artist_name'), 'name': r.get('title')})]


def _ordered(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """New releases and saved recs newest first; upcoming releases and
    concerts soonest first, after them."""
    out: List[Dict[str, Any]] = []
    for kind, newest_first in (('new_release', True), ('saved_rec', True),
                               ('upcoming', False), ('concert', False)):
        group = [r for r in rows if r.get('kind') == kind]
        key = 'updated_at' if kind == 'saved_rec' else 'item_date'
        group.sort(key=lambda r: (r.get(key) or '', r.get('id') or 0), reverse=newest_first)
        out.extend(group)
    return out


def list_items(database, profile_id: int, view: str = 'new') -> List[Dict[str, Any]]:
    """``new`` is the unread; ``saved`` what you kept."""
    states = ('saved',) if view == 'saved' else ('unread',)
    return _ordered(_visible(database, profile_id, _rows(database, profile_id, states)))


def unread_count(database, profile_id: int) -> int:
    return len(_visible(database, profile_id, _rows(database, profile_id, ('unread',))))


# ---- sources ------------------------------------------------------------------

def _watchlist(database, profile_id: int) -> Dict[str, Any]:
    return {_norm(a.artist_name): a for a in (database.get_watchlist_artists(profile_id=profile_id) or [])
            if getattr(a, 'artist_name', None)}


def _album_payload(album: Dict[str, Any]) -> Dict[str, Any]:
    ids = {src: album.get(col) for src, col in (('spotify', 'album_spotify_id'),
                                                 ('itunes', 'album_itunes_id'),
                                                 ('deezer', 'album_deezer_id')) if album.get(col)}
    return {'ids': ids, 'source': album.get('source'), 'album_type': album.get('album_type')}


def collect_releases(database, profile_id: int, today: Optional[date] = None) -> int:
    """Watchlist artists' releases from the last NEW_RELEASE_DAYS, and the
    ones not out yet."""
    today = today or date.today()
    watched = _watchlist(database, profile_id)
    if not watched:
        return 0
    albums = database.get_discovery_recent_albums(limit=500, profile_id=profile_id) or []
    oldest = (today - timedelta(days=NEW_RELEASE_DAYS)).isoformat()
    added = 0
    for album in albums:
        artist = album.get('artist_name') or ''
        released = str(album.get('release_date') or '')[:10]
        if _norm(artist) not in watched or not album.get('album_name') or len(released) < 4:
            continue
        kind = 'upcoming' if released > today.isoformat() else 'new_release'
        if kind == 'new_release' and released < oldest:
            continue
        if upsert(database, profile_id, kind, item_key(artist, album['album_name']),
                  title=album['album_name'], artist_name=artist,
                  image_url=album.get('album_cover_url') or '', item_date=released,
                  payload=_album_payload(album)):
            added += 1
    return added


class SourceFailed(RuntimeError):
    """A source that didn't answer at all this refresh."""


def collect_concerts(database, profile_id: int,
                     lookup: Optional[Callable[[str], Dict[str, Any]]] = None) -> Optional[int]:
    """Upcoming dates for watchlist artists. None when Ticketmaster isn't set
    up: that's "off", not a failure. The client reports errors in its answer
    rather than raising, so an outage is read from there: every lookup failing
    is a failed source; some failing still keeps the ones that answered."""
    from core import concerts_client
    if lookup is None:
        if not concerts_client.ticketmaster_configured():
            return None
        lookup = lambda name: concerts_client.ticketmaster_upcoming(name, limit=3)  # noqa: E731
    added, asked, errors = 0, 0, []
    for name, artist in list(_watchlist(database, profile_id).items())[:CONCERT_ARTISTS]:
        answer = lookup(artist.artist_name) or {}
        asked += 1
        if answer.get('error'):
            errors.append(str(answer['error']))
            continue
        for event in answer.get('events') or []:
            when = str(event.get('datetime') or '')[:10]
            venue = event.get('venue') or ''
            city = event.get('city') or ''
            if len(when) < 10:
                continue
            title = ' · '.join(p for p in (venue, city) if p) or 'Live'
            if upsert(database, profile_id, 'concert', item_key(name, when, venue),
                      title=title, artist_name=artist.artist_name, item_date=when,
                      payload={'url': event.get('tickets_url') or event.get('url') or '',
                               'venue': venue, 'city': city,
                               'country': event.get('country') or ''}):
                added += 1
    if asked and len(errors) == asked:
        raise SourceFailed(errors[0])
    return added


def save_rec(database, profile_id: int, entity: Dict[str, Any],
             explanation: Optional[Dict[str, Any]] = None, image_url: str = '') -> Optional[int]:
    """A recommendation kept for later from its ⋯ menu: saved from the start,
    since you put it there."""
    etype = str(entity.get('type') or '')
    name = str(entity.get('name') or '').strip()
    artist = name if etype == 'artist' else str(entity.get('artist_name') or '').strip()
    if etype not in ('artist', 'album', 'track') or not name or not artist:
        return None
    return upsert(database, profile_id, 'saved_rec', item_key(etype, artist, name),
                  title=name, artist_name=artist, image_url=image_url or '',
                  payload={'entity_type': etype, 'ids': entity.get('ids') or {},
                           'explanation': explanation or None},
                  state='saved')


def _mark_added(database, profile_id: int, rows: List[Dict[str, Any]],
                owned: Callable[[str, str], bool]) -> int:
    """Observed, not clicked: a release now in the library, a saved artist now
    watched, is added."""
    watched = _watchlist(database, profile_id)
    n = 0
    for row in rows:
        kind = row.get('kind')
        if kind in ('new_release', 'upcoming'):
            done = owned(row.get('artist_name') or '', row.get('title') or '')
        elif kind == 'saved_rec' and (row.get('payload') or {}).get('entity_type') == 'artist':
            done = _norm(row.get('artist_name')) in watched
        else:
            done = False
        if done and set_state(database, profile_id, row['id'], 'added'):
            n += 1
    return n


def _library_has(database) -> Callable[[str, str], bool]:
    def owned(artist: str, album: str) -> bool:
        try:
            with database._get_connection() as conn:
                cur = conn.cursor()
                cur.execute(
                    "SELECT 1 FROM albums al JOIN artists ar ON ar.id = al.artist_id "
                    "WHERE LOWER(al.title) = ? AND LOWER(ar.name) = ? LIMIT 1",
                    (_norm(album), _norm(artist)))
                return cur.fetchone() is not None
        except Exception as exc:  # noqa: BLE001 - unknown means not added
            logger.debug("inbox ownership check failed: %s", exc)
            return False
    return owned


def _prune(database, profile_id: int, today: date) -> None:
    with database._get_connection() as conn:
        cur = conn.cursor()
        # an upcoming release that's out arrives again as a new release
        cur.execute("DELETE FROM discovery_inbox WHERE profile_id = ? AND kind = 'upcoming' "
                    "AND item_date < ? AND state != 'saved'", (profile_id, today.isoformat()))
        cur.execute("DELETE FROM discovery_inbox WHERE profile_id = ? AND kind = 'concert' "
                    "AND item_date < ?", (profile_id, today.isoformat()))
        cur.execute("DELETE FROM discovery_inbox WHERE profile_id = ? AND state = 'dismissed' "
                    "AND updated_at < ?", (profile_id, _stamp(_now() - KEEP_DISMISSED)))
        conn.commit()


# ---- refresh --------------------------------------------------------------------

_STATUS_KEY = 'discovery_inbox_status:{}'
_locks: Dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock(profile_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(profile_id, threading.Lock())


def status(database, profile_id: int) -> Dict[str, Any]:
    try:
        return json.loads(database.get_metadata(_STATUS_KEY.format(profile_id)) or '{}') or {}
    except (TypeError, ValueError):
        return {}


def is_stale(database, profile_id: int) -> bool:
    at = status(database, profile_id).get('refreshed_at')
    try:
        return not at or _now() - datetime.fromisoformat(at) > STALE_AFTER
    except ValueError:
        return True


def refresh(database, profile_id: int, *, today: Optional[date] = None,
            concerts: Optional[Callable[[str], List[Dict[str, Any]]]] = None) -> Dict[str, Any]:
    """Run every source. Each one that fails is reported and the rest still
    land: the inbox shows what it has and says what didn't answer."""
    today = today or date.today()
    sources: Dict[str, Dict[str, Any]] = {}
    for name, run in (('releases', lambda: collect_releases(database, profile_id, today)),
                      ('concerts', lambda: collect_concerts(database, profile_id, concerts))):
        try:
            n = run()
            sources[name] = {'state': 'off'} if n is None else {'state': 'ok', 'count': n}
        except Exception as exc:  # noqa: BLE001 - one source never blanks the rest
            logger.warning("inbox source %s failed: %s", name, exc)
            sources[name] = {'state': 'failed', 'error': str(exc)[:200]}
    try:
        _prune(database, profile_id, today)
        _mark_added(database, profile_id, _rows(database, profile_id, ('unread', 'saved')),
                    _library_has(database))
    except Exception as exc:  # noqa: BLE001
        logger.debug("inbox housekeeping failed: %s", exc)
    result = {'refreshed_at': _now().isoformat(timespec='seconds'), 'sources': sources}
    try:
        database.set_metadata(_STATUS_KEY.format(profile_id), json.dumps(result))
    except Exception as exc:  # noqa: BLE001
        logger.debug("inbox status write failed: %s", exc)
    return result


def refresh_in_background(database, profile_id: int) -> bool:
    """Start a refresh unless one is already running for the profile."""
    lock = _lock(profile_id)
    if not lock.acquire(blocking=False):
        return False

    def run():
        try:
            refresh(database, profile_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("inbox refresh failed: %s", exc)
        finally:
            lock.release()

    threading.Thread(target=run, name=f"inbox-refresh-{profile_id}", daemon=True).start()
    return True


def is_refreshing(profile_id: int) -> bool:
    return _lock(profile_id).locked()


def unanswered(status_payload: Dict[str, Any]) -> List[str]:
    """The sources that failed last time, for "X didn't answer, showing the rest"."""
    return [name for name, s in (status_payload.get('sources') or {}).items()
            if s.get('state') == 'failed']
