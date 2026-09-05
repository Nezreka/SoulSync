"""Last.fm listening-history importer.

This is account-history ingestion, not metadata enrichment. It shares the
Last.fm client/config but writes canonical rows into ``listening_history`` so
Stats, discovery, and Year in Listening keep reading one source of truth.
"""

from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.lastfm_client import LastFMClient
from utils.logging_config import get_logger

logger = get_logger("lastfm_import")


def _name_key(name: Any) -> str:
    """The catalogue's folded artist key (indexed, and not ASCII-only)."""
    from core.library2.importer import normalize_name

    return normalize_name(str(name or ""))

STATE_KEY = "lastfm_listening_import_state"
SOURCE = "lastfm"
PAGE_LIMIT = 200
RECENT_OVERLAP_SECONDS = 24 * 60 * 60
STOP_AFTER_DUPLICATE_PAGES = 3
TRANSIENT_PAGE_RETRIES = 4
TRANSIENT_PAGE_RETRY_BASE_SECONDS = 5


def _safe_error_message(error: Exception) -> str:
    return re.sub(r"([?&]api_key=)[^&\s]+", r"\1REDACTED", str(error))


class LastFMListeningImportWorker:
    """Imports Last.fm scrobbles into ``listening_history``.

    The worker is intentionally small and single-flight. Automations, manual
    run buttons, and future settings toggles can all call ``start_import``; if
    a run is already active they get a skipped response instead of creating a
    second paginated crawl.
    """

    def __init__(
        self,
        database,
        config_manager,
        *,
        cache_builder: Optional[Callable[[], Any]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.db = database
        self.config_manager = config_manager
        self.cache_builder = cache_builder
        self.progress_callback = progress_callback
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()
        self._state = self._load_state()

    def is_running(self) -> bool:
        thread = self._thread
        return bool(thread and thread.is_alive())

    def status(self) -> Dict[str, Any]:
        state = dict(self._state or {})
        running = self.is_running()
        if not running and state.get("status") == "running":
            state.update(
                status="partial",
                phase="Last.fm import needs to resume",
                progress=_progress(_int(state.get("page")), _int(state.get("total_pages"))),
                last_success_at=None,
            )
        elif not running and state.get("status") == "complete" and _is_incomplete_backfill_state(state):
            state.update(
                status="partial",
                phase="Last.fm import needs to resume",
                progress=_progress(_int(state.get("page")), _int(state.get("total_pages"))),
                last_success_at=None,
            )
        state["running"] = running
        state.setdefault("status", "idle")
        state.setdefault("source", SOURCE)
        return state

    def start_import(self, username: Optional[str] = None, *, full: bool = False) -> Dict[str, Any]:
        with self._lock:
            if self.is_running():
                return {"status": "skipped", "reason": "Last.fm import already running", **self.status()}
            self._cancel.clear()
            target = self._resolve_username(username)
            if not target:
                state = self._set_state(status="error", error="Last.fm username not configured")
                return {"status": "error", "error": state["error"], **state}
            if not self.config_manager.get("lastfm.api_key", ""):
                state = self._set_state(status="error", error="Last.fm API key not configured")
                return {"status": "error", "error": state["error"], **state}

            self._thread = threading.Thread(
                target=self._run,
                args=(target, full),
                daemon=True,
                name="lastfm-listening-import",
            )
            self._thread.start()
            return {"status": "started", "username": target}

    def run_once(self, username: Optional[str] = None, *, full: bool = False) -> Dict[str, Any]:
        started = self.start_import(username, full=full)
        if started.get("status") == "started":
            thread = self._thread
            if thread:
                thread.join()
            return self.status()
        return started

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, username: str, full: bool) -> None:
        start_ts = time.time()
        previous = self._load_state()
        previous_page = _int(previous.get("page"))
        previous_total_pages = _int(previous.get("total_pages"))
        previous_complete_is_suspect = _is_incomplete_backfill_state(previous)
        backfill_complete = bool(previous.get("backfill_complete")) and not previous_complete_is_suspect
        looks_like_interrupted_backfill = (
            not full
            and previous_total_pages > 0
            and 0 < previous_page < previous_total_pages
            and not backfill_complete
        )
        use_incremental = not full and backfill_complete
        last_cursor = _int(previous.get("last_imported_ts")) if use_incremental else 0
        from_ts = max(0, last_cursor - RECENT_OVERLAP_SECONDS) if last_cursor else None
        start_page = 1 if use_incremental or full else max(1, previous_page + 1 if looks_like_interrupted_backfill else 1)
        client = LastFMClient(
            api_key=self.config_manager.get("lastfm.api_key", ""),
            api_secret=self.config_manager.get("lastfm.api_secret", ""),
            session_key=self.config_manager.get("lastfm.session_key", ""),
        )

        self._set_state(
            status="running",
            username=username,
            phase="Starting Last.fm import" if start_page == 1 else f"Resuming Last.fm import at page {start_page}",
            started_at=_now_iso(),
            finished_at=None,
            error=None,
            imported=0,
            inserted=0,
            duplicates=0,
            page=start_page - 1,
            total_pages=None,
            total_scrobbles=None,
            progress=0,
            backfill_complete=backfill_complete if not full else False,
        )

        inserted_total = 0
        duplicate_total = 0
        imported_total = 0
        highest_ts = last_cursor if use_incremental else _int(previous.get("pending_last_imported_ts"))
        duplicate_pages = 0
        page = start_page
        total_pages = None
        total_scrobbles = None
        completed_backfill = False

        try:
            while not self._cancel.is_set():
                data = self._get_recent_tracks_page(client, username, page, from_ts)
                if data is None:
                    break
                recent = (data or {}).get("recenttracks") or {}
                attr = recent.get("@attr") or {}
                total_pages = _int(attr.get("totalPages"), total_pages or 1)
                total_scrobbles = _int(attr.get("total"), total_scrobbles or 0)
                tracks = recent.get("track") or []
                if isinstance(tracks, dict):
                    tracks = [tracks]
                tracks = [t for t in tracks if not (t.get("@attr") or {}).get("nowplaying")]
                if not tracks:
                    completed_backfill = from_ts is None
                    break

                events = [ev for ev in (normalize_lastfm_scrobble(t) for t in tracks) if ev]
                self._resolve_db_track_ids(events)
                inserted = self._insert_events_deduped(events)
                imported_total += len(events)
                inserted_total += inserted
                duplicate_total += max(0, len(events) - inserted)
                if inserted == 0:
                    duplicate_pages += 1
                else:
                    duplicate_pages = 0

                for ev in events:
                    highest_ts = max(highest_ts, _played_at_ts(ev.get("played_at")))

                checkpoint = {
                    "status": "running",
                    "phase": f"Imported page {page} of {total_pages or '?'}",
                    "page": page,
                    "total_pages": total_pages,
                    "total_scrobbles": total_scrobbles,
                    "imported": imported_total,
                    "inserted": inserted_total,
                    "duplicates": duplicate_total,
                    "progress": _progress(page, total_pages),
                    "error": None,
                }
                if use_incremental:
                    checkpoint.update(
                        last_imported_ts=highest_ts or last_cursor,
                        last_imported_at=_iso_from_ts(highest_ts) if highest_ts else previous.get("last_imported_at"),
                    )
                else:
                    checkpoint.update(
                        backfill_complete=False,
                        backfill_next_page=page + 1,
                        pending_last_imported_ts=highest_ts or _int(previous.get("pending_last_imported_ts")),
                        pending_last_imported_at=(
                            _iso_from_ts(highest_ts)
                            if highest_ts
                            else previous.get("pending_last_imported_at")
                        ),
                    )
                self._set_state(**checkpoint)

                if use_incremental and duplicate_pages >= STOP_AFTER_DUPLICATE_PAGES:
                    break
                if total_pages and page >= total_pages:
                    completed_backfill = from_ts is None
                    break
                page += 1

            cancelled = self._cancel.is_set()
            status = "cancelled" if cancelled else "complete"
            final_progress = _progress(page, total_pages)
            final_updates = {
                "status": status,
                "phase": "Last.fm import cancelled" if cancelled else "Last.fm is up to date",
                "finished_at": _now_iso(),
                "last_success_at": _now_iso() if status == "complete" else previous.get("last_success_at"),
                "imported": imported_total,
                "inserted": inserted_total,
                "duplicates": duplicate_total,
                "duration_seconds": round(time.time() - start_ts, 1),
                "progress": 100 if status == "complete" else final_progress,
                "error": None,
            }
            if use_incremental or completed_backfill:
                final_updates.update(
                    backfill_complete=True,
                    backfill_next_page=None,
                    pending_last_imported_ts=None,
                    pending_last_imported_at=None,
                    last_imported_ts=highest_ts or _int(previous.get("pending_last_imported_ts")) or last_cursor,
                    last_imported_at=(
                        _iso_from_ts(highest_ts)
                        if highest_ts
                        else previous.get("pending_last_imported_at") or previous.get("last_imported_at")
                    ),
                )
            else:
                final_updates.update(
                    backfill_complete=False,
                    backfill_next_page=page,
                    pending_last_imported_ts=highest_ts or _int(previous.get("pending_last_imported_ts")),
                    pending_last_imported_at=(
                        _iso_from_ts(highest_ts)
                        if highest_ts
                        else previous.get("pending_last_imported_at")
                    ),
                    last_imported_ts=_int(previous.get("last_imported_ts")),
                    last_imported_at=previous.get("last_imported_at"),
                )
            self._set_state(**final_updates)
            if status == "complete" and self.cache_builder:
                try:
                    self._set_state(phase="Rebuilding stats cache")
                    self.cache_builder()
                    self._set_state(phase="Last.fm is up to date")
                except Exception as e:
                    logger.warning("Last.fm import finished but stats cache rebuild failed: %s", e)
        except Exception as e:
            safe_error = _safe_error_message(e)
            logger.error("Last.fm listening import failed: %s", safe_error, exc_info=True)
            self._set_state(
                status="error",
                phase="Last.fm import failed",
                error=safe_error,
                finished_at=_now_iso(),
                progress=_progress(max(page - 1, 0), total_pages),
                backfill_complete=backfill_complete if use_incremental else False,
                backfill_next_page=page if not use_incremental else None,
            )

    def _get_recent_tracks_page(self, client: LastFMClient, username: str, page: int, from_ts: Optional[int]) -> Optional[Dict[str, Any]]:
        for attempt in range(1, TRANSIENT_PAGE_RETRIES + 1):
            try:
                return client.get_user_recent_tracks(username, page=page, limit=PAGE_LIMIT, from_ts=from_ts)
            except Exception as e:
                if attempt >= TRANSIENT_PAGE_RETRIES:
                    raise
                delay = min(60, TRANSIENT_PAGE_RETRY_BASE_SECONDS * attempt)
                self._set_state(
                    status="running",
                    phase=f"Last.fm API hiccup on page {page}; retrying in {delay}s",
                    page=page - 1,
                    error=_safe_error_message(e),
                )
                if not self._sleep_retry(delay):
                    return None
        return None

    def _sleep_retry(self, seconds: int) -> bool:
        for _ in range(max(0, seconds)):
            if self._cancel.is_set():
                return False
            time.sleep(1)
        return not self._cancel.is_set()

    def _resolve_username(self, username: Optional[str]) -> str:
        configured = username or self.config_manager.get("lastfm.username", "")
        if configured:
            return str(configured).strip()
        api_secret = self.config_manager.get("lastfm.api_secret", "")
        session_key = self.config_manager.get("lastfm.session_key", "")
        if not api_secret or not session_key:
            return ""
        try:
            client = LastFMClient(
                api_key=self.config_manager.get("lastfm.api_key", ""),
                api_secret=api_secret,
                session_key=session_key,
            )
            found = client.get_authenticated_username() or ""
            if found:
                self.config_manager.set("lastfm.username", found)
            return found
        except Exception:
            return ""

    def _resolve_db_track_ids(self, events: List[Dict[str, Any]]) -> None:
        """Point every scrobble at its catalogue row (``lib2_track_id``).

        Library v2 is the catalogue (docs §32.3.1), so this reads
        ``lib2_tracks`` — the legacy ``tracks``/``artists`` pair this arrived
        against no longer exists here. The artist half matches on the indexed
        ``name_key`` fold rather than ``LOWER(name)``: SQLite's ``LOWER()`` is
        ASCII-only, so an accented artist never matched (iss29-D13). Same
        query as ``listening_stats_worker._resolve_db_track_ids_batch``, which
        fills the identical column for the media-server importer.
        """
        pairs = sorted({
            ((ev.get("title") or "").strip().lower(), _name_key(ev.get("artist")))
            for ev in events if ev.get("title")
        })
        if not pairs:
            return
        conn = self.db._get_connection()
        try:
            cursor = conn.cursor()
            found: Dict[tuple[str, str], int] = {}
            for i in range(0, len(pairs), 400):
                chunk = pairs[i:i + 400]
                placeholders = ",".join(["(?,?)"] * len(chunk))
                args = [v for pair in chunk for v in pair]
                cursor.execute(
                    f"""
                    SELECT LOWER(t.title), ar.name_key, t.id
                    FROM lib2_tracks t
                    JOIN lib2_albums al ON al.id = t.album_id
                    JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                    WHERE (LOWER(t.title), ar.name_key) IN ({placeholders})
                    """,
                    args,
                )
                for title_l, artist_key, track_id in cursor.fetchall():
                    found.setdefault((title_l, artist_key), track_id)
            for ev in events:
                # INT-01: this resolves against `lib2_tracks`, so the id is a
                # CATALOGUE id and belongs in `lib2_track_id` — the column every
                # stats reader joins on. Writing it to `db_track_id` (the media
                # server's own id namespace) left Last.fm plays with no cover,
                # no artist link and no genre, and the startup backfill then read
                # the same value as a LEGACY track id, so a numeric collision
                # could link the play to a completely different track.
                ev["lib2_track_id"] = found.get((
                    (ev.get("title") or "").strip().lower(),
                    _name_key(ev.get("artist")),
                ))
        finally:
            conn.close()

    def _insert_events_deduped(self, events: Iterable[Dict[str, Any]]) -> int:
        clean = [ev for ev in events if ev.get("title") and ev.get("played_at")]
        if not clean:
            return 0
        conn = self.db._get_connection()
        try:
            cursor = conn.cursor()
            duplicates = self._probable_duplicate_keys(cursor, clean)
            inserted = 0
            for ev in clean:
                if _event_key(ev) in duplicates:
                    continue
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO listening_history
                        (track_id, title, artist, album, played_at, duration_ms,
                         server_source, lib2_track_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev.get("track_id"),
                        ev.get("title", ""),
                        ev.get("artist", ""),
                        ev.get("album", ""),
                        ev.get("played_at"),
                        ev.get("duration_ms", 0),
                        SOURCE,
                        ev.get("lib2_track_id"),
                    ),
                )
                inserted += 1 if cursor.rowcount > 0 else 0
            conn.commit()
            return inserted
        finally:
            conn.close()

    @staticmethod
    def _probable_duplicate_keys(cursor, events: List[Dict[str, Any]]) -> set[tuple[str, str, int]]:
        windows = [(_event_key(ev), _played_at_ts(ev.get("played_at"))) for ev in events]
        windows = [(key, ts) for key, ts in windows if ts > 0]
        if not windows:
            return set()
        min_ts = min(ts for _key, ts in windows) - 120
        max_ts = max(ts for _key, ts in windows) + 120
        cursor.execute(
            """
            SELECT LOWER(title), LOWER(COALESCE(artist, '')), strftime('%s', played_at)
            FROM listening_history
            WHERE played_at >= datetime(?, 'unixepoch')
              AND played_at <= datetime(?, 'unixepoch')
            """,
            (min_ts, max_ts),
        )
        existing = []
        for title_l, artist_l, played_ts in cursor.fetchall():
            try:
                existing.append((title_l or "", artist_l or "", int(played_ts)))
            except (TypeError, ValueError):
                continue
        duplicates = set()
        for key, ts in windows:
            title_l, artist_l, _ = key
            if any(title_l == ex_title and artist_l == ex_artist and abs(ex_ts - ts) <= 120 for ex_title, ex_artist, ex_ts in existing):
                duplicates.add(key)
        return duplicates

    def _load_state(self) -> Dict[str, Any]:
        try:
            raw = self.db.get_metadata(STATE_KEY)
            return json.loads(raw) if raw else {"status": "idle", "source": SOURCE}
        except Exception:
            return {"status": "idle", "source": SOURCE}

    def _set_state(self, **updates) -> Dict[str, Any]:
        state = {**(self._state or {}), **updates, "source": SOURCE, "updated_at": _now_iso()}
        self._state = state
        try:
            self.db.set_metadata(STATE_KEY, json.dumps(state))
        except Exception as e:
            logger.debug("Could not persist Last.fm import state: %s", e)
        if self.progress_callback:
            try:
                self.progress_callback(self.status())
            except Exception as e:
                logger.debug("Last.fm import progress callback failed: %s", e)
        return state


def normalize_lastfm_scrobble(track: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    date = track.get("date") or {}
    uts = _int(date.get("uts"))
    if not uts:
        return None
    artist = _text(track.get("artist"))
    title = (track.get("name") or "").strip()
    if not title:
        return None
    album = _text(track.get("album"))
    mbid = (track.get("mbid") or "").strip()
    return {
        "track_id": mbid or f"lastfm:{artist.lower()}:{title.lower()}:{uts}",
        "title": title,
        "artist": artist,
        "album": album,
        "played_at": _iso_from_ts(uts),
        "duration_ms": _int(track.get("duration"), 0),
        "server_source": SOURCE,
        "lib2_track_id": None,
    }


def _event_key(ev: Dict[str, Any]) -> tuple[str, str, int]:
    return (
        (ev.get("title") or "").strip().lower(),
        (ev.get("artist") or "").strip().lower(),
        _played_at_ts(ev.get("played_at")),
    )


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("#text") or value.get("name") or "").strip()
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _iso_from_ts(ts: int) -> str:
    return datetime.fromtimestamp(int(ts), timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _played_at_ts(value: Any) -> int:
    if not value:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return 0


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def _is_incomplete_backfill_state(state: Dict[str, Any]) -> bool:
    page = _int(state.get("page"))
    total_pages = _int(state.get("total_pages"))
    total_scrobbles = _int(state.get("total_scrobbles"))
    imported = _int(state.get("imported"))
    if not (total_pages > 0 and 0 < page < total_pages):
        return False
    if total_scrobbles > 0 and imported > 0:
        return imported < total_scrobbles
    return True


def _progress(page: int, total_pages: Optional[int]) -> int:
    if not total_pages:
        return 0
    return max(0, min(99, round((page / max(total_pages, 1)) * 100)))

