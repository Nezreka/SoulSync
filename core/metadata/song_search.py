"""search a metadata source for one song by one artist.

free-text search ("Numb Linkin Park") is what every source does by default,
and some of them are bad at it:

- deezer's free-text index skips tracks. "Numb Linkin Park" returns 50
  results with live cuts, covers and lullaby versions but never the studio
  Numb off Meteora, which is Linkin Park's #2 top track. the field-scoped
  form (Linkin Park track:"Numb") returns it first.
- itunes searches one country's store. songs missing from that store
  (E-Type, Helena Paparizou in the US) never come back (#1398).

search_song knows each source's better path, and keeps the free-text results
too so nothing the old search found goes missing. callers still rerank.
"""

from __future__ import annotations

from typing import Any, List

from utils.logging_config import get_logger

logger = get_logger("metadata.song_search")


def _merge(*lists) -> list:
    seen = set()
    out = []
    for tracks in lists:
        for t in tracks or []:
            key = getattr(t, "id", None) or id(t)
            if key not in seen:
                seen.add(key)
                out.append(t)
    return out


def search_song(client: Any, title: str, artist: str = "", limit: int = 20) -> List[Any]:
    """tracks from `client` for `title` by `artist`, best path per source."""
    title = (title or "").strip()
    artist = (artist or "").strip()
    query = " ".join(p for p in (title, artist) if p)
    if not query or client is None:
        return []

    from core.deezer_client import DeezerClient
    from core.itunes_client import iTunesClient

    if isinstance(client, iTunesClient):
        return client.search_tracks_any_store(query, limit, expected_title=title, expected_artist=artist)

    if isinstance(client, DeezerClient) and title:
        scoped = []
        try:
            scoped = client.search_tracks(limit=limit, track=title, artist=artist or None)
        except Exception as e:  # the free-text half still stands on its own
            logger.debug("deezer scoped search failed for %r: %s", query, e)
        return _merge(scoped, client.search_tracks(query, limit=limit))

    return client.search_tracks(query, limit=limit)
