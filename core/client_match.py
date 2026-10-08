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


def audiobook_known(rows: Iterable[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Client refs of audiobook downloads that are still soulsync's."""
    known: Dict[str, Dict[str, Dict[str, str]]] = {"torrent": {}, "usenet": {}}
    for row in rows or []:
        if str(row.get("status") or "").lower() in _DEAD_STATES:
            continue
        source = str(row.get("source") or "").lower()
        ref = str(row.get("client_id") or "").strip()
        if source not in known or not ref:
            continue
        key = ref.lower() if source == "torrent" else ref
        known[source][key] = _label("audiobook", row.get("title") or row.get("release_title"))
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
                  torrent_plugin, usenet_plugin) -> Dict[str, Dict[Any, Dict[str, str]]]:
    """Every label, from getters. Best effort per source: one that fails never
    hides another's labels."""
    known: Dict[str, Dict[Any, Dict[str, str]]] = {"torrent": {}, "usenet": {}, "slskd": {}}
    parts = (
        ("video", lambda: video_known(video_rows())),
        ("music", lambda: music_task_known(music_tasks())),
        ("audiobook", lambda: audiobook_known(audiobook_rows())),
        ("plugin", lambda: plugin_known(torrent_plugin(), usenet_plugin())),
    )
    for name, build in parts:
        try:
            merge_known(known, build())
        except Exception as exc:  # noqa: BLE001 - labels are best effort
            logger.debug("[Clients] %s known-items unavailable: %s", name, exc)
    return known
