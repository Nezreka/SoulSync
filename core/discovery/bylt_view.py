"""Turning a stored Because You Listen To generation into the API payload.

pure shaping. the caller supplies the generation, a way to look a recording up
in the library, and a way to make an image url browser-safe; this decides what
the endpoint says.

the counts are the point. the old endpoint resolved saved ids against the
newest 5,000 pool rows and dropped whatever it could not find, so a shelf could
advertise ten tracks and hand back three with no explanation anywhere. every
section now reports what it asked for, what it resolved, and why the rest is
missing.

owned material is labelled owned rather than presented as new to the library.
"""

from typing import Any, Callable, Dict, List, Optional, Sequence

UNAVAILABLE_NO_ID = "missing-id"
UNAVAILABLE_NOT_IN_POOL = "not-in-pool"
UNAVAILABLE_SOURCE_UNSUPPORTED = "source-unsupported"

# every source whose ids the pool can resolve back to a row
SUPPORTED_HYDRATION_SOURCES = ("spotify", "itunes", "deezer")


def _norm(text: Any) -> str:
    return str(text or "").strip().lower()


def track_payload(row: Dict[str, Any], *, owned_lookup=None,
                  image_fix: Optional[Callable[[Any], Any]] = None) -> Dict[str, Any]:
    """One stored row as the shape the shelf renders and every action reuses.

    the identity is carried, not re-derived: a card that knows its provider id
    must never be resolved back to a track by display title alone.
    """
    title = row.get("track_name") or row.get("name") or ""
    artist = row.get("artist_name") or row.get("artist") or ""
    cover = row.get("album_cover_url") or row.get("image_url")
    owned = owned_lookup(title, artist) if owned_lookup else None
    out = {
        "id": str(row.get("track_id") or ""),
        "name": title,
        "artist": artist,
        "album": row.get("album_name") or row.get("album") or "",
        "image_url": image_fix(cover) if (image_fix and cover) else cover,
        # milliseconds, normalised once at the boundary and never again
        "duration_ms": int(row.get("duration_ms") or 0),
        "popularity": int(row.get("popularity") or 0),
        "source": row.get("source") or "",
        "spotify_track_id": row.get("spotify_track_id"),
        "itunes_track_id": row.get("itunes_track_id"),
        "deezer_track_id": row.get("deezer_track_id"),
        "track_data_json": row.get("track_data_json"),
        "relation": row.get("relation") or "",
        "relation_detail": row.get("relation_detail") or "",
        # 1 = closest. kept so the shelf can rank its own evidence and a
        # later tune has the signal without regenerating.
        "relation_rank": row.get("relation_rank"),
        "owned": bool(owned),
        "library_track_id": (owned or {}).get("id") if owned else None,
    }
    return out


def section_payload(section: Dict[str, Any], *, owned_lookup=None,
                    image_fix: Optional[Callable[[Any], Any]] = None) -> Dict[str, Any]:
    rows = section.get("tracks") or []
    tracks, unavailable = [], {}
    for row in rows:
        if not str(row.get("track_id") or "").strip():
            unavailable[UNAVAILABLE_NO_ID] = unavailable.get(UNAVAILABLE_NO_ID, 0) + 1
            continue
        tracks.append(track_payload(row, owned_lookup=owned_lookup, image_fix=image_fix))
    seed_image = section.get("seed_image")
    return {
        "seed_key": section.get("seed_key"),
        # the heading the vanilla shelf reads, kept under its old name so the
        # renderer contract does not change under the port
        "artist_name": section.get("seed_name"),
        "artist_image": image_fix(seed_image) if (image_fix and seed_image) else seed_image,
        "reason": section.get("reason") or {},
        "presentation": section.get("presentation") or "compact",
        "diagnostics": section.get("diagnostics") or {},
        "requested": len(rows),
        "resolved": len(tracks),
        "unavailable": len(rows) - len(tracks),
        "unavailable_reasons": unavailable,
        "legacy": bool(section.get("legacy")),
        "tracks": tracks,
    }


def payload_from_generation(
    generation: Dict[str, Any],
    *,
    failure: Optional[Dict[str, Any]] = None,
    history_scope: str = "shared",
    history_note: Optional[str] = None,
    owned_lookup=None,
    image_fix: Optional[Callable[[Any], Any]] = None,
) -> Dict[str, Any]:
    """The served payload for a stored generation.

    a failure marker newer than the generation does not hide it: the last good
    content is served, flagged stale, with the error attached. that is the
    difference between "nothing to recommend" and "the last run broke".
    """
    sections = [section_payload(s, owned_lookup=owned_lookup, image_fix=image_fix)
                for s in (generation.get("sections") or [])]
    return {
        "success": True,
        "generation_id": generation.get("generation_id"),
        "generated_at": generation.get("generated_at"),
        "algorithm": generation.get("algorithm"),
        "source": generation.get("source"),
        "profile_id": generation.get("profile_id"),
        "status": "stale" if failure else (generation.get("status") or "ok"),
        "error": (failure or {}).get("message"),
        "error_at": (failure or {}).get("attempted_at"),
        "history_scope": history_scope,
        "history_note": history_note,
        "legacy": False,
        "sections": sections,
    }


def payload_from_legacy(
    slots: Sequence[Dict[str, Any]],
    hydrated: Dict[str, Any],
    active_source: str,
    *,
    history_scope: str = "shared",
    history_note: Optional[str] = None,
    owned_lookup=None,
    image_fix: Optional[Callable[[Any], Any]] = None,
    seed_image_lookup: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """The pre-generation ordinal rows, served once and labelled as legacy.

    ``hydrated`` maps stored id -> a pool row dict. a saved id that no longer
    resolves is reported with a reason instead of vanishing, and a source that
    cannot hydrate ids at all says so rather than rendering an empty shelf.
    """
    supported = _norm(active_source) in SUPPORTED_HYDRATION_SOURCES
    sections = []
    for slot in slots:
        ids = slot.get("track_ids") or []
        rows, unavailable = [], {}
        for tid in ids:
            row = hydrated.get(str(tid))
            if row is None:
                reason = (UNAVAILABLE_NOT_IN_POOL if supported
                          else UNAVAILABLE_SOURCE_UNSUPPORTED)
                unavailable[reason] = unavailable.get(reason, 0) + 1
                continue
            rows.append(row)
        payload = section_payload(
            {
                "seed_key": slot.get("seed_key"),
                "seed_name": slot.get("seed_name"),
                "seed_image": (seed_image_lookup(slot.get("seed_name"))
                               if seed_image_lookup else None),
                "reason": {"kind": "legacy",
                           "label": f"From your {slot.get('seed_name')} listening",
                           "evidence": []},
                "presentation": "compact",
                "diagnostics": {"heading_scope": slot.get("heading_scope")},
                "legacy": True,
                "tracks": rows,
            },
            owned_lookup=owned_lookup, image_fix=image_fix)
        # the requested count is the STORED count, not what survived hydration
        payload["requested"] = len(ids)
        payload["unavailable"] = len(ids) - payload["resolved"]
        payload["unavailable_reasons"] = unavailable
        sections.append(payload)
    return {
        "success": True,
        "generation_id": None,
        "generated_at": None,
        "algorithm": "legacy-slots",
        "source": active_source,
        "profile_id": None,
        "status": "legacy",
        "error": None,
        "error_at": None,
        "history_scope": history_scope,
        "history_note": history_note,
        "legacy": True,
        "sections": [s for s in sections if s["tracks"]],
    }


def empty_payload(*, source: str, history_scope: str = "shared",
                  history_note: Optional[str] = None,
                  failure: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """No generation and no legacy rows.

    a failure marker with nothing to fall back on is still reported as a
    failure - an empty success would be cached and read as "you have no
    recommendations", which is a different statement.
    """
    return {
        "success": True,
        "generation_id": None,
        "generated_at": None,
        "algorithm": None,
        "source": source,
        "profile_id": None,
        "status": "failed" if failure else "empty",
        "error": (failure or {}).get("message"),
        "error_at": (failure or {}).get("attempted_at"),
        "history_scope": history_scope,
        "history_note": history_note,
        "legacy": False,
        "sections": [],
    }


def pool_row_to_dict(track: Any) -> Dict[str, Any]:
    """A hydrated DiscoveryTrack in the stored-row shape, so the legacy path
    and the generation path share one renderer."""
    return {
        "track_id": (getattr(track, "spotify_track_id", None)
                     or getattr(track, "deezer_track_id", None)
                     or getattr(track, "itunes_track_id", None)),
        "spotify_track_id": getattr(track, "spotify_track_id", None),
        "itunes_track_id": getattr(track, "itunes_track_id", None),
        "deezer_track_id": getattr(track, "deezer_track_id", None),
        "track_name": getattr(track, "track_name", ""),
        "artist_name": getattr(track, "artist_name", ""),
        "album_name": getattr(track, "album_name", ""),
        "album_cover_url": getattr(track, "album_cover_url", None),
        "duration_ms": getattr(track, "duration_ms", 0),
        "popularity": getattr(track, "popularity", 0),
        "source": getattr(track, "source", ""),
        "track_data_json": getattr(track, "track_data_json", None),
    }


def owned_lookup_from_library(resolved: Dict[tuple, Dict[str, Any]]):
    """Wrap ``resolve_library_tracks``' output as a (title, artist) lookup."""
    def _lookup(title: Any, artist: Any):
        return resolved.get((_norm(title), _norm(artist)))
    return _lookup


def library_pairs(sections: Sequence[Dict[str, Any]]) -> List[tuple]:
    """Every (title, artist) a generation shows, for one batched library read."""
    out = []
    for section in sections or ():
        for row in section.get("tracks") or ():
            title = row.get("track_name") or row.get("name")
            artist = row.get("artist_name") or row.get("artist")
            if title:
                out.append((title, artist))
    return out


__all__ = [
    "SUPPORTED_HYDRATION_SOURCES",
    "UNAVAILABLE_NOT_IN_POOL",
    "UNAVAILABLE_NO_ID",
    "UNAVAILABLE_SOURCE_UNSUPPORTED",
    "empty_payload",
    "library_pairs",
    "owned_lookup_from_library",
    "payload_from_generation",
    "payload_from_legacy",
    "pool_row_to_dict",
    "section_payload",
    "track_payload",
]
