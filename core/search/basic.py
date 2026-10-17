"""Basic download-source file search — flat list of file results sorted by quality.

Used by the basic search UI on the Search page and by ``/api/search``.

``run_basic_search`` replaced ``run_basic_soulseek_search`` so the caller
can target any active download source (not just slskd). The old name is
kept as a thin alias for backwards compat with any callers outside this
module.
"""

from __future__ import annotations

import contextvars
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Mapping, Optional, Sequence

logger = logging.getLogger(__name__)
# Queries of one entity search asked of a source at the same time.
_PARALLEL_QUERIES = 4
_CANONICAL_SOURCES = frozenset({
    'soulseek', 'torrent', 'usenet', 'youtube', 'hifi', 'qobuz', 'tidal',
    'deezer_dl', 'lidarr', 'soundcloud', 'amazon',
})


def _effective_source_metadata(result: Mapping[str, Any]) -> dict:
    direct = result.get('_source_metadata')
    if isinstance(direct, dict):
        return direct
    tracks = result.get('tracks')
    if isinstance(tracks, list) and tracks and isinstance(tracks[0], dict):
        return tracks[0].get('_source_metadata') or {}
    return {}


def _decorate_result(
    result: dict,
    *,
    source: Optional[str],
    entity_context: Optional[Mapping[str, Any]],
) -> dict:
    username = str(result.get('username') or '').strip().lower()
    metadata = _effective_source_metadata(result)
    protocol = str(metadata.get('protocol') or '').strip().lower()
    result['source'] = (source or protocol or (
        username if username in _CANONICAL_SOURCES else 'soulseek')).strip().lower()
    if protocol not in ('torrent', 'usenet'):
        return result
    if metadata.get('release_title'):
        result['release_title'] = metadata['release_title']
    if entity_context:
        for target, context_key in (
            ('artist', 'artist_name'), ('matched_album_title', 'album_name'),
            ('matched_track_title', 'track_title'),
        ):
            if entity_context.get(context_key):
                result[target] = entity_context[context_key]
    return result


def _quality_score(result) -> float:
    """Read ``result.quality_score`` as a plain float.

    ``quality_score`` is a ``@property`` on both ``SearchResult`` and
    ``AlbumResult``, so it is NOT in ``__dict__`` and does not survive the
    ``__dict__.copy()`` serialisation below — it has to be read off the
    object and written onto the dict explicitly. Without this the score
    reaches neither the sort here nor the browser, which is why the Quality
    sort and the quality term of the frontend's relevance score both did
    nothing.

    Defensive because the property computes: it calls ``.lower()`` on
    ``quality``/``dominant_quality``, which a source is free to leave None.
    A single odd result must not take down the whole search.
    """
    try:
        return float(result.quality_score)
    except Exception as exc:  # noqa: BLE001 - one bad result must not kill a search
        logger.debug("quality_score unavailable for %r: %s", type(result).__name__, exc)
        return 0.0


def _search_all(search: Callable[[str], tuple], queries: Sequence[str]) -> tuple:
    """Every query at once; one row per hit. A failing query costs only its
    own hits -- unless every query failed."""
    def attempt(q):
        try:
            return search(q), None
        except Exception as exc:  # noqa: BLE001 - one query must not sink the others
            logger.warning("basic search: query %r failed: %s", q, exc)
            return ([], []), exc

    # each query thread keeps the request's context (candidate binding, ...)
    contexts = [contextvars.copy_context() for _ in queries]
    with ThreadPoolExecutor(max_workers=min(_PARALLEL_QUERIES, len(queries))) as pool:
        answers = list(pool.map(lambda ctx, q: ctx.run(attempt, q), contexts, queries))
    errors = [error for _, error in answers if error is not None]
    if len(errors) == len(answers):
        raise errors[0]
    tracks, albums = {}, {}
    for (found_tracks, found_albums), _error in answers:
        for row in found_tracks or []:
            tracks.setdefault((row.username, row.filename), row)
        for row in found_albums or []:
            albums.setdefault((row.username, row.album_path), row)
    from core.download_plugins.release_identity import dedupe_release_candidates
    return dedupe_release_candidates(list(tracks.values())), list(albums.values())


def run_basic_search(
    query: str,
    download_orchestrator,
    run_async: Callable,
    *,
    source: Optional[str] = None,
    entity_context: Optional[Mapping[str, Any]] = None,
    judge: Optional[Callable[[list, list, str], dict]] = None,
    more_queries: Sequence[str] = (),
    track_hint: Optional[Mapping[str, Any]] = None,
) -> list[dict]:
    """Search ``source`` (or the active/first hybrid source) for ``query``.

    Returns dicts with ``result_type`` set to ``"album"`` or ``"track"``
    and sorted by ``quality_score`` descending. Empty list on any failure.

    Parameters
    ----------
    source:
        Optional source name to override the orchestrator's default selection.
        Must be a canonical name from ``DownloadPluginRegistry`` (e.g.
        ``"soulseek"``, ``"tidal"``, ``"qobuz"``). When ``None``, behaviour
        is unchanged from before: orchestrator.search() picks the active
        source (single mode) or the first in chain (hybrid).
    judge:
        ``(tracks, albums, query) -> {id(result): decision}`` -- the download
        pipeline's verdict per hit, stamped on its row as ``decision`` before
        any display decoration rewrites the row's artist.
    more_queries / track_hint:
        An entity search asks the same queries an automatic download walks,
        side by side, under the song's hint (release sources add its
        artist + album query, core/downloads/track_hint.py); one hit found
        by several queries -- or by several indexers -- shows once.
    """
    # A pasted SoundCloud link only resolves on the SoundCloud source (its
    # share URL carries the access token; no other source can find an
    # unlisted track) — force the route no matter which source is selected,
    # the same way manual search does (#865). The user just pastes the link.
    forced_soundcloud = False
    try:
        from core.soundcloud_client import is_soundcloud_url
        if is_soundcloud_url(query):
            source = 'soundcloud'
            forced_soundcloud = True
    except Exception as exc:   # noqa: BLE001 - routing sugar must never kill a search
        logger.debug("soundcloud URL routing skipped: %s", exc)

    target = download_orchestrator
    if source and download_orchestrator:
        # Target a specific source: resolve the client and call search()
        # directly instead of going through the orchestrator chain.
        try:
            client = download_orchestrator.client(source)
        except Exception as exc:
            logger.warning("basic search: could not resolve client for %r: %s", source, exc)
            client = None

        if client is None:
            if forced_soundcloud:
                # a SoundCloud URL can ONLY resolve on SoundCloud — falling
                # back would search the raw URL as text and silently return
                # nothing. Say what's wrong instead (parity with manual search).
                raise ValueError("SoundCloud isn't connected — enable it in "
                                 "Settings to resolve a SoundCloud link.")
            logger.warning("basic search: no client for source %r — falling back to orchestrator", source)
            source = None
        else:
            logger.info("basic search: targeting %r for %r", source, query)
            target = client

    def search(q):
        if track_hint is None:
            return run_async(target.search(q))
        from core.downloads.track_hint import track_hint_context

        async def hinted():
            # set inside the coroutine: the shared loop runs it in its own context
            with track_hint_context(dict(track_hint)):
                return await target.search(q)
        return run_async(hinted())

    queries = list(dict.fromkeys([query, *more_queries]))
    if len(queries) == 1:
        tracks, albums = search(query)
    else:
        tracks, albums = _search_all(search, queries)

    verdicts = {}
    if judge is not None:
        try:
            verdicts = judge(list(tracks or []), list(albums or []), query) or {}
        except Exception as exc:  # noqa: BLE001 - an unjudged hit is still a hit
            logger.warning("basic search: judging results failed: %s", exc)

    processed_albums = []
    for album in albums:
        album_dict = album.__dict__.copy()
        album_dict['tracks'] = [
            dict(track.__dict__, quality_score=_quality_score(track)) for track in album.tracks
        ]
        album_dict['result_type'] = 'album'
        album_dict['quality_score'] = _quality_score(album)
        if id(album) in verdicts:
            album_dict['decision'] = verdicts[id(album)]
        processed_albums.append(_decorate_result(
            album_dict, source=source, entity_context=entity_context))

    processed_tracks = []
    for track in tracks:
        track_dict = track.__dict__.copy()
        track_dict['result_type'] = 'track'
        track_dict['quality_score'] = _quality_score(track)
        if id(track) in verdicts:
            track_dict['decision'] = verdicts[id(track)]
        processed_tracks.append(_decorate_result(
            track_dict, source=source, entity_context=entity_context))

    if entity_context:
        def is_release(row):
            return (
                str(_effective_source_metadata(row).get('protocol') or '').lower()
                in ('torrent', 'usenet')
            )
        if entity_context.get('track_id') is not None:
            processed_albums = [row for row in processed_albums if not is_release(row)]
        elif entity_context.get('album_id') is not None:
            processed_tracks = [row for row in processed_tracks if not is_release(row)]

    return sorted(
        processed_albums + processed_tracks,
        key=lambda x: x.get('quality_score', 0),
        reverse=True,
    )


# Backwards-compat alias for any callers that haven't been updated yet.
run_basic_soulseek_search = run_basic_search
