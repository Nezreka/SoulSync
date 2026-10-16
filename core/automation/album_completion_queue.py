"""Safe, profile-scoped album-completion batch creation."""

import logging
import uuid

logger = logging.getLogger(__name__)


def prepare_completion_tracks(album, missing_tracks, source, artist, *, expected_album_id=None):
    from core.search.enriched_download import album_context, enriched_track_info

    if not isinstance(album, dict) or not isinstance(missing_tracks, list):
        raise ValueError("Invalid album completion metadata")
    if not source or str(album.get("source") or "").casefold() != str(source).casefold():
        raise ValueError("Canonical album source mismatch")
    if expected_album_id is not None and str(album.get("id") or "") != str(expected_album_id):
        raise ValueError("Canonical album release ID mismatch")
    context = album_context(album, source, artist)
    tracks = []
    for track in missing_tracks:
        if not isinstance(track, dict) or not track.get("id") or not track.get("name"):
            raise ValueError("Invalid missing track")
        tracks.append(enriched_track_info(track, context, source, as_album=True))
    if not tracks:
        raise ValueError("No missing tracks to complete")
    return context, tracks


def queue_album_completion(*, canonical_source, canonical_album_id, album_id, album_title, artist, missing_tracks, deps, canonical_album=None, related_album_ids=None):
    """Create a missing-track-only batch, scoped to one profile."""
    if not missing_tracks or not canonical_source or not canonical_album_id:
        return {"status": "skipped", "reason": "Missing release or tracks"}
    profile_id = deps.profile_id
    verify_scope = getattr(deps, "verify_album_scope", None)
    if not callable(verify_scope):
        return {"status": "skipped", "reason": "Album ownership verification unavailable"}
    try:
        if verify_scope(album_id, profile_id) is not True:
            return {"status": "skipped", "reason": "Album is not in the requesting shared library"}
    except Exception:
        return {"status": "skipped", "reason": "Album ownership verification failed"}

    verify = getattr(deps, "verify_missing_tracks", None)
    if not callable(verify):
        return {"status": "skipped", "reason": "Library verification unavailable"}
    try:
        if related_album_ids is None:
            related_album_ids = [album_id]
        missing_tracks = verify(
            album_id, missing_tracks, profile_id, related_album_ids
        )
        if not missing_tracks:
            return {"status": "skipped", "reason": "Album already complete"}
        album_ctx, prepared_tracks = prepare_completion_tracks(
            canonical_album, missing_tracks, canonical_source, artist,
            expected_album_id=canonical_album_id,
        )
    except Exception:
        return {"status": "skipped", "reason": "Library verification failed"}
    batches = deps.download_batches

    def _blocked():
        # called under tasks_lock. no db work in here, the lock is the download hot path
        for batch in batches.values():
            if (batch.get("phase") not in ("complete", "error", "cancelled", "failed")
                    and batch.get("album_completion_only")
                    and batch.get("profile_id") == profile_id
                    and str(batch.get("canonical_source", "")).casefold() == canonical_source.casefold()
                    and str(batch.get("canonical_album_id", "")) == str(canonical_album_id)):
                return {"status": "skipped", "reason": "Release already queued"}
        if sum(1 for batch in batches.values() if batch.get("phase") == "analysis") >= 3:
            return {"status": "skipped", "reason": "Too many active analysis batches"}
        return None

    with deps.tasks_lock:
        blocked = _blocked()
    if blocked:
        return blocked

    claim = getattr(deps, "claim_completion_attempt", None)
    release = getattr(deps, "release_completion_attempt", None)
    if not callable(claim) or not callable(release):
        return {"status": "skipped", "reason": "Persistent cooldown unavailable"}
    # claim outside the lock, it's a sqlite write
    try:
        cooldown_token = claim(
            profile_id, canonical_source, canonical_album_id
        )
    except Exception:
        return {"status": "skipped", "reason": "Persistent cooldown failed"}
    if not cooldown_token:
        return {"status": "skipped", "reason": "Release is cooling down"}

    batch_id = str(uuid.uuid4())
    new_batch = {
        "phase": "analysis",
        "playlist_id": "album_completion:" + str(album_id),
        "playlist_name": album_title,
        "queue": [],
        "active_count": 0,
        "max_concurrent": 1,
        "permanently_failed_tracks": [],
        "cancelled_tracks": set(),
        "queue_index": 0,
        "analysis_total": len(missing_tracks),
        "analysis_processed": 0,
        "analysis_results": [],
        "profile_id": profile_id,
        "canonical_source": canonical_source,
        "canonical_album_id": canonical_album_id,
        "album_completion_only": True,
        "is_album_download": True,
        "force_download_all": False,
        "force_replace": False,
        "ignore_manual_matches": False,
        "ignore_blocklist": False,
        "playlist_folder_mode": False,
        "album_context": album_ctx,
        "artist_context": {
            "name": (album_ctx.get("artists") or [{}])[0].get("name", artist),
            "id": "",
            "genres": [],
            "source": canonical_source,
        },
        "wing_it": False,
        "skip_acoustid": False,
        "batch_source": canonical_source,
        "playlist_source": canonical_source,
        "requested_quality_profile_id": None,
    }
    with deps.tasks_lock:
        # recheck, another batch may have landed while we claimed
        blocked = _blocked()
        if not blocked:
            batches[batch_id] = new_batch
    if blocked:
        try:
            release(profile_id, canonical_source, canonical_album_id, cooldown_token)
        except Exception:
            logger.debug("[Album Completion] cooldown release failed", exc_info=True)
        return blocked
    try:
        deps.submit(batch_id, "album_completion:" + str(album_id), prepared_tracks)
    except Exception:
        with deps.tasks_lock:
            batches.pop(batch_id, None)
        release = getattr(deps, "release_completion_attempt", None)
        try:
            if not callable(release):
                raise RuntimeError("Cooldown rollback unavailable")
            release(
                profile_id, canonical_source,
                canonical_album_id, cooldown_token
            )
        except Exception:
            return {
                "status": "failed",
                "reason": "Submission failed; cooldown rollback failed",
            }
        return {"status": "failed", "reason": "Download submission failed"}
    return {"status": "queued", "batch_id": batch_id}
