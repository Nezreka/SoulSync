"""player setup: what an admin decides once for the whole server.

the player works before any of this is saved (one Movies, one TV Shows,
everything visible, the usual sidebar). setup is where an admin names the
server, groups its folders into libraries (core/player/libraries.py), says
who sees which, and sets the sidebar everyone starts from. ``done`` only
records that an admin has been through it, so the player stops offering it.

kept in player.db; the table is made by the first write.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any, Dict

from core.player.settings import SettingsError, _nav_hidden, _nav_ids

TABLE = "player_setup"
MAX_NAME = 60

_ready: set = set()
_lock = threading.Lock()
_cache: Dict[str, Dict[str, Any]] = {}

DEFAULTS: Dict[str, Any] = {
    "done": False,
    # shown in the player and the app; empty is "SoulSync"
    "server_name": "",
    # the order every profile starts from (a profile's own order wins)
    "sidebar_order": [],
    # places nobody on this server gets (a profile can hide more, not fewer)
    "sidebar_hidden": [],
    # who may rename, re-date and re-picture home videos: "admins" or
    # "everyone" (anyone who can see the clip)
    "home_editors": "admins",
}


class SetupError(ValueError):
    pass


def _key(db: Any) -> str:
    return str(getattr(db, "database_path", id(db)))


def _clean(key: str, value: Any) -> Any:
    try:
        if key == "done":
            if not isinstance(value, bool):
                raise SetupError("done is true or false")
            return value
        if key == "server_name":
            name = " ".join(str(value or "").split())
            if len(name) > MAX_NAME:
                raise SetupError(f"the server's name is at most {MAX_NAME} characters")
            return name
        if key == "sidebar_order":
            return _nav_ids(value)
        if key == "sidebar_hidden":
            return _nav_hidden(value)
        if key == "home_editors":
            if value not in ("admins", "everyone"):
                raise SetupError("home_editors is admins or everyone")
            return value
    except SettingsError as e:
        raise SetupError(str(e)) from None
    raise SetupError(f"no setup value called {key}")


def get(db: Any) -> Dict[str, Any]:
    key = _key(db)
    with _lock:
        hit = _cache.get(key)
    if hit is None:
        hit = dict(DEFAULTS)
        try:
            with db._get_connection() as conn:
                for k, raw in conn.execute(f"SELECT key, value_json FROM {TABLE}"):
                    if k in DEFAULTS:
                        try:
                            hit[k] = _clean(k, json.loads(raw))
                        except (SetupError, ValueError, TypeError):
                            continue
        except sqlite3.OperationalError as e:
            if "no such table" not in str(e).lower():
                raise
        with _lock:
            _cache[key] = hit
    return {k: (list(v) if isinstance(v, list) else v) for k, v in hit.items()}


def put(db: Any, changes: Any) -> Dict[str, Any]:
    """save some of it (all or nothing); returns it all"""
    if not isinstance(changes, dict):
        raise SetupError("setup is an object")
    clean = {k: _clean(k, v) for k, v in changes.items()}
    with db._get_connection() as conn:
        if _key(db) not in _ready:
            conn.execute(f"CREATE TABLE IF NOT EXISTS {TABLE} (key TEXT PRIMARY KEY, value_json TEXT NOT NULL)")
            _ready.add(_key(db))
        for k, v in clean.items():
            conn.execute(
                f"INSERT INTO {TABLE} (key, value_json) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json", (k, json.dumps(v)))
        conn.commit()
    with _lock:
        _cache.pop(_key(db), None)
    return get(db)


def sidebar_for(db: Any, own_order: list, own_hidden: list) -> Dict[str, list]:
    """a profile's sidebar: its own order if it made one, else the server's;
    hidden is the server's list plus its own"""
    server = get(db)
    hidden = list(server["sidebar_hidden"])
    hidden += [h for h in own_hidden if h not in hidden]
    return {"sidebar_order": list(own_order) if own_order else list(server["sidebar_order"]),
            "sidebar_hidden": hidden}


__all__ = ["SetupError", "DEFAULTS", "get", "put", "sidebar_for"]
