"""Development-only fixtures for UI states that only exist at runtime.

The Library-v2 download badges read ``core.runtime_state.download_tasks`` and
``matched_downloads_context`` — process-global, in-memory, and populated only
by a real Soulseek/Usenet transfer. There is no way to look at the queued /
searching / downloading / processing rendering without actually downloading
something, which makes those four states the least-reviewed pixels in the
Library.

This module injects synthetic entries into those same structures so the badges
can be seen and screenshotted. It writes nothing to the database and starts no
transfer.

Registered ONLY when ``SOULSYNC_DEV_FIXTURES=1`` is set in the environment;
production installs never see these routes. ``dev.py`` does not set it — start
the dev server with it explicitly:

    SOULSYNC_DEV_FIXTURES=1 .venv/Scripts/python.exe dev.py
"""

from __future__ import annotations

import os
from typing import Any, Dict, List

from core.runtime_state import (
    download_tasks,
    matched_context_lock,
    matched_downloads_context,
    tasks_lock,
)

ENV_FLAG = "SOULSYNC_DEV_FIXTURES"

# Task-id prefix so a teardown can remove exactly what this module added and
# never a real in-flight download.
_TASK_PREFIX = "devfixture-"

# The raw download_tasks statuses behind each rendered bucket.
_STATUS_BY_BUCKET = {
    "queued": "queued",
    "searching": "searching",
    "downloading": "downloading",
    "processing": "post_processing",
}


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").lower() in {"1", "true", "yes", "on"}


def _clear() -> int:
    removed = 0
    with tasks_lock:
        for key in [k for k in download_tasks if str(k).startswith(_TASK_PREFIX)]:
            del download_tasks[key]
            removed += 1
    with matched_context_lock:
        for key in [k for k in matched_downloads_context if str(k).startswith(_TASK_PREFIX)]:
            del matched_downloads_context[key]
            removed += 1
    return removed


def _inject(entries: List[Dict[str, Any]], transfers: Dict[str, Any]) -> int:
    added = 0
    with tasks_lock:
        for i, entry in enumerate(entries):
            bucket = entry.get("bucket", "queued")
            raw_status = _STATUS_BY_BUCKET.get(bucket)
            if raw_status is None:
                continue
            username = f"devfixture-peer-{i}"
            filename = f"devfixture-{i}.flac"
            download_tasks[f"{_TASK_PREFIX}{i}"] = {
                "status": raw_status,
                "username": username,
                "filename": filename,
                "track_info": {
                    "source_info": {
                        "lib2_track_id": int(entry["track_id"]),
                        "lib2_album_id": int(entry["album_id"]),
                    },
                },
            }
            if bucket == "downloading":
                transfers[(username, filename)] = int(entry.get("progress_pct", 0))
            added += 1
    return added


def register_dev_fixture_routes(app, *, make_context_key, transfer_overrides):
    """Wire the fixture routes onto ``app``. No-op unless the flag is set.

    ``transfer_overrides`` is the same dict the patched
    ``get_cached_transfer_data`` merges over the real client data, keyed by
    ``make_context_key(username, filename)`` — that is how a synthetic
    'downloading' task gets a percentage without a live transfer.
    """
    if not enabled():
        return False

    from flask import jsonify, request

    @app.route("/api/dev/fixtures/queue", methods=["POST"])
    def dev_fixtures_queue():
        """Body: {"entries": [{track_id, album_id, bucket, progress_pct}]}."""
        payload = request.get_json(silent=True) or {}
        entries = payload.get("entries") or []
        _clear()
        transfer_overrides.clear()
        raw: Dict[Any, int] = {}
        added = _inject(entries, raw)
        for (username, filename), pct in raw.items():
            transfer_overrides[make_context_key(username, filename)] = {
                "state": "InProgress",
                "percentComplete": pct,
            }
        return jsonify({"success": True, "injected": added})

    @app.route("/api/dev/fixtures/queue", methods=["DELETE"])
    def dev_fixtures_queue_clear():
        removed = _clear()
        transfer_overrides.clear()
        return jsonify({"success": True, "removed": removed})

    return True
