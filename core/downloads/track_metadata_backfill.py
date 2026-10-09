"""Track-position resolution + album-context hydration helper.

Lifted out of ``core/downloads/candidates.py`` to break a quiet
regression. The pre-extract code fetched detailed track data from
Spotify only when ``track_number`` was missing — the same API call
*also* backfilled the lean ``spotify_album_context`` (release_date,
total_tracks, image_url) but only as a side-effect of the
track_number branch. When a wishlist row carried a poisoned
``track_number=1`` (older payload helpers defaulted missing values
to 1), the conditional short-circuited, the API call never fired,
and the album context stayed lean — producing folders without a
year subfolder for residual per-track wishlist downloads.

The fix splits the two concerns: ``track_number`` resolution
follows its precedence chain (track_info → track object → API),
but album hydration runs whenever ``spotify_album_context`` is
missing any of release_date / total_tracks regardless of whether
``track_number`` was already known. A single API call still serves
both — the side-effect coupling is gone but the network cost
isn't paid twice.
"""

from __future__ import annotations

from utils.logging_config import get_logger
from typing import Any, Dict, NamedTuple, Optional

logger = get_logger("downloads.track_metadata_backfill")


class ResolvedTrackMetadata(NamedTuple):
    """Result of ``hydrate_download_metadata``.

    ``track_number`` is ``None`` when every source — track_info,
    track object, API — failed to produce a positive integer. The
    caller (candidates.py) treats ``None`` as "fall back to the
    setdefault(0)" path so the existing 0-floor sentinel behaviour
    is preserved.
    """

    track_number: Optional[int]
    disc_number: int
    source: str  # 'track_info' | 'track_object' | 'api' | 'none'


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str) and value.isdigit():
        coerced = int(value)
        return coerced if coerced > 0 else None
    return None


def _album_is_lean(album_context: Any) -> bool:
    """An album context is "lean" when it lacks release_date OR
    total_tracks. Both fields are required downstream:
    ``release_date`` drives the year folder, ``total_tracks`` drives
    the TRCK tag denominator + the album-vs-single classification
    in ``build_import_album_info``."""
    if not isinstance(album_context, dict):
        return True
    if not album_context.get('release_date'):
        return True
    if not album_context.get('total_tracks'):
        return True
    return False


def _backfill_album_context(
    album_context: Dict[str, Any], detailed_track: Dict[str, Any]
) -> None:
    """Copy missing fields from ``detailed_track['album']`` into the
    in-place album_context dict. Existing values are preserved —
    never overwritten — because the caller's context may carry
    fields the API doesn't (e.g. enhanced search shapes can include
    artists arrays the bare track endpoint omits)."""
    dt_album = detailed_track.get('album')
    if not isinstance(dt_album, dict) or not isinstance(album_context, dict):
        return

    for key in ('release_date', 'album_type', 'total_tracks', 'id', 'upc', 'barcode'):
        if not album_context.get(key) and dt_album.get(key):
            album_context[key] = dt_album[key]

    # the album's own credit, when the caller had none. a playlist track's
    # album often arrives without artists, and the album is filed under its
    # album artist (#1385)
    if not album_context.get('artists'):
        artists = dt_album.get('artists')
        if isinstance(artists, list) and artists:
            album_context['artists'] = artists

    if not album_context.get('image_url'):
        images = dt_album.get('images')
        if isinstance(images, list) and images:
            first = images[0]
            if isinstance(first, dict) and first.get('url'):
                album_context['image_url'] = first['url']


# Placeholder album ids used when no real source album id is known — never queryable.
_SENTINEL_ALBUM_IDS = {'explicit_album', 'from_sync_modal', ''}


def _album_id_source(source: Any) -> str:
    """the track's own metadata source, when it names one. anything else
    (a download source, a stray label) is ignored so the primary is used."""
    from core.metadata.registry import METADATA_SOURCE_PRIORITY
    name = str(source or '').strip().lower()
    return name if name in METADATA_SOURCE_PRIORITY else ''


def backfill_album_context_from_source(
    album_context: Dict[str, Any],
    primary_source: Optional[str],
    get_album_for_source_fn: Any,
    album_source: Optional[str] = None,
) -> bool:
    """Hydrate a lean album context from the user's PRIMARY metadata source (#915).

    Post-processing's only album backfill (:func:`hydrate_download_metadata`) goes through
    ``spotify_client.get_track_details`` — Spotify-only. An iTunes/Deezer-primary user's
    download therefore kept a lean context (no ``release_date``), so the path dropped the
    ``$year`` and the date defaulted to ``YYYY-01-01`` — until they ran a Reorganize, which
    reads the full album from the PRIMARY source. This closes that gap by doing the same:
    fetch the full album from the primary source and backfill, so a download's pathing/tags
    match what a later reorganize would produce.

    ``get_album_for_source_fn(source, album_id)`` is injected (the real one is
    ``core.metadata.album_tracks.get_album_for_source``) so this stays pure + testable.
    ``album_source`` is the source the track (and so its album id) came from; it wins over
    the primary when it names a metadata source.
    No-op when: the context is already complete; the album's source is spotify (the existing
    track-details path covers it); or no real source album id is present. Returns True when
    it filled anything. Never raises — a backfill failure must not break a download.
    """
    if not isinstance(album_context, dict):
        return False
    # no album credit counts as lean here too (#1385), but only here: the
    # spotify track-details hydrate keeps its own trigger, so this never adds
    # spotify calls
    if not _album_is_lean(album_context) and album_context.get('artists'):
        return False
    # the album id belongs to the source the track came from. a deezer search
    # result on a spotify-primary install carries a deezer album id, asking the
    # primary with it finds nothing (#1605)
    source = _album_id_source(album_source) or primary_source
    if not source or source == 'spotify':
        return False
    album_id = album_context.get('id')
    if not album_id or str(album_id) in _SENTINEL_ALBUM_IDS:
        return False
    try:
        album = get_album_for_source_fn(source, str(album_id))
    except Exception as e:  # noqa: BLE001 — defensive: never let backfill break a download
        logger.warning("[Context] %s album backfill failed: %s", source, e)
        return False
    if not isinstance(album, dict):
        return False
    before = album_context.get('release_date')
    _backfill_album_context(album_context, {'album': album})
    if album_context.get('release_date') and album_context.get('release_date') != before:
        logger.info(
            "[Context] Hydrated lean album context from %s "
            "(release_date=%r, total_tracks=%r)",
            source, album_context.get('release_date'), album_context.get('total_tracks'),
        )
    return True


def hydrate_download_metadata(
    track: Any,
    track_info: Any,
    spotify_album_context: Dict[str, Any],
    spotify_client: Any,
) -> ResolvedTrackMetadata:
    """Resolve track position and hydrate lean album context.

    Steps:
      1. ``track_info['track_number']`` when positive
      2. ``track.track_number`` when truthy
      3. ``spotify_client.get_track_details(track.id)`` — fires when
         EITHER track_number unresolved OR album_context lean. The
         same call serves both concerns; only one round-trip per task.

    ``spotify_album_context`` is mutated in place when API returns
    richer data. Returns the resolved track_number / disc_number /
    source. ``track_number=None`` signals "no usable value found";
    the caller decides whether to floor it to 1 or leave 0.
    """
    ti = track_info if isinstance(track_info, dict) else {}

    # Step 1: track_info top-level — wishlist + frontend payloads.
    tn = _positive_int(ti.get('track_number'))
    if tn is not None:
        dn = _positive_int(ti.get('disc_number')) or 1
        source = 'track_info'
    else:
        tn = None
        dn = 1
        source = 'none'

    # Step 2: track object — Spotify Track dataclass from search.
    if tn is None:
        track_tn = getattr(track, 'track_number', None)
        coerced = _positive_int(track_tn)
        if coerced is not None:
            tn = coerced
            dn = _positive_int(getattr(track, 'disc_number', None)) or 1
            source = 'track_object'

    needs_api_for_tn = tn is None
    needs_api_for_album = _album_is_lean(spotify_album_context)
    track_id = getattr(track, 'id', None)

    if (needs_api_for_tn or needs_api_for_album) and track_id:
        try:
            detailed = spotify_client.get_track_details(track_id)
        except Exception as e:  # noqa: BLE001 — defensive log + continue
            logger.error("[Context] API track details failed: %s", e)
            detailed = None

        if isinstance(detailed, dict):
            if needs_api_for_tn:
                api_tn = _positive_int(detailed.get('track_number'))
                if api_tn is not None:
                    tn = api_tn
                    dn = _positive_int(detailed.get('disc_number')) or 1
                    source = 'api'
                    logger.info(
                        "[Context] Resolved track_number=%d disc_number=%d from API",
                        tn, dn,
                    )

            if needs_api_for_album and isinstance(spotify_album_context, dict):
                _backfill_album_context(spotify_album_context, detailed)
                logger.info(
                    "[Context] Backfilled album context from API "
                    "(release_date=%r, total_tracks=%r)",
                    spotify_album_context.get('release_date'),
                    spotify_album_context.get('total_tracks'),
                )

    return ResolvedTrackMetadata(track_number=tn, disc_number=dn, source=source)


__all__ = [
    'ResolvedTrackMetadata',
    'hydrate_download_metadata',
    'backfill_album_context_from_source',
]
