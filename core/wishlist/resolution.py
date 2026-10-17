"""Wishlist resolution and removal helpers."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from core.imports.context import (
    get_import_original_search,
    get_import_search_result,
    get_import_source,
    get_import_source_ids,
    get_import_track_info,
)
from core.wishlist.library_match import artist_names
from core.wishlist.removal_guard import (
    REASON_ALREADY_OWNED,
    REASON_ATOMIC_PUBLISHED,
    REASON_DOWNLOAD_COMPLETE,
    explain_refusal,
    may_remove,
)
from core.wishlist.service import get_wishlist_service
from database.music_database import get_database
from utils.logging_config import get_logger


logger = get_logger("wishlist.resolution")


def _primary_track_artist_name(track_info: Dict[str, Any]) -> str:
    artists = (track_info or {}).get("artists", [])
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict):
            return str(first.get("name", "") or "")
        return str(first or "")
    if isinstance(artists, str):
        return artists
    return str((track_info or {}).get("artist", "") or "")


def _album_name(payload: Dict[str, Any]) -> str:
    album = (payload or {}).get("album")
    if isinstance(album, dict):
        return str(album.get("name", "") or "")
    return str(album or "")


def _normalized(value: Any) -> str:
    return str(value or "").strip().lower()


def _candidate_track_id(wishlist_track: Dict[str, Any]) -> str:
    """The source track id of a wishlist row, whichever key it arrived under."""
    return (wishlist_track.get("track_id")
            or wishlist_track.get("spotify_track_id")
            or wishlist_track.get("id")
            or "")


def _all_profile_wishlist_tracks(wishlist_service, database=None,
                                 profile_ids: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
    """Wishlist rows across profiles, each tagged with the profile it came from.

    ``profile_ids`` narrows the sweep to the profiles that can actually see the
    file being matched, so a title/artist fallback can no longer reach into an
    unrelated profile's requests.
    """
    database = database or get_database()
    if profile_ids:
        ids = list(profile_ids)
    else:
        ids = [profile["id"] for profile in database.get_all_profiles()]
    wishlist_tracks: List[Dict[str, Any]] = []
    for pid in ids:
        for row in wishlist_service.get_wishlist_tracks_for_download(profile_id=pid):
            row = dict(row)
            row.setdefault("_profile_id", pid)
            wishlist_tracks.append(row)
    return wishlist_tracks


def _profiles_owning_path(published_path: Any, database=None) -> List[Any]:
    """Every profile whose library root actually contains ``published_path``.

    This is what "the track is now in the library" means once profiles can have
    their own library root (#1199). The success branch of ``update_wishlist_retry``
    used to ``DELETE`` the row for EVERY profile on the reasoning that the
    library is shared — true for the common single-root install, and wrong for
    an own-library profile, whose request was silently cleared by somebody
    else's download of the same track into a folder it cannot see.

    Computing owners from the path reproduces the old behavior exactly where it
    was right (one shared root → every profile owns it) and narrows it only
    where it was wrong.
    """
    if not published_path:
        return []
    try:
        from core.imports.paths import library_root_for_profile, shared_transfer_root
        database = database or get_database()
        profiles = database.get_all_profiles() or []
    except Exception as exc:  # noqa: BLE001 - no db / no paths → caller falls back
        logger.debug("[Wishlist] Could not resolve profile libraries: %s", exc)
        return []

    try:
        path_n = os.path.normpath(os.path.abspath(str(published_path)))
    except (OSError, ValueError):
        return []

    shared_root: Optional[str] = None
    owners: List[Any] = []  # (root length, profile id)
    for profile in profiles:
        pid = profile.get("id") if isinstance(profile, dict) else None
        if pid is None:
            continue
        root = None
        try:
            root = library_root_for_profile(pid, announce=False)
        except Exception:  # noqa: BLE001 - treat as shared
            root = None
        if not root:
            if shared_root is None:
                try:
                    shared_root = os.path.normpath(os.path.abspath(shared_transfer_root()))
                except Exception:  # noqa: BLE001
                    shared_root = ""
            root_n = shared_root
        else:
            try:
                root_n = os.path.normpath(os.path.abspath(root))
            except (OSError, ValueError):
                continue
        if root_n and (path_n == root_n or path_n.startswith(root_n + os.sep)):
            owners.append((len(root_n), pid))
    # an own folder inside the shared one holds its own files, not everyone's:
    # the deepest root that contains the path is the library it is in
    deepest = max((n for n, _pid in owners), default=0)
    return [pid for n, pid in owners if n == deepest]


def _log_removal(event: str, *, quiet: bool = False, **fields: Any) -> None:
    """One structured line per wishlist removal decision.

    The old log named a Spotify id and nothing else, so reconstructing why a
    track vanished meant correlating adjacent timestamps across three loggers
    and guessing. Every decision now carries the ids needed to answer it
    directly, and ``database.record_wishlist_removal`` keeps the same fields
    queryable after the logs roll.
    """
    payload = " ".join(f"{k}={v!r}" for k, v in sorted(fields.items()) if v not in (None, ""))
    if event == "refused":
        # backstop callers refuse on nearly every track (the pipeline already
        # cleared the row, or deferred it for the album publish), so a warning
        # there reads like a failure when nothing is wrong
        log = logger.debug if quiet else logger.warning
        log("[Wishlist] removal refused %s", payload)
    else:
        logger.info("[Wishlist] removal %s %s", event, payload)


def check_and_remove_from_wishlist(
    context: Dict[str, Any],
    wishlist_service=None,
    database=None,
    *,
    published_path: Any = None,
    reason: str = REASON_DOWNLOAD_COMPLETE,
    transfer_dir: Optional[str] = None,
    batch_id: Optional[str] = None,
    owner_profiles: Optional[List[Any]] = None,
    quiet_refusal: bool = False,
) -> bool:
    """Remove a wishlist row for a download that finished — if it really did.

    ``published_path`` is the proof, and it is required for the download-
    completion reasons: a track whose file is still in atomic-publish staging,
    still sitting in the downloads folder, or not on disk at all has NOT
    satisfied the request, and deleting its row destroys the only durable record
    that anyone wanted it. See :mod:`core.wishlist.removal_guard`.

    ``quiet_refusal`` is for the backstop callers (batch completion and the
    completed-task sweep): the import already settled the row, so their
    refusals are expected and log at debug.

    Returns True when a row was removed.
    """
    try:
        wishlist_service = wishlist_service or get_wishlist_service()
        if published_path is None:
            published_path = context.get("_final_processed_path")

        allowed, state = may_remove(published_path, reason=reason, transfer_dir=transfer_dir)
        if not allowed:
            _log_removal(
                "refused",
                quiet=quiet_refusal,
                reason=reason,
                state=state,
                path=published_path,
                batch_id=batch_id,
                why=explain_refusal(state),
            )
            return False

        source = get_import_source(context)
        source_ids = get_import_source_ids(context)
        source_label = {
            "spotify": "Spotify",
            "itunes": "iTunes",
            "deezer": "Deezer",
            "discogs": "Discogs",
            "hydrabase": "Hydrabase",
        }.get(source, "Source")
        track_info = get_import_track_info(context) or get_import_search_result(context)
        search_result = get_import_original_search(context) or get_import_search_result(context)

        # Only the profiles that can actually see the published file may have
        # their request cleared by it. A caller clearing a whole album's worth of
        # tracks out of one library resolves this once and passes it in — the
        # atomic publish runs under the downloads lock, so a per-track profile
        # lookup there would hold it for the length of the album.
        if owner_profiles is None:
            owner_profiles = _profiles_owning_path(published_path, database=database)
        match_profiles = owner_profiles or None

        track_id = source_ids.get("track_id") or None
        if track_id:
            logger.info("[Wishlist] Found %s track ID from source_ids: %s", source_label, track_id)
        elif "wishlist_id" in track_info:
            wishlist_id = track_info["wishlist_id"]
            logger.info("[Wishlist] Found wishlist_id in context: %s", wishlist_id)
            wishlist_tracks = _all_profile_wishlist_tracks(
                wishlist_service, database=database, profile_ids=match_profiles)
            for wishlist_track in wishlist_tracks:
                if wishlist_track.get("wishlist_id") == wishlist_id:
                    track_id = wishlist_track.get("track_id") or wishlist_track.get("spotify_track_id") or wishlist_track.get("id")
                    logger.info("[Wishlist] Found track ID from wishlist entry: %s", track_id)
                    break

        if not track_id:
            track_name = track_info.get("name") or search_result.get("title", "")
            artist_name = _primary_track_artist_name(track_info) or _primary_track_artist_name(search_result)
            album_name = _album_name(track_info) or _album_name(search_result)

            if track_name and artist_name:
                logger.warning(
                    "[Wishlist] No track ID found, checking for fuzzy match: '%s' by '%s'",
                    track_name,
                    artist_name,
                )

                wishlist_tracks = _all_profile_wishlist_tracks(
                    wishlist_service, database=database, profile_ids=match_profiles)
                track_id = _match_by_metadata(
                    wishlist_tracks, track_name, artist_name, album_name,
                    log_prefix="[Wishlist]")

        if track_id:
            logger.info("[Wishlist] Attempting to remove track from wishlist: %s", track_id)
            removed = wishlist_service.mark_track_download_result(
                track_id, success=True,
                profile_ids=owner_profiles or None,
                audit={
                    "reason": reason,
                    "final_path": str(published_path or ""),
                    "batch_id": str(batch_id or context.get("batch_id") or ""),
                    "source": source,
                },
            )
            _log_removal(
                "removed" if removed else "no-op",
                track_id=track_id,
                reason=reason,
                path=published_path,
                profiles=owner_profiles or "all",
                batch_id=batch_id or context.get("batch_id"),
            )
            if not removed:
                logger.warning("ℹ️ [Wishlist] Track not found in wishlist or already removed: %s", track_id)
            return bool(removed)

        logger.warning("ℹ️ [Wishlist] No track ID found for wishlist removal check")
        return False
    except Exception as exc:
        logger.error("[Wishlist] Error in wishlist removal check: %s", exc)
        return False


def _match_by_metadata(wishlist_tracks: List[Dict[str, Any]], track_name: str,
                       artist_name: str, album_name: str = "",
                       *, log_prefix: str = "[Wishlist]") -> Optional[str]:
    """Find a wishlist row by title + primary artist, with the album as a tiebreak.

    This fallback deletes a row chosen by metadata rather than by id, so a
    misidentified download takes an unrelated request down with it — a real
    hazard, because a badly tagged Soulseek file is routinely imported under a
    neighbouring track's name. Two guards: when both sides name an album they
    have to agree, and when several rows tie on title+artist the match is
    abandoned rather than guessed, because picking one at random is how a user
    loses the request they were actually waiting on.
    """
    wanted_name = _normalized(track_name)
    wanted_artist = _normalized(artist_name)
    wanted_album = _normalized(album_name)

    candidates: List[Dict[str, Any]] = []
    for wishlist_track in wishlist_tracks:
        if _normalized(wishlist_track.get("name")) != wanted_name:
            continue
        wl_names = artist_names(wishlist_track.get("artists"))
        if _normalized(wl_names[0] if wl_names else "") != wanted_artist:
            continue
        candidates.append(wishlist_track)

    if not candidates:
        return None

    # Ambiguity is about distinct REQUESTS, not rows. The same track wishlisted
    # by two profiles is two rows and one request, and the sweep returns a row
    # per profile — counting rows made the fallback refuse every match on any
    # multi-profile install, so nothing was ever cleared and the track was
    # re-downloaded on every cycle.
    by_id: Dict[str, Dict[str, Any]] = {}
    for candidate in candidates:
        track_id = _candidate_track_id(candidate)
        if track_id:
            by_id.setdefault(track_id, candidate)

    if not by_id:
        return None

    # The album only breaks a tie. Vetoing a LONE candidate on a album mismatch
    # cannot prevent a wrong pick — there is no other request to pick — and a
    # wishlist row stored from one release ("Album") against a download resolved
    # from another ("Album (Deluxe Edition)") is completely ordinary, so the veto
    # just left the track on the wishlist to be downloaded again forever.
    if wanted_album and len(by_id) > 1:
        narrowed = {
            track_id: candidate for track_id, candidate in by_id.items()
            if _normalized(_album_name(candidate)) in ("", wanted_album)
        }
        if narrowed:
            by_id = narrowed

    if len(by_id) > 1:
        logger.warning(
            "%s Metadata match ambiguous — %d wishlist requests match '%s' by '%s'; "
            "keeping them all rather than guessing",
            log_prefix, len(by_id), track_name, artist_name)
        return None

    track_id, match = next(iter(by_id.items()))
    logger.info("%s Found metadata match - track ID: %s (profile %s)",
                log_prefix, track_id, match.get("_profile_id"))
    return track_id


def check_and_remove_track_from_wishlist_by_metadata(
    track_data: Dict[str, Any],
    wishlist_service=None,
    database=None,
    *,
    matched_path: Any = None,
) -> bool:
    """Remove a wishlist track by metadata after a database/library match.

    Not gated by :mod:`core.wishlist.removal_guard`: the caller has already
    matched the track against the library, so ownership is its proof, not a
    download's. ``matched_path`` is recorded for the audit trail when known.
    """
    try:
        wishlist_service = wishlist_service or get_wishlist_service()
        track_name = track_data.get("name", "")
        track_id = track_data.get("id", "")
        artists = track_data.get("artists", [])

        logger.info("[Analysis] Checking if track should be removed from wishlist: '%s' (ID: %s)", track_name, track_id)

        audit = {"reason": REASON_ALREADY_OWNED, "final_path": str(matched_path or "")}

        if track_id:
            removed = wishlist_service.mark_track_download_result(
                track_id, success=True, audit=audit)
            if removed:
                _log_removal("removed", track_id=track_id, reason=REASON_ALREADY_OWNED,
                             path=matched_path, via="direct-id")
                return True

        if track_name and artists:
            primary_artist = _primary_track_artist_name(track_data)
            if primary_artist:
                logger.warning(
                    "[Analysis] No direct ID match, trying metadata match: '%s' by '%s'",
                    track_name,
                    primary_artist,
                )

                wishlist_tracks = _all_profile_wishlist_tracks(wishlist_service, database=database)
                matched_id = _match_by_metadata(
                    wishlist_tracks, track_name, primary_artist, _album_name(track_data),
                    log_prefix="[Analysis]")
                if matched_id:
                    removed = wishlist_service.mark_track_download_result(
                        matched_id, success=True, audit=audit)
                    if removed:
                        _log_removal("removed", track_id=matched_id,
                                     reason=REASON_ALREADY_OWNED, path=matched_path,
                                     via="metadata")
                        return True

        logger.warning("ℹ️ [Analysis] Track not found in wishlist or already removed: '%s'", track_name)
        return False

    except Exception as e:
        logger.error("[Analysis] Error checking wishlist removal by metadata: %s", e)
        import traceback

        traceback.print_exc()
        return False


def remove_published_wishlist_entries(pending: List[Dict[str, Any]],
                                      published_paths: Dict[str, str],
                                      *, batch_id: str = "",
                                      wishlist_service=None,
                                      database=None) -> int:
    """Clear the wishlist rows for tracks an atomic album publish just made live.

    ``pending`` is the batch's deferred-removal roster (one entry per staged
    track, carrying the import context's source ids); ``published_paths`` maps
    staged path → final path as the publish actually moved them. A row is only
    cleared when its file is in that map AND exists at the final path, so a
    publish that silently skipped a file cannot clear the request for it.
    """
    removed = 0
    owner_profiles: Optional[List[Any]] = None
    for entry in pending or []:
        staged = os.path.normpath(str(entry.get("staged_path") or ""))
        final = published_paths.get(staged) or entry.get("final_path")
        if not final or not os.path.exists(final):
            logger.warning(
                "[Wishlist] Batch %s: keeping wishlist entry for %r — no published file at %r",
                batch_id, entry.get("track_name") or staged, final)
            continue
        if owner_profiles is None:
            # Every track of a published album lands in the same library, so the
            # owning profiles are the same for all of them.
            owner_profiles = _profiles_owning_path(final, database=database)
        context = dict(entry.get("context") or {})
        context.setdefault("_final_processed_path", final)
        if check_and_remove_from_wishlist(
            context,
            wishlist_service=wishlist_service,
            database=database,
            published_path=final,
            reason=REASON_ATOMIC_PUBLISHED,
            batch_id=batch_id,
            owner_profiles=owner_profiles,
        ):
            removed += 1
    return removed


__all__ = [
    "check_and_remove_from_wishlist",
    "check_and_remove_track_from_wishlist_by_metadata",
    "remove_published_wishlist_entries",
]
