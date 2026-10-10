"""Phase 2 wanted-loop worker: periodically retries ``subtitle_wanted`` rows.

New imports still fetch immediately (the import hook); this loop is for retries —
rows that missed at import, rows created while keyless, rows whose backoff has
expired. It never hammers: per-row exponential backoff, a per-cycle batch cap, a
configurable interval, and a per-provider daily download quota.

Isolated: stdlib + the provider package + video_database; no music imports.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_started = False
_lock = threading.Lock()

#: A cycle never attempts more than this many rows (before backoff filtering).
DEFAULT_BATCH = 10
#: Minutes between cycles.
DEFAULT_INTERVAL_MIN = 30
#: OpenSubtitles free-tier downloads/day. Tunable via ``subtitle_daily_quota``.
DEFAULT_DAILY_QUOTA = 20


def backoff_hours(attempts: int) -> int:
    """1h, 2h, 4h … capped at 7 days. Pure — keep in sync with
    ``VideoDatabase._subtitle_backoff_hours``."""
    try:
        a = max(0, int(attempts))
    except (TypeError, ValueError):
        a = 0
    return min(2 ** a, 168)


def _parse_ts(raw) -> datetime | None:
    try:
        s = str(raw or "").strip()
        if not s:
            return None
        # SQLite datetime('now') → 'YYYY-MM-DD HH:MM:SS' (UTC, naive).
        return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def row_eligible(row: dict, now: datetime | None = None) -> bool:
    """True when the worker may attempt this row now. 'wanted' rows that were
    never attempted are always eligible; 'failed' rows wait out their backoff.
    Pure."""
    now = now or datetime.now(timezone.utc)
    try:
        attempts = int(row.get("attempts") or 0)
    except (TypeError, ValueError):
        attempts = 0
    if str(row.get("status") or "") == "wanted" and attempts == 0:
        return True
    last = _parse_ts(row.get("last_attempt_at"))
    if last is None:
        return True
    wait_hours = backoff_hours(attempts)
    return (now - last).total_seconds() >= wait_hours * 3600


def _resolve_file(db, row: dict):
    """(absolute_path | None, identity dict) for a wanted row.

    - ``download`` rows: the download's dest_path (absolute).
    - ``movie``/``episode`` rows: media_files stored path resolved against the
      library roots (``core.video.path_resolver``).
    Never raises."""
    try:
        info = db.subtitle_video_info(row.get("video_kind"), row.get("video_id"))
    except Exception:  # noqa: BLE001
        return None, {}
    identity: dict = {}
    for key in ("tmdb_id", "imdb_id", "season", "episode"):
        if info.get(key) is not None:
            identity[key] = info[key]
    path = info.get("dest_path") or info.get("relative_path")
    if not path:
        return None, identity
    if os.path.isabs(str(path)):
        # Download rows carry an absolute dest_path. If it's gone, do NOT
        # re-root tail segments against library roots — that can resolve to a
        # wrong same-named file and misattribute the subtitle. Unresolvable.
        if os.path.exists(str(path)):
            return str(path), identity
        return None, identity
    try:
        from core.video.path_resolver import resolve_video_file_path, video_base_dirs
        resolved = resolve_video_file_path(str(path), video_base_dirs(db))
        return resolved, identity
    except Exception:  # noqa: BLE001
        return None, identity


def _maybe_rekey(db, row: dict, file_path: str | None) -> dict:
    """Lazy download→library re-key: if a download-keyed row's file now has a
    library row (media_files), move the wanted row onto it. Best-effort;
    returns the (possibly updated) row. Never raises."""
    try:
        if str(row.get("video_kind") or "") != "download" or not file_path:
            return row
        from core.video.path_resolver import video_base_dirs
        roots = video_base_dirs(db)
        rel = None
        for root in roots:
            root = str(root or "").rstrip("/\\")
            if not root:
                continue
            try:
                if os.path.commonpath([os.path.abspath(file_path),
                                       os.path.abspath(root)]) == os.path.abspath(root):
                    rel = os.path.relpath(file_path, root)
                    break
            except ValueError:
                continue
        if not rel:
            return row
        conn = db._get_connection()
        try:
            mf = conn.execute(
                "SELECT movie_id, episode_id FROM media_files WHERE relative_path=? "
                "ORDER BY id LIMIT 1", (rel,)).fetchone()
        finally:
            conn.close()
        if not mf:
            return row
        if mf["movie_id"]:
            new_kind, new_id = "movie", int(mf["movie_id"])
        elif mf["episode_id"]:
            new_kind, new_id = "episode", int(mf["episode_id"])
        else:
            return row
        moved = db.subtitle_rekey(row.get("video_id"), new_kind, new_id)
        if moved:
            row = dict(row)
            row["video_kind"], row["video_id"] = new_kind, new_id
    except Exception:  # noqa: BLE001 - re-key is hygiene, never fatal
        logger.exception("subtitle re-key failed for row %s", row.get("id"))
    return row


def quota_ok(db, provider_order, get_setting, quota_limit: int) -> bool:
    """True when at least one configured provider has daily quota left."""
    from .providers import get_providers
    providers = get_providers()
    for pid in provider_order or []:
        if not isinstance(pid, str):
            continue
        provider = providers.get(pid)
        if provider is None:
            continue
        try:
            if not provider.is_configured(get_setting):
                continue
        except Exception:  # noqa: BLE001
            continue
        try:
            if int(db.subtitle_quota_used(pid)) < quota_limit:
                return True
        except Exception:  # noqa: BLE001
            return True  # quota unreadable → don't block on it
    return False


def run_cycle(db, settings, fs=None) -> dict:
    """One worker cycle. Returns stats. Never raises.

    ``settings`` is the normalized settings dict (needs ``download_subtitles``,
    ``subtitle_langs`` not needed here — rows carry their language).
    ``fs`` injects ``list_dir``/``write_text`` (defaults to the real filesystem).
    """
    stats = {"attempted": 0, "downloaded": 0, "missed": 0, "skipped": 0}
    try:
        settings = settings or {}
        if not settings.get("download_subtitles"):
            stats["skipped"] = "disabled"
            return stats
        try:
            batch = max(1, int(settings.get("subtitle_worker_batch", DEFAULT_BATCH)))
        except (TypeError, ValueError):
            batch = DEFAULT_BATCH
        try:
            quota_limit = max(1, int(settings.get("subtitle_daily_quota",
                                                  DEFAULT_DAILY_QUOTA)))
        except (TypeError, ValueError):
            quota_limit = DEFAULT_DAILY_QUOTA

        from . import parse_provider_order, srt_name
        from .providers import fetch_subtitle_detailed
        from .providers.base import SubtitleQuery
        from .scoring import opensubtitles_hash

        provider_order = parse_provider_order(settings.get("subtitle_provider_order"))
        # Blob-first, top-level fallback (same as the import hook): Phase 2
        # tuning knobs (subtitle_min_score, …) live in the organization blob;
        # provider secrets (opensubtitles_api_key, …) live top-level.
        _top_get = getattr(db, "get_setting", None)
        if not callable(_top_get):
            _top_get = lambda k, d=None: d  # noqa: E731
        _blob = settings or {}
        def get_setting(k, d=None):  # noqa: E306
            if k in _blob:
                return _blob[k]
            return _top_get(k, d)

        now = datetime.now(timezone.utc)
        rows = db.subtitle_get_retryable(batch * 3)
        if fs is None:
            import os as _os

            class _RealFS:
                @staticmethod
                def list_dir(folder):
                    return _os.listdir(folder)

                @staticmethod
                def write_text(path, content):
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(content)

            fs = _RealFS()

        for row in rows:
            if stats["attempted"] >= batch:
                break
            if not row_eligible(row, now):
                continue
            if not quota_ok(db, provider_order, get_setting, quota_limit):
                logger.info("subtitle worker: daily quota exhausted, ending cycle")
                break
            video_kind = str(row.get("video_kind") or "")
            video_id = row.get("video_id")
            lang = str(row.get("language") or "en")
            hi = bool(row.get("hi"))
            forced = bool(row.get("forced"))

            # Orphan cleanup: if the source row (download/library) is provably
            # gone — not on transient DB errors (None) — drop the wanted rows
            # instead of retrying them forever.
            try:
                _exists = db.subtitle_source_exists(video_kind, video_id)
            except Exception:  # noqa: BLE001
                _exists = None
            if _exists is False:
                try:
                    db.subtitle_delete_for_video(video_kind, video_id)
                    db.subtitle_log_fetch(video_kind, video_id, lang, "error",
                                          provider="", hi=hi, forced=forced,
                                          candidate_title="orphaned row cleaned up")
                except Exception:  # noqa: BLE001
                    pass
                stats["missed"] += 1
                continue

            file_path, identity = _resolve_file(db, row)
            if not file_path or not os.path.exists(file_path):
                db.subtitle_mark(video_kind, video_id, lang, "failed",
                                 hi=hi, forced=forced)
                db.subtitle_log_fetch(video_kind, video_id, lang, "error",
                                      provider="", hi=hi, forced=forced,
                                      candidate_title="unresolvable file")
                stats["missed"] += 1
                continue

            # Lazy re-key: download rows whose file now has a library row move
            # onto it (Phase 1 residual).
            row = _maybe_rekey(db, row, file_path)
            video_kind = str(row.get("video_kind") or video_kind)
            video_id = row.get("video_id", video_id)

            filename = os.path.basename(file_path)
            # Disk check BEFORE any network fetch: if the subtitle landed
            # between cycles (user drop, another process), mark 'have' without
            # burning provider quota on a download we don't need.
            name = srt_name(file_path, lang)
            folder = os.path.dirname(file_path)
            try:
                if name.lower() in {str(n).lower()
                                    for n in (fs.list_dir(folder) or [])}:
                    db.subtitle_mark(video_kind, video_id, lang, "have",
                                     hi=hi, forced=forced, count_attempt=False)
                    continue
            except Exception:  # noqa: BLE001 - a failed listing just means "not there"
                pass
            moviehash = opensubtitles_hash(file_path)
            query = SubtitleQuery(identity=dict(identity), language=lang,
                                  hi=hi, forced=forced,
                                  filename=filename, moviehash=moviehash)
            stats["attempted"] += 1
            def _bump(_pid):
                # Every successful provider.download() burns real quota — count
                # each one, not just the fetch, since several candidates can
                # burn quota before one succeeds.
                try:
                    db.subtitle_quota_bump(str(_pid))
                except Exception:  # noqa: BLE001 - quota is accounting, never fatal
                    pass
            try:
                text, pid, candidate, score = fetch_subtitle_detailed(
                    query, provider_order, get_setting, on_download=_bump)
            except Exception:  # noqa: BLE001 - fetch is best-effort by contract
                text, pid, candidate, score = None, None, None, 0.0

            if text:
                try:
                    fs.write_text(os.path.join(folder, name), text)
                except Exception:  # noqa: BLE001
                    logger.exception("subtitle worker: sidecar write failed for %s", name)
                    db.subtitle_mark(video_kind, video_id, lang, "failed",
                                     hi=hi, forced=forced)
                    db.subtitle_log_fetch(video_kind, video_id, lang, "error",
                                          provider=str(pid or ""), hi=hi, forced=forced)
                    stats["missed"] += 1
                    continue
                db.subtitle_mark(video_kind, video_id, lang, "have",
                                 hi=hi, forced=forced)
                db.subtitle_log_fetch(video_kind, video_id, lang, "downloaded",
                                      provider=str(pid or ""),
                                      candidate_title=getattr(candidate, "title", None),
                                      score=score, hi=hi, forced=forced)
                stats["downloaded"] += 1
            else:
                db.subtitle_mark(video_kind, video_id, lang, "failed",
                                 hi=hi, forced=forced)
                outcome = "below_threshold" if (score or 0) > 0 else "miss"
                db.subtitle_log_fetch(video_kind, video_id, lang, outcome,
                                      provider="", hi=hi, forced=forced,
                                      candidate_title=getattr(candidate, "title", None),
                                      score=score)
                stats["missed"] += 1
    except Exception:  # noqa: BLE001 - the worker never takes down the process
        logger.exception("subtitle worker cycle failed")
    return stats


def _load_settings(db) -> dict:
    try:
        from core.video import organization
        return organization.load(db)
    except Exception:  # noqa: BLE001
        try:
            from core.video import organization as _org
            return _org.default_settings()
        except Exception:  # noqa: BLE001
            return {}


def _run(db_provider) -> None:
    while True:
        try:
            db = db_provider()
            if db is not None:
                settings = _load_settings(db)
                interval = settings.get("subtitle_worker_interval_min",
                                        DEFAULT_INTERVAL_MIN)
                run_cycle(db, settings)
            else:
                interval = DEFAULT_INTERVAL_MIN
        except Exception:  # noqa: BLE001
            logger.exception("subtitle worker cycle raised")
            interval = DEFAULT_INTERVAL_MIN
        try:
            time.sleep(max(1, int(interval)) * 60)
        except (TypeError, ValueError):
            time.sleep(DEFAULT_INTERVAL_MIN * 60)


def ensure_started(db_provider) -> None:
    """Start the worker thread once (idempotent). Call when the video subsystem
    starts (alongside the download monitor). Settings are loaded from the DB
    each cycle, so changes take effect without a restart."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
        threading.Thread(target=_run, args=(db_provider,),
                         daemon=True, name="subtitle-wanted-worker").start()


__all__ = ["ensure_started", "run_cycle", "row_eligible", "backoff_hours", "quota_ok"]
