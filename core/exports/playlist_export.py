"""Orchestrate resolving a playlist's tracks to recording MBIDs for export (#903).

This is the testable heart of the export job: walk the playlist's tracks, resolve each to a
MusicBrainz recording MBID via an injected ``resolve_fn`` (which the job wires to the
cache -> DB -> file -> MusicBrainz waterfall), dedup repeated songs within the run so they
only cost one resolution, build the ordered "pseudo-playlist" of resolved tracks, and tally
live stats (resolved / unmatched / per-source / deduped) for the on-card status display.

Pure: all I/O (DB, file reads, MusicBrainz, cache) is behind ``resolve_fn`` and the optional
``on_progress`` callback, so the dedup + accounting logic is unit-testable without any
network or database. The returned ``resolved`` list feeds straight into ``jspf_export``.
"""

from __future__ import annotations

import inspect

from typing import Any, Callable, Dict, List, Optional, Tuple

from core.exports.mbid_resolver import normalize_key

# resolve_fn(artist, title) -> (recording_mbid|None, source_label|None)
# May optionally accept a third `track` argument (the full mirrored-playlist row) — see
# `_resolve_fn_wants_track` below. Existing 2-arg resolve_fns are unaffected.
ResolveFn = Callable[..., Tuple[Optional[str], Optional[str]]]
ProgressFn = Callable[[int, int, Dict[str, Any]], None]


def _resolve_fn_wants_track(resolve_fn: Callable[..., Any]) -> bool:
    """Whether ``resolve_fn`` accepts a third (``track``) argument, so callers that
    still define ``resolve_fn(artist, title)`` — every pre-existing test and the
    service-export resolver — keep being called exactly as before."""
    try:
        params = list(inspect.signature(resolve_fn).parameters.values())
    except (TypeError, ValueError):
        return False
    if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in params):
        return True
    positional = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    return len(positional) >= 3


def _field(track: Dict[str, Any], *names: str) -> str:
    """First non-empty value among ``names`` (handles both playlist + LB-cache shapes)."""
    for n in names:
        v = track.get(n)
        if v:
            return str(v)
    return ""


def resolve_playlist_tracks(
    tracks: List[Dict[str, Any]],
    resolve_fn: ResolveFn,
    *,
    on_progress: Optional[ProgressFn] = None,
    id_key: str = "recording_mbid",
) -> Dict[str, Any]:
    """Resolve every track to an ID and build the export pseudo-playlist.

    ``resolve_fn(artist, title) -> (id, source)`` returns whatever ID the target needs —
    a MusicBrainz recording MBID for ListenBrainz/JSPF (the default), or a Spotify/Deezer
    track ID for service export. ``id_key`` names the field that ID lands under in each
    resolved entry (defaults to ``recording_mbid`` so existing LB/JSPF callers are
    untouched). The dedup + stats + ordering logic is identical regardless of ID type.

    ``tracks`` items may use ``artist``/``artist_name`` and ``title``/``track_name`` and
    ``album``/``album_name`` (both the mirrored-playlist and LB-cache shapes are accepted).

    Returns ``{"resolved": [...], "stats": {...}}`` where each resolved entry is
    ``{artist, title, album, <id_key>}`` (the ID is None when unmatched), in original
    order, and stats carries ``total, resolved, unmatched, deduped, by_source``.
    """
    total = len(tracks or [])
    # Memoized per (artist, title) text, NOT per-track — two rows with identical
    # artist+title but different source ISRCs are the same song for playlist purposes,
    # so the second one reuses the first's resolution rather than paying for (or
    # re-checking) its own ISRC/MB lookup.
    memo: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
    resolved: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {
        "total": total, "resolved": 0, "unmatched": 0, "deduped": 0, "by_source": {},
    }
    wants_track = _resolve_fn_wants_track(resolve_fn)

    for i, t in enumerate(tracks or []):
        if not isinstance(t, dict):
            t = {}
        artist = _field(t, "artist", "artist_name", "creator")
        title = _field(t, "title", "track_name", "name")
        album = _field(t, "album", "album_name", "release_name")
        key = normalize_key(artist, title)

        if key in memo:
            mbid, source = memo[key]
            stats["deduped"] += 1
            fresh = False
        else:
            mbid, source = resolve_fn(artist, title, t) if wants_track else resolve_fn(artist, title)
            memo[key] = (mbid, source)
            fresh = True

        resolved.append({"artist": artist, "title": title, "album": album, id_key: mbid})

        if mbid:
            stats["resolved"] += 1
            if fresh and source:
                stats["by_source"][source] = stats["by_source"].get(source, 0) + 1
        else:
            stats["unmatched"] += 1

        if on_progress is not None:
            try:
                on_progress(i + 1, total, stats)
            except Exception:  # noqa: S110 — a progress-display error must never fail the export
                pass

    return {"resolved": resolved, "stats": stats}


__all__ = ["resolve_playlist_tracks"]
