"""Torrent client adapters.

Each adapter wraps one BitTorrent client (qBittorrent, Transmission,
Deluge, aria2) behind the ``TorrentClientAdapter`` Protocol so the rest of
SoulSync can talk to whichever client the user picked through one
uniform surface.

Supports the global default client (``torrent_client.type``) plus any
number of named client instances (``torrent_client.clients``) for
per-library download routing (#2265, #2336).
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from core.settings import config_manager

from core.torrent_clients.aria2 import Aria2Adapter
from core.torrent_clients.base import TorrentClientAdapter, TorrentStatus
from core.torrent_clients.deluge import DelugeAdapter
from core.torrent_clients.qbittorrent import QBittorrentAdapter
from core.torrent_clients.transmission import TransmissionAdapter

__all__ = [
    "TorrentClientAdapter",
    "TorrentStatus",
    "QBittorrentAdapter",
    "TransmissionAdapter",
    "DelugeAdapter",
    "Aria2Adapter",
    "TORRENT_CLIENT_TYPES",
    "get_active_adapter",
    "adapter_for_type",
    "list_configured_clients",
    "get_client_profile",
    "upsert_client",
    "delete_client",
    "get_adapter_for_client",
    "get_adapter_for_library",
]

TORRENT_CLIENT_TYPES = ("qbittorrent", "transmission", "deluge", "aria2")
_profile_lock = threading.Lock()


def _build_adapter_with_profile(profile: Dict[str, Any]) -> Optional[TorrentClientAdapter]:
    """Instantiate an adapter using a named torrent client profile's connection fields."""
    client_type = str(profile.get("type") or "").strip().lower()
    if client_type not in TORRENT_CLIENT_TYPES:
        return None
    overrides = {
        "torrent_client.type": client_type,
        "torrent_client.url": profile.get("url", ""),
        "torrent_client.username": profile.get("username", ""),
        "torrent_client.password": profile.get("password", ""),
        "torrent_client.category": profile.get("category", "soulsync") or "soulsync",
        "torrent_client.save_path": profile.get("save_path", "") or "",
        "torrent_client.start_torrents": bool(profile.get("start_torrents", False)),
    }
    with _profile_lock:
        orig_get = config_manager.get

        def _scoped_get(key: str, default: Any = None) -> Any:
            if key in overrides:
                return overrides[key]
            return orig_get(key, default)

        try:
            config_manager.get = _scoped_get  # type: ignore[method-assign]
            return adapter_for_type(client_type)
        finally:
            config_manager.get = orig_get  # type: ignore[method-assign]


def list_configured_clients(*, include_secrets: bool = False) -> List[Dict[str, Any]]:
    """Return the global default torrent client plus any additional named clients."""
    out: List[Dict[str, Any]] = []
    default_type = (config_manager.get("torrent_client.type", "") or "").strip().lower()
    if default_type:
        item: Dict[str, Any] = {
            "id": "default",
            "name": f"Default ({default_type})",
            "type": default_type,
            "url": config_manager.get("torrent_client.url", "") or "",
            "username": config_manager.get("torrent_client.username", "") or "",
            "has_password": bool(config_manager.get("torrent_client.password", "")),
            "category": config_manager.get("torrent_client.category", "soulsync") or "soulsync",
            "save_path": config_manager.get("torrent_client.save_path", "") or "",
            "completed_path": config_manager.get("torrent_client.completed_path", "") or "",
            "start_torrents": bool(config_manager.get("torrent_client.start_torrents", False)),
            "is_default": True,
        }
        if include_secrets:
            item["password"] = config_manager.get("torrent_client.password", "") or ""
        out.append(item)
    extra = config_manager.get("torrent_client.clients", []) or []
    if isinstance(extra, list):
        for entry in extra:
            if not isinstance(entry, dict):
                continue
            cid = str(entry.get("id") or "").strip()
            ctype = str(entry.get("type") or "").strip().lower()
            if not cid or ctype not in TORRENT_CLIENT_TYPES or cid == "default":
                continue
            item = {
                "id": cid,
                "name": str(entry.get("name") or cid).strip(),
                "type": ctype,
                "url": str(entry.get("url") or "").strip(),
                "username": str(entry.get("username") or "").strip(),
                "has_password": bool(entry.get("password")),
                "category": str(entry.get("category") or "soulsync").strip(),
                "save_path": str(entry.get("save_path") or "").strip(),
                "completed_path": str(entry.get("completed_path") or "").strip(),
                "start_torrents": bool(entry.get("start_torrents", False)),
                "is_default": False,
            }
            if include_secrets:
                item["password"] = str(entry.get("password") or "")
            out.append(item)
    return out


def get_client_profile(client_id: Optional[str] = None, *, include_secrets: bool = False) -> Optional[Dict[str, Any]]:
    """Return a single configured torrent client profile by id (defaulting to 'default')."""
    cid = str(client_id or "").strip() or "default"
    for c in list_configured_clients(include_secrets=include_secrets):
        if c["id"] == cid:
            return c
    if cid != "default":
        for c in list_configured_clients(include_secrets=include_secrets):
            if c["id"] == "default":
                return c
    return None


def upsert_client(entry: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update a named torrent client instance in ``torrent_client.clients``."""
    cid = str(entry.get("id") or "").strip()
    ctype = str(entry.get("type") or "").strip().lower()
    if not cid or cid == "default":
        raise ValueError("Client id is required and cannot be 'default'")
    if ctype not in TORRENT_CLIENT_TYPES:
        raise ValueError(f"Unsupported torrent client type: {ctype}")
    existing_list = config_manager.get("torrent_client.clients", []) or []
    clients = [dict(c) for c in existing_list if isinstance(c, dict)]
    prev = next((c for c in clients if str(c.get("id") or "").strip() == cid), None)
    # preserve existing password when caller omits it on update
    password = entry.get("password")
    if password is None and prev is not None:
        password = prev.get("password", "")
    record = {
        "id": cid,
        "name": str(entry.get("name") or cid).strip(),
        "type": ctype,
        "url": str(entry.get("url") or "").strip(),
        "username": str(entry.get("username") or "").strip(),
        "password": str(password or ""),
        "category": str(entry.get("category") or "soulsync").strip(),
        "save_path": str(entry.get("save_path") or "").strip(),
        "completed_path": str(entry.get("completed_path") or "").strip(),
        "start_torrents": bool(entry.get("start_torrents", False)),
    }
    clients = [c for c in clients if str(c.get("id") or "").strip() != cid]
    clients.append(record)
    config_manager.set("torrent_client.clients", clients)
    return get_client_profile(cid) or record


def delete_client(client_id: str) -> bool:
    """Remove a named torrent client instance. Cannot delete 'default'."""
    cid = str(client_id or "").strip()
    if not cid or cid == "default":
        return False
    existing_list = config_manager.get("torrent_client.clients", []) or []
    clients = [dict(c) for c in existing_list if isinstance(c, dict)]
    kept = [c for c in clients if str(c.get("id") or "").strip() != cid]
    if len(kept) == len(clients):
        return False
    config_manager.set("torrent_client.clients", kept)
    return True


def get_adapter_for_client(client_id: Optional[str] = None) -> Optional[TorrentClientAdapter]:
    """Return an adapter for a specific configured client id, falling back to the global default."""
    cid = str(client_id or "").strip()
    if not cid or cid == "default":
        return get_active_adapter()
    extra = config_manager.get("torrent_client.clients", []) or []
    if isinstance(extra, list):
        for entry in extra:
            if isinstance(entry, dict) and str(entry.get("id") or "").strip() == cid:
                return _build_adapter_with_profile(entry)
    return get_active_adapter()


def get_adapter_for_library(
    library_key: Optional[str] = None,
    library_cfg: Optional[Dict[str, Any]] = None,
) -> Optional[TorrentClientAdapter]:
    """Resolve the torrent client adapter assigned to a library (Movies, TV, Music, Audiobooks, etc.)."""
    cid = None
    if isinstance(library_cfg, dict):
        cid = library_cfg.get("torrent_client_id")
    if not cid and library_key:
        routing = config_manager.get("download_routing.libraries", {}) or {}
        if isinstance(routing, dict):
            entry = routing.get(str(library_key))
            if isinstance(entry, dict) and entry.get("torrent_client_id"):
                cid = entry.get("torrent_client_id")
    if not cid and library_key:
        assignments = config_manager.get("torrent_client.library_assignments", {}) or {}
        if isinstance(assignments, dict):
            cid = assignments.get(str(library_key))
    return get_adapter_for_client(cid)


def adapter_for_type(client_type: str) -> Optional[TorrentClientAdapter]:
    """Build a fresh adapter instance for the given client type string."""
    if client_type == "qbittorrent":
        return QBittorrentAdapter()
    if client_type == "transmission":
        return TransmissionAdapter()
    if client_type == "deluge":
        return DelugeAdapter()
    if client_type == "aria2":
        return Aria2Adapter()
    return None


def get_active_adapter() -> Optional[TorrentClientAdapter]:
    """Return an adapter for whichever torrent client the user has selected as global default."""
    client_type = (config_manager.get("torrent_client.type", "") or "").strip().lower()
    if not client_type:
        return None
    return adapter_for_type(client_type)
