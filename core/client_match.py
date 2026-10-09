"""Match & import: adopt a download SoulSync did not send.

the clients tab lists everything in the torrent and usenet clients. a row
soulsync dispatched is labelled; anything else can be matched by hand to a
catalogue item and then handed to the SAME tracking the matching grab would
have written, so it shows on the downloads page and imports like any other
download of that type. there is no separate import path for matched items.

this module holds the pieces with no flask in them: which client rows soulsync
already owns, and the per-kind adopters.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("client_match")

# a record in one of these states no longer tracks its download, so the row is
# up for matching again. that is the "lost record" case: the torrent finished
# in the client but the soulsync side of it failed.
_DEAD_STATES = frozenset({"failed", "import_failed", "cancelled", "canceled"})

KINDS = ("album", "track", "movie", "episode", "season", "audiobook")


def _label(kind: str, title: Any) -> Dict[str, str]:
    return {"kind": kind, "title": str(title or "")}


def soulseek_keys(client_ref: Any) -> list:
    """The slskd rows behind one soulseek job, as the clients tab keys them:
    (username, remote filename) for a filename ref, ("id", transfer id) for a
    transfer id. A soulseek job is a folder of transfers packed by
    core.audiobook_soulseek.encode_refs."""
    from core.audiobook_soulseek import decode_refs
    unpacked = decode_refs(client_ref)
    keys = []
    for ref in unpacked["refs"]:
        if "\\" in ref or "/" in ref:
            keys.append((unpacked["username"], ref))
        else:
            keys.append(("id", ref))
    return keys


def soulseek_job(username: str, filenames: Iterable[str]) -> str:
    """Pack transfers already in slskd into one job ref, the shape an audiobook
    soulseek grab writes, so the same status / cancel / landing-path code
    follows it. Remote filenames are the refs: the transfers were not started
    here, so there are no ids to remember, and core.audiobook_soulseek
    matches a filename ref within its peer."""
    import re
    from core.audiobook_soulseek import encode_refs
    names = [str(n) for n in filenames if n]
    folder = ""
    if names:
        parts = [p for p in re.split(r"[\\/]+", names[0]) if p]
        folder = parts[-2] if len(parts) >= 2 else ""
        if len(names) == 1 and folder:
            # a lone file: point at the file, not a shared folder it sits in
            folder = os.path.join(folder, parts[-1])
    return encode_refs(names, username, folder)


def audiobook_known(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[Any, Dict[str, str]]]:
    """Client refs of audiobook downloads that are still soulsync's."""
    known: Dict[str, Dict[Any, Dict[str, str]]] = {"torrent": {}, "usenet": {}, "slskd": {}}
    for row in rows or []:
        if str(row.get("status") or "").lower() in _DEAD_STATES:
            continue
        source = str(row.get("source") or "").lower()
        ref = str(row.get("client_id") or "").strip()
        if not ref:
            continue
        label = _label("audiobook", row.get("title") or row.get("release_title"))
        if source == "soulseek":
            for key in soulseek_keys(ref):
                known["slskd"][key] = label
        elif source in ("torrent", "usenet"):
            known[source][ref.lower() if source == "torrent" else ref] = label
    return known


def plugin_known(torrent_plugin: Any, usenet_plugin: Any) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Client refs of music downloads the torrent / usenet plugins are running."""
    known: Dict[str, Dict[str, Dict[str, str]]] = {"torrent": {}, "usenet": {}}
    for source, plugin, field in (("torrent", torrent_plugin, "torrent_hash"),
                                  ("usenet", usenet_plugin, "job_id")):
        rows = _plugin_rows(plugin)
        for row in rows:
            ref = str(row.get(field) or "").strip()
            if not ref:
                continue
            key = ref.lower() if source == "torrent" else ref
            known[source][key] = _label("album", row.get("display_name") or row.get("filename"))
    return known


def _plugin_rows(plugin: Any) -> list:
    if plugin is None:
        return []
    rows = getattr(plugin, "active_downloads", None)
    if not isinstance(rows, dict):
        return []
    lock = getattr(plugin, "_lock", None)
    try:
        if lock is not None:
            with lock:
                return [dict(r) for r in rows.values() if isinstance(r, dict)]
        return [dict(r) for r in rows.values() if isinstance(r, dict)]
    except Exception as exc:  # noqa: BLE001 - labels are best effort
        logger.debug("could not read plugin rows: %s", exc)
        return []


def merge_known(base: Dict[str, Dict[Any, Any]], *extra: Optional[Dict[str, Dict[Any, Any]]]) -> Dict[str, Dict[Any, Any]]:
    """Fold extra label maps into ``base`` without overwriting a label already there."""
    for more in extra:
        for source, labels in (more or {}).items():
            bucket = base.setdefault(source, {})
            for key, label in labels.items():
                bucket.setdefault(key, label)
    return base


def video_known(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[Any, Dict[str, str]]]:
    """Video downloads keyed by client_ref (torrent hash / nzo id), or by
    (username, filename) for soulseek grabs."""
    known: Dict[str, Dict[Any, Dict[str, str]]] = {"torrent": {}, "usenet": {}, "slskd": {}}
    for dl in rows or []:
        if not isinstance(dl, dict):
            continue
        if str(dl.get("status") or "").lower() in _DEAD_STATES:
            continue
        label = _label(dl.get("kind") or "video", dl.get("title") or dl.get("release_title"))
        ref = str(dl.get("client_ref") or "").strip()
        source = dl.get("source")
        if source == "torrent" and ref:
            known["torrent"][ref.lower()] = label
        elif source == "usenet" and ref:
            known["usenet"][ref] = label
        elif source == "soulseek" and dl.get("username") and dl.get("filename"):
            known["slskd"][(dl["username"], dl["filename"])] = label
    return known


def music_task_known(tasks: Iterable[Dict[str, Any]]) -> Dict[str, Dict[Any, Dict[str, str]]]:
    """Music soulseek transfers, keyed by (username, filename)."""
    known: Dict[str, Dict[Any, Dict[str, str]]] = {"slskd": {}}
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        username, filename = task.get("username"), task.get("filename")
        if not username or not filename:
            continue
        info = task.get("track_info") if isinstance(task.get("track_info"), dict) else {}
        known["slskd"][(username, filename)] = _label(
            "track", info.get("name") or task.get("track_name"))
    return known


def compose_known(*, video_rows, music_tasks, audiobook_rows,
                  torrent_plugin, usenet_plugin,
                  music_matches=lambda: []) -> Dict[str, Dict[Any, Dict[str, str]]]:
    """Every label, from getters. Best effort per source: one that fails never
    hides another's labels."""
    known: Dict[str, Dict[Any, Dict[str, str]]] = {"torrent": {}, "usenet": {}, "slskd": {}}
    parts = (
        ("video", lambda: video_known(video_rows())),
        ("music", lambda: music_task_known(music_tasks())),
        ("audiobook", lambda: audiobook_known(audiobook_rows())),
        ("plugin", lambda: plugin_known(torrent_plugin(), usenet_plugin())),
        ("music match", lambda: music_match_known(music_matches())),
    )
    for name, build in parts:
        try:
            merge_known(known, build())
        except Exception as exc:  # noqa: BLE001 - labels are best effort
            logger.debug("[Clients] %s known-items unavailable: %s", name, exc)
    return known


# ---------------------------------------------------------------------------
# music: a download in the client, matched to an album or a track
# ---------------------------------------------------------------------------
#
# audiobooks and video already have a monitor that follows a client job to the
# library, so matching one only writes the record that monitor reads. music has
# none: its torrent and usenet plugins follow a download from inside the worker
# that started it. so a music match is kept here, durably (a restart must not
# lose it, losing records is how this feature started), and a small watcher
# follows it: wait for the client to finish, copy the audio out (the original
# keeps seeding), import the copy with the import page's own album / single
# import, and report on the downloads page the way audiobooks do.

MUSIC_KINDS = ("album", "track")
MATCH_BATCH_ID = "client-matches"
_ACTIVE = ("waiting", "importing")
# client misses before a match gives up, counted only while the client answers
_GIVE_UP_AFTER_MISSES = 6
_POLL_SECONDS = 15
# the private folder copies are imported from, inside the download folder: the
# import validator allows it and the auto-import worker never scans it
_COPY_DIR = ".soulsync-matches"


def ensure_schema(cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS client_music_matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client TEXT NOT NULL,
            client_ref TEXT NOT NULL,
            kind TEXT NOT NULL,
            match_json TEXT NOT NULL,
            release_title TEXT,
            profile_id INTEGER,
            status TEXT NOT NULL DEFAULT 'waiting',
            error TEXT,
            created_at REAL,
            updated_at REAL
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_client_music_matches_ref "
                   "ON client_music_matches (client, client_ref)")


class MusicMatchStore:
    """The durable list of music matches, in the music database."""

    def __init__(self, connect):
        self._connect = connect

    def _rows(self, sql, args=()):
        conn = self._connect()
        try:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, args).fetchall()]
        finally:
            conn.close()

    def add(self, *, client, client_ref, kind, match, release_title="", profile_id=None) -> int:
        now = time.time()
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO client_music_matches (client, client_ref, kind, match_json, "
                "release_title, profile_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'waiting', ?, ?)",
                (client, client_ref, kind, json.dumps(match), release_title, profile_id, now, now))
            conn.commit()
            return int(cur.lastrowid)
        finally:
            conn.close()

    def active(self):
        return self._rows("SELECT * FROM client_music_matches WHERE status IN ('waiting', 'importing') "
                          "ORDER BY id")

    def recent(self, limit=200):
        return self._rows("SELECT * FROM client_music_matches ORDER BY id DESC LIMIT ?", (int(limit),))

    def following(self, client, client_ref) -> bool:
        ref = str(client_ref or "").lower()
        return any(str(r["client_ref"]).lower() == ref and r["client"] == client for r in self.active())

    def update(self, match_id, **fields):
        if not fields:
            return
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn = self._connect()
        try:
            conn.execute(f"UPDATE client_music_matches SET {cols} WHERE id = ?",  # noqa: S608 - keys are ours
                         (*fields.values(), match_id))
            conn.commit()
        finally:
            conn.close()


def music_store() -> MusicMatchStore:
    from database.music_database import get_database
    return MusicMatchStore(get_database()._get_connection)


def music_match_known(rows) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Matches still being followed, so their cards say so instead of offering
    another match."""
    known: Dict[str, Dict[Any, Dict[str, str]]] = {"torrent": {}, "usenet": {}, "slskd": {}}
    for row in rows or []:
        client = row.get("client")
        if row.get("status") not in _ACTIVE or client not in ("torrent", "usenet", "soulseek"):
            continue
        try:
            match = json.loads(row.get("match_json") or "{}")
        except (TypeError, ValueError):
            match = {}
        label = _label(row.get("kind") or "album", _match_title(match))
        ref = str(row.get("client_ref") or "")
        if client == "soulseek":
            for key in soulseek_keys(ref):
                known["slskd"][key] = label
        else:
            known[client][ref.lower() if client == "torrent" else ref] = label
    return known


def _match_title(match: Dict[str, Any]) -> str:
    artist, name = str(match.get("artist") or ""), str(match.get("name") or "")
    return f"{artist} - {name}" if artist and name else (name or artist)


# ── the downloads page card, the same shared state audiobooks write ─────────

_CARD_FLAGS = {"is_music": False, "managed_externally": True,
               "batch_type": "client_match", "source_page": "Clients"}


def _card_key(match_id) -> str:
    return f"client-match-{match_id}"


def card_register(row: Dict[str, Any]) -> None:
    from core.runtime_state import download_batches, download_tasks, tasks_lock
    try:
        match = json.loads(row.get("match_json") or "{}")
    except (TypeError, ValueError):
        match = {}
    key = _card_key(row["id"])
    with tasks_lock:
        batch = download_batches.get(MATCH_BATCH_ID)
        if batch is None:
            batch = {"queue": [], "active_count": 0, "max_concurrent": 1, "queue_index": 0,
                     "playlist_id": MATCH_BATCH_ID, "playlist_name": "Matched downloads",
                     "phase": "downloading"}
            download_batches[MATCH_BATCH_ID] = batch
        # re-stamped every time: a batch that lost these would be picked up by
        # the music workers on their next pass
        batch.update(_CARD_FLAGS)
        if key not in batch["queue"]:
            batch["queue"].append(key)
        if key in download_tasks:
            return
        title = str(match.get("name") or row.get("release_title") or "Matched download")
        download_tasks[key] = {
            "status": "queued",
            "track_info": {"title": title, "name": title, "track_name": title,
                           "artist": str(match.get("artist") or ""),
                           "artist_name": str(match.get("artist") or ""),
                           "album": title if row.get("kind") == "album" else str(match.get("album") or ""),
                           "album_name": title if row.get("kind") == "album" else str(match.get("album") or ""),
                           "artwork_url": str(match.get("image_url") or "")},
            "playlist_id": MATCH_BATCH_ID, "batch_id": MATCH_BATCH_ID,
            "track_index": max(0, len(batch["queue"]) - 1),
            "download_source": f"Matched ({row.get('client')})",
            "quality": str(row.get("client") or ""),
            "progress": 0.0, "speed": 0.0, "bytes_transferred": 0, "size": 0,
            "status_change_time": time.time(), "cancel_requested": False,
            "error_message": None, "username": "",
            "release_title": str(row.get("release_title") or ""),
        }


def card_update(match_id, *, status=None, progress=None, speed=None, size=None,
                done=None, error=None) -> None:
    from core.runtime_state import download_tasks, tasks_lock
    with tasks_lock:
        task = download_tasks.get(_card_key(match_id))
        if not task:
            return
        if status and task["status"] != status:
            task["status"] = status
            task["status_change_time"] = time.time()
        if progress is not None:
            task["progress"] = max(0.0, min(100.0, float(progress)))
        if speed is not None:
            task["speed"] = max(0.0, float(speed))
        if size:
            task["size"] = int(size)
        if done is not None:
            task["bytes_transferred"] = int(done)
        if error is not None:
            task["error_message"] = error or None
        if status == "completed":
            task["progress"] = 100.0


def card_cancelled(match_id) -> bool:
    from core.runtime_state import download_tasks, tasks_lock
    with tasks_lock:
        task = download_tasks.get(_card_key(match_id))
        return bool(task and (task.get("cancel_requested") or task.get("status") == "cancelled"))


# ── copying out of the client and importing the copy ────────────────────────

def _audio_files(path: str) -> list:
    from core.imports.staging import AUDIO_EXTENSIONS
    if os.path.isfile(path):
        return [path] if os.path.splitext(path)[1].lower() in AUDIO_EXTENSIONS else []
    found = []
    for root, _dirs, files in os.walk(path):
        for name in sorted(files):
            if os.path.splitext(name)[1].lower() in AUDIO_EXTENSIONS:
                found.append(os.path.join(root, name))
    return found


def copy_audio(source: str, dest: str) -> list:
    """Copy every audio file under ``source`` into ``dest`` with the album
    bundle's own staging copy (temp file, then rename, so nothing ever sees a
    half-written file). Copies, never moves: the original is the client's and
    keeps seeding. The matcher reads disc numbers from tags, so the copy is
    flat, the same as an album bundle's."""
    from pathlib import Path
    from core.download_plugins.album_bundle import copy_audio_files_atomically
    return copy_audio_files_atomically([Path(p) for p in _audio_files(source)], Path(dest))


def import_copy(kind: str, match: Dict[str, Any], folder: str, files: list, runtime) -> Dict[str, Any]:
    """Import the copied files with the import page's own album / single import.
    Returns {ok, imported, error}."""
    if kind == "album":
        from core.imports.album import build_album_import_match_payload
        from core.imports.routes import album_process
        payload = build_album_import_match_payload(
            str(match.get("id") or ""), album_name=str(match.get("name") or ""),
            album_artist=str(match.get("artist") or ""), file_paths=files,
            source=str(match.get("source") or "") or None, root=folder)
        if not payload.get("success"):
            return {"ok": False, "imported": 0, "error": payload.get("error") or "Could not load that album."}
        matches = [m for m in payload.get("matches") or [] if m.get("staging_file")]
        if not matches:
            return {"ok": False, "imported": 0,
                    "error": "None of the files matched that album's tracks."}
        result, status = album_process(runtime, {"album": payload["album"], "matches": matches,
                                                 "source": payload.get("source")})
        processed = int(result.get("processed") or 0) if isinstance(result, dict) else 0
        if status >= 400 or not processed:
            return {"ok": False, "imported": processed,
                    "error": (result or {}).get("error") or "The import failed."}
        return {"ok": True, "imported": processed, "error": ""}

    # a single: the largest audio file is the track, anything else is extras
    from core.imports.routes import process_single_import_file
    track_file = max(files, key=lambda p: os.path.getsize(p))
    outcome, message = process_single_import_file(runtime, {
        "full_path": track_file, "filename": os.path.basename(track_file),
        "manual_match": {"id": str(match.get("id") or ""), "source": str(match.get("source") or "")},
    })
    if outcome == "error":
        return {"ok": False, "imported": 0, "error": message}
    return {"ok": True, "imported": 1, "error": ""}


def _leftovers_to_staging(folder: str) -> int:
    """Whatever the import did not take moves into the import folder, so it
    shows on the import page instead of hiding in a private folder."""
    from core.imports.staging import get_staging_path
    left = _audio_files(folder)
    if left:
        target = os.path.join(get_staging_path(), os.path.basename(folder.rstrip(os.sep)))
        for path in left:
            dest = os.path.join(target, os.path.relpath(path, folder))
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            shutil.move(path, dest)
    shutil.rmtree(folder, ignore_errors=True)
    return len(left)


# ── the watcher ─────────────────────────────────────────────────────────────

def _status(client: str, ref: str):
    import asyncio
    if client == "soulseek":
        # the same folder-of-transfers reader audiobook soulseek grabs use
        from core.audiobook_download_monitor import _SoulseekStatus
        from core.audiobook_soulseek import _shared_client, status_for
        rolled = status_for(ref)
        if rolled is None:
            return None, False
        if rolled.get("state") == "unavailable":
            return None, _shared_client() is not None
        return _SoulseekStatus(rolled), True
    if client == "torrent":
        from core.torrent_clients import get_active_adapter
    else:
        from core.usenet_clients import get_active_adapter
    adapter = get_active_adapter()
    if adapter is None:
        return None, False
    try:
        return asyncio.run(adapter.get_status(ref)), True
    except Exception:  # noqa: BLE001 - a poll that fails is "unknown right now"
        return None, False


def _is_complete(status) -> bool:
    state = str(getattr(status, "state", "") or "").lower()
    progress = float(getattr(status, "progress", 0) or 0)
    return state in ("completed", "seeding") or progress >= 1.0 or progress >= 100.0


def _copy_root() -> str:
    # built exactly the way the import validator builds its download root, so
    # the copy is always inside a folder it accepts
    from core.settings import config_manager
    from core.imports.paths import docker_resolve_path
    base = docker_resolve_path(config_manager.get("soulseek.download_path", "./downloads"))
    return os.path.join(base, _COPY_DIR)


def process_match(row: Dict[str, Any], store: MusicMatchStore, *, get_status=_status,
                  resolve=None, runtime_factory=None, misses: Optional[Dict[int, int]] = None) -> str:
    """Advance one match by a tick. Returns its status afterwards."""
    misses = misses if misses is not None else {}
    match_id = row["id"]
    if card_cancelled(match_id):
        store.update(match_id, status="cancelled", error="Cancelled by you")
        card_update(match_id, status="cancelled")
        return "cancelled"

    status, reachable = get_status(row["client"], row["client_ref"])
    if status is None:
        if reachable:
            misses[match_id] = misses.get(match_id, 0) + 1
            if misses[match_id] >= _GIVE_UP_AFTER_MISSES:
                misses.pop(match_id, None)
                error = "The download client no longer has this download."
                store.update(match_id, status="failed", error=error)
                card_update(match_id, status="failed", error=error)
                return "failed"
        return row["status"]
    misses.pop(match_id, None)

    progress = float(getattr(status, "progress", 0) or 0)
    card_update(match_id, status="downloading", progress=progress * 100 if progress <= 1 else progress,
                speed=getattr(status, "download_speed", 0), size=getattr(status, "size", 0),
                done=getattr(status, "downloaded", 0))
    if not _is_complete(status):
        return row["status"]

    reported = getattr(status, "content_path", None) or getattr(status, "save_path", None)
    if resolve is None:
        from core.download_plugins.album_bundle import resolve_reported_save_path as resolve
    path = resolve(reported) if reported else None
    if not path or not os.path.exists(path):
        # finished but not visible from here (yet): keep waiting, and say why
        card_update(match_id, error=f"Waiting to see the files at {reported or 'the client path'}")
        return row["status"]

    store.update(match_id, status="importing", error="")
    card_update(match_id, status="importing", error="")
    folder = os.path.join(_copy_root(), f"{match_id}-{_safe_name(row.get('release_title'))}")
    try:
        files = copy_audio(path, folder)
        if not files:
            raise RuntimeError("No audio files in this download.")
        match = json.loads(row.get("match_json") or "{}")
        runtime = runtime_factory(row) if runtime_factory else _default_runtime(row)
        result = import_copy(row["kind"], match, folder, files, runtime)
    except Exception as exc:  # noqa: BLE001 - a failed import is a failed match, never a crash
        logger.warning("music match %s failed: %s", match_id, exc, exc_info=True)
        result = {"ok": False, "imported": 0, "error": str(exc)}
    left = _leftovers_to_staging(folder) if os.path.isdir(folder) else 0
    note = f" {left} file{'s' if left != 1 else ''} left on the import page." if left else ""
    if result["ok"]:
        store.update(match_id, status="completed", error=note.strip())
        card_update(match_id, status="completed", error=note.strip())
        return "completed"
    error = (result.get("error") or "The import failed.") + note
    store.update(match_id, status="failed", error=error)
    card_update(match_id, status="failed", error=error)
    return "failed"


def _safe_name(text) -> str:
    keep = "".join(c if c.isalnum() or c in " ._-()[]" else "_" for c in str(text or "match"))
    return keep.strip()[:80] or "match"


def _default_runtime(row):
    from api.import_routes import _build_import_route_runtime
    runtime = _build_import_route_runtime()
    if row.get("profile_id"):
        runtime.profile_id = row["profile_id"]
    return runtime


class MusicMatchWatcher:
    """The timer around process_match, one pass every few seconds."""

    def __init__(self, app=None):
        self._app = app
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._misses: Dict[int, int] = {}

    def start(self) -> bool:
        if self._thread and self._thread.is_alive():
            return False
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="client-music-matches", daemon=True)
        self._thread.start()
        return True

    def tick(self) -> int:
        store = music_store()
        rows = store.active()
        for row in rows:
            card_register(row)   # cards are runtime state; a restart drops them
            process_match(row, store, misses=self._misses)
        return len(rows)

    def _loop(self):
        while not self._stop.is_set():
            try:
                if self._app is not None:
                    with self._app.app_context():
                        busy = self.tick()
                else:
                    busy = self.tick()
            except Exception:  # noqa: BLE001 - the loop must outlive one bad pass
                logger.exception("music match pass failed")
                busy = 1
            if not busy:
                return   # nothing to follow: sleep until the next match wakes it
            self._stop.wait(_POLL_SECONDS)


_watcher: Optional[MusicMatchWatcher] = None


def ensure_watcher(app=None) -> bool:
    global _watcher
    if _watcher is None:
        _watcher = MusicMatchWatcher(app)
    elif app is not None and _watcher._app is None:
        _watcher._app = app
    return _watcher.start()


# ---------------------------------------------------------------------------
# a first guess from the release name, so the match window opens filled in
# ---------------------------------------------------------------------------

_BOOK_WORDS = ("m4b", "audiobook", "unabridged", "abridged", "narrated", "read by", "graphicaudio")
_MUSIC_WORDS = ("flac", "mp3", "320kbps", "320", "v0", "24bit", "16bit", "24-96", "24-192",
                "16-44", "alac", "lossless", "discography", "vinyl", "cdda")


def suggest_from_name(name: str) -> Dict[str, Any]:
    """{kind, query, year, season, episode}: a guess the user corrects. kind is
    None when nothing in the name says what it is."""
    import re
    from core.video.release_parse import parse_release, search_title

    raw = str(name or "")
    parsed = parse_release(raw) or {}
    words = set(re.split(r"[\s._\-\[\]()]+", raw.lower()))
    lowered = raw.lower()
    query = search_title(raw) or raw
    season, episode, year = parsed.get("season"), parsed.get("episode"), parsed.get("year")

    if season is not None and episode is not None:
        kind = "episode"
    elif season is not None:
        kind = "season"
    elif any(w in words or (" " in w and w in lowered) for w in _BOOK_WORDS):
        kind = "audiobook"
    elif any(w in words for w in _MUSIC_WORDS):
        kind = "album"
    elif parsed.get("resolution") or parsed.get("codec"):
        kind = "movie"
    else:
        kind = None
    return {"kind": kind, "query": query, "year": year, "season": season, "episode": episode}
