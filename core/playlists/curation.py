"""Decision semantics for optional mirrored-playlist curation.

Persistence lives in :mod:`database.music_database`; this module keeps the
precedence rule and API projection pure so sync, wishlist, download and UI
paths all answer eligibility the same way.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
from typing import Any, Iterable, Mapping


CURATION_DECISIONS = frozenset({"keep", "pending", "reject"})


class CurationPolicyError(RuntimeError):
    """A persisted curation policy could not be read safely."""


class CurationPolicyStale(CurationPolicyError):
    """A curated operation no longer matches the persisted policy revision."""


def mirrored_membership_fingerprint(tracks: Iterable[Mapping[str, Any]]) -> str:
    """Stable identity for ordered mirrored membership, including duplicates."""
    source_track_ids = [str(track.get("source_track_id") or "") for track in tracks]
    payload = json.dumps(source_track_ids, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def effective_decision(explicit: Any, globally_rejected: bool) -> str:
    """Resolve one source track's effective decision.

    Explicit Keep is the sole override for a profile/source rejection.
    Pending remains distinguishable from the absence of a decision row.
    """
    decision = str(explicit or "").strip().lower()
    if decision == "keep":
        return "keep"
    if decision in ("pending", "reject"):
        return decision
    return "reject" if globally_rejected else "unreviewed"


def decorate_tracks(
    tracks: Iterable[Mapping[str, Any]],
    *,
    enabled: bool,
    decisions: Mapping[str, str],
    global_rejections: Iterable[str],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Add explicit/effective decision fields without changing track order."""
    rejected = {str(value) for value in global_rejections if value}
    counts: Counter[str] = Counter()
    decorated: list[dict[str, Any]] = []
    for original in tracks:
        track = dict(original)
        source_track_id = str(track.get("source_track_id") or "")
        explicit = decisions.get(source_track_id)
        effective = effective_decision(explicit, source_track_id in rejected)
        track["curation_decision"] = explicit
        track["globally_rejected"] = source_track_id in rejected
        track["effective_decision"] = effective
        track["curation_eligible"] = not enabled or effective == "keep"
        counts[effective] += 1
        decorated.append(track)
    return decorated, {
        "keep": counts["keep"],
        "pending": counts["pending"],
        "reject": counts["reject"],
        "unreviewed": counts["unreviewed"],
    }


def curated_tracks_for_operation(database, playlist_id: int, tracks, *, profile_id=None):
    """Return the tracks allowed by one strict policy read and its snapshot.

    Disabled playlists keep their original track list and return ``None`` for
    the snapshot, which keeps all legacy call paths unchanged.
    """
    policy = database.get_mirrored_playlist_curation(
        int(playlist_id), profile_id=profile_id
    )
    if policy is None:
        raise CurationPolicyError(f"Mirrored playlist {playlist_id} was not found")
    original = list(tracks or [])
    if not policy["enabled"]:
        return original, None
    allowed = []
    source_track_ids = []
    rejected = policy["global_rejections"]
    for track in original:
        source_track_id = str(track.get("source_track_id") or "")
        if effective_decision(
            policy["decisions"].get(source_track_id),
            source_track_id in rejected,
        ) == "keep":
            allowed.append(track)
            source_track_ids.append(source_track_id)
    return allowed, {
        "playlist_id": int(policy["playlist_id"]),
        "profile_id": int(policy["profile_id"]),
        "revision": int(policy["revision"]),
        "source_track_ids": source_track_ids,
        "membership_fingerprint": mirrored_membership_fingerprint(original),
    }


def verify_curation_snapshot(database, snapshot: Mapping[str, Any] | None) -> None:
    """Fail closed when a curated run is stale or no longer all Keep."""
    if not snapshot:
        return
    playlist_id = int(snapshot["playlist_id"])
    policy = database.get_mirrored_playlist_curation(
        playlist_id, profile_id=int(snapshot["profile_id"])
    )
    if policy is None or not policy["enabled"]:
        raise CurationPolicyStale(
            f"Curation policy changed while mirrored playlist {playlist_id} was running"
        )
    if int(policy["revision"]) != int(snapshot["revision"]):
        raise CurationPolicyStale(
            f"Curation policy changed while mirrored playlist {playlist_id} was running"
        )
    expected_membership = snapshot.get("membership_fingerprint")
    if expected_membership is not None:
        current_tracks = database.get_mirrored_playlist_tracks(playlist_id)
        if mirrored_membership_fingerprint(current_tracks or []) != expected_membership:
            raise CurationPolicyStale(
                f"Mirrored playlist {playlist_id} membership changed while sync was running"
            )
    rejected = policy["global_rejections"]
    for source_track_id in snapshot.get("source_track_ids") or []:
        source_track_id = str(source_track_id or "")
        if effective_decision(
            policy["decisions"].get(source_track_id),
            source_track_id in rejected,
        ) != "keep":
            raise CurationPolicyStale(
                f"Track {source_track_id!r} is no longer approved for mirrored playlist {playlist_id}"
            )


def curation_origin(source_info: Any) -> dict[str, Any] | None:
    """Extract the durable fields that identify one mirrored-track origin."""
    if not isinstance(source_info, Mapping):
        return None
    playlist_id = source_info.get("mirrored_playlist_id")
    source_track_id = str(source_info.get("source_track_id") or "").strip()
    if playlist_id in (None, "") or not source_track_id:
        return None
    try:
        playlist_id = int(playlist_id)
    except (TypeError, ValueError):
        return None
    return {
        "mirrored_playlist_id": playlist_id,
        "source_track_id": source_track_id,
    }


def merge_playlist_origins(previous: Any, incoming: Any) -> list[dict[str, Any]]:
    """Union playlist origins without losing older independent requests."""
    origins: list[dict[str, Any]] = []
    seen: set[tuple[int, str]] = set()
    for context in (previous, incoming):
        candidates = []
        if isinstance(context, Mapping):
            candidates.extend(context.get("playlist_origins") or [])
            direct = curation_origin(context)
            if direct:
                candidates.append(direct)
        for candidate in candidates:
            origin = curation_origin(candidate)
            if not origin:
                continue
            key = (origin["mirrored_playlist_id"], origin["source_track_id"])
            if key not in seen:
                seen.add(key)
                origins.append(origin)
    return origins


def wishlist_track_allowed(database, track: Mapping[str, Any]) -> bool:
    """Whether any durable origin still permits deferred acquisition.

    Manual rows and legacy rows without durable mirrored provenance preserve
    their historical behavior. Policy reads remain strict for curated origins.
    """
    if str(track.get("source_type") or "").lower() == "manual":
        return True
    source_info = track.get("source_info") or {}
    origins = merge_playlist_origins({}, source_info)
    if not origins:
        return True
    failures = []
    for origin in origins:
        try:
            policy = database.get_mirrored_playlist_curation(
                origin["mirrored_playlist_id"]
            )
            if policy is None:
                continue
            if not policy["enabled"]:
                return True
            source_track_id = origin["source_track_id"]
            if effective_decision(
                policy["decisions"].get(source_track_id),
                source_track_id in policy["global_rejections"],
            ) == "keep":
                # Decisions deliberately survive refresh, but a retained Keep
                # for a track that left the mirror must not keep authorizing
                # deferred acquisition. Disabled curation still returns above.
                current_tracks = database.get_mirrored_playlist_tracks(
                    origin["mirrored_playlist_id"]
                )
                if any(
                    str(track.get("source_track_id") or "") == source_track_id
                    for track in (current_tracks or [])
                ):
                    return True
        except CurationPolicyError as exc:
            failures.append(exc)
    if failures:
        raise failures[0]
    return False


__all__ = [
    "CURATION_DECISIONS",
    "CurationPolicyError",
    "CurationPolicyStale",
    "curated_tracks_for_operation",
    "curation_origin",
    "decorate_tracks",
    "effective_decision",
    "merge_playlist_origins",
    "mirrored_membership_fingerprint",
    "verify_curation_snapshot",
    "wishlist_track_allowed",
]
