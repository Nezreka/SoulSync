"""Usenet album hints and scene-release parsing, isolated from Torrent search."""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from core.download_plugins.release_identity import dedupe_prowlarr_releases
from core.download_plugins.torrent import prowlarr_search_with_variants as _search_query
from core.prowlarr_client import DEFAULT_MUSIC_CATEGORIES, ProwlarrClient, ProwlarrSearchError, ProwlarrSearchResult, canonical_protocol

_SCENE_AUDIO_SUFFIX = re.compile(
    r'-(?:(?:16|24|32)[-_ ]?BIT-)?'
    r'(?:\d{2,3}(?:[._]\d+)?-KHZ-)?'
    r'(?:(?:WEB|CD|VINYL|SACD|DVD)-(?:FLAC|ALAC|APE|WAV|MP3|AAC|OGG|OPUS)(?:-(?:19|20)\d{2})?'
    r'|(?:FLAC|ALAC|APE|WAV|MP3|AAC|OGG|OPUS)-(?:19|20)\d{2})'
    r'(?:-[A-Z0-9]+)?$',
    re.IGNORECASE,
)


def _parse_release_title(title: str, *, artist_hint: Optional[str] = None) -> Tuple[str, str]:
    """Split a release title into ``(artist, title)`` using the
    ``Artist - Title`` / ``Artist - Album`` convention almost every
    indexer follows. Scene releases also use ``Artist-Album-WEB-FLAC-...``;
    only a recognized audio suffix permits an unspaced artist/title split.
    Returns ``('', title)`` when no unambiguous separator is found.

    Without this, ``TrackResult.__post_init__`` runs the bare
    filename through ``parse_filename_metadata`` — and our filename
    starts with the indexer's download URL, so the auto-parser
    extracts garbage like ``download?apikey=...`` as the artist
    and shows it in the search-result UI's "by" line. Pre-filling
    the artist field short-circuits the auto-parse.
    """
    if not title:
        return ('', '')
    # Strip common quality / format tags so the dash split doesn't
    # eat them — "Artist - Album [FLAC] (2020)" → "Artist", "Album".
    cleaned = re.sub(r'\s*[\[\(][^\]\)]*[\]\)]\s*$', '', title.strip())
    scene_suffix = _SCENE_AUDIO_SUFFIX.search(cleaned)
    if scene_suffix:
        cleaned = cleaned[:scene_suffix.start()].replace('_', ' ').strip()
    # Prefer a spaced boundary: it preserves hyphenated artist names such
    # as "Jay-Z - Album". A hint must never shorten an explicit artist name.
    parts = re.split(r'\s+-\s+|\s+-(?=\S)|(?<=\S)-\s+', cleaned, maxsplit=1)
    if len(parts) == 1 and scene_suffix:
        if artist_hint:
            # With no spaces, "Jay-Z-Album" cannot identify the boundary on
            # its own. The requested artist is useful only if the actual
            # release starts with that full name followed by a separator.
            hint_pattern = re.escape(artist_hint.strip()).replace(r'\ ', r'[ ._]+')
            match = re.match(rf'^{hint_pattern}\s*-\s*(.+)$', cleaned, re.IGNORECASE)
            if match:
                return artist_hint.strip(), match.group(1).strip()
        parts = cleaned.split('-', 1)
    if len(parts) == 2:
        artist = parts[0].strip()
        rest = parts[1].strip()
        # Reject obvious non-artist prefixes (URLs, hashes, single
        # punctuation) so we don't propagate garbage.
        if artist and not re.match(r'^https?:|^[a-f0-9]{32,}$', artist):
            return (artist, rest or cleaned)
    return ('', cleaned)



async def prowlarr_track_search(
    prowlarr: ProwlarrClient, query: str, protocol: str, *, timeout: Optional[int] = None,
) -> List[ProwlarrSearchResult]:
    """Collect a track query plus one known artist/album hint for this source.

    The hint belongs to one worker task. Cache the album answer (including an
    empty answer or transport error) across its track-query ladder so retries
    do not multiply album requests. Only release plugins opt into this helper.
    """
    from core.downloads.track_hint import current_track_hint

    hint = current_track_hint() or {}
    artist = str(hint.get('artist') or '').strip()
    album = str(hint.get('album') or '').strip()
    title = str(hint.get('title') or '').strip()
    additional = []
    if artist and album and album.casefold() not in ('unknown album', title.casefold()):
        additional.append(f"{artist} {album}")
    cache = hint.setdefault('_prowlarr_album_queries', {}) if hint else None
    return await prowlarr_search_with_variants(
        prowlarr, query, protocol, timeout=timeout,
        additional_queries=additional, additional_query_cache=cache,
    )


async def prowlarr_search_with_variants(
    prowlarr: ProwlarrClient,
    query: str,
    protocol: str,
    *,
    timeout: Optional[int] = None,
    categories=DEFAULT_MUSIC_CATEGORIES,
    additional_queries=(),
    additional_query_cache=None,
) -> List[ProwlarrSearchResult]:
    """Track variants plus at most one artist/album query, de-duplicated.

    Raw hits are not proof of a match: the album query must still run after
    irrelevant track hits. The worker applies its ordinary artist, version
    and quality gates to the combined result. Direct track hits remain first.
    Every query uses the existing supported free-text endpoint and throttle.
    """
    protocol = canonical_protocol(protocol)
    additional = list(additional_queries)[:1]
    album_keys = {' '.join(str(value or '').split()).casefold() for value in additional}
    queries = [query, *additional]
    results, seen, searched = [], set(), set()
    first_error = None
    for candidate_query in queries:
        query_key = ' '.join(str(candidate_query or '').split()).casefold()
        if not query_key or query_key in searched:
            continue
        searched.add(query_key)
        cache_key = (protocol, query_key)
        cache_album = query_key in album_keys and additional_query_cache is not None
        cached = cache_album and cache_key in additional_query_cache
        try:
            answer = additional_query_cache[cache_key] if cached else await _search_query(
                prowlarr, candidate_query, protocol, timeout=timeout, categories=categories)
            if isinstance(answer, Exception):
                raise answer
            if cache_album:
                additional_query_cache[cache_key] = answer
        except ProwlarrSearchError as exc:
            first_error = first_error or exc
            if cache_album:
                additional_query_cache[cache_key] = exc
            continue
        for result in answer:
            key = (result.indexer_id, result.guid or result.download_url or result.magnet_uri or result.title)
            if key not in seen:
                seen.add(key)
                results.append(result)
    if not results and first_error is not None:
        raise first_error
    return dedupe_prowlarr_releases(results)
