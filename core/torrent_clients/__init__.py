"""Torrent client adapters.

Each adapter wraps one BitTorrent client (qBittorrent, Transmission,
Deluge) behind the ``TorrentClientAdapter`` Protocol so the rest of
SoulSync can talk to whichever client the user picked through one
uniform surface.

The active adapter is selected at runtime by the ``torrent_client.type``
config key. See ``get_active_adapter()`` for the factory.
"""

from __future__ import annotations

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
    "get_active_adapter",
    "adapter_for_type",
    "list_configured_clients",
    "get_adapter_for_client",
    "get_adapter_for_library",
]


def _build_adapter_with_profile(profile: Dict[str, Any]) -> Optional[TorrentClientAdapter]:
    """Instantiate an adapter using a named torrent client profile's connection fields."""
    client_type = str(profile.get("type") or "").strip().lower()
    if not client_type:
        return None
    orig_get = config_manager.get
    overrides = {
        "torrent_client.type": client_type,
        "torrent_client.url": profile.get("url", ""),
        "torrent_client.username": profile.get("username", ""),
        "torrent_client.password": profile.get("password", ""),
        "torrent_client.category": profile.get("category", "soulsync") or "soulsync",
        "torrent_client.save_path": profile.get("save_path", "") or "",
        "torrent_client.start_torrents": bool(profile.get("start_torrents", False)),
    }

    def _scoped_get(key: str, default: Any = None) -> Any:
        if key in overrides:
            return overrides[key]
        return orig_get(key, default)

    try:
        config_manager.get = _scoped_get  # type: ignore[method-assign]
        return adapter_for_type(client_type)
    finally:
        config_manager.get = orig_get  # type: ignore[method-assign]


def list_configured_clients() -> List[Dict[str, Any]]:
    """Return the global default torrent client plus any additional named clients (#2265)."""
    out: List[Dict[str, Any]] = []
    default_type = (config_manager.get("torrent_client.type", "") or "").strip().lower()
    if default_type:
        out.append({
            "id": "default",
            "name": f"Default ({default_type})",
            "type": default_type,
            "url": config_manager.get("torrent_client.url", "") or "",
            "category": config_manager.get("torrent_client.category", "soulsync") or "soulsync",
            "save_path": config_manager.get("torrent_client.save_path", "") or "",
            "is_default": True,
        })
    extra = config_manager.get("torrent_client.clients", []) or []
    if isinstance(extra, list):
        for entry in extra:
            if not isinstance(entry, dict):
                continue
            cid = str(entry.get("id") or "").strip()
            ctype = str(entry.get("type") or "").strip().lower()
            if not cid or not ctype or cid == "default":
                continue
            out.append({
                "id": cid,
                "name": str(entry.get("name") or cid).strip(),
                "type": ctype,
                "url": str(entry.get("url") or "").strip(),
                "category": str(entry.get("category") or "soulsync").strip(),
                "save_path": str(entry.get("save_path") or "").strip(),
                "is_default": False,
            })
    return out


def get_adapter_for_client(client_id: Optional[str] = None) -> Optional[TorrentClientAdapter]:
    """Return an adapter for a specific configured client id, falling back to the global default (#2265)."""
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
    """Resolve the torrent client adapter assigned to a library (Movies, TV, Audiobooks, etc.) (#2265).

    Checks ``library_cfg['torrent_client_id']`` first, then ``torrent_client.library_assignments[library_key]``,
    falling back seamlessly to the global default torrent client.
    """
    cid = None
    if isinstance(library_cfg, dict):
        cid = library_cfg.get("torrent_client_id")
    if not cid and library_key:
        assignments = config_manager.get("torrent_client.library_assignments", {}) or {}
        if isinstance(assignments, dict):
            cid = assignments.get(str(library_key))
    return get_adapter_for_client(cid)


def adapter_for_type(client_type: str) -> Optional[TorrentClientAdapter]:
    """Build a fresh adapter instance for the given client type string.

    ``None`` for unknown types so callers can present a helpful error
    rather than crashing on a typo'd config value.
    """
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
    """Return an adapter for whichever torrent client the user has
    selected in Settings. Reads ``torrent_client.type`` each call so a
    settings change is picked up without restarting the process."""
    client_type = (config_manager.get('torrent_client.type', '') or '').strip().lower()
    if not client_type:
        return None
    return adapter_for_type(client_type)
