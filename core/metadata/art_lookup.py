"""Per-source album-art lookups + availability for the cover-art picker.

Bridges the pure resolver in ``art_sources.py`` to the real metadata clients.
Each supported source contributes two things:

- **availability** — is this source usable for the current user right now?
  Free sources (CAA, Deezer, iTunes, AudioDB) are always available; account
  sources (Spotify) only when connected. This is what powers "not everybody
  has access to every source": the UI offers only available sources and the
  resolver skips the rest.
- **lookup** — ``(artist, album, metadata) -> cover_url | None``, calling an
  EXISTING client method. Every lookup is individually guarded so any error or
  miss degrades to ``None`` (the resolver then falls through to the next
  source, finally to the download's own art) — a flaky source can never raise
  into, or break, a download.

Lookups are cached per album via ``build_art_lookup`` so resolving art for a
16-track album hits each source at most once.

Client accessors are imported lazily inside each function to keep this module
import-light (the pure resolver + its tests never pull a network client).
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Optional

from utils.logging_config import get_logger

from core.metadata.art_sources import ART_CAPABLE_SOURCES

logger = get_logger("metadata.art_lookup")

# Sources that need no account/config — always offered.
_FREE_SOURCES = ("caa", "deezer", "itunes", "audiodb")


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def _spotify_available() -> bool:
    """Spotify art is only usable when the user is connected. Reuse the
    canonical accessor, which returns a client only when authenticated."""
    try:
        from core.metadata.registry import get_client_for_source
        return get_client_for_source("spotify") is not None
    except Exception:
        return False


_AVAILABILITY: Dict[str, Callable[[], bool]] = {
    "spotify": _spotify_available,
}


def is_art_source_available(source: str) -> bool:
    """Is ``source`` usable right now? Free sources are always available;
    account sources defer to their connection check. Unknown/unsupported
    sources are never available."""
    name = (source or "").strip().lower()
    if name not in ART_CAPABLE_SOURCES:
        return False
    if name in _FREE_SOURCES:
        return True
    check = _AVAILABILITY.get(name)
    return bool(check()) if check else False


def available_art_sources() -> List[str]:
    """The supported art sources currently usable for this user, in the
    default priority order — for populating the settings UI."""
    return [s for s in ART_CAPABLE_SOURCES if is_art_source_available(s)]


# ---------------------------------------------------------------------------
# Album-match validation. Every client's search returns its top hit
# unvalidated (results[0]), so a source that lacks the album could hand back a
# DIFFERENT one — embedding wrong-album art, which is worse than the download's
# own cover. We therefore confirm the returned album matches the request before
# trusting its art; a mismatch returns None so the resolver falls through,
# preserving the "worst case = the cover you'd get today" guarantee.
# ---------------------------------------------------------------------------

_STOPWORDS = {"the", "a", "an"}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(s or "").lower()).strip()


def _significant_tokens(s) -> set:
    return {t for t in _norm(s).split() if t not in _STOPWORDS}


def _result_album_artist(obj):
    """Best-effort (album_name, artist_name) from a search result — handles
    both raw dicts (Deezer/AudioDB) and dataclasses (iTunes/Spotify)."""
    if isinstance(obj, dict):
        name = obj.get("title") or obj.get("name") or obj.get("strAlbum") or ""
        artist = obj.get("strArtist") or ""
        a = obj.get("artist")
        if not artist and isinstance(a, dict):
            artist = a.get("name", "")
        elif not artist and isinstance(a, str):
            artist = a
        return name, artist
    name = getattr(obj, "name", None) or getattr(obj, "title", None) or ""
    arts = getattr(obj, "artists", None)
    if arts is None:
        arts = getattr(obj, "artist", None)
    if isinstance(arts, (list, tuple)):
        artist = ", ".join(str(x) for x in arts if x)
    else:
        artist = str(arts) if arts else ""
    return name, artist


def _album_matches(req_artist, req_album, got_artist, got_album) -> bool:
    """True when the returned album plausibly IS the requested one.

    Both album and artist are compared by significant-token subset (stopwords
    dropped), which tolerates leading articles, word order, "(Deluxe)"/
    "- Remastered" suffixes, punctuation, and "feat."/"&"/multi-artist ordering.
    Lenient enough to never reject a genuine hit — a false reject just falls
    back to today's art — yet strict enough to drop a different album (the
    generic-title case, e.g. two "Greatest Hits", is caught by the artist gate)."""
    ra, ga = _significant_tokens(req_album), _significant_tokens(got_album)
    if not ra or not ga:
        return False
    if not (ra <= ga or ga <= ra):
        return False
    # Sokhi: the subset tolerance exists for '(Deluxe)'/'- Remastered'
    # suffixes, but a NUMERIC difference is a different release, not a
    # suffix. 'B小町 …CD Vol.4' normalizes to {b,tv,cd,vol,4} — a subset of
    # Vol.4.5's {b,tv,cd,vol,4,5} — so volume 4 was hanging volume 4.5's
    # cover. Any number present on only ONE side (volume, part, sequel,
    # remaster year) rejects the match; the resolver then falls through to
    # the next source / the download's own art, which is the designed cost
    # of a false reject here. (Shared rule — the MusicBrainz release matcher
    # applies the same guard so the MBID-keyed CAA path can't slip either.)
    from core.text.title_match import numeric_tokens_differ
    if numeric_tokens_differ(req_album, got_album):
        return False
    ta, tg = _significant_tokens(req_artist), _significant_tokens(got_artist)
    if not ta:
        return True                      # requested artist unknown -> album match suffices
    if not tg:
        return False                     # asked for an artist, none returned -> can't confirm
    return ta <= tg or tg <= ta or bool(ta & tg)


# ---------------------------------------------------------------------------
# Per-source lookups — each returns a cover URL or None, never raises.
# Non-CAA sources validate the returned album before trusting its art.
# ---------------------------------------------------------------------------


def _caa_art(artist: str, album: str, metadata: dict) -> Optional[str]:
    # Cover Art Archive is keyed by the MusicBrainz release id, so it's THE
    # release's art by definition — no fuzzy match to validate. Resolves only
    # once MusicBrainz enrichment has found the release.
    mbid = (metadata or {}).get("musicbrainz_release_id")
    if not mbid:
        return None
    return f"https://coverartarchive.org/release/{mbid}/front-1200"


def _deezer_art(artist: str, album: str, metadata: dict) -> Optional[str]:
    from core.metadata.registry import get_deezer_client
    client = get_deezer_client()
    if not client:
        return None
    data = client.search_album(artist, album)
    if not data:
        return None
    got_album, got_artist = _result_album_artist(data)
    if not _album_matches(artist, album, got_artist, got_album):
        return None
    url = data.get("cover_xl") or data.get("cover_big") or data.get("cover_medium")
    if not url:
        return None
    try:
        from core.deezer_client import _upgrade_deezer_cover_url
        return _upgrade_deezer_cover_url(url)
    except Exception:
        return url


def _itunes_art(artist: str, album: str, metadata: dict) -> Optional[str]:
    from core.metadata.registry import get_itunes_client
    client = get_itunes_client()
    if not client:
        return None
    for alb in (client.search_albums(f"{artist} {album}") or []):
        url = getattr(alb, "image_url", None)
        if not url:
            continue
        got_album, got_artist = _result_album_artist(alb)
        if _album_matches(artist, album, got_artist, got_album):
            # iTunes serves any size via the WxH segment — request the max so
            # iTunes contributes high-res art, not the 600px default.
            return re.sub(r"/\d+x\d+bb\.", "/3000x3000bb.", url)
    return None


def _audiodb_art(artist: str, album: str, metadata: dict) -> Optional[str]:
    from core.audiodb_client import AudioDBClient
    data = AudioDBClient().search_album(artist, album)
    if not data:
        return None
    got_album, got_artist = _result_album_artist(data)
    if not _album_matches(artist, album, got_artist, got_album):
        return None
    return data.get("strAlbumThumb") or None


def _spotify_art(artist: str, album: str, metadata: dict) -> Optional[str]:
    from core.metadata.registry import get_client_for_source
    client = get_client_for_source("spotify")
    if not client:
        return None
    for alb in (client.search_albums(f"{artist} {album}") or []):
        url = getattr(alb, "image_url", None)
        if not url:
            continue
        got_album, got_artist = _result_album_artist(alb)
        if _album_matches(artist, album, got_artist, got_album):
            return url
    return None


_LOOKUPS: Dict[str, Callable[[str, str, dict], Optional[str]]] = {
    "caa": _caa_art,
    "deezer": _deezer_art,
    "itunes": _itunes_art,
    "audiodb": _audiodb_art,
    "spotify": _spotify_art,
}


def select_preferred_art_url(
    artist: Optional[str],
    album: Optional[str],
    metadata: Optional[dict],
    configured_order,
    validate: Optional[Callable[[str, str], bool]] = None,
) -> Optional[str]:
    """Pick a cover-art URL from the user's configured source order, or None.

    ``None`` means "feature off, or nothing in the list resolved" — the caller
    then keeps its existing art (today's behavior), so the worst case is simply
    the cover you'd get today. This is the single entry point the art pipeline
    calls; it's a no-op (returns ``None`` immediately) unless ``album_art_order``
    is an explicit non-empty list, which keeps every existing install untouched.

    ``validate(source, url)`` is an optional gate forwarded to the resolver — the
    art pipeline passes one that fetches the candidate and rejects images below a
    minimum resolution, so a too-small cover (e.g. a low-res Cover Art Archive
    upload) is skipped and the next source is tried instead of winning by
    priority alone.
    """
    if not isinstance(configured_order, (list, tuple)) or not configured_order:
        return None
    from core.metadata.art_sources import effective_art_order, resolve_cover_art
    order = [s for s in effective_art_order(configured_order) if is_art_source_available(s)]
    if not order:
        return None
    lookup = build_art_lookup(artist or "", album or "", metadata or {})
    url, _src = resolve_cover_art(order, lookup, validate=validate)
    return url


def build_art_lookup(
    artist: str,
    album: str,
    metadata: Optional[dict] = None,
) -> Callable[[str], Optional[str]]:
    """Return a ``source_name -> cover_url | None`` callable for one album,
    suitable to pass straight to ``art_sources.resolve_cover_art``. Results are
    cached per source so re-resolving across an album's tracks costs at most one
    lookup per source, and every lookup is guarded (errors → None)."""
    meta = metadata or {}
    cache: Dict[str, Optional[str]] = {}

    def lookup(source: str) -> Optional[str]:
        name = (source or "").strip().lower()
        if name in cache:
            return cache[name]
        fn = _LOOKUPS.get(name)
        url: Optional[str] = None
        if fn is not None:
            try:
                url = fn(artist, album, meta)
            except Exception as exc:
                logger.debug("[art] %s lookup failed: %s", name, exc)
                url = None
        cache[name] = url
        return url

    return lookup


# ---------------------------------------------------------------------------
# Candidate gathering for the cover-art PICKER. Unlike the resolver above
# (which picks ONE url by priority), the picker wants EVERY option so the user
# can choose. Cover Art Archive contributes all images for the release; the
# other sources contribute their single validated best.
# ---------------------------------------------------------------------------

_COVER_ART_ARCHIVE = "https://coverartarchive.org"

# Order the picker offers single-cover sources in (CAA images come first).
_PICKER_SINGLE_SOURCES = ("deezer", "itunes", "spotify", "audiodb")


def _parse_caa_images(data: Optional[dict]) -> List[dict]:
    """Pure: a Cover Art Archive ``/release/{mbid}`` JSON payload -> candidate dicts.

    Prefers the 1200px thumbnail (matching the resolver's ``front-1200``), falls back to the
    full-size image. Front covers are listed before back/other art."""
    out: List[dict] = []
    for img in (data or {}).get("images", []) or []:
        if not isinstance(img, dict):
            continue
        thumbs = img.get("thumbnails") or {}
        url = thumbs.get("1200") or thumbs.get("large") or img.get("image") or thumbs.get("500")
        if not url:
            continue
        types = img.get("types") or []
        is_front = bool(img.get("front"))
        kind = types[0] if types else ("Front" if is_front else "Other")
        out.append({"url": url, "source": "caa", "type": kind, "front": is_front})
    out.sort(key=lambda c: not c.get("front"))   # front images first, stable otherwise
    return out


def _fetch_caa_release(mbid: str) -> Optional[dict]:
    import json
    import urllib.request
    req = urllib.request.Request(
        f"{_COVER_ART_ARCHIVE}/release/{mbid}",
        headers={"User-Agent": "SoulSync/1.0 (cover-art picker)"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:   # noqa: S310 (trusted host)
        return json.loads(resp.read().decode("utf-8"))


def _caa_art_candidates(metadata: Optional[dict], *, fetch=None) -> List[dict]:
    """All Cover Art Archive images for the album's MusicBrainz release, or [] (never raises)."""
    mbid = (metadata or {}).get("musicbrainz_release_id")
    if not mbid:
        return []
    fetch = fetch or _fetch_caa_release
    try:
        return _parse_caa_images(fetch(mbid))
    except Exception as exc:
        logger.debug("[art] CAA candidates fetch failed: %s", exc)
        return []


def _mb_raw_client():
    """The low-level MusicBrainzClient (has ``get_release`` / ``browse_release_group_releases``).

    ``registry.get_musicbrainz_client()`` returns the higher-level ``MusicBrainzSearchClient``, which
    wraps the raw client at ``._client`` — those browse/lookup methods aren't on the wrapper. Return
    the raw client, tolerating either shape."""
    from core.metadata.registry import get_musicbrainz_client
    c = get_musicbrainz_client()
    return getattr(c, "_client", c)


def _resolve_release_group_mbid(release_mbid, *, get_release=None) -> Optional[str]:
    """The MusicBrainz release-GROUP id for a release, or None. ``get_release`` injectable. The group
    is the logical album; enumerating its releases is what surfaces every edition's cover art. Never
    raises."""
    if not release_mbid:
        return None
    if get_release is None:
        def get_release(mbid):
            client = _mb_raw_client()
            return client.get_release(mbid, includes=["release-groups"]) if client else None
    try:
        rel = get_release(release_mbid) or {}
        return (rel.get("release-group") or {}).get("id")
    except Exception as exc:
        logger.debug("[art] release-group resolve failed: %s", exc)
        return None


def _caa_release_group_candidates(release_group_mbid, *, browse=None, limit=40) -> List[dict]:
    """One Cover Art Archive front cover per RELEASE (edition) in the release-group that actually has
    art — the "loads of covers across editions" source. A single MusicBrainz browse call; the front
    URL is built from the deterministic CAA path (no per-release CAA fetch). ``browse`` injectable.
    Never raises."""
    if not release_group_mbid:
        return []
    if browse is None:
        def browse(rg):
            client = _mb_raw_client()
            return client.browse_release_group_releases(rg) if client else []
    try:
        releases = browse(release_group_mbid) or []
    except Exception as exc:
        logger.debug("[art] release-group browse failed: %s", exc)
        return []
    out: List[dict] = []
    seen: set = set()
    for rel in releases:
        if not isinstance(rel, dict):
            continue
        mbid = rel.get("id")
        caa = rel.get("cover-art-archive") or {}
        if not mbid or mbid in seen or not caa.get("front"):
            continue
        seen.add(mbid)
        out.append({
            "url": f"{_COVER_ART_ARCHIVE}/release/{mbid}/front-1200",
            "source": "caa", "type": "Front", "front": True,
        })
        if len(out) >= limit:
            break
    return out


def _caa_candidates_for(metadata: Optional[dict]) -> List[dict]:
    """CAA candidates for an album — every edition's front across the release-group when it can be
    resolved (the rich path), else just the one known release's images. Never raises."""
    meta = metadata or {}
    rg = meta.get("musicbrainz_release_group") or _resolve_release_group_mbid(
        meta.get("musicbrainz_release_id"))
    if rg:
        group = _caa_release_group_candidates(rg)
        if group:
            return group
    return _caa_art_candidates(meta)


def gather_album_art_candidates(
    artist: Optional[str],
    album: Optional[str],
    metadata: Optional[dict] = None,
    *,
    lookup: Optional[Callable[[str], Optional[str]]] = None,
    caa_candidates: Optional[List[dict]] = None,
) -> List[dict]:
    """Every cover-art option for one album, de-duplicated by URL, for the picker UI.

    Cover Art Archive contributes all of the release's images; each available single-cover source
    (Deezer/iTunes/Spotify/AudioDB) contributes its one validated best. ``lookup``/``caa_candidates``
    are injectable for tests; in production they default to the live clients. Never raises — a failing
    source is simply absent from the list.
    """
    from concurrent.futures import ThreadPoolExecutor

    meta = metadata or {}
    if lookup is None:
        lookup = build_art_lookup(artist or "", album or "", meta)
    srcs = [s for s in _PICKER_SINGLE_SOURCES if is_art_source_available(s)]

    def _safe(fn, *a):
        try:
            return fn(*a)
        except Exception as exc:
            logger.debug("[art] picker task failed: %s", exc)
            return None

    # Fan out the CAA/MusicBrainz path and every single-cover source concurrently — a picker click
    # shouldn't wait for them in series (that was ~15s of sequential network). Total ≈ slowest call.
    with ThreadPoolExecutor(max_workers=len(srcs) + 1) as ex:
        caa_future = None if caa_candidates is not None else ex.submit(_safe, _caa_candidates_for, meta)
        single_futures = [(s, ex.submit(_safe, lookup, s)) for s in srcs]
        caa = caa_candidates if caa_candidates is not None else (caa_future.result() or [])
        singles = [(s, f.result()) for s, f in single_futures]

    candidates: List[dict] = []
    seen: set = set()

    def _add(url, source, kind="cover", front=False):
        if not url or url in seen:
            return
        seen.add(url)
        candidates.append({"url": url, "source": source, "type": kind, "front": front})

    for c in caa or []:
        _add(c.get("url"), "caa", c.get("type", "Front"), c.get("front", False))
    for s, url in singles:
        _add(url, s)

    return candidates
