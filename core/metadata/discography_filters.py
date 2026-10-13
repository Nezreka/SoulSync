"""Track-level filters for the user-facing Download Discography flow.

GitHub issue #559 (trackhacs): clicking "Download Discography" on an
artist also pulled in tracks where the artist's name appeared in the
title of someone else's song. Two failure modes underneath:

1. **Cross-artist tracks.** Spotify's `artist_albums` endpoint returns
   compilation / appears_on / various-artists albums where the requested
   artist is featured on one or two tracks. The endpoint then added
   *every* track from those albums to the wishlist, including tracks by
   unrelated artists that just happened to mention the requested artist
   in the title.

2. **Remix / live / acoustic / instrumental versions.** The watchlist
   scanner has user-toggleable filters for these (default: exclude),
   stored at `watchlist.global_include_*`. The discography backfill
   repair job already honors them. The user-facing Download Discography
   endpoint did not — those filters never fired for one-off discography
   downloads, so users got remix-ladder bloat.

These helpers live alongside the existing `core.metadata.discography`
because they belong to the same conceptual layer (discography fetch
results, pre-wishlist) and are independently testable. The watchlist
content-type detectors (``is_remix_version`` etc.) are reused from
``core.watchlist_scanner`` rather than re-implemented — same patterns,
single source of truth.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

# Split a combined artist credit into its individual artists. Sources like
# iTunes return collabs as ONE string ("TRVNSPORTER, Narvent & SKVLENT"), not a
# list — so an exact full-string compare drops every collaborator's discography
# entry (#830). Split on the common credit separators; " and " / " with " are
# deliberately excluded (too many real band names contain them).
_ARTIST_CREDIT_SPLIT_RE = re.compile(
    r"\s*[,&;/]\s*|\s+(?:feat\.?|ft\.?|featuring|vs\.?|x)\s+",
    re.IGNORECASE,
)


def _artist_credit_components(name: str) -> List[str]:
    """Return the individual artist names within a (possibly combined) credit,
    always including the full string itself (so exact band names with internal
    separators still match)."""
    name = (name or "").strip()
    if not name:
        return []
    parts = [name]
    parts.extend(p.strip() for p in _ARTIST_CREDIT_SPLIT_RE.split(name) if p.strip())
    return parts

from core.watchlist_scanner import (
    is_acoustic_version,
    is_compilation_album,
    is_instrumental_version,
    is_live_version,
    is_remix_version,
    matches_custom_exclude_terms,
)
from core.library.existing_album_folder import _release_kinds_compatible
from utils.logging_config import get_logger

logger = get_logger("metadata.discography_filters")


def track_artist_matches(track_artists: Any, requested_artist_name: str) -> bool:
    """Return True if the requested artist appears in the track's
    artists list (case-insensitive exact-name membership).

    `track_artists` can be the list-of-strings shape produced by
    ``core.metadata.album_tracks._normalize_track_artists`` (which is
    what the discography fetch returns), or the list-of-dicts shape
    that some upstreams pass directly. Both are accepted.

    Returns True for primary-artist tracks AND feature/collab appearances —
    the requested artist need only be one of the credited artists, INCLUDING
    when a source (iTunes, etc.) packs the collab into one combined string like
    "TRVNSPORTER, Narvent & SKVLENT" (#830). Only drops tracks where the
    requested artist isn't credited at all (the cross-artist compilation case
    from #559).
    """
    if not requested_artist_name:
        # No artist to compare against — don't filter; let the caller
        # decide. Defensive: avoids dropping every track when the
        # caller forgot to pass the artist name.
        return True

    target = requested_artist_name.strip().lower()
    if not target:
        return True

    if not track_artists:
        return False

    for entry in track_artists:
        if isinstance(entry, dict):
            name = entry.get('name', '') or ''
        else:
            name = str(entry or '')
        # Match the requested artist as a component of the credit, so combined
        # collab strings ("A, B & C") keep B's discography entry. Component
        # matching is still exact per-name, so true contamination (the artist
        # genuinely absent) is dropped exactly as before.
        for component in _artist_credit_components(name):
            if component.strip().lower() == target:
                return True

    return False


def content_type_skip_reason(
    track_name: str,
    album_name: str,
    settings: Dict[str, Any],
) -> Optional[str]:
    """Return a short skip-reason string if the track is a content type
    the user has chosen to exclude, else None.

    `settings` is a dict keyed by the same names as the watchlist
    globals (``include_live`` / ``include_remixes`` / ``include_acoustic``
    / ``include_instrumentals``). All default to False — i.e. exclude
    by default — matching the watchlist scanner's default contract.
    """
    if not settings.get('include_live', False) and is_live_version(track_name, album_name):
        return 'live'
    if not settings.get('include_remixes', False) and is_remix_version(track_name, album_name):
        return 'remix'
    if not settings.get('include_acoustic', False) and is_acoustic_version(track_name, album_name):
        return 'acoustic'
    if not settings.get('include_instrumentals', False) and is_instrumental_version(track_name, album_name):
        return 'instrumental'
    return None


def load_global_content_filter_settings(config_manager: Any) -> Dict[str, Any]:
    """Read the four watchlist content-type globals from config.

    Centralises the key names so the endpoint and the helper agree on
    where the settings live. All four default to False (exclude) — same
    contract as the watchlist scanner.
    """
    if config_manager is None:
        return {
            'include_live': False,
            'include_remixes': False,
            'include_acoustic': False,
            'include_instrumentals': False,
        }
    try:
        return {
            'include_live': bool(config_manager.get('watchlist.global_include_live', False)),
            'include_remixes': bool(config_manager.get('watchlist.global_include_remixes', False)),
            'include_acoustic': bool(config_manager.get('watchlist.global_include_acoustic', False)),
            'include_instrumentals': bool(config_manager.get('watchlist.global_include_instrumentals', False)),
        }
    except Exception:
        return {
            'include_live': False,
            'include_remixes': False,
            'include_acoustic': False,
            'include_instrumentals': False,
        }


def _normalize_artist_name(name: str) -> str:
    """Fold an artist name for watchlist-row matching: strip diacritics
    ("Étienne" ~ "Etienne"), casefold, trim. Watchlist rows store the name
    as-added (source-dependent), so exact matching misses the messy
    real-world variants. Used only as a fallback behind an exact match,
    and only when exactly one row folds to the target — ambiguity yields
    no label rather than the wrong artist's settings."""
    import unicodedata

    folded = unicodedata.normalize('NFKD', str(name or ''))
    folded = ''.join(c for c in folded if not unicodedata.combining(c))
    return folded.casefold().strip()


def _watchlist_cfg_get(key: str, default: Any = None) -> Any:
    """Read one watchlist config value, defensive against exotic config
    managers (some test doubles only implement get_active_media_server)."""
    try:
        from core.settings import config_manager as _cfg
    except Exception:
        return default
    try:
        return _cfg.get(key, default)
    except Exception:
        return default


def resolve_watchlist_content_settings(
    db: Any,
    artist_name: str,
    profile_id: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    """#1550: effective watchlist filter preferences for an artist, or None.

    Returns the settings dict the scanner would apply to this artist's
    releases — the per-artist row, or the globals when
    ``watchlist.global_override_enabled`` (same resolution as
    ``WatchlistScanner._apply_global_watchlist_overrides``). Returns None
    when the artist isn't watched at all (the scan never runs for them, so
    there is no filter mismatch to explain) or when the lookup fails —
    fail silent, never mislabel.

    Split out from the reason computation so callers on a hot path (the
    artist-page completion stream) resolve once per artist instead of once
    per release.
    """
    if not artist_name:
        return None
    try:
        if profile_id is None:
            try:
                from core.profile_context import get_current_profile_id

                profile_id = get_current_profile_id() or 1
            except Exception:
                profile_id = 1
        rows = db.get_watchlist_artists(profile_id=int(profile_id)) or []
    except Exception:
        return None

    target_exact = artist_name.strip()
    target_norm = _normalize_artist_name(artist_name)
    artist_row = None
    norm_candidates = []
    for row in rows:
        row_name = str(getattr(row, 'artist_name', '') or '')
        if row_name.strip() == target_exact:
            # Exact match wins outright — never let a folded collision
            # override it.
            artist_row = row
            break
        if _normalize_artist_name(row_name) == target_norm:
            norm_candidates.append(row)
    else:
        # Exactly one folded match: safe to use. Zero, or several distinct
        # rows folding together ("José" vs "Jose" as separate artists):
        # ambiguous — yield no label rather than risk the WRONG artist's
        # settings producing a wrong label.
        if len(norm_candidates) == 1:
            artist_row = norm_candidates[0]
    if artist_row is None:
        return None

    # Same effective-preference resolution as
    # WatchlistScanner._apply_global_watchlist_overrides: the global
    # override wins when enabled, otherwise the artist's own row. All
    # config reads go through _watchlist_cfg_get (defensive against exotic
    # config managers); the adapter below feeds the shared loader.
    override_enabled = bool(_watchlist_cfg_get('watchlist.global_override_enabled', False))
    # Custom exclusion terms are global-only (the scan reads them from
    # config per track); resolve once here so the hot path doesn't.
    exclude_terms: list = []
    try:
        terms_str = _watchlist_cfg_get('watchlist.exclude_terms', '') or ''
        exclude_terms = [t.strip() for t in str(terms_str).split(',') if t.strip()]
    except Exception:
        exclude_terms = []

    class _CfgAdapter:
        def get(self, key, default=None):
            return _watchlist_cfg_get(key, default)

    if override_enabled:
        settings = load_global_content_filter_settings(_CfgAdapter())
        settings['include_compilations'] = bool(
            _watchlist_cfg_get('watchlist.global_include_compilations', False)
        )
        settings['include_albums'] = bool(_watchlist_cfg_get('watchlist.global_include_albums', True))
        settings['include_eps'] = bool(_watchlist_cfg_get('watchlist.global_include_eps', True))
        settings['include_singles'] = bool(_watchlist_cfg_get('watchlist.global_include_singles', True))
        settings['exclude_terms'] = exclude_terms
        return settings
    settings = {
        'include_live': bool(getattr(artist_row, 'include_live', False)),
        'include_remixes': bool(getattr(artist_row, 'include_remixes', False)),
        'include_acoustic': bool(getattr(artist_row, 'include_acoustic', False)),
        'include_instrumentals': bool(getattr(artist_row, 'include_instrumentals', False)),
        'include_compilations': bool(getattr(artist_row, 'include_compilations', False)),
        # Release-type prefs default True (include) — the inverse of the
        # content-type prefs — matching the scanner's getattr defaults.
        'include_albums': bool(getattr(artist_row, 'include_albums', True)),
        'include_eps': bool(getattr(artist_row, 'include_eps', True)),
        'include_singles': bool(getattr(artist_row, 'include_singles', True)),
        'exclude_terms': exclude_terms,
    }
    return settings


def release_kind_for_scan(expected_tracks: Any) -> Optional[str]:
    """#1550: which release-type bucket the SCANNER would put a release in.

    Mirrors ``WatchlistScanner._should_include_release``: 7+ tracks ->
    'albums', 4-6 -> 'eps', 1-3 -> 'singles'. Returns None when the count is
    unknown (0/unparseable): the scan ``continue``s on empty track lists
    before classifying, so an unknown count means the scan never reaches
    the release-type gate — the caller must skip that gate rather than
    guess a bucket.
    """
    try:
        n = int(expected_tracks or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 7:
        return 'albums'
    if n >= 4:
        return 'eps'
    if n >= 1:
        return 'singles'
    return None


def content_exclusion_reason(
    settings: Dict[str, Any],
    track_name: str,
    album_name: str,
    release_kind: Optional[str],
) -> Optional[str]:
    """#1550: why the scanner would skip this release, or None.

    Pure function over already-resolved settings. Check order mirrors the
    scanner: the release-type gate (``_should_include_release``) runs
    before any per-track filter, then compilation (the album-level gate in
    ``_should_include_track``), then the track-level content-type filters,
    then the user's custom exclusion terms (the scan's final gate).

    ``release_kind`` is None when the track count is unknown — the
    release-type gate is then skipped, not guessed.

    ``track_name``/``album_name`` are the RELEASE's own titles: for a
    single-track single that's exactly what the scanner judges; for a
    multi-track release it yields the album-level verdict (release-type
    gate + album-title-driven content gates), which is all that's
    attributable at release granularity. Track-title-specific exclusions
    on multi-track releases are unknowable here — fail silent, no label.

    Reason codes: 'albums' | 'eps' | 'singles' | 'compilation' | 'live' |
    'remix' | 'acoustic' | 'instrumental' | 'custom'.
    """
    if not settings:
        return None
    if release_kind == 'albums' and not settings.get('include_albums', True):
        return 'albums'
    if release_kind == 'eps' and not settings.get('include_eps', True):
        return 'eps'
    if release_kind == 'singles' and not settings.get('include_singles', True):
        return 'singles'
    if not settings.get('include_compilations', False) and is_compilation_album(album_name or ''):
        return 'compilation'
    reason = content_type_skip_reason(track_name or '', album_name or '', settings)
    if reason:
        return reason
    exclude_terms = settings.get('exclude_terms') or []
    if exclude_terms and matches_custom_exclude_terms(track_name or '', album_name or '', exclude_terms):
        return 'custom'
    return None


def watchlist_exclusion_reason(
    db: Any,
    artist_name: str,
    track_name: str,
    album_name: str,
    *,
    release_kind: Optional[str],
    profile_id: Optional[int] = None,
) -> Optional[str]:
    """#1550: would the watchlist scanner skip this track for THIS artist?

    Convenience wrapper: resolves the artist's effective settings, then
    computes the reason. ``release_kind`` is required (no guessing the
    bucket) — use ``release_kind_for_scan`` to derive it. Callers on a hot
    path should resolve once via ``resolve_watchlist_content_settings``
    and reuse ``content_exclusion_reason``.
    """
    if not artist_name or not track_name:
        return None
    settings = resolve_watchlist_content_settings(db, artist_name, profile_id=profile_id)
    return content_exclusion_reason(settings or {}, track_name, album_name, release_kind)


def attach_watchlist_exclusion(
    event: Dict[str, Any],
    settings: Optional[Dict[str, Any]],
    release_name: str,
) -> Dict[str, Any]:
    """#1550: stamp 'watchlist_excluded' onto a completion event dict.

    Mutates ``event`` in place and returns it. When the release is missing
    and settings are available the key is ALWAYS set — to the reason code,
    or to None when no exclusion is attributable — so a later event for
    the same release correctly clears a stale label instead of leaving it
    stuck. Otherwise the event is returned untouched.
    """
    if not isinstance(event, dict) or event.get('status') != 'missing' or not settings:
        return event
    kind = release_kind_for_scan(event.get('expected_tracks'))
    event['watchlist_excluded'] = content_exclusion_reason(
        settings, release_name or '', release_name or '', kind
    )
    return event


def track_already_owned(
    db: Any,
    track_name: str,
    requested_artist: str,
    album_name: str,
    server_source: Optional[str],
    confidence_threshold: float = 0.7,
    candidate_tracks: Optional[List[Any]] = None,
    candidate_tracks_exhaustive: bool = False,
) -> bool:
    """Return True if the track is already in the user's library.

    Discord report (Skowl): clicking "Download Discography" twice on
    the same artist re-queued every track instead of skipping the
    half already on disk. Trace: the endpoint added each track to the
    wishlist via ``db.add_to_wishlist``, which only dedups against the
    wishlist itself — once a wishlist track downloads it leaves the
    wishlist, so the second discography click re-inserted everything.

    The discography backfill repair job already runs the same check
    via ``db.check_track_exists`` — this helper centralises the
    contract so the user-facing endpoint matches that behavior.

    `check_track_exists` is name+artist+album based, format-agnostic.
    Skowl's "Blasphemy mode" library (FLAC converted to MP3 then
    original deleted) matches just fine — track_name + artist + album
    don't change with format.

    ``candidate_tracks`` (when not None) is the artist's library tracks,
    pre-fetched ONCE by the caller, so the check scores in-memory instead
    of firing per-track fuzzy SQL scans against the whole library. Pass an
    empty list for an artist the user owns nothing of — WITH
    ``candidate_tracks_exhaustive=True`` it still routes through the fast
    in-memory path (scores against zero candidates → instant "not owned")
    rather than the slow per-track search. A bare ``[]`` without the flag
    means "no information" and falls through to the legacy SQL path
    (#1621). None preserves the original per-track-SQL behaviour for
    callers that don't pre-fetch.

    Returns False on any exception so a transient DB hiccup doesn't
    silently nuke a discography fetch — a redundant wishlist add is
    much cheaper to recover from than a missed track.
    """
    if not requested_artist or not track_name:
        return False
    try:
        match, confidence = db.check_track_exists(
            track_name, requested_artist,
            confidence_threshold=confidence_threshold,
            server_source=server_source,
            album=album_name or None,
            candidate_tracks=candidate_tracks,
            candidate_tracks_exhaustive=candidate_tracks_exhaustive,
        )
    except Exception:
        # Fail-open is deliberate (a redundant wishlist add is cheaper than a
        # missed track), but never silent: without this, any exception inside
        # check_track_exists degrades to "not owned" with zero log signal.
        logger.debug("track_already_owned check failed; treating as not owned",
                     exc_info=True)
        return False
    return bool(match) and confidence >= confidence_threshold


def _stored_release_kind_for_gate(db: Any, album_id: Any) -> str:
    """The library row's known release kind, '' when unknown.

    Mirrors completion._stored_release_kind: ``record_type`` first (the
    populated column), legacy ``album_type`` as fallback — queried
    separately so a missing column can't hide the other.
    """
    for col in ("record_type", "album_type"):
        try:
            conn = db._get_connection()
            try:
                row = conn.execute(
                    f"SELECT {col} FROM albums WHERE id = ?", (str(album_id),)
                ).fetchone()
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: S110 - cleanup only
                    pass
        except Exception:
            continue
        # Strip before the truthiness check: a whitespace-only value is
        # unknown and must fall through to the next column, not return "".
        val = str(row[0]).strip() if row and row[0] else ""
        if val:
            return val.lower()
    return ""


def owned_release_tracks(
    db: Any,
    album_name: str,
    artist: str,
    expected_tracks: int,
    release_date: Optional[str],
    server_source: Optional[str],
    candidate_albums: Optional[List[Any]] = None,
    candidate_tracks: Optional[List[Any]] = None,
    metadata_source: Optional[str] = None,
    card_source_id: Optional[str] = None,
    card_album_type: Optional[str] = None,
) -> Optional[List[Any]]:
    """the library's tracks for THIS release, found the way the artist page
    finds it (check_album_exists_with_completeness, strict, year-gated).

    the discography download checked each song against everything the
    artist owns, so a single whose song is also on an album counted as owned
    and was never queued, while the artist page, which asks whether the
    release itself is in the library, showed it missing (discord,
    SeadogsBooty: Yellowcard's singles). checking songs against this list
    instead makes the two agree.

    `metadata_source` / `card_source_id` carry the card's provider and
    provider-side album id through so the #1289 Deezer reissue-date
    exemption (and its ID-conflict guard) applies here exactly as on the
    artist page — the downloader's ownership check never disagrees with
    what the page shows.

    `card_album_type` carries the card's release kind ('single', 'ep',
    'album', ...). For single cards the same release-kind gate + track-count
    guard as the artist page apply: a known album-kind row (or a 4+ track
    row) is never this single's release — without it the page says missing
    while the downloader skips the single's tracks as owned.

    [] when the release isn't in the library. None when the lookup failed:
    the caller falls back to the artist-wide check, since a redundant skip is
    cheaper than re-downloading a whole discography.
    """
    year = None
    if release_date and str(release_date)[:4].isdigit():
        year = int(str(release_date)[:4])
    # Normalized once: the kind gate, the count guard and the single-kind
    # title stripping all key off the same value.
    _card_kind = (card_album_type or '').strip().lower()
    try:
        db_album, _confidence, *_rest = db.check_album_exists_with_completeness(
            title=album_name,
            artist=artist,
            expected_track_count=expected_tracks if expected_tracks and expected_tracks > 0 else None,
            confidence_threshold=0.7,
            server_source=server_source,
            candidate_albums=candidate_albums,
            strict_discography_match=True,
            expected_year=year,
            metadata_source=metadata_source,
            card_source_id=card_source_id,
            strip_single_kind=(_card_kind == 'single'),
        )
    except Exception:
        return None
    if db_album is None:
        return []
    if _card_kind == 'single':
        _kind = _stored_release_kind_for_gate(db, getattr(db_album, 'id', None))
        _gate_killed = False
        if not _release_kinds_compatible('single', _kind):
            _gate_killed = True
        else:
            # Count guard scaled to the card's own size, like the artist page:
            # a single-shaped card (<=3 tracks) is never a 4+ track row; a
            # larger card is never a row larger than itself. Unknown card
            # sizes stay lenient, exactly like the page.
            _row_tc = getattr(db_album, 'track_count', None) or 0
            if (expected_tracks or 0) > 0 and _row_tc > max(expected_tracks or 0, 3):
                _gate_killed = True
        if _gate_killed:
            # The gate killed the fuzzy match — but the #1071 id proof is
            # the stronger signal, same second chance as the artist page.
            # (Lazy import: completion imports this module lazily.)
            from core.metadata.completion import _library_album_by_source_id
            _rescued = _library_album_by_source_id(
                db, metadata_source, card_source_id, candidate_albums)
            if _rescued is not None:
                logger.debug(
                    "owned_release_tracks: gate rejected '%s' but the card's %s id "
                    "proves the release — using the rescued row",
                    album_name, metadata_source)
                db_album = _rescued
            else:
                logger.debug(
                    "owned_release_tracks: single '%s' matched only a known %s row — "
                    "not in library", album_name, _kind or 'unknown')
                return []
    album_id = getattr(db_album, 'id', None)
    if candidate_tracks is not None:
        return [t for t in candidate_tracks if getattr(t, 'album_id', None) == album_id]
    try:
        return db.get_candidate_tracks_for_albums([album_id]) or []
    except Exception:
        return None


__all__ = [
    'track_artist_matches',
    'content_type_skip_reason',
    'load_global_content_filter_settings',
    'track_already_owned',
    'owned_release_tracks',
    'watchlist_exclusion_reason',
    'resolve_watchlist_content_settings',
    'release_kind_for_scan',
    'content_exclusion_reason',
    'attach_watchlist_exclusion',
]
