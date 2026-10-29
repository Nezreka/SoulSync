"""Bounded catalogue discovery using independently supported album context."""
from __future__ import annotations

from core.matching.audio_verification import normalize
from core.text.fold import fold_title
from utils.logging_config import get_logger

logger = get_logger('reconciliation.discovery')
GENERIC_ARTISTS = frozenset({'', 'various', 'various artists', 'unknown', 'unknown artist'})


def supported_search_context(album, files, candidate, metadata):
    """A fitting provider may supply a missing artist, never a release verdict.

    A different non-generic local artist additionally requires majority audio
    corroboration. Compilation credits and incomplete candidate fits stay local.
    """
    if not album or not candidate or candidate[1] != len(files) or not files:
        return album
    if normalize(metadata.get('name') or metadata.get('title') or '') != normalize(album.get('album_title') or ''):
        return album
    artists = metadata.get('artists') or metadata.get('artist-credit') or []
    names = {str(row.get('name') or (row.get('artist') or {}).get('name') or '').strip()
             for row in artists if isinstance(row, dict)} - {''}
    if len(names) != 1:
        return album
    artist = next(iter(names))
    key = fold_title(artist)
    local = fold_title(str(album.get('artist_name') or ''))
    if key in GENERIC_ARTISTS or key == local:
        return album
    supported = sum(any(
        float(row.get('score') or 0) >= .8 and fold_title(str(row.get('artist') or '')) == key
        for row in (file.get('acoustid') or {}).get('recordings') or []) for file in files)
    if (local not in GENERIC_ARTISTS and supported <= len(files) / 2) or candidate[0] <= 0:
        return album
    logger.debug('MusicBrainz search for %r uses %r from %s:%s; local artist is %r',
                 album.get('album_title'), artist, candidate[3], candidate[4], album.get('artist_name'))
    return {**album, 'artist_name': artist}


def search_release_candidates(album, context, search_cache):
    """One title/artist query, at most four candidates, cached by actual input."""
    if not album or not context.mb_client:
        return []
    title = str(album.get('album_title') or '').strip()
    artist = str(album.get('artist_name') or '').strip()
    if not title:
        return []
    key = (fold_title(title), fold_title(artist))
    if key not in search_cache:
        try:
            search_cache[key] = context.mb_client.search_release(title, artist_name=artist or None, limit=4)
        except Exception as exc:
            logger.debug('Could not search MusicBrainz release %r by %r: %s', title, artist, exc)
            search_cache[key] = []
    candidates = []
    for row in search_cache[key] or []:
        release_id = str(row.get('id') or '')
        if (release_id and float(row.get('score') or 0) >= 90
                and normalize(title) == normalize(str(row.get('title') or ''))):
            candidates.append(('musicbrainz', release_id))
    return list(dict.fromkeys(candidates))[:4]
