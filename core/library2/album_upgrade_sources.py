"""Where a whole-album upgrade looks for the complete release.

Each fetcher yields candidates for one album from one source, best first. A
candidate either carries ``rejected`` (it was turned down from what the source
said about it, before anything was downloaded) or a ``fetch(staging_dir)``
that puts the release's audio into ``staging_dir`` and returns a
:class:`Fetched`. Everything that decides whether the release is good enough
happens afterwards, on the real files, in ``core.library2.album_upgrade``.

* Soulseek: one folder from one user. The folder listing is checked against
  the tracklist before a byte is downloaded, files the user blacklisted or
  that failed an import before rule a folder out, and the download is never
  topped up from another peer (``preferred_alternatives=[]``).
* Torrent, Usenet, Lidarr: the source's own whole-release grab
  (``download_album_to_staging``), the same one album downloads use.
* Qobuz, Tidal, Deezer: the album's own tracklist on the service (the id the
  library already holds when it has one), every track from that one album.
"""

from __future__ import annotations

import os
import shutil
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Iterator, List, Optional

from utils.async_helpers import run_async
from utils.logging_config import get_logger

logger = get_logger("library2.album_upgrade_sources")

BUNDLE_SOURCES = ("torrent", "usenet", "lidarr")
STREAMING_SOURCES = ("qobuz", "tidal", "deezer")
SUPPORTED_SOURCES = ("soulseek",) + BUNDLE_SOURCES + STREAMING_SOURCES
SOULSEEK_FOLDERS_TRIED = 3
STREAM_POLL_SECONDS = 2.0
STREAM_TIMEOUT_BASE = 600
STREAM_TIMEOUT_PER_TRACK = 180
_ALIASES = {"deezer_dl": "deezer"}


@dataclass
class FetchContext:
    database: Any
    unit: Any
    config_get: Callable[..., Any]
    emit: Callable[[str], None]
    check: Callable[..., Any]


@dataclass
class Fetched:
    error: str = ""


@dataclass
class Candidate:
    label: str
    rejected: str = ""
    fetch: Optional[Callable[[str], Fetched]] = None


def enabled_sources(config_get: Callable[..., Any]) -> List[str]:
    """The download sources in use, in the user's order, that can supply a
    whole album."""
    mode = str(config_get("download_source.mode", "soulseek") or "soulseek").lower()
    if mode == "hybrid":
        order = config_get("download_source.hybrid_order", None) or [
            config_get("download_source.hybrid_primary", "")]
    else:
        order = [mode]
    out: List[str] = []
    for raw in order or []:
        source = _ALIASES.get(str(raw or "").lower(), str(raw or "").lower())
        if source in SUPPORTED_SOURCES and source not in out:
            out.append(source)
    return out


def _orchestrator():
    from core.download_orchestrator import get_download_orchestrator
    return get_download_orchestrator()


def _client(source: str):
    try:
        client = _orchestrator().client(source)
    except Exception as exc:  # noqa: BLE001
        logger.debug("[Album upgrade] no %s client: %s", source, exc)
        return None
    if client is None:
        return None
    configured = getattr(client, "is_configured", None)
    try:
        if callable(configured) and not configured():
            return None
    except Exception:  # noqa: BLE001
        return None
    return client


def _profile_id(ctx: FetchContext) -> Optional[int]:
    profile = ctx.unit.quality_profile or {}
    try:
        return int(profile["id"]) if profile.get("id") is not None else None
    except (TypeError, ValueError):
        return None


def _reaches_cutoff_on_claims(qualities: Iterable[Any], profile_id: Optional[int]) -> bool:
    """Every file, on what the source says about it, reaches the cutoff. A fact
    a source never stated cannot rule a file out; the real files are checked
    after the download either way."""
    try:
        from core.quality.model import satisfies_a_target_on_stated_facts
        from core.quality.upgrades import upgrade_bar
        targets, cutoff_index, _label = upgrade_bar(profile_id)
    except Exception:  # noqa: BLE001
        return True
    if not targets:
        return True
    wanted = targets if cutoff_index is None else targets[:cutoff_index + 1]
    for quality in qualities:
        try:
            if not satisfies_a_target_on_stated_facts(quality, wanted):
                return False
        except Exception:  # noqa: BLE001
            continue
    return True


# ── Soulseek ────────────────────────────────────────────────────────────────

def _listing_items(tracks: List[Any], folder: str) -> List[Dict[str, Any]]:
    items = []
    for track in tracks:
        name = str(getattr(track, "filename", "") or "").replace("\\", "/")
        base = folder.replace("\\", "/").rstrip("/")
        rel = name[len(base) + 1:] if base and name.startswith(base + "/") else name.rsplit("/", 1)[-1]
        duration = getattr(track, "duration", None)
        items.append({
            "relative_path": rel,
            "title": getattr(track, "title", None),
            "track_number": getattr(track, "track_number", None),
            "disc_number": None,
            "duration_seconds": (float(duration) / 1000.0) if duration else None,
        })
    return items


def _blocked(database, tracks: List[Any]) -> bool:
    from core.downloads.failed_blocklist import is_candidate_blocked
    for track in tracks:
        try:
            if database.is_blacklisted(getattr(track, "username", ""), getattr(track, "filename", "")):
                return True
        except Exception as exc:  # noqa: BLE001 - an unreadable blacklist blocks nothing
            logger.debug("[Album upgrade] blacklist check failed: %s", exc)
        if is_candidate_blocked(database, track, "soulseek"):
            return True
    return False


def soulseek_candidates(ctx: FetchContext, source: str = "soulseek") -> Iterator[Candidate]:
    client = _client("soulseek")
    if client is None:
        yield Candidate("soulseek", rejected="Soulseek is not set up")
        return
    unit = ctx.unit
    query = f"{unit.artist} {unit.title}".strip()
    try:
        _tracks, albums = run_async(client.search(query, timeout=30))
    except Exception as exc:  # noqa: BLE001
        yield Candidate("soulseek", rejected=f"the search failed ({exc})")
        return
    if not albums:
        yield Candidate("soulseek", rejected="no album folders came back")
        return
    profile_id = _profile_id(ctx)
    expected = unit.expected_dicts()
    ranked = client._rank_album_bundle_folders(
        albums, unit.title, unit.artist, quality_profile_id=profile_id, expected_tracks=expected)
    if not ranked:
        yield Candidate("soulseek", rejected="no folder had enough of your tracks")
        return
    offered = 0
    for folder in ranked:
        if offered >= SOULSEEK_FOLDERS_TRIED:
            break
        username = getattr(folder, "username", "") or ""
        path = getattr(folder, "album_path", "") or ""
        label = f"{username}: {path.replace(chr(92), '/').rsplit('/', 1)[-1] or path}"
        tracks = client.filter_results_by_quality_preference(
            list(getattr(folder, "tracks", []) or []), profile_id=profile_id)
        ok, reason, _pairs = ctx.check(unit, _listing_items(tracks, path))
        if not ok:
            yield Candidate(label, rejected=reason)
            continue
        if not _reaches_cutoff_on_claims([t.audio_quality for t in tracks], profile_id):
            yield Candidate(label, rejected="its files don't reach your cutoff")
            continue
        if _blocked(ctx.database, tracks):
            yield Candidate(label, rejected="it has a file on your blocklist")
            continue
        offered += 1
        yield Candidate(label, fetch=_soulseek_fetch(client, ctx, username, path, tracks, profile_id))


def _soulseek_fetch(client, ctx, username, path, tracks, profile_id):
    def fetch(staging: str) -> Fetched:
        unit = ctx.unit
        outcome = client.download_album_to_staging(
            unit.title, unit.artist, staging, None,
            preferred_source={"username": username, "folder_path": path},
            preferred_tracks=tracks,
            preferred_alternatives=[],
            expected_tracks=unit.expected_dicts(),
            quality_profile_id=profile_id,
        ) or {}
        if not outcome.get("success"):
            return Fetched(outcome.get("error") or "the download failed")
        if outcome.get("partial"):
            return Fetched(f"only {outcome.get('completed_count')} of "
                           f"{outcome.get('expected_count')} files came down")
        return Fetched()
    return fetch


# ── Torrent / Usenet / Lidarr ───────────────────────────────────────────────

def bundle_candidates(ctx: FetchContext, source: str) -> Iterator[Candidate]:
    client = _client(source)
    if client is None or not hasattr(client, "download_album_to_staging"):
        yield Candidate(source, rejected=f"{source} is not set up")
        return
    unit = ctx.unit
    kwargs: Dict[str, Any] = {}
    if source == "lidarr":
        if unit.year:
            kwargs["release_year"] = unit.year
    else:
        profile_id = _profile_id(ctx)
        if profile_id is not None:
            kwargs["quality_profile_id"] = profile_id
        total = sum((t.duration_ms or 0) for t in unit.tracks) / 1000.0
        if total:
            kwargs["expected_duration_seconds"] = total

    def fetch(staging: str) -> Fetched:
        outcome = client.download_album_to_staging(
            unit.title, unit.artist, staging, None, **kwargs) or {}
        if not outcome.get("success"):
            return Fetched(outcome.get("error") or "the download failed")
        if outcome.get("partial"):
            return Fetched("the release came down incomplete")
        return Fetched()

    yield Candidate(f"{source} release", fetch=fetch)


# ── Qobuz / Tidal / Deezer ──────────────────────────────────────────────────

def _metadata_client(source: str, download_client):
    if source == "qobuz":
        return download_client
    if source == "tidal":
        from core.tidal_client import get_tidal_client
        return get_tidal_client()
    if source == "deezer":
        from core.metadata.registry import get_deezer_client
        return get_deezer_client()
    return None


def _num(value: Any) -> Optional[int]:
    try:
        number = int(str(value).split("/")[0])
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def streaming_tracklist(source: str, meta, album_id: str) -> List[Dict[str, Any]]:
    """``[{id, title, track_number, disc_number, duration_ms}]`` of one album."""
    out: List[Dict[str, Any]] = []
    if source == "qobuz":
        data = meta.get_album(album_id) or {}
        for item in ((data.get("tracks") or {}).get("items") or []):
            out.append({"id": item.get("id"), "title": item.get("title"),
                        "track_number": _num(item.get("track_number")),
                        "disc_number": _num(item.get("media_number")) or 1,
                        "duration_ms": int(float(item.get("duration") or 0) * 1000) or None})
    elif source == "tidal":
        for track in meta.get_album_tracks(album_id) or []:
            out.append({"id": getattr(track, "id", None), "title": getattr(track, "name", None),
                        "track_number": _num(getattr(track, "track_number", None)),
                        "disc_number": _num(getattr(track, "disc_number", None)) or 1,
                        "duration_ms": getattr(track, "duration_ms", None) or None})
    elif source == "deezer":
        for item in meta.get_album_tracks_raw(album_id) or []:
            out.append({"id": item.get("id"), "title": item.get("title"),
                        "track_number": _num(item.get("track_position")),
                        "disc_number": _num(item.get("disk_number")) or 1,
                        "duration_ms": int(float(item.get("duration") or 0) * 1000) or None})
    return [t for t in out if t.get("id") is not None]


def _find_album_id(source: str, meta, unit) -> tuple:
    """(album id, label, why not) on the service: the id the library holds,
    else a search whose title has to be the album's."""
    known = str((unit.provider_ids or {}).get(source) or "").strip()
    if known:
        return known, f"{source} album {known}", ""
    try:
        found = meta.search_album(unit.artist, unit.title)
    except Exception as exc:  # noqa: BLE001
        return None, source, f"the album search failed ({exc})"
    if not found:
        return None, source, "the album was not found"
    title = str(found.get("title") or "")
    from core.acquisition.bundle_matching import normalize_title, title_similarity
    if title_similarity(normalize_title(title), normalize_title(unit.title)) < 0.85:
        return None, f"{source}: {title}", f"the closest album there is {title!r}"
    return str(found.get("id")), f"{source}: {title}", ""


def streaming_candidates(ctx: FetchContext, source: str) -> Iterator[Candidate]:
    client = _client(source)
    if client is None:
        yield Candidate(source, rejected=f"{source} is not set up")
        return
    unit = ctx.unit
    try:
        meta = _metadata_client(source, client)
    except Exception as exc:  # noqa: BLE001
        meta = None
        logger.debug("[Album upgrade] no %s metadata client: %s", source, exc)
    if meta is None:
        yield Candidate(source, rejected=f"{source} album lookup is not available")
        return
    album_id, label, why = _find_album_id(source, meta, unit)
    if not album_id:
        yield Candidate(label, rejected=why)
        return
    try:
        tracklist = streaming_tracklist(source, meta, album_id)
    except Exception as exc:  # noqa: BLE001
        yield Candidate(label, rejected=f"its tracklist could not be read ({exc})")
        return
    items = [{
        "relative_path": f"{t['disc_number']}-{(t['track_number'] or 0):02d} {t['title']}",
        "title": t["title"], "track_number": t["track_number"], "disc_number": t["disc_number"],
        "duration_seconds": (t["duration_ms"] / 1000.0) if t.get("duration_ms") else None,
    } for t in tracklist]
    ok, reason, _pairs = ctx.check(unit, items)
    if not ok:
        yield Candidate(label, rejected=reason)
        return
    yield Candidate(label, fetch=_streaming_fetch(client, source, unit, tracklist))


def _safe_name(text: str) -> str:
    cleaned = "".join("_" if ch in '<>:"/\\|?*' or ord(ch) < 32 else ch for ch in str(text or ""))
    return cleaned.strip().strip(".")[:120] or "track"


def _streaming_fetch(client, source, unit, tracklist):
    def fetch(staging: str) -> Fetched:
        pending: Dict[str, Dict[str, Any]] = {}
        try:
            for track in tracklist:
                display = f"{unit.artist} - {track['title']}"
                download_id = run_async(client.download(source, f"{track['id']}||{display}", 0))
                if not download_id:
                    raise RuntimeError(f"{track['title']} could not be queued")
                pending[str(download_id)] = track
        except Exception as exc:  # noqa: BLE001
            _cancel(client, pending)
            return Fetched(f"the download could not start ({exc})")
        done: Dict[str, str] = {}
        deadline = time.monotonic() + STREAM_TIMEOUT_BASE + STREAM_TIMEOUT_PER_TRACK * len(tracklist)
        while len(done) < len(pending):
            if time.monotonic() > deadline:
                _cancel(client, {k: v for k, v in pending.items() if k not in done})
                return Fetched("the download took too long")
            for download_id, track in pending.items():
                if download_id in done:
                    continue
                try:
                    status = run_async(client.get_download_status(download_id))
                except Exception:  # noqa: BLE001 - ask again next round
                    continue
                state = str(getattr(status, "state", "") or "")
                if "Succeeded" in state and getattr(status, "file_path", None):
                    done[download_id] = status.file_path
                elif any(word in state for word in ("Errored", "Cancelled", "Failed", "Rejected")):
                    _cancel(client, {k: v for k, v in pending.items() if k not in done})
                    reason = getattr(status, "error", None) or state
                    return Fetched(f"{track['title']} failed to download ({reason})")
            if len(done) < len(pending):
                time.sleep(STREAM_POLL_SECONDS)
        multi_disc = len({t["disc_number"] for t in tracklist}) > 1
        for download_id, path in done.items():
            track = pending[download_id]
            ext = os.path.splitext(path)[1].lower()
            number = f"{track['track_number'] or 0:02d}"
            prefix = f"{track['disc_number']}-{number}" if multi_disc else number
            target = os.path.join(staging, f"{prefix} - {_safe_name(track['title'])}{ext}")
            try:
                shutil.copy2(path, target)
            except OSError as exc:
                return Fetched(f"could not stage {track['title']} ({exc})")
            try:
                os.remove(path)
            except OSError:
                pass
        return Fetched()
    return fetch


def _cancel(client, pending: Dict[str, Any]) -> None:
    for download_id in pending:
        try:
            run_async(client.cancel_download(download_id, remove=True))
        except Exception as exc:  # noqa: BLE001
            logger.debug("[Album upgrade] cancel of %s failed: %s", download_id, exc)


FETCHERS: Dict[str, Callable[[FetchContext, str], Iterable[Candidate]]] = {
    "soulseek": soulseek_candidates,
    **{source: bundle_candidates for source in BUNDLE_SOURCES},
    **{source: streaming_candidates for source in STREAMING_SOURCES},
}


__all__ = ["Candidate", "FETCHERS", "FetchContext", "Fetched", "enabled_sources",
           "streaming_tracklist", "SUPPORTED_SOURCES"]
