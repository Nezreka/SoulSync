"""Background worker that fills the ``similar_artists`` table for LIBRARY artists.

The watchlist scanner only populates similar artists for *watchlist* artists, so
the artist map / discover surfaces are rich for watchlisted artists and sparse
for the rest of the library. This worker closes that gap: for every
source-matched library artist it asks MusicMap for ~25 similar artists, matches
each one to the user's metadata source chain (primary + active fallbacks) via the
shared :func:`core.metadata.similar_artists.get_musicmap_similar_artists`, and
stores the matched results keyed by the library artist's **metadata source id** —
the same key the watchlist scanner and the artist map use, so the two cooperate
(idempotent upsert + a retry window keep them from double-fetching).

It plugs into the existing enrichment-worker pattern (background thread, status /
pause / resume, ``matched / not_found / pending / errors`` stats) so it shows up
as a bubble in the dashboard / Manage Enrichment Workers modal like every other
source worker.

The pure seams below (:func:`pick_source_artist_id`,
:func:`map_payload_to_store_kwargs`, :func:`process_artist`) carry the logic and
are unit-tested in isolation; the class wires them to the DB + MusicMap.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, Optional

from utils.logging_config import get_logger
from core.worker_utils import interruptible_sleep

logger = get_logger("similar_artists_worker")


# A matched MusicMap payload is {id, source, name, image_url, genres, popularity}.
# Map its single (id, source) onto the right add_or_update_similar_artist id
# kwarg. The table only has columns for these four providers; a match on any
# other source (e.g. discogs) is still stored, name-only.
_SOURCE_ID_FIELD = {
    'spotify': 'similar_artist_spotify_id',
    'itunes': 'similar_artist_itunes_id',
    'deezer': 'similar_artist_deezer_id',
    'musicbrainz': 'similar_artist_musicbrainz_id',
}

# Library-artist source-id columns, in the same priority the watchlist scanner
# uses to key its rows — so a library artist and (if also watchlisted) its
# watchlist row resolve to the SAME source_artist_id and don't duplicate work.
_LIBRARY_ID_COLUMNS = ('spotify_artist_id', 'itunes_artist_id', 'deezer_id', 'musicbrainz_id')


def map_payload_to_store_kwargs(payload: Dict[str, Any]) -> Dict[str, Optional[str]]:
    """Turn a matched MusicMap payload into the id kwarg for the store call."""
    src = str(payload.get('source') or '').lower()
    pid = str(payload.get('id') or '')
    field = _SOURCE_ID_FIELD.get(src)
    return {field: pid} if (field and pid) else {}


def pick_source_artist_id(row: Dict[str, Any]) -> Optional[str]:
    """The metadata source id to key a library artist's similars by, or None if
    the artist isn't matched to any metadata source yet (→ skip it)."""
    for key in _LIBRARY_ID_COLUMNS:
        v = row.get(key)
        if v:
            return str(v)
    return None


def process_artist(
    source_artist_id: str,
    artist_name: str,
    fetch_similars: Callable[[str, int], Dict[str, Any]],
    store_similar: Callable[..., bool],
    limit: int = 25,
    profile_id: int = 1,
) -> tuple:
    """Fetch + store similar artists for one library artist.

    ``fetch_similars(name, limit)`` returns the ``get_musicmap_similar_artists``
    payload; ``store_similar(**kwargs)`` is ``add_or_update_similar_artist``.
    Returns ``(status, stored_count, detail)`` where status is one of:
      - ``'matched'``   — stored ≥1 similar artist
      - ``'not_found'`` — MusicMap had no entry / nothing matched a source
      - ``'error'``     — MusicMap/source failure (transient; eligible for retry)
    ``detail`` is a short human-readable reason (status code + message, or
    ``'no matches'`` / ``''``) so the worker can surface WHY a fetch failed
    instead of swallowing it — needed to diagnose error rates.
    """
    try:
        result = fetch_similars(artist_name, limit) or {}
    except Exception as exc:
        return ('error', 0, f'exception: {exc}')

    if not result.get('success'):
        # 404/400 = genuinely no MusicMap entry → 'not_found' (don't keep retrying);
        # anything else (timeout, 5xx, no providers) = transient → 'error' (retry).
        code = result.get('status_code')
        detail = f"{code}: {result.get('error') or 'unknown'}"
        return ('not_found' if code in (400, 404) else 'error', 0, detail)

    sims = result.get('similar_artists') or []
    if not sims:
        return ('not_found', 0, 'no matches')

    stored = 0
    for rank, s in enumerate(sims, 1):
        name = s.get('name')
        if not name:
            continue
        kwargs = map_payload_to_store_kwargs(s)
        if not kwargs:
            # The match resolved to a source with no id column in similar_artists
            # (e.g. discogs). Storing it name-only would be useless — you can't
            # navigate/explore/download it. Enforce the standard: every stored
            # similar carries a metadata source id, or we skip it.
            logger.debug("Skipping similar '%s' (matched %s — no storable source id)", name, s.get('source'))
            continue
        try:
            ok = store_similar(
                source_artist_id=source_artist_id,
                similar_artist_name=name,
                similarity_rank=rank,
                profile_id=profile_id,
                image_url=s.get('image_url'),
                genres=s.get('genres'),
                popularity=s.get('popularity', 0) or 0,
                **kwargs,
            )
            if ok:
                stored += 1
        except Exception as exc:
            logger.debug("store similar failed for %s: %s", name, exc)

    return ('matched' if stored else 'not_found', stored, '')


class SimilarArtistsWorker:
    """Background worker that populates similar artists for library artists."""

    def __init__(self, database):
        self.db = database
        self.running = False
        self.paused = False
        self.should_stop = False
        self.thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.current_item: Optional[str] = None
        self.stats = {'matched': 0, 'not_found': 0, 'pending': 0, 'errors': 0}
        self.retry_days = 30
        self.limit = 25
        self._err_logged = 0          # how many fetch errors we've logged at WARNING this session
        self._last_error = None       # most recent fetch-error reason (for diagnosis)
        # similar_artists rows are profile-scoped; the library is shared. v1 keys
        # under the default profile (matches single-profile setups, which is the
        # common case). Multi-profile per-source-chain population is future work.
        self.profile_id = 1
        logger.info("Similar Artists background worker initialized")

    # ── lifecycle (mirrors the other enrichment workers) ──────────────────
    def start(self):
        if self.running:
            return
        self.running = True
        self.should_stop = False
        self._stop_event.clear()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        logger.info("Similar Artists worker started")

    def stop(self):
        self.should_stop = True
        self.running = False
        self._stop_event.set()

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def get_stats(self) -> Dict[str, Any]:
        # Report PERSISTENT counts from the DB (not the in-memory session
        # counters), so the dashboard orb and the Manage modal always agree and
        # survive restarts — same approach as the other enrichment workers.
        c = self._db_counts()
        self.stats = {
            'matched': c['matched'], 'not_found': c['not_found'],
            'pending': c['pending'], 'errors': c['error'],
        }
        total = c['total']
        is_running = self.running and (self.thread is not None and self.thread.is_alive())
        is_idle = is_running and not self.paused and c['pending'] == 0 and self.current_item is None
        return {
            'enabled': True,
            'running': is_running and not self.paused,
            'paused': self.paused,
            'idle': is_idle,
            'current_item': self.current_item,
            'stats': self.stats.copy(),
            # Artist-only progress (no album/track phases) so the orb tooltip can
            # show "matched / total (percent%)" like every other worker.
            'progress': {
                'artists': {
                    'matched': c['matched'], 'total': total,
                    'percent': int(round(c['matched'] / total * 100)) if total else 0,
                }
            },
        }

    # ── worker loop ────────────────────────────────────────────────────────
    def _run(self):
        logger.info("Similar Artists worker thread started")
        # Imported lazily so the worker module stays import-light for tests.
        from core.metadata.similar_artists import get_musicmap_similar_artists

        while not self.should_stop:
            try:
                if self.paused:
                    interruptible_sleep(self._stop_event, 1)
                    continue

                self.current_item = None
                artist = self._get_next_artist()
                if not artist:
                    interruptible_sleep(self._stop_event, 15)
                    continue

                sid = pick_source_artist_id(artist)
                if not sid:
                    # Query already filters to artists with a source id; guard anyway.
                    self._mark(artist['id'], 'error')
                    continue

                self.current_item = artist.get('name')
                status, count, detail = process_artist(
                    sid, artist['name'],
                    get_musicmap_similar_artists,
                    self.db.add_or_update_similar_artist,
                    limit=self.limit, profile_id=self.profile_id,
                )
                self._mark(artist['id'], status)
                if status == 'matched':
                    self.stats['matched'] += 1
                    logger.debug("Similar artists: %s → stored %d", artist['name'], count)
                elif status == 'not_found':
                    self.stats['not_found'] += 1
                else:
                    self.stats['errors'] += 1
                    # Surface WHY fetches error — the first handful at WARNING (so
                    # the cause is visible in app.log without spamming a 4000-artist
                    # run), the rest at DEBUG. Keep the most recent reason for stats.
                    self._last_error = detail
                    if self._err_logged < 15:
                        self._err_logged += 1
                        logger.warning("Similar artists fetch error for '%s' — %s", artist['name'], detail)
                    else:
                        logger.debug("Similar artists fetch error for '%s' — %s", artist['name'], detail)

                # Pace MusicMap (name search per candidate is heavy + rate-limited).
                interruptible_sleep(self._stop_event, 3)
            except Exception as exc:
                logger.error("Similar Artists worker loop error: %s", exc)
                interruptible_sleep(self._stop_event, 5)

        logger.info("Similar Artists worker thread finished")

    # ── DB helpers (thin; the testable logic lives in the pure seams above) ──
    def _has_source_id_clause(self) -> str:
        return '(' + ' OR '.join(f"{c} IS NOT NULL AND {c} != ''" for c in _LIBRARY_ID_COLUMNS) + ')'

    def _get_next_artist(self) -> Optional[Dict[str, Any]]:
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cols = "id, name, " + ", ".join(_LIBRARY_ID_COLUMNS)
            have_id = self._has_source_id_clause()
            # 1) Unattempted source-matched artists.
            cursor.execute(f"""
                SELECT {cols} FROM artists
                WHERE id IS NOT NULL AND name IS NOT NULL
                  AND similar_artists_match_status IS NULL
                  AND {have_id}
                ORDER BY id ASC LIMIT 1
            """)
            row = cursor.fetchone()
            # 2) Retry transient failures (and re-check 'not_found') after retry_days.
            if not row:
                cursor.execute(f"""
                    SELECT {cols} FROM artists
                    WHERE id IS NOT NULL AND name IS NOT NULL
                      AND similar_artists_match_status IN ('error', 'not_found')
                      AND (similar_artists_last_attempted IS NULL
                           OR similar_artists_last_attempted < datetime('now', ?))
                      AND {have_id}
                    ORDER BY similar_artists_last_attempted ASC LIMIT 1
                """, (f'-{self.retry_days} days',))
                row = cursor.fetchone()
            if not row:
                return None
            keys = ['id', 'name'] + list(_LIBRARY_ID_COLUMNS)
            return dict(zip(keys, row, strict=False))
        except Exception as exc:
            logger.debug("Similar Artists _get_next_artist failed: %s", exc)
            return None

    def _mark(self, artist_id, status: str):
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE artists SET similar_artists_match_status = ?, "
                "similar_artists_last_attempted = CURRENT_TIMESTAMP WHERE id = ?",
                (status, artist_id),
            )
            conn.commit()
        except Exception as exc:
            logger.debug("Similar Artists _mark failed for %s: %s", artist_id, exc)

    def _count_pending(self) -> int:
        return self._db_counts()['pending']

    def _db_counts(self) -> Dict[str, int]:
        """Persistent tallies over the worker's universe (source-matched library
        artists): matched / not_found / error / pending(NULL) / total."""
        out = {'matched': 0, 'not_found': 0, 'error': 0, 'pending': 0, 'total': 0}
        try:
            conn = self.db._get_connection()
            cursor = conn.cursor()
            cursor.execute(f"""
                SELECT
                    SUM(CASE WHEN similar_artists_match_status = 'matched' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN similar_artists_match_status = 'not_found' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN similar_artists_match_status = 'error' THEN 1 ELSE 0 END),
                    SUM(CASE WHEN similar_artists_match_status IS NULL THEN 1 ELSE 0 END),
                    COUNT(*)
                FROM artists WHERE {self._has_source_id_clause()}
            """)
            row = cursor.fetchone() or (0, 0, 0, 0, 0)
            out.update(matched=int(row[0] or 0), not_found=int(row[1] or 0),
                       error=int(row[2] or 0), pending=int(row[3] or 0), total=int(row[4] or 0))
        except Exception as exc:
            logger.debug("Similar Artists _db_counts failed: %s", exc)
        return out
