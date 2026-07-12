"""Library artist discography policy.

The library view intentionally uses commercial catalogues instead of the
configured metadata primary source.  iTunes is preferred, Deezer is the only
external fallback, and locally owned releases omitted by both catalogues are
merged back into the result.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from core.metadata.lookup import MetadataLookupOptions

logger = logging.getLogger(__name__)

COMMERCIAL_DISCOGRAPHY_SOURCES: Sequence[str] = ("itunes", "deezer")

_SOURCE_ID_FIELDS = {
    "itunes": "itunes_album_id",
    "deezer": "deezer_id",
    "musicbrainz": "musicbrainz_release_id",
    "spotify": "spotify_album_id",
}

_VARIANT_TERMS = (
    "anniversary",
    "clean",
    "collector",
    "deluxe",
    "edition",
    "expanded",
    "explicit",
    "legacy",
    "mono",
    "remaster",
    "remastered",
    "reissue",
    "redux",
    "special",
    "stereo",
    "super deluxe",
    "version",
)
_VARIANT_GROUP_RE = re.compile(
    r"\s*[\(\[][^\)\]]*(?:" + "|".join(re.escape(term) for term in _VARIANT_TERMS) + r")[^\)\]]*[\)\]]\s*",
    re.IGNORECASE,
)
_VARIANT_TRAILING_RE = re.compile(
    r"\s*[-–—:]?\s*(?:\d+(?:st|nd|rd|th)\s+)?(?:"
    + "|".join(re.escape(term) for term in _VARIANT_TERMS)
    + r")(?:\s+(?:edition|version|remaster|remastered))?\s*$",
    re.IGNORECASE,
)
_SUBTITLE_SEPARATORS = (":", " - ", " – ", " — ")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP_TOKENS = {"a", "an", "and", "of", "the"}
_SERIES_TOKENS = {"cd", "chapter", "disc", "part", "pt", "vol", "volume"}


def _ascii_casefold(value: Any) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return normalized.encode("ascii", "ignore").decode("ascii").casefold().strip()


def _normalized_title(value: Any) -> str:
    return " ".join(_TOKEN_RE.findall(_ascii_casefold(value)))


def _canonical_title(value: Any) -> str:
    cleaned = _ascii_casefold(value)
    previous = None
    while cleaned != previous:
        previous = cleaned
        cleaned = _VARIANT_GROUP_RE.sub(" ", cleaned)
        cleaned = _VARIANT_TRAILING_RE.sub("", cleaned)
    return " ".join(_TOKEN_RE.findall(cleaned))


def _is_base_title_with_subtitle(left: Any, right: Any) -> bool:
    left_raw = _ascii_casefold(left)
    right_raw = _ascii_casefold(right)
    for shorter, longer in ((left_raw, right_raw), (right_raw, left_raw)):
        if not shorter or len(shorter) < 4:
            continue
        if any(longer.startswith(shorter + separator) for separator in _SUBTITLE_SEPARATORS):
            return True
    return False


def _significant_tokens(value: str) -> set[str]:
    return {token for token in value.split() if token not in _STOP_TOKENS}


def titles_represent_same_release(left: Any, right: Any) -> bool:
    """Return whether two same-artist release titles represent one catalogue item.

    The comparison is deliberately conservative but understands common commercial
    edition suffixes, subtitles, and catalogue titles that include the artist name.
    """
    left_normal = _normalized_title(left)
    right_normal = _normalized_title(right)
    if not left_normal or not right_normal:
        return False
    if left_normal == right_normal:
        return True
    if _is_base_title_with_subtitle(left, right):
        return True

    left_canonical = _canonical_title(left)
    right_canonical = _canonical_title(right)
    if left_canonical == right_canonical:
        return True

    left_tokens = _significant_tokens(left_canonical)
    right_tokens = _significant_tokens(right_canonical)
    if left_tokens and right_tokens:
        smaller, larger = sorted((left_tokens, right_tokens), key=len)
        extra_tokens = larger - smaller
        has_distinct_series_suffix = any(
            token.isdigit() or token in _SERIES_TOKENS
            for token in extra_tokens
        )
        if len(smaller) >= 2 and smaller.issubset(larger) and not has_distinct_series_suffix:
            return True

        shared = left_tokens & right_tokens
        smaller_coverage = len(shared) / min(len(left_tokens), len(right_tokens))
        jaccard = len(shared) / len(left_tokens | right_tokens)
        if smaller_coverage >= 0.9 and jaccard >= 0.65:
            return True

    return SequenceMatcher(None, left_canonical, right_canonical).ratio() >= 0.9


def _release_title(release: Mapping[str, Any]) -> str:
    return str(release.get("title") or release.get("name") or "").strip()


def _sort_releases(releases: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    def release_year(item: Mapping[str, Any]) -> int:
        value = item.get("release_date") or item.get("year") or ""
        try:
            return int(str(value)[:4])
        except (TypeError, ValueError):
            return 0

    return sorted((dict(release) for release in releases), key=release_year, reverse=True)


def _iter_release_buckets(discography: Mapping[str, Any]) -> Iterable[tuple[str, Mapping[str, Any]]]:
    for bucket in ("albums", "eps", "singles"):
        for release in discography.get(bucket, []) or []:
            yield bucket, release


def _stamp_source(discography: Mapping[str, Any], source: str) -> Dict[str, Any]:
    stamped: Dict[str, Any] = dict(discography)
    for bucket in ("albums", "eps", "singles"):
        stamped[bucket] = [
            {**dict(release), "source": str(release.get("source") or source)}
            for release in discography.get(bucket, []) or []
        ]
    stamped["source"] = source
    return stamped


def _local_release_source(
    local_id: str,
    source_refs: Mapping[str, Mapping[str, Any]],
    active_source: Optional[str],
) -> tuple[str, str]:
    refs = source_refs.get(local_id, {}) or {}
    source_order: List[str] = []
    if active_source:
        source_order.append(active_source)
    source_order.extend(["itunes", "deezer", "musicbrainz", "spotify"])

    seen = set()
    for source in source_order:
        if source in seen:
            continue
        seen.add(source)
        field = _SOURCE_ID_FIELDS.get(source)
        value = refs.get(field) if field else None
        if value is not None and str(value).strip():
            return source, str(value).strip()

    return "library", f"library:{local_id}"


def _local_release_is_represented(
    release: Mapping[str, Any],
    local_bucket: str,
    source_refs: Mapping[str, Any],
    external_releases: Sequence[tuple[str, Mapping[str, Any]]],
) -> bool:
    local_title = _release_title(release)

    for external_bucket, external in external_releases:
        external_source = str(external.get("source") or "").strip().lower()
        external_id = str(external.get("id") or "").strip()
        source_field = _SOURCE_ID_FIELDS.get(external_source)
        source_id = source_refs.get(source_field) if source_field else None
        if source_id is not None and str(source_id).strip() == external_id:
            return True

        same_single_class = (local_bucket == "singles") == (external_bucket == "singles")
        if same_single_class and titles_represent_same_release(local_title, _release_title(external)):
            return True

    return False


def merge_owned_releases(
    discography: Mapping[str, Any],
    owned_releases: Optional[Mapping[str, Sequence[Mapping[str, Any]]]],
    owned_source_refs: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Append locally owned releases missing from an external discography.

    External cards always win when a provider ID or title match exists.  Only a
    genuinely omitted local release is appended, with the best available provider
    ID stamped on the card so track lookup and wishlist actions remain source-aware.
    """
    merged = {
        **dict(discography),
        "albums": [dict(release) for release in discography.get("albums", []) or []],
        "eps": [dict(release) for release in discography.get("eps", []) or []],
        "singles": [dict(release) for release in discography.get("singles", []) or []],
    }
    if not owned_releases:
        return merged

    source_refs = owned_source_refs or {}
    active_source = str(merged.get("source") or "").strip().lower() or None
    external_releases = list(_iter_release_buckets(merged))
    seen_local_ids = set()

    for bucket, album_type in (("albums", "album"), ("eps", "ep"), ("singles", "single")):
        for release in owned_releases.get(bucket, []) or []:
            local_id = str(release.get("local_album_id") or release.get("id") or "").strip()
            if not local_id or local_id in seen_local_ids:
                continue
            seen_local_ids.add(local_id)

            refs = source_refs.get(local_id, {}) or {}
            if _local_release_is_represented(release, bucket, refs, external_releases):
                continue

            source, provider_id = _local_release_source(local_id, source_refs, active_source)
            title = _release_title(release) or provider_id
            card = {
                **dict(release),
                "id": provider_id,
                "local_album_id": local_id,
                "name": title,
                "title": title,
                "album_type": str(release.get("album_type") or album_type).lower(),
                "source": source,
                "owned": True,
                "secondary_types": list(release.get("secondary_types") or []),
                "downloadable": source != "library",
            }
            merged[bucket].append(card)
            external_releases.append((bucket, card))

    for bucket in ("albums", "eps", "singles"):
        merged[bucket] = _sort_releases(merged[bucket])

    merged["success"] = bool(merged["albums"] or merged["eps"] or merged["singles"])
    if merged["success"]:
        merged["error"] = None
    return merged


def get_library_artist_discography(
    artist_id: str,
    artist_name: str,
    artist_source_ids: Mapping[str, Any],
    owned_releases: Optional[Mapping[str, Sequence[Mapping[str, Any]]]] = None,
    owned_source_refs: Optional[Mapping[str, Mapping[str, Any]]] = None,
    *,
    lookup: Optional[Callable[..., Dict[str, Any]]] = None,
    limit: int = 200,
    skip_cache: bool = False,
    max_pages: int = 0,
) -> Dict[str, Any]:
    """Build the commercial discography used by library artist pages.

    iTunes is attempted first and Deezer only when iTunes returns no releases.
    The configured primary metadata source is intentionally ignored here.
    """
    if lookup is None:
        from core.metadata.discography import get_artist_detail_discography

        lookup = get_artist_detail_discography

    attempted_sources: List[str] = []
    selected: Optional[Dict[str, Any]] = None

    for source in COMMERCIAL_DISCOGRAPHY_SOURCES:
        source_id = artist_source_ids.get(source)
        if source_id is None or not str(source_id).strip():
            continue

        attempted_sources.append(source)
        try:
            result = lookup(
                artist_id,
                artist_name=artist_name,
                options=MetadataLookupOptions(
                    source_override=source,
                    allow_fallback=False,
                    skip_cache=skip_cache,
                    max_pages=max_pages,
                    limit=limit,
                    artist_source_ids={source: str(source_id).strip()},
                ),
            )
        except Exception as exc:
            logger.debug("Commercial discography lookup failed for %s: %s", source, exc)
            continue
        if result.get("success"):
            selected = _stamp_source(result, source)
            break

    if selected is None:
        selected = {
            "success": False,
            "albums": [],
            "eps": [],
            "singles": [],
            "source": "library",
            "source_priority": attempted_sources,
            "error": f'No commercial releases found for artist "{artist_name or artist_id}"',
        }
    else:
        selected["source_priority"] = attempted_sources

    return merge_owned_releases(selected, owned_releases, owned_source_refs)
