"""Basic download-source file search — flat list of file results sorted by quality.

Used by the basic search UI on the Search page and by ``/api/search``.

``run_basic_search`` replaced ``run_basic_soulseek_search`` so the caller
can target any active download source (not just slskd). The old name is
kept as a thin alias for backwards compat with any callers outside this
module.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

logger = logging.getLogger(__name__)


def run_basic_search(
    query: str,
    download_orchestrator,
    run_async: Callable,
    *,
    source: Optional[str] = None,
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
            tracks, albums = run_async(download_orchestrator.search(query))
        else:
            logger.info("basic search: targeting %r for %r", source, query)
            tracks, albums = run_async(client.search(query))
    else:
        tracks, albums = run_async(download_orchestrator.search(query))

    processed_albums = []
    for album in albums:
        album_dict = album.__dict__.copy()
        album_dict['tracks'] = [track.__dict__ for track in album.tracks]
        album_dict['result_type'] = 'album'
        processed_albums.append(album_dict)

    processed_tracks = []
    for track in tracks:
        track_dict = track.__dict__.copy()
        track_dict['result_type'] = 'track'
        processed_tracks.append(track_dict)

    return sorted(
        processed_albums + processed_tracks,
        key=lambda x: x.get('quality_score', 0),
        reverse=True,
    )


# Backwards-compat alias for any callers that haven't been updated yet.
run_basic_soulseek_search = run_basic_search
