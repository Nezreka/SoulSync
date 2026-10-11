"""What a Library-v2 Interactive Search looks for, and the download
pipeline's verdict on what it finds.

The search takes the two angles an automatic download takes -- the track
itself, and the release it is on -- and judges every hit with the same
``evaluate_candidates`` the pipeline uses, so the modal can show what the
pipeline would take and keep the rest one click away.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.entity_search")


def entity_search_queries(lib2_context: Mapping[str, Any],
                          clean: Optional[Callable[[str], str]] = None) -> List[str]:
    """``artist track`` and ``artist release`` for a track, ``artist release``
    for a release; a single named after its track searches once."""
    clean = clean or (lambda text: text)
    artist = str(lib2_context.get("artist_name") or "").strip()
    track_artist = (lib2_context.get("track_artists") or [artist])[0]
    candidates = []
    if lib2_context.get("track_title"):
        candidates.append(f"{track_artist} {clean(str(lib2_context['track_title']))}")
    if lib2_context.get("album_name"):
        candidates.append(f"{artist} {clean(str(lib2_context['album_name']))}")
    queries, seen = [], set()
    for query in (" ".join(q.split()) for q in candidates):
        if query and query.casefold() not in seen:
            seen.add(query.casefold())
            queries.append(query)
    return queries


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
        from core.downloads.pinned_batch import track_object
        target = track_object({
            "name": lib2_context.get("track_title") or "",
            "artists": lib2_context.get("track_artists") or [lib2_context.get("artist_name")],
            "album": lib2_context.get("album_name") or "",
            "duration_ms": lib2_context.get("duration_ms") or 0,
        })
        return {id(row): decision.to_dict() for row, decision in validation.evaluate_candidates(
            tracks, target, query, lib2_context.get("quality_profile_id"))}

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


def merge_results(result_lists: Iterable[List[dict]]) -> List[dict]:
    """One row per file or folder across the queries; a hit one query's judge
    accepted beats the same hit another rejected. Best quality first."""
    picked: Dict[tuple, dict] = {}
    for results in result_lists:
        for row in results:
            key = (row.get("username"), row.get("filename") or row.get("album_path")
                   or row.get("album_title"))
            held = picked.get(key)
            if held is None or (_accepted(row) and not _accepted(held)):
                picked[key] = row
    return sorted(picked.values(), key=lambda row: row.get("quality_score", 0), reverse=True)


def _accepted(row: Mapping[str, Any]) -> bool:
    return (row.get("decision") or {}).get("accepted", True) is not False


__all__ = ["entity_search_queries", "judge_results", "merge_results"]
