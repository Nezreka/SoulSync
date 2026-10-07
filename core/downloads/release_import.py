"""Find the requested track in a downloaded release, and import the album's
other tracks when the quality profile asks for them.

Release/indexer hints never replace the ordinary per-file import checks. Every
other album track becomes an ordinary import of its own catalogue track: the
normal pipeline accepts, quarantines or rejects it alone, exactly like a
wishlist download of that track.
"""

from __future__ import annotations

import os
import re
import threading
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import partial
from pathlib import Path
from typing import Any

from core.imports.filename import parse_filename_metadata
from core.quality.release_format import format_from_extension
from core.tag_writer import read_file_tags
from core.text.title_match import recording_version_markers
from mutagen import File as MutagenFile
from utils.logging_config import get_logger

logger = get_logger("downloads.release_import")


@dataclass(frozen=True)
class ReleaseFile:
    path: str
    title: str = ""
    artist: str = ""
    album: str = ""
    track_number: int = 0
    disc_number: int = 0
    duration_ms: float = 0
    tagged: bool = False


def _text(value: Any) -> str:
    return "".join(c.casefold() for c in str(value or "") if c.isalnum())


def _number(value: Any, default: int = 0) -> int:
    try:
        return int(str(value).split("/")[0])
    except (TypeError, ValueError):
        return default


def read_release_file(path: str) -> ReleaseFile:
    """Read existing tags before the importer replaces them with matched data."""
    tags = read_file_tags(path)
    duration = 0
    try:
        audio = MutagenFile(path)
        duration = float(getattr(getattr(audio, "info", None), "length", 0) or 0) * 1000
    except Exception as exc:
        logger.debug("Release duration is unavailable for %s: %s", path, exc)
    parsed = parse_filename_metadata(path)
    return ReleaseFile(
        str(path),
        str(tags.get("title") or parsed.get("title") or Path(path).stem).replace("_", " "),
        str(tags.get("artist") or tags.get("album_artist") or parsed.get("artist") or ""),
        str(tags.get("album") or ""),
        _number(tags.get("track_number")),
        _number(tags.get("disc_number")),
        duration,
        not tags.get("error") and bool(tags.get("title")),
    )


def _artist(track: dict) -> str:
    artists = track.get("artists") or []
    if isinstance(artists, str):
        return artists
    if isinstance(artists, dict):
        return str(artists.get("name") or "")
    if artists:
        first = artists[0]
        return str(first.get("name") or "") if isinstance(first, dict) else str(first)
    return str(track.get("artist") or track.get("artist_name") or "")


def _title(value: str) -> str:
    edition = r"(?:\d{4}[ -]+)?(?:remaster(?:ed)?|mono|stereo)(?:[ -]+\d{4})?"
    suffix = rf"\s*(?:[\[(]{edition}[\])]|[-–]\s*{edition})\s*$"
    return _text(re.sub(suffix, "", value.replace("_", " "), flags=re.IGNORECASE))


def release_match_score(item: ReleaseFile, track: dict) -> float:
    expected = str(track.get("name") or track.get("title") or "")
    if recording_version_markers(expected) != recording_version_markers(item.title):
        return 0.0
    wanted, actual = _title(expected), _title(item.title)
    if not wanted or not actual:
        return 0.0
    title_score = SequenceMatcher(None, wanted, actual).ratio()
    artist = _artist(track)
    if artist and item.artist:
        from core.matching.artist_aliases import artist_names_match

        matched, _ = artist_names_match(
            artist,
            item.artist,
            threshold=0.80,
            aliases=track.get("artist_aliases"),
            similarity=lambda a, b: SequenceMatcher(None, _text(a), _text(b)).ratio(),
        )
        if not matched:
            return 0.0
    duration = float(track.get("duration_ms") or 0)
    if duration and item.duration_ms and abs(duration - item.duration_ms) > max(4000, duration * 0.05):
        return 0.0
    return title_score


def profile_formats(profile: dict | None) -> list[str]:
    """The profile's ranked target formats, best first."""
    targets = (profile or {}).get("ranked_targets") or []
    return [str(target.get("format") or "").lower() for target in targets if isinstance(target, dict)]


def _format_rank(item: ReleaseFile, preferred_formats) -> int:
    fmt = format_from_extension(Path(item.path).suffix.lower())
    return preferred_formats.index(fmt) if fmt in preferred_formats else len(preferred_formats)


def select_requested_file(files: list[ReleaseFile], track: dict, preferred_formats=()) -> ReleaseFile | None:
    """Refuse close alternatives rather than apply one song's tags to another.

    The same song twice is settled by the track's own disc/number (one title on
    two discs), then by the profile's format order (a FLAC+MP3 release).
    """
    unique = {str(Path(item.path).resolve()): item for item in files}
    scored = sorted(((release_match_score(item, track), item) for item in unique.values()), key=lambda entry: entry[0], reverse=True)
    if not scored or scored[0][0] < 0.80:
        return None
    tied = [item for score, item in scored if scored[0][0] - score < 0.03]
    number, disc = _number(track.get("track_number")), _number(track.get("disc_number"))
    if len(tied) > 1 and number:
        tied = [item for item in tied if item.track_number == number and (not disc or (item.disc_number or 1) == disc)] or tied
    if len(tied) > 1 and preferred_formats:
        ranks = [_format_rank(item, list(preferred_formats)) for item in tied]
        tied = [item for item, rank in zip(tied, ranks, strict=True) if rank == min(ranks)]
    return tied[0] if len(tied) == 1 else None


def match_album_tracks(files: list[ReleaseFile], tracks: list[dict], album_name: str, preferred_formats=()):
    """Pair catalogue tracks with the release files that are unambiguously them.

    Only files tagged with this album count, at the track's own disc/number and
    with a strict title/artist/duration score. Tracks the release lacks or
    cannot identify stay unpaired; they never block the others.
    """
    if not _text(album_name):
        return []
    files = [item for item in {str(Path(item.path).resolve()): item for item in files}.values()
             if item.tagged and _text(item.album) == _text(album_name)]
    pairs, used = [], set()
    for index, track in enumerate(tracks, 1):
        if _text(_artist(track)) in {"", "unknown", "unknownartist", "variousartists", "none", "null"}:
            continue
        disc = _number(track.get("disc_number"), 1)
        number = _number(track.get("track_number"), index)
        candidates = [item for item in files if item.path not in used and item.track_number == number and (item.disc_number or 1) == disc]
        item = select_requested_file(candidates, track, preferred_formats)
        if item is None or release_match_score(item, track) < 0.95:
            continue
        used.add(item.path)
        pairs.append((track, item))
    return pairs


def _album_track_context(parent: dict, track: dict, album: dict, source: str) -> dict:
    """An ordinary download context for one catalogue track of the album.

    Never inherits the requested song's IDs, outcome flags or task. The context
    is what a quarantine entry stores, so an approval imports it like any track.
    """
    from core.imports.context import get_import_context_artist
    from core.imports.paths import import_profile_id

    artists = track.get("artists") or [get_import_context_artist(parent)]
    artist = deepcopy(artists[0] if isinstance(artists, list) else artists)
    if not isinstance(artist, dict):
        artist = {"name": str(artist)}
    info = deepcopy(track)
    info["quality_profile_id"] = (parent.get("track_info") or {}).get("quality_profile_id")
    info["album"] = album.get("name") or ""
    ctx = {
        "artist": artist,
        "album": deepcopy(album),
        "track_info": info,
        "source": source,
        "profile_id": import_profile_id(parent),
        "is_album_download": True,
        "has_clean_metadata": True,
        "has_full_metadata": True,
        "original_search_result": {
            "id": info.get("id") or "",
            "source": source,
            "clean_title": info.get("name") or info.get("title") or "",
            "clean_artist": artist.get("name") or "",
            "clean_album": info["album"],
            "album": info["album"],
            "track_number": info.get("track_number"),
            "disc_number": info.get("disc_number"),
            "duration_ms": info.get("duration_ms"),
        },
    }
    # Source labels are keyed by download username, independently of metadata
    # provider. Carry safe origin breadcrumbs, never the requested song's IDs.
    original = parent.get("original_search_result") or parent.get("search_result") or {}
    username = parent.get("_download_username") or original.get("username")
    if username:
        ctx["_download_username"] = username
        ctx["original_search_result"]["username"] = username
    for key in ("filename", "indexer_id", "indexer_name"):
        if original.get(key) is not None:
            ctx["original_search_result"][key] = deepcopy(original[key])
    from core.downloads.origin import derive_download_origin

    origin, origin_context = derive_download_origin(parent)
    if origin:
        info["_dl_origin"] = origin
        info["_dl_origin_context"] = origin_context
    return ctx


def _owned_in_library(track: dict, album_name: str, profile_id=None) -> bool:
    """Ownership in the requester's library, as the missing-track analysis decides it."""
    from core.library_scope import library_scope_for_profile, reset_library_scope, set_library_scope
    from core.settings import config_manager
    from database.music_database import MusicDatabase

    title = str(track.get("name") or track.get("title") or "")
    artists = track.get("artists") or []
    names = [a.get("name") if isinstance(a, dict) else a for a in (artists if isinstance(artists, list) else [artists])]
    token = set_library_scope(library_scope_for_profile(profile_id))
    try:
        db = MusicDatabase()
        server = config_manager.get_active_media_server()
        for name in filter(None, names):
            found, confidence = db.check_track_exists(
                title, str(name), confidence_threshold=0.7, server_source=server, album=album_name)
            if found and confidence >= 0.7:
                return True
    except Exception as exc:
        # Unknown ownership must not create a second copy of an owned song.
        logger.warning("[Album Tracks] Ownership check failed for %r, skipping it: %s", title, exc)
        return True
    finally:
        reset_library_scope(token)
    return False


# One expansion per album at a time. A second download of the same album skips
# its expansion instead of holding a post-processing worker while it waits.
_active_albums: set[str] = set()
# Tracks this process already imported. A media server's library table only
# learns about them on its next scan.
_recent_imports: OrderedDict[str, None] = OrderedDict()
_state_lock = threading.Lock()


def _recently_imported(key: str, *, remember: bool = False) -> bool:
    with _state_lock:
        if remember:
            _recent_imports[key] = None
            while len(_recent_imports) > 4096:
                _recent_imports.popitem(last=False)
        return key in _recent_imports


def import_album_tracks(context_key: str, context: dict, files: list[ReleaseFile], requested: ReleaseFile,
                        transfer_dir: str, process_file, copy_file, *, lookup_album=None, is_owned=None) -> int:
    """Import the album's other tracks from a release that delivered the request.

    Opt-in per quality profile. Each paired, not yet owned track is copied out
    of the client's download (originals stay for seeding) and runs the complete
    normal import pipeline with the same profile. Returns how many imported.
    """
    from core.imports.context import get_import_context_album, get_import_source
    from core.imports.pipeline import _resolve_context_quality_profile
    from core.runtime_state import matched_context_lock, matched_downloads_context

    profile = _resolve_context_quality_profile(context)
    if profile.get("release_import_mode") != "album_tracks" or process_file is None:
        return 0
    album = deepcopy(get_import_context_album(context))
    name = album.get("name") or (context.get("track_info") or {}).get("album")
    if isinstance(name, dict):
        album = deepcopy(name)
        name = album.get("name")
    if not name or not album.get("id"):
        logger.info("[Album Tracks] The request carries no album id; importing the requested track only")
        return 0
    artist_name = _artist(context.get("track_info") or {})
    identity = f"{os.path.realpath(transfer_dir)}::{_text(artist_name)}::{_text(name)}"
    with _state_lock:
        if identity in _active_albums:
            logger.info("[Album Tracks] %r is already being imported by another download", name)
            return 0
        _active_albums.add(identity)
    imported = 0
    try:
        if lookup_album is None:
            from core.metadata.album_tracks import get_artist_album_tracks

            lookup_album = get_artist_album_tracks
        payload = lookup_album(album["id"], artist_name=artist_name, album_name=name,
                               source_override=get_import_source(context) or None)
        if not isinstance(payload, dict) or not payload.get("success"):
            logger.info("[Album Tracks] Track list for %r is unavailable", name)
            return 0
        catalogue_album = payload.get("album") or {}
        if _text(catalogue_album.get("name")) != _text(name):
            logger.info("[Album Tracks] Catalogue edition %r is not %r", catalogue_album.get("name"), name)
            return 0
        tracks = payload.get("tracks") or []
        requested_path = Path(requested.path).resolve()

        def is_request(track, item):
            # The request is the initiating task's import, in whatever format
            # it chose; never a second copy of it (a FLAC+MP3 release).
            return Path(item.path).resolve() == requested_path or (
                requested.track_number == _number(track.get("track_number"), -1)
                and (requested.disc_number or 1) == _number(track.get("disc_number"), 1)
                and _text(requested.album) == _text(name))

        pairs = [(track, item) for track, item in match_album_tracks(files, tracks, name, profile_formats(profile))
                 if not is_request(track, item)]
        album.update(catalogue_album)
        album.setdefault("total_tracks", len(tracks))
        source = payload.get("source") or get_import_source(context)
        if is_owned is None:
            from core.imports.paths import import_profile_id

            is_owned = partial(_owned_in_library, profile_id=import_profile_id(context))
        for index, (track, item) in enumerate(pairs):
            key = f"{source}:{track.get('id') or ''}:{_text(name)}:{track.get('disc_number')}:{track.get('track_number')}"
            if _recently_imported(key) or is_owned(track, name):
                logger.info("[Album Tracks] %r is already in the library", track.get("name"))
                continue
            path = copy_file(item.path, transfer_dir)
            if not path:
                continue
            ctx = _album_track_context(context, track, album, source)
            track_key = f"{context_key}::album-track:{index}"
            try:
                process_file(track_key, ctx, path)
            except Exception as exc:
                logger.warning("[Album Tracks] Import of %r failed: %s", track.get("name"), exc)
            finally:
                # The pipeline keeps a failed context and its file for a retry
                # that never comes for an extra track; drop both.
                with matched_context_lock:
                    matched_downloads_context.pop(track_key, None)
                if os.path.exists(path):
                    try:
                        os.unlink(path)
                    except OSError as exc:
                        logger.warning("[Album Tracks] Could not remove unused copy %s: %s", path, exc)
            if ctx.get("_pipeline_import_succeeded"):
                imported += 1
                _recently_imported(key, remember=True)
        logger.info("[Album Tracks] Imported %d of %d other tracks of %r", imported, len(pairs), name)
    except Exception as exc:
        logger.warning("[Album Tracks] Album expansion stopped: %s", exc, exc_info=True)
    finally:
        with _state_lock:
            _active_albums.discard(identity)
    return imported
