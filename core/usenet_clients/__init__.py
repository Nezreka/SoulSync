"""Usenet client adapters.

Each adapter wraps one Usenet downloader (SABnzbd, NZBGet) behind
the ``UsenetClientAdapter`` Protocol so the rest of SoulSync can
talk to whichever client the user picked through one uniform surface.

Supports the global default client (``usenet_client.type``) plus any
number of named client instances (``usenet_client.clients``) for
per-library download routing (#2336).
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from core.settings import config_manager

from core.usenet_clients.base import UsenetClientAdapter, UsenetStatus
from core.usenet_clients.nzbget import NZBGetAdapter
from core.usenet_clients.sabnzbd import SABnzbdAdapter

__all__ = [
    "UsenetClientAdapter",
    "UsenetStatus",
    "SABnzbdAdapter",
    "NZBGetAdapter",
    "USENET_CLIENT_TYPES",
    "get_active_adapter",
    "adapter_for_type",
    "list_configured_clients",
    "get_client_profile",
    "upsert_client",
    "delete_client",
    "get_adapter_for_client",
    "get_adapter_for_library",
]

USENET_CLIENT_TYPES = ("sabnzbd", "nzbget")
_profile_lock = threading.Lock()


def _build_adapter_with_profile(profile: Dict[str, Any]) -> Optional[UsenetClientAdapter]:
    """Instantiate an adapter using a named usenet client profile's connection fields."""
    client_type = str(profile.get("type") or "").strip().lower()
    if client_type not in USENET_CLIENT_TYPES:
        return None
    overrides = {
        "usenet_client.type": client_type,
        "usenet_client.url": profile.get("url", ""),
        "usenet_client.api_key": profile.get("api_key", ""),
        "usenet_client.username": profile.get("username", ""),
        "usenet_client.password": profile.get("password", ""),
        "usenet_client.category": profile.get("category", "soulsync") or "soulsync",
        "usenet_client.completed_path": profile.get("completed_path", "") or "",
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
    """Return the global default usenet client plus any additional named clients."""
    out: List[Dict[str, Any]] = []
    default_type = (config_manager.get("usenet_client.type", "") or "").strip().lower()
    if default_type:
        item: Dict[str, Any] = {
            "id": "default",
            "name": f"Default ({default_type})",
            "type": default_type,
            "url": config_manager.get("usenet_client.url", "") or "",
            "username": config_manager.get("usenet_client.username", "") or "",
            "has_api_key": bool(config_manager.get("usenet_client.api_key", "")),
            "has_password": bool(config_manager.get("usenet_client.password", "")),
            "category": config_manager.get("usenet_client.category", "soulsync") or "soulsync",
            "completed_path": config_manager.get("usenet_client.completed_path", "") or "",
            "is_default": True,
        }
        if include_secrets:
            item["api_key"] = config_manager.get("usenet_client.api_key", "") or ""
            item["password"] = config_manager.get("usenet_client.password", "") or ""
        out.append(item)
    extra = config_manager.get("usenet_client.clients", []) or []
    if isinstance(extra, list):
        for entry in extra:
            if not isinstance(entry, dict):
                continue
            cid = str(entry.get("id") or "").strip()
            ctype = str(entry.get("type") or "").strip().lower()
            if not cid or ctype not in USENET_CLIENT_TYPES or cid == "default":
                continue
            item = {
                "id": cid,
                "name": str(entry.get("name") or cid).strip(),
                "type": ctype,
                "url": str(entry.get("url") or "").strip(),
                "username": str(entry.get("username") or "").strip(),
                "has_api_key": bool(entry.get("api_key")),
                "has_password": bool(entry.get("password")),
                "category": str(entry.get("category") or "soulsync").strip(),
                "completed_path": str(entry.get("completed_path") or "").strip(),
                "is_default": False,
            }
            if include_secrets:
                item["api_key"] = str(entry.get("api_key") or "")
                item["password"] = str(entry.get("password") or "")
            out.append(item)
    return out


def get_client_profile(client_id: Optional[str] = None, *, include_secrets: bool = False) -> Optional[Dict[str, Any]]:
    """Return a single configured usenet client profile by id (defaulting to 'default')."""
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
    """Create or update a named usenet client instance in ``usenet_client.clients``."""
    cid = str(entry.get("id") or "").strip()
    ctype = str(entry.get("type") or "").strip().lower()
    if not cid or cid == "default":
        raise ValueError("Client id is required and cannot be 'default'")
    if ctype not in USENET_CLIENT_TYPES:
        raise ValueError(f"Unsupported usenet client type: {ctype}")
    existing_list = config_manager.get("usenet_client.clients", []) or []
    clients = [dict(c) for c in existing_list if isinstance(c, dict)]
    prev = next((c for c in clients if str(c.get("id") or "").strip() == cid), None)
    api_key = entry.get("api_key")
    if api_key is None and prev is not None:
        api_key = prev.get("api_key", "")
    password = entry.get("password")
    if password is None and prev is not None:
        password = prev.get("password", "")
    record = {
        "id": cid,
        "name": str(entry.get("name") or cid).strip(),
        "type": ctype,
        "url": str(entry.get("url") or "").strip(),
        "api_key": str(api_key or ""),
        "username": str(entry.get("username") or "").strip(),
        "password": str(password or ""),
        "category": str(entry.get("category") or "soulsync").strip(),
        "completed_path": str(entry.get("completed_path") or "").strip(),
    }
    clients = [c for c in clients if str(c.get("id") or "").strip() != cid]
    clients.append(record)
    config_manager.set("usenet_client.clients", clients)
    return get_client_profile(cid) or record


def delete_client(client_id: str) -> bool:
    """Remove a named usenet client instance. Cannot delete 'default'."""
    cid = str(client_id or "").strip()
    if not cid or cid == "default":
        return False
    existing_list = config_manager.get("usenet_client.clients", []) or []
    clients = [dict(c) for c in existing_list if isinstance(c, dict)]
    kept = [c for c in clients if str(c.get("id") or "").strip() != cid]
    if len(kept) == len(clients):
        return False
    config_manager.set("usenet_client.clients", kept)
    return True


def get_adapter_for_client(client_id: Optional[str] = None) -> Optional[UsenetClientAdapter]:
    """Return an adapter for a specific configured client id, falling back to the global default."""
    cid = str(client_id or "").strip()
    if not cid or cid == "default":
        return get_active_adapter()
    extra = config_manager.get("usenet_client.clients", []) or []
    if isinstance(extra, list):
        for entry in extra:
            if isinstance(entry, dict) and str(entry.get("id") or "").strip() == cid:
                return _build_adapter_with_profile(entry)
    return get_active_adapter()


def get_adapter_for_library(
    library_key: Optional[str] = None,
    library_cfg: Optional[Dict[str, Any]] = None,
) -> Optional[UsenetClientAdapter]:
    """Resolve the usenet client adapter assigned to a library."""
    cid = None
    if isinstance(library_cfg, dict):
        cid = library_cfg.get("usenet_client_id")
    if not cid and library_key:
        routing = config_manager.get("download_routing.libraries", {}) or {}
        if isinstance(routing, dict):
            entry = routing.get(str(library_key))
            if isinstance(entry, dict) and entry.get("usenet_client_id"):
                cid = entry.get("usenet_client_id")
    if not cid and library_key:
        assignments = config_manager.get("usenet_client.library_assignments", {}) or {}
        if isinstance(assignments, dict):
            cid = assignments.get(str(library_key))
    return get_adapter_for_client(cid)


def adapter_for_type(client_type: str) -> Optional[UsenetClientAdapter]:
    """Build a fresh adapter instance for the given client type string."""
    if client_type == "sabnzbd":
        return SABnzbdAdapter()
    if client_type == "nzbget":
        return NZBGetAdapter()
    return None


def get_active_adapter() -> Optional[UsenetClientAdapter]:
    """Return an adapter for whichever usenet client the user has selected as global default."""
    client_type = (config_manager.get("usenet_client.type", "") or "").strip().lower()
    if not client_type:
        return None
    return adapter_for_type(client_type)
