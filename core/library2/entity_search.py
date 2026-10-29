"""What a Library-v2 Interactive Search looks for, and the download
pipeline's verdict on what it finds.

A track is searched exactly like an automatic download searches it: the same
query ladder (core/downloads/search_queries.py), asked of every source, with
the song as the hint release sources use to add its artist + album query.
Every hit is judged with the same ``evaluate_candidates`` the pipeline uses, so
the modal can show what the pipeline would take and keep the rest at hand.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.entity_search")


def _target_track(lib2_context: Mapping[str, Any]):
    from core.downloads.pinned_batch import track_object
    return track_object({
        "name": lib2_context.get("track_title") or "",
        "artists": lib2_context.get("track_artists") or [lib2_context.get("artist_name")],
        "album": lib2_context.get("album_name") or "",
        "duration_ms": lib2_context.get("duration_ms") or 0,
    })


def entity_search_queries(lib2_context: Mapping[str, Any], matching_engine: Any) -> List[str]:
    """A track's automatic-download query ladder; ``artist release`` for a
    release."""
    if lib2_context.get("track_title"):
        from core.downloads.search_queries import automatic_search_queries
        return automatic_search_queries(matching_engine, _target_track(lib2_context))
    query = " ".join(f"{lib2_context.get('artist_name') or ''} {lib2_context.get('album_name') or ''}".split())
    return [query] if query else []


def entity_track_hint(lib2_context: Mapping[str, Any]) -> Optional[Dict[str, Any]]:
    """The song for sources that search better with it (core/downloads/
    track_hint.py) -- without the catalogue context a download carries, whose
    hydration would write to the library during a search."""
    if not lib2_context.get("track_title"):
        return None
    return {"title": lib2_context["track_title"],
            "artist": (lib2_context.get("track_artists") or [lib2_context.get("artist_name")])[0],
            "album": lib2_context.get("album_name") or "", "deezer_id": None}


def entity_search_target(lib2_context: Mapping[str, Any]) -> Dict[str, Any]:
    """What the window says it is looking for: the song and everyone on it."""
    return {"title": lib2_context.get("track_title"), "album": lib2_context.get("album_name"),
            "artists": list(lib2_context.get("track_artists") or [lib2_context.get("artist_name")])}


def _album_credits(db, lib2_context: Mapping[str, Any]) -> List[str]:
    from core.library2.grab_context import album_grab_tracks
    names = [str(lib2_context.get("artist_name") or "")]
    for track in album_grab_tracks(db, lib2_context):
        names.extend(track["track_artists"])
    return [name for name in dict.fromkeys(names) if name]


def judge_results(db, lib2_context: Mapping[str, Any], tracks: Iterable[Any],
                  albums: Iterable[Any], query: str) -> Dict[int, dict]:
    """``{id(result): decision}`` for one source's hits.

    A track search runs the pipeline's own judge against the library track
    with every credited artist, so a release filed under the second artist
    ("Topic x A7S") is still that song. A release search only checks the
    artist: there is no single track to hold a folder to.
    """
    from core.downloads import decisions, validation

    tracks, albums = list(tracks or []), list(albums or [])
    if lib2_context.get("track_id") is not None:
        if not tracks:
            return {}
        return {id(row): decision.to_dict() for row, decision in validation.evaluate_candidates(
            tracks, _target_track(lib2_context), query, lib2_context.get("quality_profile_id"))}

    from core.imports.compilation import is_various_artists_name
    if validation.matching_engine is None or is_various_artists_name(lib2_context.get("artist_name")):
        return {}
    credits = _album_credits(db, lib2_context)
    verdicts = {}
    for row in tracks + albums:
        artist = str(getattr(row, "artist", None) or "").strip()
        if not artist or validation._release_artist_matches(credits, artist):
            verdicts[id(row)] = decisions.accept().to_dict()
        else:
            verdicts[id(row)] = decisions.reject(
                "artist_mismatch", f"{artist} vs {', '.join(credits)}").to_dict()
    return verdicts


__all__ = ["entity_search_queries", "entity_search_target", "entity_track_hint", "judge_results"]
