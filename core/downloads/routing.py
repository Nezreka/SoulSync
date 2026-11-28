"""Per-library download routing across Music, Audiobooks, Movies, TV Shows,
and custom video libraries (#2336).

Each library can independently override:
- torrent_client_id / usenet_client_id (which configured client instance to use)
- torrent_category / usenet_category
- torrent_save_path (path sent to the download client)
- completed_path (completed download path as seen by SoulSync)
- prowlarr_indexer_ids (subset of Prowlarr indexers to search)
- download_source_priority (ordered list of download sources to try)

Any field left blank inherits from the chosen client instance profile and then
from the existing global settings, so nothing breaks for single-client setups.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from core.settings import config_manager

BUILTIN_LIBRARIES = (
    {"key": "movies", "name": "Movies", "media_type": "video"},
    {"key": "tv", "name": "TV Shows", "media_type": "video"},
    {"key": "music", "name": "Music", "media_type": "music"},
    {"key": "audiobooks", "name": "Audiobooks", "media_type": "audiobooks"},
)

VALID_SOURCES = ("soulseek", "torrent", "usenet", "extto", "youtube", "tidal", "deezer", "qobuz", "amazon")


@dataclass
class LibraryDownloadRouting:
    library_key: str
    torrent_client_id: str = "default"
    usenet_client_id: str = "default"
    torrent_category: str = "soulsync"
    usenet_category: str = "soulsync"
    torrent_save_path: str = ""
    completed_path: str = ""
    prowlarr_indexer_ids: Optional[List[int]] = None
    download_source_priority: Optional[List[str]] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _norm_indexer_ids(raw: Any) -> Optional[List[int]]:
    if raw is None or raw == "" or raw == []:
        return None
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        return None
    out: List[int] = []
    for p in parts:
        try:
            n = int(p)
            if n > 0 and n not in out:
                out.append(n)
        except (TypeError, ValueError):
            continue
    return out or None


def _norm_priority(raw: Any) -> Optional[List[str]]:
    if raw is None or raw == "" or raw == []:
        return None
    if isinstance(raw, str):
        parts = [p.strip().lower() for p in raw.split(",") if p.strip()]
    elif isinstance(raw, (list, tuple)):
        parts = [str(p or "").strip().lower() for p in raw]
    else:
        return None
    out: List[str] = []
    for s in parts:
        if s in VALID_SOURCES and s not in out:
            out.append(s)
    return out or None


def _stored_map() -> Dict[str, Dict[str, Any]]:
    raw = config_manager.get("download_routing.libraries", {}) or {}
    return {str(k): dict(v) for k, v in raw.items() if isinstance(v, dict)} if isinstance(raw, dict) else {}


def get_library_overrides(library_key: str) -> Dict[str, Any]:
    """Return raw saved overrides for one library key (empty dict = all defaults)."""
    return _stored_map().get(str(library_key).strip(), {})


def set_library_overrides(library_key: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """Persist per-library download routing overrides."""
    key = str(library_key or "").strip()
    if not key:
        raise ValueError("library_key is required")
    data = _stored_map()
    cur = dict(data.get(key, {}))
    for field in ("torrent_client_id", "usenet_client_id", "torrent_category", "usenet_category",
                  "torrent_save_path", "completed_path"):
        if field in patch:
            val = str(patch.get(field) or "").strip()
            if val:
                cur[field] = val
            else:
                cur.pop(field, None)
    if "prowlarr_indexer_ids" in patch:
        ids = _norm_indexer_ids(patch.get("prowlarr_indexer_ids"))
        if ids is not None:
            cur["prowlarr_indexer_ids"] = ids
        else:
            cur.pop("prowlarr_indexer_ids", None)
    if "download_source_priority" in patch:
        prio = _norm_priority(patch.get("download_source_priority"))
        if prio is not None:
            cur["download_source_priority"] = prio
        else:
            cur.pop("download_source_priority", None)
    if cur:
        data[key] = cur
    else:
        data.pop(key, None)
    config_manager.set("download_routing.libraries", data)
    return cur


def delete_library_overrides(library_key: str) -> bool:
    key = str(library_key or "").strip()
    data = _stored_map()
    if key not in data:
        return False
    data.pop(key, None)
    config_manager.set("download_routing.libraries", data)
    return True


def resolve_library_routing(
    library_key: Optional[str] = None,
    library_cfg: Optional[Dict[str, Any]] = None,
) -> LibraryDownloadRouting:
    """Resolve effective download routing for ``library_key`` with fallback chain:
    library_cfg -> stored per-library override -> chosen client profile -> global defaults."""
    from core.torrent_clients import get_client_profile as get_torrent_profile
    from core.usenet_clients import get_client_profile as get_usenet_profile

    key = str(library_key or "").strip()
    stored = get_library_overrides(key) if key else {}
    cfg = library_cfg if isinstance(library_cfg, dict) else {}

    def _pick(field: str, *alts: str) -> Any:
        for name in (field, *alts):
            val = cfg.get(name)
            if val is not None and val != "" and val != []:
                return val
        val = stored.get(field)
        if val is not None and val != "" and val != []:
            return val
        return None

    t_cid = str(_pick("torrent_client_id") or "default").strip() or "default"
    u_cid = str(_pick("usenet_client_id") or "default").strip() or "default"

    t_prof = get_torrent_profile(t_cid) or {}
    u_prof = get_usenet_profile(u_cid) or {}

    t_cat = str(
        _pick("torrent_category", "download_category")
        or t_prof.get("category")
        or config_manager.get("torrent_client.category", "soulsync")
        or "soulsync"
    ).strip()
    u_cat = str(
        _pick("usenet_category", "download_category")
        or u_prof.get("category")
        or config_manager.get("usenet_client.category", "soulsync")
        or "soulsync"
    ).strip()
    t_save = str(
        _pick("torrent_save_path", "download_save_path")
        or t_prof.get("save_path")
        or config_manager.get("torrent_client.save_path", "")
        or ""
    ).strip()
    completed = str(
        _pick("completed_path")
        or t_prof.get("completed_path")
        or u_prof.get("completed_path")
        or config_manager.get("torrent_client.completed_path", "")
        or config_manager.get("usenet_client.completed_path", "")
        or ""
    ).strip()

    indexers = _norm_indexer_ids(_pick("prowlarr_indexer_ids", "indexer_ids"))
    priority = _norm_priority(_pick("download_source_priority", "hybrid_order"))

    return LibraryDownloadRouting(
        library_key=key or "default",
        torrent_client_id=t_cid,
        usenet_client_id=u_cid,
        torrent_category=t_cat,
        usenet_category=u_cat,
        torrent_save_path=t_save,
        completed_path=completed,
        prowlarr_indexer_ids=indexers,
        download_source_priority=priority,
    )


def resolve_completed_path_for_client(
    reported_path: str,
    *,
    source: str = "torrent",
    client_id: Optional[str] = None,
    library_key: Optional[str] = None,
    library_cfg: Optional[Dict[str, Any]] = None,
) -> str:
    """Map a client-reported save path to the SoulSync-visible completed path
    using per-library or per-client ``completed_path`` when configured."""
    raw = str(reported_path or "").strip()
    if not raw:
        return raw
    if os.path.exists(raw):
        return raw

    routing = resolve_library_routing(library_key, library_cfg)
    cid = client_id or (routing.torrent_client_id if source == "torrent" else routing.usenet_client_id)

    from core.torrent_clients import get_client_profile as get_torrent_profile
    from core.usenet_clients import get_client_profile as get_usenet_profile

    prof = (get_torrent_profile(cid) if source == "torrent" else get_usenet_profile(cid)) or {}
    local_root = routing.completed_path or str(prof.get("completed_path") or "").strip()
    remote_root = routing.torrent_save_path or str(prof.get("save_path") or "").strip()

    if local_root:
        norm_raw = raw.replace("\\", "/")
        norm_remote = remote_root.replace("\\", "/").rstrip("/") if remote_root else ""
        if norm_remote and norm_raw.lower().startswith(norm_remote.lower()):
            rel = norm_raw[len(norm_remote):].lstrip("/")
            candidate = os.path.join(local_root, *rel.split("/")) if rel else local_root
            if os.path.exists(candidate):
                return candidate
        leaf = os.path.basename(norm_raw.rstrip("/"))
        if leaf:
            candidate = os.path.join(local_root, leaf)
            if os.path.exists(candidate):
                return candidate
        if os.path.isdir(local_root):
            return local_root

    try:
        from core.download_plugins.album_bundle import resolve_reported_save_path
        return resolve_reported_save_path(raw)
    except Exception:  # noqa: BLE001
        return raw


def list_all_routings(extra_libraries: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Return routing summaries (overrides + effective resolved values) for all libraries."""
    items: List[Dict[str, Any]] = []
    seen = set()
    for lib in BUILTIN_LIBRARIES:
        key = lib["key"]
        seen.add(key)
        items.append({
            **lib,
            "overrides": get_library_overrides(key),
            "effective": resolve_library_routing(key).to_dict(),
        })
    for lib in extra_libraries or []:
        key = str(lib.get("key") or f"video_library:{lib.get('id')}")
        if key in seen:
            continue
        seen.add(key)
        items.append({
            "key": key,
            "name": str(lib.get("name") or key),
            "media_type": str(lib.get("media_type") or "video"),
            "overrides": get_library_overrides(key),
            "effective": resolve_library_routing(key, lib).to_dict(),
        })
    return items
