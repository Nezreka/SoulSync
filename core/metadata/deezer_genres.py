"""Deezer artist genres, worked out from the artist's albums.

deezer's artist object has no genres at all, only albums carry a genre_id.
so DeezerClient.get_artist() always answered 'genres': [], the discovery scan
stored nothing, and every deezer track in the discovery pool had no genre.
genre-based playlists (daily mix, genre mixes) came back empty for anyone on
deezer. this turns an artist's album list into genre names instead.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List

# deezer's catch-all "All" genre, and the ids it uses for "unknown"
_NOT_A_GENRE = {0, -1}

MAX_ARTIST_GENRES = 3


def rank_album_genre_ids(album_items: Iterable[Dict[str, Any]]) -> List[int]:
    """genre ids across an artist's raw deezer albums, most used first.

    ties keep the order they first showed up in (deezer lists newest albums
    first, so a tie goes to what the artist makes now).
    """
    counts: Counter = Counter()
    first_seen: Dict[int, int] = {}
    for position, album in enumerate(album_items or []):
        if not isinstance(album, dict):
            continue
        try:
            genre_id = int(album.get('genre_id'))
        except (TypeError, ValueError):
            continue
        if genre_id in _NOT_A_GENRE:
            continue
        counts[genre_id] += 1
        first_seen.setdefault(genre_id, position)
    return sorted(counts, key=lambda gid: (-counts[gid], first_seen[gid]))


def artist_genres_from_albums(
    album_items: Iterable[Dict[str, Any]],
    genre_names: Dict[int, str],
    limit: int = MAX_ARTIST_GENRES,
) -> List[str]:
    """genre names for an artist, from their raw album list + deezer's id->name map."""
    names: List[str] = []
    for genre_id in rank_album_genre_ids(album_items):
        name = (genre_names.get(genre_id) or '').strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= limit:
            break
    return names


def genre_names_from_response(data: Any) -> Dict[int, str]:
    """deezer's GET /genre answer -> {id: name}, dropping the "All" entry."""
    out: Dict[int, str] = {}
    for row in (data or {}).get('data', []) if isinstance(data, dict) else []:
        if not isinstance(row, dict):
            continue
        try:
            genre_id = int(row.get('id'))
        except (TypeError, ValueError):
            continue
        name = str(row.get('name') or '').strip()
        if genre_id not in _NOT_A_GENRE and name:
            out[genre_id] = name
    return out
