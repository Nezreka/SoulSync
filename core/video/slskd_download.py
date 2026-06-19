"""Start + track Soulseek (slskd) downloads for the video pipeline.

Thin wrapper over slskd's transfer API (the same shared instance the music side uses):
  - ``start_download(username, filename, size)`` → POST /transfers/downloads/{username}
  - ``list_downloads()``                         → GET  /transfers/downloads (flattened)
The flatten + state-classification helpers are pure (unit-tested); the HTTP is glue.

Isolated: stdlib + requests + shared ``config_manager``; no music imports.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import requests


def _conn():
    from config.settings import config_manager
    base = str(config_manager.get("soulseek.slskd_url", "") or "").rstrip("/")
    key = config_manager.get("soulseek.api_key", "") or ""
    return base, ({"X-API-Key": key} if key else {})


def start_download(username: str, filename: str, size_bytes: int = 0) -> dict:
    """Ask slskd to download one file from a user. Returns {ok[, error]}."""
    base, headers = _conn()
    if not base:
        return {"ok": False, "error": "slskd isn't configured"}
    try:
        r = requests.post(base + "/api/v0/transfers/downloads/" + quote(str(username or "")),
                          json=[{"filename": filename, "size": int(size_bytes or 0)}],
                          headers=headers, timeout=15)
        r.raise_for_status()
        return {"ok": True}
    except Exception as e:   # noqa: BLE001 - surface any slskd/network failure to the caller
        return {"ok": False, "error": str(e)}


def list_downloads() -> list:
    """Current slskd downloads, flattened to one dict per file."""
    base, headers = _conn()
    if not base:
        return []
    try:
        r = requests.get(base + "/api/v0/transfers/downloads", headers=headers, timeout=15)
        if not r.ok:
            return []
        data = r.json()
    except Exception:   # noqa: BLE001, S110 - transient slskd error → no downloads this tick
        return []
    return flatten_downloads(data)


def flatten_downloads(data: Any) -> list:
    """slskd's nested users→directories→files → a flat list of file transfers. Pure."""
    out = []
    for user in (data if isinstance(data, list) else []):
        if not isinstance(user, dict):
            continue
        un = user.get("username", "")
        for d in (user.get("directories") or []):
            for f in (d.get("files") or []):
                out.append({
                    "username": un, "filename": f.get("filename", ""), "id": f.get("id", ""),
                    "state": f.get("state", ""), "size": f.get("size", 0) or 0,
                    "transferred": f.get("bytesTransferred", 0) or 0,
                })
    return out


def classify_state(state: Any) -> str:
    """slskd state string → 'completed' | 'failed' | 'active'. Pure."""
    s = str(state or "").lower()
    if "completed" in s and "succeed" in s:
        return "completed"
    if any(x in s for x in ("error", "cancel", "timed", "failed", "reject")):
        return "failed"
    if "completed" in s:        # completed but not succeeded → treat as failed
        return "failed"
    return "active"


def progress_pct(transfer: dict) -> float:
    """0–100 from bytesTransferred/size (100 once completed). Pure."""
    transfer = transfer or {}
    if classify_state(transfer.get("state")) == "completed":
        return 100.0
    size = transfer.get("size", 0) or 0
    tr = transfer.get("transferred", 0) or 0
    return round(min(99.0, (tr / size * 100.0) if size else 0.0), 1)


def find_transfer(transfers: list, username: str, filename: str) -> dict:
    """The transfer matching a started download (by username + filename). Pure."""
    for t in (transfers or []):
        if t.get("username") == username and t.get("filename") == filename:
            return t
    return {}


__all__ = ["start_download", "list_downloads", "flatten_downloads", "classify_state",
           "progress_pct", "find_transfer"]
