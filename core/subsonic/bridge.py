"""files SoulSync can't read, played through the media server that can.

off by default, one switch per media server ("Stream files SoulSync can't
read through <server>"), music only. a song that goes this way is marked
(``soulsyncStreamVia``) so an app or a person can tell. the play is
recorded by SoulSync either way: the app reports it to SoulSync, never to
the server.

the server's stream url carries the server's own credentials, so it is
only ever fetched here and proxied, never handed to an app.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable, Dict, Optional, Tuple

from utils.logging_config import get_logger

logger = get_logger("subsonic.bridge")

SOURCES = ("plex", "jellyfin", "navidrome")
SOURCE_NAMES = {"plex": "Plex", "jellyfin": "Jellyfin", "navidrome": "Navidrome"}
MARKER_FIELD = "soulsyncStreamVia"


def setting_key(source: str) -> str:
    return "music_apps.bridge.%s" % source


def enabled(cfg_get: Callable[..., Any], source: Optional[str]) -> bool:
    if source not in SOURCES:
        return False
    try:
        return bool(cfg_get(setting_key(source), False))
    except Exception:  # noqa: BLE001 - unreadable: off
        return False


def settings(cfg_get: Callable[..., Any]) -> Dict[str, bool]:
    return {s: enabled(cfg_get, s) for s in SOURCES}


# ── can SoulSync read it ─────────────────────────────────────────────────

class Readable:
    """does a stored path resolve to a file here? asked once per folder and
    remembered for TTL seconds, so marking a page of songs costs a stat or
    two per album, not one per song."""

    TTL = 600.0
    MAX = 20000

    def __init__(self, resolve: Callable[[str], Optional[str]]):
        self._resolve = resolve
        self._dirs: Dict[str, Tuple[float, bool]] = {}
        self._lock = threading.Lock()

    def local_path(self, path: str) -> Optional[str]:
        if not path:
            return None
        try:
            return self._resolve(path)
        except Exception:  # noqa: BLE001
            return None

    def folder_readable(self, path: str, now: Optional[float] = None) -> bool:
        if not path:
            return False
        folder = os.path.dirname(path.replace("\\", "/"))
        mono = time.monotonic() if now is None else now
        with self._lock:
            hit = self._dirs.get(folder)
        if hit and mono - hit[0] < self.TTL:
            return hit[1]
        ok = self.local_path(path) is not None
        with self._lock:
            if len(self._dirs) >= self.MAX:
                self._dirs.clear()
            self._dirs[folder] = (mono, ok)
        return ok


def marker(cfg_get: Callable[..., Any], readable: Readable, source: Optional[str], path: str) -> Optional[str]:
    """the marker for a song: the server it will play through, or None
    when SoulSync reads it itself (or the switch is off)."""
    if not enabled(cfg_get, source):
        return None
    if readable.folder_readable(path):
        return None
    return source


# ── the server's stream ──────────────────────────────────────────────────

def server_item_id(conn, track_id: int, source: str) -> Optional[str]:
    row = conn.execute(
        "SELECT server_id FROM lib2_media_server_mappings WHERE entity_type = 'track' AND entity_id = ? "
        "AND server_source = ? AND server_id IS NOT NULL ORDER BY id DESC LIMIT 1",
        (int(track_id), source)).fetchone()
    if row and row[0]:
        return str(row[0])
    row = conn.execute("SELECT server_id FROM lib2_tracks WHERE id = ? AND server_source = ?",
                       (int(track_id), source)).fetchone()
    return str(row[0]) if row and row[0] else None


def sources_for(conn, track_id: int, file_source: Optional[str]) -> list:
    """the servers that know this track, the file's own first."""
    found = [r[0] for r in conn.execute(
        "SELECT DISTINCT server_source FROM lib2_media_server_mappings WHERE entity_type = 'track' "
        "AND entity_id = ? AND server_id IS NOT NULL", (int(track_id),)).fetchall() if r[0] in SOURCES]
    if file_source in SOURCES and file_source not in found:
        found.insert(0, file_source)
    found.sort(key=lambda s: s != file_source)
    return found


def stream_url(engine: Any, source: str, item_id: str, *, want_size: Any = None,
               want_path: Any = None) -> Optional[str]:
    """the server's own url for the original file, or None."""
    client = engine.client(source) if engine is not None else None
    if client is None:
        return None
    try:
        ensure = getattr(client, "ensure_connection", None)
        if callable(ensure):
            ensure()
    except Exception:  # noqa: BLE001 - a dead server is "no stream"
        return None
    if source == "navidrome":
        build = getattr(client, "build_stream_url", None)
        return build(item_id) if callable(build) else None
    if source == "jellyfin":
        base, key = getattr(client, "base_url", None), getattr(client, "api_key", None)
        if not base or not key:
            return None
        return "%s/Audio/%s/stream?static=true&api_key=%s" % (str(base).rstrip("/"), item_id, key)
    if source == "plex":
        server = getattr(client, "server", None)
        base, token = getattr(server, "_baseurl", None), getattr(server, "_token", None)
        if not base or not token:
            return None
        from core.video.media_stream import _plex_part_key, plex_part_url
        part = _plex_part_key(base, token, item_id, want_size=want_size, want_path=want_path)
        return plex_part_url(base, part, token) if part else None
    return None


__all__ = ["SOURCES", "SOURCE_NAMES", "MARKER_FIELD", "setting_key", "enabled", "settings", "Readable",
           "marker", "server_item_id", "sources_for", "stream_url"]
