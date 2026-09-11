"""Soulseek as an audiobook source.

Prowlarr answers with a release: one torrent or NZB that a client fetches on
its own. Soulseek answers with a FOLDER on one person's machine, and fetching
it means asking for every file in it and following each transfer separately.
The two are different enough that pretending otherwise inside the Prowlarr
module would have made both harder to read, so the shape is translated here
and everything downstream keeps working in releases.

The translation:
  * An slskd AlbumResult becomes an AudiobookRelease with protocol
    ``soulseek``, so the existing ranking scores it against the wanted
    narrator, language and abridgement exactly like a torrent.
  * ``soulseek`` on the release carries what it takes to fetch it back: the
    peer's name, the folder, and every file with its size.
  * A grab starts one transfer per file and hands back all their ids. The
    download row stores them as a JSON list in ``client_id``, and the monitor
    aggregates them into one progress number.

Nothing here reaches into music's soulseek flow. It borrows the client (which
is where the connection, the throttle and the retry live) and does its own
bookkeeping, so a book can never land in a music batch or take a slot the
music pool was counting on.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional, Sequence

from utils.logging_config import get_logger

logger = get_logger("audiobook_soulseek")

# Peers share whole discographies. A search for one book routinely comes back
# with a folder of forty, and downloading that is not what was asked for.
MAX_FILES_PER_BOOK = 400

# Below this a "folder" is a stray sample or a broken share.
MIN_FILES_PER_BOOK = 1

_AUDIO_SUFFIXES = (".mp3", ".m4a", ".m4b", ".flac", ".ogg", ".opus", ".wav", ".aac", ".wma")


def _run(coro):
    return asyncio.run(coro)


def is_enabled() -> bool:
    """Whether the audiobook source chain wants Soulseek at all.

    Reads the audiobook chain, never music's. A user who runs Soulseek for
    music and torrents for books gets exactly that.
    """
    try:
        from core.settings import config_manager
        mode = str(config_manager.get("audiobooks.download_source.mode", "hybrid") or "hybrid")
        if mode == "soulseek":
            return True
        if mode in ("torrent", "usenet"):
            return False
        order = config_manager.get("audiobooks.download_source.hybrid_order",
                                   ["torrent", "usenet", "soulseek"])
        return "soulseek" in (order or [])
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read the audiobook source chain: %s", exc)
        return False


def _shared_client():
    """The process-wide Soulseek client.

    Deliberately NOT cached here, and deliberately reading no config here: the
    audiobook side must never consult music's soulseek settings to decide
    anything (tests/test_audiobooks_isolation.py holds that line). The cache and
    the config read both live in core/soulseek_client.py, which owns them.
    """
    from core.soulseek_client import get_shared_soulseek_client
    return get_shared_soulseek_client()


def is_available() -> bool:
    """Whether Soulseek can actually answer: wanted by the chain AND configured.

    The chain saying "ask soulseek" on an install with no slskd URL is not a
    source, it is a setting. The difference matters upstream: a source that was
    never really asked must not count towards "everything is down".
    """
    if not is_enabled():
        return False
    try:
        return bool(str(getattr(_shared_client(), "base_url", "") or ""))
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not check whether slskd is configured: %s", exc)
        return False


def audio_files(album: Any) -> List[Dict[str, Any]]:
    """Every audio file in a folder result, as ``{filename, size}``.

    Non-audio is dropped here rather than downloaded and deleted: peers share
    scans, playlists and stray archives alongside the audio, and none of it
    belongs in a book folder.
    """
    files: List[Dict[str, Any]] = []
    for track in getattr(album, "tracks", None) or []:
        name = str(getattr(track, "filename", "") or "")
        if not name or not name.lower().endswith(_AUDIO_SUFFIXES):
            continue
        files.append({"filename": name, "size": int(getattr(track, "size", 0) or 0)})
    return files


def folder_name(album: Any) -> str:
    """The last path segment of a shared folder, whatever slashes it uses."""
    path = str(getattr(album, "album_path", "") or "")
    parts = [part for part in re.split(r"[\\/]+", path) if part]
    return parts[-1] if parts else str(getattr(album, "album_title", "") or "")


def dominant_format(files: Sequence[Dict[str, Any]]) -> str:
    """The extension most of the files carry, for the format badge."""
    counts: Dict[str, int] = {}
    for entry in files:
        suffix = os.path.splitext(str(entry.get("filename") or ""))[1].lower().lstrip(".")
        if suffix:
            counts[suffix] = counts.get(suffix, 0) + 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda item: item[1])[0]


def album_to_release(album: Any, book: Dict[str, Any]) -> Optional[Any]:
    """Turn one slskd folder into a release the ranker can score.

    Returns None for a folder that is not plausibly this book: no audio, a
    single stray file, or a share so large it is somebody's whole library.
    """
    from core.audiobook_release_search import (
        AudiobookRelease,
        abridgement_verdict,
        detect_bitrate,
        is_abridged,
        language_verdict,
        narrator_verdict,
    )

    files = audio_files(album)
    if not MIN_FILES_PER_BOOK <= len(files) <= MAX_FILES_PER_BOOK:
        return None

    username = str(getattr(album, "username", "") or "")
    album_path = str(getattr(album, "album_path", "") or "")
    if not username or not album_path:
        return None

    # The folder name is what the release title is judged on. It is what a
    # peer actually named the book, so it carries the narrator and the
    # unabridged marker the same way a torrent name does.
    name = folder_name(album)
    size = sum(int(entry.get("size") or 0) for entry in files)

    wanted_narrators = book.get("narrator_names") or book.get("narrators") or []

    return AudiobookRelease(
        source="soulseek",
        protocol="soulseek",
        title=name,
        # Shown where an indexer name would be. The peer IS the source here,
        # and knowing who it came from is what lets a user recognise a good one.
        indexer=f"soulseek:{username}",
        size_bytes=size,
        guid=f"soulseek::{username}::{album_path}",
        download_url=None,
        magnet_uri=None,
        # Free upload slots stand in for seeders: both answer "can I actually
        # get this right now", which is what the ranking uses the number for.
        seeders=int(getattr(album, "free_upload_slots", 0) or 0),
        publish_date=None,
        audio_format=dominant_format(files),
        bitrate_kbps=detect_bitrate(name),
        abridged=is_abridged(name),
        narrator_verdict=narrator_verdict(name, wanted_narrators),
        abridgement_verdict=abridgement_verdict(name, book.get("format_type")),
        language_verdict=language_verdict(name, book.get("language")),
        soulseek={
            "username": username,
            "album_path": album_path,
            "files": files,
            "file_count": len(files),
            "queue_length": int(getattr(album, "queue_length", 0) or 0),
        },
    )


def search(
    book: Dict[str, Any],
    limit: int = 25,
    min_relevance: float = 0.5,
    client: Any = None,
    narrator_mode: str = "exact",
) -> List[Any]:
    """Folders on Soulseek that plausibly hold this book, best first.

    Fails open like the Prowlarr search: an unconfigured or unreachable slskd
    returns [], and the caller reports "no releases" rather than a 500.
    """
    from core.audiobook_release_search import (
        build_queries,
        deduplicate,
        rank_releases,
    )

    if client is None and not is_enabled():
        return []

    queries = build_queries(book)
    if not queries:
        return []

    if client is None:
        try:
            client = _shared_client()
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Could not build a Soulseek client: %s", exc)
            return []

    if not str(getattr(client, "base_url", "") or ""):
        logger.debug("slskd is not configured; no Soulseek audiobook search")
        return []

    collected: List[Any] = []
    for query in queries:
        try:
            _tracks, albums = _run(client.search(query))
        except Exception as exc:                            # noqa: BLE001
            logger.warning("Soulseek audiobook search failed for %r: %s", query, exc)
            continue

        for album in albums or []:
            release = album_to_release(album, book)
            if release is not None:
                collected.append(release)

        ranked = rank_releases(deduplicate(collected), book, min_relevance, narrator_mode)
        # Same reason as the Prowlarr search: every query is a real search
        # against the whole network, so stop once one has answered well.
        if len(ranked) >= 5:
            return ranked[:limit]

    return rank_releases(deduplicate(collected), book, min_relevance, narrator_mode)[:limit]


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------

def grab(release: Any, save_path: Optional[str] = None, client: Any = None) -> Dict[str, Any]:
    """Start every file in a folder. Returns ``{ok, refs, username, error}``.

    A partial start is still a start: peers drop files, and the completeness
    gate at the end is what decides whether the book is whole, not whether
    every request was accepted up front. Zero accepted is a failure.
    """
    payload = release.get("soulseek") if isinstance(release, dict) \
        else getattr(release, "soulseek", None)
    if not payload:
        return {"ok": False, "error": "That release carries no Soulseek folder.",
                "refs": [], "username": "", "folder": ""}

    username = str(payload.get("username") or "")
    files = payload.get("files") or []
    if not username or not files:
        return {"ok": False, "error": "That Soulseek folder is empty.",
                "refs": [], "username": username, "folder": ""}

    if client is None:
        try:
            client = _shared_client()
        except Exception as exc:                            # noqa: BLE001
            return {"ok": False, "error": f"Soulseek is not reachable: {exc}",
                    "refs": [], "username": username}

    refs: List[str] = []
    for entry in files:
        filename = str(entry.get("filename") or "")
        if not filename:
            continue
        try:
            ref = _run(client.download(username, filename, int(entry.get("size") or 0)))
        except Exception as exc:                            # noqa: BLE001
            logger.debug("Soulseek refused %s: %s", filename, exc)
            continue
        if ref:
            refs.append(str(ref))

    if not refs:
        return {"ok": False, "error": f"{username} accepted none of the files.",
                "refs": [], "username": username, "folder": ""}

    logger.info("Started %d/%d files from %s", len(refs), len(files), username)
    return {"ok": True, "refs": refs, "username": username,
            "folder": _folder_of(str(payload.get("album_path") or "")), "error": ""}


def _folder_of(album_path: str) -> str:
    parts = [part for part in re.split(r"[\\/]+", album_path) if part]
    return parts[-1] if parts else ""


def encode_refs(refs: Sequence[str], username: str, folder: str = "") -> str:
    """Pack the transfer ids into the single client_id column.

    JSON rather than a new column: the download row is shared with the torrent
    and usenet paths, and adding a column for one source's bookkeeping would
    put a soulseek-shaped hole in both of the others.

    The folder name rides along because slskd reports no save path for a
    folder, and the organizer needs somewhere to copy FROM.
    """
    return json.dumps({"username": username, "refs": list(refs), "folder": folder})


def decode_refs(client_id: Any) -> Dict[str, Any]:
    """Unpack what encode_refs stored. Tolerates a plain id from before it."""
    text = str(client_id or "").strip()
    if not text:
        return {"username": "", "refs": [], "folder": ""}
    try:
        loaded = json.loads(text)
        if isinstance(loaded, dict):
            return {"username": str(loaded.get("username") or ""),
                    "refs": [str(ref) for ref in (loaded.get("refs") or [])],
                    "folder": str(loaded.get("folder") or "")}
    except (ValueError, TypeError):
        pass
    return {"username": "", "refs": [text], "folder": ""}


def landing_path(folder: str, client: Any = None) -> str:
    """Where slskd will have put a folder, as this process can see it.

    slskd downloads into its own configured root and recreates the peer's
    folder under it. The music side already maps that root across a container
    boundary, so its resolver is reused rather than re-derived.
    """
    if not folder:
        return ""
    try:
        if client is None:
            client = _shared_client()
        root = str(getattr(client, "download_path", "") or "")
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Could not read the Soulseek download path: %s", exc)
        return ""
    if not root:
        return ""
    return os.path.join(root, folder)


# ---------------------------------------------------------------------------
# Following the transfers
# ---------------------------------------------------------------------------

# slskd's own words for a transfer, folded to the three the monitor knows.
_DONE = ("completed, succeeded", "succeeded", "completed")
_FAILED = ("cancelled", "canceled", "errored", "failed", "rejected", "timedout")


def _state_of(status: Any) -> str:
    raw = str(getattr(status, "state", "") or "").strip().lower()
    if any(token in raw for token in _FAILED):
        return "failed"
    if any(token in raw for token in _DONE):
        return "done"
    return "running"


def aggregate(statuses: Sequence[Any], expected: int = 0) -> Dict[str, Any]:
    """Roll per-file transfers into one download's worth of progress.

    A folder is only finished when every file it started is finished. One
    failed file out of thirty is NOT a failed book: the completeness gate
    measures what actually landed against the published runtime, and it is a
    better judge of "is this whole" than a transfer state is. So a partial
    finish reports done and lets the gate decide.
    """
    statuses = list(statuses or [])
    if not statuses:
        return {"state": "queued", "progress": 0.0, "transferred": 0, "size": 0,
                "finished": 0, "failed": 0, "total": expected, "save_path": ""}

    total_size = sum(int(getattr(s, "size", 0) or 0) for s in statuses)
    transferred = sum(int(getattr(s, "transferred", 0) or 0) for s in statuses)

    finished = 0
    failed = 0
    for status in statuses:
        state = _state_of(status)
        if state == "done":
            finished += 1
        elif state == "failed":
            failed += 1

    settled = finished + failed
    expected = expected or len(statuses)

    if settled >= expected and finished == 0:
        state = "failed"
    elif settled >= expected:
        state = "done"
    else:
        state = "downloading"

    progress = (transferred / total_size * 100.0) if total_size else (
        finished / expected * 100.0 if expected else 0.0
    )

    return {
        "state": state,
        "progress": round(min(progress, 100.0), 2),
        "transferred": transferred,
        "size": total_size,
        "finished": finished,
        "failed": failed,
        "total": expected,
        "save_path": "",
    }


def status_for(client_id: Any, client: Any = None) -> Optional[Dict[str, Any]]:
    """Where a Soulseek book has got to, or None when slskd cannot be asked."""
    unpacked = decode_refs(client_id)
    wanted = set(unpacked["refs"])
    if not wanted:
        return None

    if client is None:
        try:
            client = _shared_client()
        except Exception as exc:                            # noqa: BLE001
            logger.debug("Could not build a Soulseek client to poll: %s", exc)
            return None

    try:
        # One call for everything in flight rather than one per file: a book
        # is dozens of transfers, and slskd should be asked once.
        everything = _run(client.get_all_downloads())
    except Exception as exc:                                # noqa: BLE001
        logger.debug("Soulseek status poll failed: %s", exc)
        return None

    mine = [status for status in (everything or [])
            if str(getattr(status, "id", "")) in wanted]
    rolled = aggregate(mine, expected=len(wanted))
    rolled["save_path"] = landing_path(unpacked["folder"], client)
    return rolled


def cancel(client_id: Any, client: Any = None) -> bool:
    """Stop every transfer behind one book. True when at least one stopped."""
    unpacked = decode_refs(client_id)
    if not unpacked["refs"]:
        return False

    if client is None:
        try:
            client = _shared_client()
        except Exception as exc:                            # noqa: BLE001
            logger.debug("Could not build a Soulseek client to cancel: %s", exc)
            return False

    stopped = 0
    for ref in unpacked["refs"]:
        try:
            if _run(client.cancel_download(ref, username=unpacked["username"] or None,
                                           remove=True)):
                stopped += 1
        except Exception as exc:                            # noqa: BLE001
            logger.debug("Could not cancel %s: %s", ref, exc)
    return stopped > 0
