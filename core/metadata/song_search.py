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


class _SongFirstPass:
    """a deezer client whose FIRST search_tracks call also runs search_song.

    the discovery loops search free-text queries ("Auli'i Cravalho How Far
    I'll Go") and score what comes back. deezer's free text ranks karaoke,
    key-shifted and reprise tracks above the original and can leave the
    original out entirely, so with no duration to rule the reprise out it
    won (#1565). the first call merges in search_song's field-scoped results,
    which carry the original, and the scorer picks as before. later calls
    pass straight through, so the loop's other queries cost nothing extra.
    """

    def __init__(self, client: Any, title: str, artist: str):
        self._client = client
        self._title = title
        self._artist = artist
        self._done = False

    def search_tracks(self, query, limit: int = 10, **kwargs):
        plain = self._client.search_tracks(query, limit=limit, **kwargs)
        if self._done:
            return plain
        self._done = True
        try:
            songs = search_song(self._client, self._title, self._artist, limit=25)
        except Exception as e:  # the plain results still stand
            logger.debug("song first pass failed for %r: %s", self._title, e)
            songs = []
        return _merge(songs, plain)

    def __getattr__(self, name):
        return getattr(self._client, name)


def with_song_first_pass(client: Any, title: str, artist: str = "") -> Any:
    """``client`` for one track's discovery search, deezer wrapped so the
    original recording is in the candidates. any other source, or no title,
    comes back unchanged."""
    from core.deezer_client import DeezerClient

    title = (title or "").strip()
    if not title or not isinstance(client, DeezerClient):
        return client
    return _SongFirstPass(client, title, (artist or "").strip())


def search_typed_query(client: Any, query: str, limit: int = 10, **kwargs) -> List[Any]:
    """``client.search_tracks(query)`` for a query a person typed into a search box.

    Deezer's free text ranks reprises and karaoke above the real song and can
    leave it out of the page, so for "Auli'i Cravalho How Far I'll Go" the
    original never reaches the list. When the query names an artist, also run
    ``track:"title" artist`` and put those results first. The artist is read
    from the plain results' own artist names; when none of them is in the query
    (the plain results for "Taylor Swift Love Story" are all covers) a few
    short artist lookups find it instead. The scoped results are used only if
    one of them is actually credited to that artist. Any other source, a query
    that names no artist, or a failure returns the plain results unchanged.
    """
    plain = client.search_tracks(query, limit=limit, **kwargs)

    from core.deezer_client import DeezerClient

    if kwargs or not isinstance(client, DeezerClient) or not plain:
        return plain
    try:
        from core.deezer_track_query import (
            artist_lookup_phrases,
            artist_scoped_query,
            credits_artist,
            exact_artist_match,
            merge_by_id,
            split_query_by_artist,
        )

        def scoped_for(names):
            scoped_query = artist_scoped_query(query, names)
            split = split_query_by_artist(query, names) if scoped_query else None
            if not split:
                return None
            scoped = client.search_tracks(scoped_query, limit=limit)
            # Deezer only RANKS by the artist words (its artist filter is broken),
            # so covers by other artists can still lead: put the artist's own
            # tracks first, and trust the scoped list only if there are any.
            by_artist = [t for t in scoped if credits_artist(getattr(t, "artists", None), split[0])]
            if by_artist:
                rest = [t for t in scoped if t not in by_artist]
                return merge_by_id(by_artist, rest, plain, limit=limit)
            return None

        names = [a for t in plain for a in (getattr(t, "artists", None) or []) if isinstance(a, str)]
        found = scoped_for(names)
        if found:
            return found

        for phrase in artist_lookup_phrases(query):
            candidates = [getattr(a, "name", "") for a in (client.search_artists(phrase, limit=5) or [])]
            hit = exact_artist_match(phrase, candidates)
            if hit:
                found = scoped_for([hit])
                if found:
                    return found
        return plain
    except Exception as e:  # the plain results still stand
        logger.debug("typed-query exact-title search failed for %r: %s", query, e)
        return plain
