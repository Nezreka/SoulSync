"""Live per-task detail for the download status payloads (#1156).

The engine has always known where a task is searching, what it found and who
it's pulling from — the task dict carries it, the status builders dropped it.
This module is the one place that turns those private fields into the payload
both UIs render, so the modal and the downloads page can never drift apart.

Everything here is best-effort decoration: a missing field degrades to an
absent key, never an error — the status frame must survive any task shape.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

# username → display label. Streaming plugins use the source name as the
# transfer username; anything else is a Soulseek peer. Shared with the
# history writer (core/imports/side_effects.py) so a live row and its later
# history row can never disagree on a source's name.
SOURCE_LABELS = {
    "youtube": "YouTube",
    "tidal": "Tidal",
    "qobuz": "Qobuz",
    "hifi": "HiFi",
    "deezer_dl": "Deezer",
    "lidarr": "Lidarr",
    "soundcloud": "SoundCloud",
    "amazon": "Amazon",
    "staging": "Staging",
    "torrent": "Torrent",
    "usenet": "Usenet",
    # Auto-import isn't a download source, but flows through the same
    # post-process pipeline. Labeling it avoids mislabeling staging-folder
    # imports as Soulseek downloads.
    "auto_import": "Auto-Import",
}

_LIVE_STATUSES = frozenset(("searching", "downloading", "queued", "post_processing"))


def resolve_source_label(username: Optional[str]) -> str:
    """Display label for a transfer username ('' when there is none yet)."""
    if not username:
        return ""
    return SOURCE_LABELS.get(username, "Soulseek")


def _basename(path: Any) -> str:
    return str(path or "").replace("\\", "/").rsplit("/", 1)[-1]


def _add_shared_history(detail: Dict[str, Any], task: Dict[str, Any]) -> None:
    """Fields that narrate the task's journey so far, in any live state:
    how many peer+file pairs were already attempted across retries, and
    which sources spent their whole retry budget."""
    tried = task.get("used_sources")
    if tried and len(tried) > 1:
        detail["tried_sources"] = len(tried)
    exhausted = task.get("exhausted_download_sources")
    if exhausted:
        detail["exhausted_sources"] = sorted(str(s) for s in exhausted)


def build_live_detail(task: Dict[str, Any], live_info: Optional[Dict[str, Any]],
                      status: str) -> Optional[Dict[str, Any]]:
    """The ``live_detail`` dict for one task, or None for terminal states.

    ``status`` is the FINAL status the payload will carry (the builders mutate
    it after reading the raw task), so the detail always matches the badge the
    user is looking at.
    """
    if status not in _LIVE_STATUSES:
        return None
    detail: Dict[str, Any] = {}
    try:
        if status == "searching":
            query = task.get("current_query")
            if query:
                detail["query"] = str(query)
            query_count = task.get("query_count")
            if query_count:
                detail["query_index"] = int(task.get("current_query_index") or 0)
                detail["query_count"] = int(query_count)
            source = task.get("current_source")
            if source:
                detail["source"] = str(source)
            live = task.get("search_live")
            if isinstance(live, dict):
                # the slskd poll ticker ('responses' = peers answered) and the
                # per-source result split for best-quality pools
                for key in ("responses", "results", "by_source"):
                    if live.get(key) is not None:
                        detail[key] = live[key]
            _add_shared_history(detail, task)
        else:
            username = task.get("username")
            if username:
                detail["source"] = resolve_source_label(username)
                detail["username"] = str(username)
            filename = task.get("filename")
            if filename:
                detail["filename"] = _basename(filename)
            picked = task.get("picked_candidate")
            if isinstance(picked, dict):
                detail["picked"] = picked
            candidate_count = task.get("candidate_count")
            if candidate_count:
                detail["candidate_index"] = int(task.get("current_candidate_index") or 0)
                detail["candidate_count"] = int(candidate_count)
            if isinstance(live_info, dict):
                # the RAW slskd state — 'Queued, Remotely' and 'InProgress'
                # are different situations the UI could never distinguish
                # while the builder collapsed both to one word
                state = live_info.get("state")
                if state:
                    detail["slskd_state"] = str(state)
                    # ...and HOW LONG it has sat there, from the monitor's
                    # own stall clock
                    queued_since = task.get("queued_start_time")
                    if "Queued" in str(state) and queued_since:
                        waited = int(time.time() - float(queued_since))
                        if waited >= 0:
                            detail["queued_seconds"] = waited
                for src_key, dst_key in (("averageSpeed", "speed"),
                                         ("size", "size"),
                                         ("bytesTransferred", "bytes")):
                    if live_info.get(src_key) is not None:
                        detail[dst_key] = live_info[src_key]
            _add_shared_history(detail, task)
    except Exception:
        # decoration only — a malformed task must not break the status frame
        return detail or None
    return detail or None
