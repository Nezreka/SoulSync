"""ListenBrainz listening-history importer.

This is account-history ingestion, not metadata enrichment. It shares the
ListenBrainz client/config but writes canonical rows into ``listening_history`` so
Stats, discovery, and Year in Listening keep reading one source of truth.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Optional

from core.listenbrainz_client import ListenBrainzClient
from core.listening_import.dedup import insert_import_events
from core.listening_import.profiles import ProfileImportWorkers
from core.listening_scope import SHARED_OWNER, owner_key
from utils.logging_config import get_logger

logger = get_logger("listenbrainz_import")

STATE_KEY = "listenbrainz_listening_import_state"
SOURCE = "listenbrainz"
PAGE_LIMIT = 100
RECENT_OVERLAP_SECONDS = 24 * 60 * 60
TRANSIENT_PAGE_RETRIES = 4
TRANSIENT_PAGE_RETRY_BASE_SECONDS = 5
# how many more listens listenbrainz can hold than we imported, past the gap we
# already know about, before an incremental sync goes back for older history.
MISSED_HISTORY_SLACK = 50


def _safe_error_message(error: Exception) -> str:
    return re.sub(r"(Token\s+)[^\s]+", r"\1REDACTED", str(error))


class ListenBrainzListeningImportWorker:
    """Imports ListenBrainz scrobbles into ``listening_history``.

    The worker is single-flight and thread-safe. Automations, manual
    run buttons, and settings can all call ``start_import``; if
    a run is already active they get a skipped response instead of creating a
    second crawl.

    profile_id is the pile it fills (#1293). the shared one uses the account in
    Settings, any other profile uses the listenbrainz it connected itself, and
    each keeps its own resume state.
    """

    def __init__(
        self,
        database,
        config_manager,
        *,
        cache_builder: Optional[Callable[[], Any]] = None,
        progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
        profile_id: int = SHARED_OWNER,
    ):
        self.db = database
        self.config_manager = config_manager
        self.cache_builder = cache_builder
        self.progress_callback = progress_callback
        self.profile_id = int(profile_id)
        self._state_key = owner_key(STATE_KEY, self.profile_id)
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
                phase="ListenBrainz import needs to resume",
                progress=_progress(_int(state.get("page")), _int(state.get("total_pages"))),
                last_success_at=None,
            )
        elif not running and state.get("status") == "complete" and _is_incomplete_backfill_state(state):
            state.update(
                status="partial",
                phase="ListenBrainz import needs to resume",
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
                return {"status": "skipped", "reason": "ListenBrainz import already running", **self.status()}
            self._cancel.clear()
            target = self._resolve_username(username)
            token = self._credentials()[0]
            if not target:
                err = "ListenBrainz user token not configured" if not token else "ListenBrainz username not configured"
                state = self._set_state(status="error", error=err)
                return {"status": "error", "error": state["error"], **state}

            self._thread = threading.Thread(
                target=self._run,
                args=(target, full),
                daemon=True,
                name=f"listenbrainz-listening-import-{self.profile_id}",
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
        was_incremental = self._crawl(username, full)
        # an incremental sync only reads listens newer than the last one. history
        # imported into listenbrainz later (an old spotify zip, say) lands behind
        # that cursor, so when the account grew by more than we pulled in, go
        # read the whole thing once. dedup keeps the rows we already have.
        if (
            was_incremental
            and self._state.get("status") == "complete"
            and not self._cancel.is_set()
            and self._missed_older_listens(username)
        ):
            self._crawl(username, True, phase="Found older listens on ListenBrainz, reading your full history")

    def _crawl(self, username: str, full: bool, *, phase: Optional[str] = None) -> bool:
        """one pass over the account. returns whether it ran as an incremental sync."""
        start_ts = time.time()
        previous = self._load_state()
        if previous.get("username") and str(previous["username"]).casefold() != username.casefold():
            previous = {}
            self._state = {}

        previous_complete_is_suspect = _is_incomplete_backfill_state(previous)
        backfill_complete = bool(previous.get("backfill_complete")) and not previous_complete_is_suspect
        looks_like_interrupted_backfill = (
            not full
            and not backfill_complete
            and _int(previous.get("pending_max_ts")) > 0
        )
        use_incremental = not full and backfill_complete
        last_cursor = _int(previous.get("last_imported_ts")) if use_incremental else 0
        min_ts = max(0, last_cursor - RECENT_OVERLAP_SECONDS) if last_cursor else None

        token, base_url, _ = self._credentials()
        client = ListenBrainzClient(token=token, base_url=base_url or None)

        total_scrobbles = client.get_user_listen_count(username) if not use_incremental else None
        total_pages = max(1, math.ceil(total_scrobbles / PAGE_LIMIT)) if total_scrobbles else None

        start_page = 1 if use_incremental or full else max(1, _int(previous.get("page")) + 1 if looks_like_interrupted_backfill else 1)
        current_max_ts = _int(previous.get("pending_max_ts")) if looks_like_interrupted_backfill else None

        self._set_state(
            status="running",
            username=username,
            phase=phase or ("Starting ListenBrainz import" if start_page == 1 else f"Resuming ListenBrainz import at page {start_page}"),
            started_at=_now_iso(),
            finished_at=None,
            error=None,
            imported=0 if not looks_like_interrupted_backfill else _int(previous.get("imported")),
            inserted=0 if not looks_like_interrupted_backfill else _int(previous.get("inserted")),
            duplicates=0 if not looks_like_interrupted_backfill else _int(previous.get("duplicates")),
            page=start_page - 1,
            total_pages=total_pages,
            total_scrobbles=total_scrobbles,
            progress=0 if not looks_like_interrupted_backfill else _progress(start_page - 1, total_pages),
            backfill_complete=backfill_complete if not full else False,
        )

        inserted_total = _int(previous.get("inserted")) if looks_like_interrupted_backfill else 0
        duplicate_total = _int(previous.get("duplicates")) if looks_like_interrupted_backfill else 0
        imported_total = _int(previous.get("imported")) if looks_like_interrupted_backfill else 0
        highest_ts = last_cursor if use_incremental else _int(previous.get("pending_last_imported_ts"))
        page = start_page
        completed_backfill = False

        try:
            while not self._cancel.is_set():
                data = self._get_user_listens_page(
                    client=client,
                    username=username,
                    min_ts=None,  # Crawl newest-first; apply the overlap floor locally.
                    max_ts=current_max_ts,
                    page_num=page,
                )
                if data is None:
                    break

                payload = (data or {}).get("payload") or {}
                listens = payload.get("listens") or []
                if not listens:
                    completed_backfill = not use_incremental
                    break

                timestamps = [_int(item.get("listened_at")) for item in listens]
                if any(ts <= 0 for ts in timestamps):
                    raise ValueError("History page contains an invalid listened_at timestamp")
                oldest_in_batch = min(timestamps)
                if current_max_ts is not None and oldest_in_batch >= current_max_ts:
                    raise ValueError("History API pagination cursor did not advance")
                events = [ev for ev in (normalize_listenbrainz_listen(item) for item in listens)
                          if ev and (min_ts is None or _played_at_ts(ev["played_at"]) > min_ts)]

                self._resolve_db_track_ids(events)
                inserted = self._insert_events_deduped(events)
                imported_total += len(events)
                inserted_total += inserted
                duplicate_total += max(0, len(events) - inserted)
                highest_ts = max(highest_ts, max(timestamps))
                current_max_ts = oldest_in_batch

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

                # Commit the incremental high-water mark only after the whole
                # window succeeds. Errors/cancellation replay safely through dedup.
                if not use_incremental:
                    checkpoint.update(
                        backfill_complete=False,
                        pending_max_ts=current_max_ts,
                        pending_last_imported_ts=highest_ts or _int(previous.get("pending_last_imported_ts")),
                        pending_last_imported_at=(
                            _iso_from_ts(highest_ts)
                            if highest_ts
                            else previous.get("pending_last_imported_at")
                        ),
                    )
                self._set_state(**checkpoint)

                if use_incremental and min_ts is not None and oldest_in_batch <= min_ts:
                    break

                # If batch is smaller than limit, we've reached the end of history
                if len(listens) < PAGE_LIMIT:
                    completed_backfill = not use_incremental
                    break

                page += 1
                time.sleep(1)

            cancelled = self._cancel.is_set()
            status = "cancelled" if cancelled else "complete"
            final_progress = _progress(page, total_pages)
            final_updates = {
                "status": status,
                "phase": "ListenBrainz import cancelled" if cancelled else "ListenBrainz is up to date",
                "finished_at": _now_iso(),
                "last_success_at": _now_iso() if status == "complete" else previous.get("last_success_at"),
                "imported": imported_total,
                "inserted": inserted_total,
                "duplicates": duplicate_total,
                "duration_seconds": round(time.time() - start_ts, 1),
                "progress": 100 if status == "complete" else final_progress,
                "error": None,
            }

            if (use_incremental and not cancelled) or completed_backfill:
                final_updates.update(
                    backfill_complete=True,
                    pending_max_ts=None,
                    pending_last_imported_ts=None,
                    pending_last_imported_at=None,
                    last_imported_ts=highest_ts or _int(previous.get("pending_last_imported_ts")) or last_cursor,
                    last_imported_at=(
                        _iso_from_ts(highest_ts)
                        if highest_ts
                        else previous.get("pending_last_imported_at") or previous.get("last_imported_at")
                    ),
                )
                if completed_backfill and total_scrobbles:
                    # listens we can't import (no title, same-second repeats) keep
                    # the counts apart forever. remember that gap so it never
                    # reads as missing history.
                    imported_count = self._imported_count()
                    if imported_count is not None:
                        final_updates["reconciled_gap"] = max(0, total_scrobbles - imported_count)
            else:
                final_updates.update(
                    backfill_complete=backfill_complete if use_incremental else False,
                    pending_max_ts=None if use_incremental else current_max_ts,
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
                    self._set_state(phase="ListenBrainz is up to date")
                except Exception as e:
                    logger.warning("ListenBrainz import finished but stats cache rebuild failed: %s", e)
        except Exception as e:
            safe_error = _safe_error_message(e)
            logger.error("ListenBrainz listening import failed: %s", safe_error, exc_info=True)
            self._set_state(
                status="error",
                phase="ListenBrainz import failed",
                error=safe_error,
                finished_at=_now_iso(),
                progress=_progress(max(page - 1, 0), total_pages),
                backfill_complete=backfill_complete if use_incremental else False,
                pending_max_ts=current_max_ts if not use_incremental else None,
            )
        return use_incremental

    def _missed_older_listens(self, username: str) -> bool:
        """true when listenbrainz holds more listens than we imported, beyond the known gap."""
        try:
            token, base_url, _ = self._credentials()
            total = ListenBrainzClient(token=token, base_url=base_url or None).get_user_listen_count(username)
        except Exception as e:
            logger.debug("ListenBrainz listen count check failed: %s", _safe_error_message(e))
            return False
        imported_count = self._imported_count()
        if not total or imported_count is None:
            return False
        gap = max(0, total - imported_count)
        known = self._state.get("reconciled_gap")
        if gap > _int(known) + MISSED_HISTORY_SLACK:
            logger.info(
                "ListenBrainz has %s listens, %s imported (known gap %s), reading full history",
                total, imported_count, _int(known),
            )
            return True
        if known is None or gap < _int(known):
            self._set_state(reconciled_gap=gap)
        return False

    def _imported_count(self) -> Optional[int]:
        """listenbrainz listens this pile has taken in, one alias per listen."""
        try:
            conn = self.db._get_connection()
            try:
                row = conn.execute(
                    "SELECT COUNT(*) FROM listening_import_events WHERE profile_id = ? AND source = ?",
                    (self.profile_id, SOURCE),
                ).fetchone()
                return int(row[0]) if row else 0
            finally:
                conn.close()
        except Exception as e:
            logger.debug("ListenBrainz imported count failed: %s", e)
            return None

    def _get_user_listens_page(
        self,
        client: ListenBrainzClient,
        username: str,
        min_ts: Optional[int],
        max_ts: Optional[int],
        page_num: int,
    ) -> Optional[Dict[str, Any]]:
        for attempt in range(1, TRANSIENT_PAGE_RETRIES + 1):
            try:
                data = client.get_user_listens(username, min_ts=min_ts, max_ts=max_ts, count=PAGE_LIMIT)
                payload = data.get("payload") if isinstance(data, dict) and not data.get("error") else None
                if not isinstance(payload, dict) or not isinstance(payload.get("listens"), list):
                    raise ValueError("History API returned an invalid listens response")
                return data
            except Exception as e:
                if attempt >= TRANSIENT_PAGE_RETRIES:
                    raise
                delay = min(60, TRANSIENT_PAGE_RETRY_BASE_SECONDS * attempt)
                self._set_state(
                    status="running",
                    phase=f"ListenBrainz API hiccup on page {page_num}; retrying in {delay}s",
                    page=page_num - 1,
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

    def _credentials(self) -> tuple:
        """(token, base_url, username) for this worker's pile."""
        if self.profile_id == SHARED_OWNER:
            return (
                self.config_manager.get("listenbrainz.token", "") or "",
                self.config_manager.get("listenbrainz.base_url", "") or "",
                self.config_manager.get("listenbrainz.username", "") or "",
            )
        try:
            s = self.db.get_profile_listenbrainz(self.profile_id) or {}
        except Exception as e:
            logger.debug("profile %s listenbrainz lookup failed: %s", self.profile_id, e)
            s = {}
        return (s.get("token") or "", s.get("base_url") or "", s.get("username") or "")

    def _resolve_username(self, username: Optional[str]) -> str:
        token, base_url, stored = self._credentials()
        # a profile imports its own account, never a name someone typed in.
        # the shared pile keeps the username setting it always had.
        configured = stored if self.profile_id != SHARED_OWNER else (username or stored)
        if configured:
            return str(configured).strip()
        if not token:
            return ""
        try:
            client = ListenBrainzClient(token=token, base_url=base_url or None)
            found = client.get_authenticated_username() or ""
            if found and self.profile_id == SHARED_OWNER:
                self.config_manager.set("listenbrainz.username", found)
            return found
        except Exception:
            return ""

    def _resolve_db_track_ids(self, events: List[Dict[str, Any]]) -> None:
        pairs = sorted({
            ((ev.get("title") or "").strip().lower(), (ev.get("artist") or "").strip().lower())
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
                    SELECT LOWER(t.title), LOWER(ar.name), t.id
                    FROM tracks t
                    JOIN artists ar ON ar.id = t.artist_id
                    WHERE (LOWER(t.title), LOWER(ar.name)) IN ({placeholders})
                    """,
                    args,
                )
                for title_l, artist_l, track_id in cursor.fetchall():
                    found.setdefault((title_l, artist_l), track_id)
            for ev in events:
                ev["db_track_id"] = found.get(((ev.get("title") or "").strip().lower(), (ev.get("artist") or "").strip().lower()))
        finally:
            conn.close()

    def _insert_events_deduped(self, events: Iterable[Dict[str, Any]]) -> int:
        return insert_import_events(self.db, events, SOURCE, profile_id=self.profile_id)

    def _load_state(self) -> Dict[str, Any]:
        try:
            raw = self.db.get_metadata(self._state_key)
            return json.loads(raw) if raw else {"status": "idle", "source": SOURCE}
        except Exception:
            return {"status": "idle", "source": SOURCE}

    def _set_state(self, **updates) -> Dict[str, Any]:
        state = {**(self._state or {}), **updates, "source": SOURCE, "updated_at": _now_iso()}
        self._state = state
        try:
            self.db.set_metadata(self._state_key, json.dumps(state))
        except Exception as e:
            logger.debug("Could not persist ListenBrainz import state: %s", e)
        if self.progress_callback:
            try:
                self.progress_callback(self.status())
            except Exception as e:
                logger.debug("ListenBrainz import progress callback failed: %s", e)
        return state


def normalize_listenbrainz_listen(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    listened_at = _int(item.get("listened_at"))
    if not listened_at:
        return None
    track_metadata = item.get("track_metadata") or {}
    title = str(track_metadata.get("track_name") or "").strip()
    if not title:
        return None
    artist = str(track_metadata.get("artist_name") or "").strip()
    album = str(track_metadata.get("release_name") or "").strip()
    additional_info = track_metadata.get("additional_info") or {}
    recording_mbid = str(additional_info.get("recording_mbid") or "").strip()
    recording_msid = str(item.get("recording_msid") or "").strip()

    duration_ms = _int(additional_info.get("duration_ms"))
    if not duration_ms and additional_info.get("duration"):
        duration_ms = _int(additional_info.get("duration")) * 1000

    track_id = recording_mbid or recording_msid or f"listenbrainz:{artist.lower()}:{title.lower()}:{listened_at}"

    return {
        "track_id": track_id,
        "title": title,
        "artist": artist,
        "album": album,
        "played_at": _iso_from_ts(listened_at),
        "duration_ms": duration_ms,
        "server_source": SOURCE,
        "db_track_id": None,
    }


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
    if state.get("backfill_complete") is True:
        return False
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


class ListenBrainzImportWorkers(ProfileImportWorkers):
    """one listenbrainz importer per pile, see core/listening_import/profiles.py."""

    worker_cls = ListenBrainzListeningImportWorker
    source = SOURCE
    redact = staticmethod(_safe_error_message)
