"""Pure-function resolver for the import pipeline's track_number lookup.

Lifted from ``core/imports/pipeline.py`` so the multi-source fallback
chain can be unit-tested in isolation. The pipeline integration is
one call site that delegates to ``resolve_track_number`` and then
applies the >=1 floor as the last-resort default.

Resolution order (first valid positive int wins):

1. ``album_info.track_number`` — set by upstream album-info builders
   when they have authoritative track position data (e.g. the
   album-bundle dispatch from ``core/downloads/master.py``).
2. ``track_info.track_number`` — Spotify-shaped track dict carried
   on the per-task download context. Populated by the per-track
   flow when the wishlist payload still has Spotify's position.
3. ``track_info.spotify_data.track_number`` — nested spotify_data
   dict inside track_info; common for wishlist-loop payloads that
   wrapped the source spotify dict under an outer envelope.
4. ``extract_track_number_from_filename(file_path)`` — last resort
   when none of the metadata sources carried the value.

Pre-fix, the pipeline only consulted ``album_info`` and fell straight
to the filename when it was None. That broke for VA-collection
source files like ``417 Fountains of Wayne - Stacys Mom.flac`` where
the leading number isn't the album track position — extract returned
None or the wrong number, post-process defaulted to 1, and every
such wishlist import landed as ``01 - <title>`` regardless of the
real source position.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from core.imports.filename import extract_explicit_track_number


def _coerce_positive(value: Any) -> Optional[int]:
    """Coerce ``value`` to a positive int, or return None when the
    value is missing / non-numeric / non-positive. Centralised so
    every check in ``resolve_track_number`` applies the same rules."""
    try:
        v = int(value)
        return v if v >= 1 else None
    except (TypeError, ValueError):
        return None


def _coerce_spotify_data(track_info: Any) -> dict:
    """Extract the nested ``spotify_data`` dict from a track_info
    payload, coercing string-JSON shapes and bad inputs to an empty
    dict so the caller can use ``.get`` safely."""
    if not isinstance(track_info, dict):
        return {}
    raw = track_info.get('spotify_data')
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


def read_embedded_track_number(file_path: str) -> Optional[int]:
    """Read the track position from a downloaded audio file's own tags.

    Streaming sources (Deezer/deemix, Qobuz, Tidal) and most Soulseek
    uploads write the correct album position into the file itself. That
    tag is authoritative for the *source's* idea of the track's place on
    its album — more reliable than a filename guess — so the resolver
    consults it before falling back to the filename / default-1 floor.

    Issue #874-adjacent / "Track 01" bug: a single Deezer track is matched
    via Deezer's ``/search/track`` endpoint, which omits ``track_position``
    (core/deezer_client.py), so the metadata context never carried the
    real number — but the downloaded file *does* (deemix wrote it). This
    recovers it with no network call.

    Returns a positive int, or None when the file has no usable
    tracknumber tag / can't be read. Never raises. Handles the common
    ``"2/15"`` (number/total) form by taking the leading number.
    """
    if not file_path:
        return None
    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(file_path, easy=True)
        if audio is None:
            return None
        raw = audio.get('tracknumber')
        if isinstance(raw, list):
            raw = raw[0] if raw else None
        if raw is None:
            return None
        # "2/15" -> "2"; bare "2" -> "2".
        text = str(raw).split('/', 1)[0].strip()
        return _coerce_positive(text)
    except Exception:
        return None


def resolve_track_number(
    album_info: Any,
    track_info: Any,
    file_path: str,
    embedded_track_number: Any = None,
) -> Optional[int]:
    """Walk the resolution chain and return the first valid positive
    int found, or None when every source is missing / unusable.

    Order: album_info -> track_info -> nested spotify_data -> filename ->
    ``embedded_track_number`` (the source-written file tag, when the caller
    supplies it). Caller is responsible for the final default-1 floor —
    leaving that out of this function so tests can pin "everything missing
    returns None" separate from the floor behaviour.

    ``embedded_track_number`` is passed in (not read here) so this stays a
    pure function — the file I/O lives in :func:`read_embedded_track_number`.
    It is consulted **last**, only when every other source came up empty, so
    it can never override a value the pre-fix resolver already produced — it
    only fills the gap that would otherwise hit the default-1 floor.
    """
    album_info = album_info if isinstance(album_info, dict) else {}
    track_info = track_info if isinstance(track_info, dict) else {}
    spotify_data = _coerce_spotify_data(track_info)

    resolved = (
        _coerce_positive(album_info.get('track_number'))
        or _coerce_positive(track_info.get('track_number'))
        or _coerce_positive(spotify_data.get('track_number'))
    )
    if resolved is not None:
        return resolved

    # Filename fallback — use the EXPLICIT extractor variant which
    # returns 0 when no numeric prefix is recognised (vs. the default
    # variant that silently returns 1 for the unknown case). We want
    # "unknown" to stay unknown here so the pipeline's final
    # default-1 floor is the single source of that fallback —
    # otherwise this resolver would silently fill 1 and the
    # downstream floor logic would have no effect.
    if file_path:
        try:
            from_filename = extract_explicit_track_number(file_path)
        except Exception:
            from_filename = None
        ff = _coerce_positive(from_filename)
        if ff is not None:
            return ff

    # Embedded source-written file tag is consulted LAST — only when every
    # other source (metadata + the ripped-album "NN - Title" filename) came
    # up empty. This is deliberate: it can ONLY fill the gap that would
    # otherwise hit the caller's default-1 floor, so it never overrides a
    # value the pre-fix resolver would have used. A correctly-named file
    # with a stale/wrong embedded tag is therefore never regressed.
    return _coerce_positive(embedded_track_number)


def normalize_disc_number(value) -> int:
    """Coerce a disc value to a positive int, defaulting to 1.

    Every track in a multi-disc album MUST carry a disc number, or Jellyfin/Plex
    leave the disc-less ones floating ungrouped above the disc sections (Sokhi's
    "tracks 3/9/15 at the top"). Upstream sources can hand back 0, None, '', or a
    non-numeric string for some tracks — especially when a track resolved to a
    different edition than its siblings — and the tag-writer only wrote the disc
    tag when it was truthy, so those tracks lost it entirely on the clear-then-
    rewrite. Flooring to >=1 here means a track is never written disc-less.
    """
    try:
        n = int(value)                      # int, float, or clean int-string
    except (TypeError, ValueError):
        try:
            n = int(float(str(value).strip()))   # tolerate "2.0"
        except (TypeError, ValueError):
            return 1
    return n if n >= 1 else 1


def resolve_disc_for_track(original_search, album_info) -> int:
    """The disc number for a track — resolved IDENTICALLY for the 'Disc N' folder
    (import pipeline) and the embedded tag (metadata.source), so the two can never
    disagree.

    Sokhi: the pipeline synced the resolved track_number into album_info (so the
    folder matched the tag) but never did the same for disc — the folder used
    album_info's original disc (often 1) while the tag took the per-track disc
    (e.g. 2/3 from a MusicBrainz multi-medium release). Result: a disc-2/3 track
    landed in the Disc 1 folder, collapsing every disc's tracks into one folder.

    Returns the first VALID positive disc — the per-track search's, else the album
    context's — else 1. A falsy/unknown (0/None/'') per-track disc falls through to
    the album rather than flooring early. Single source of truth so both call sites
    stay in lockstep."""
    for src in ((original_search or {}), (album_info or {})):
        raw = src.get("disc_number")
        try:
            n = int(raw)
        except (TypeError, ValueError):
            try:
                n = int(float(str(raw).strip()))
            except (TypeError, ValueError):
                continue
        if n >= 1:
            return n
    return 1
