"""The library rows both artist graphs start from.

The Taste Map (``/api/graph/library``) and the Discovery Web
(``/api/graph/discovery``) each begin with "every artist the library holds",
keyed by folded name, plus enough of the row to draw a node and open the
artist. Shared here because they had two copies of the same read, and because
the id in those nodes has a contract worth stating once (§50.4.4.23).
"""

from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

# What a library node's link means. `/artist-detail/<source>/<id>` reads
# `library:<n>` as a catalogue row and redirects into Library V2 as
# `?artist=<n>` (ldp-01); anything else is taken for a PROVIDER discovery
# request. The old rows handed out `server_source` here ('plex'), which asked
# the front end to discover an artist from a provider that does not exist.
LIBRARY_NODE_SOURCE = "library"


def load_library_artists(conn) -> Tuple[Set[str], Dict[str, Dict[str, Any]],
                                        List[Tuple[Any, ...]]]:
    """``(owned names, meta by name, node tuples)`` for every library artist.

    ``meta`` is keyed by the folded name and carries what a node renders
    (``thumb_url``, ``genres`` as the stored JSON string) plus the catalogue
    ``id`` the artist link resolves. The node tuples are
    ``(name, genres, thumb_url, id, source)``, the shape
    ``core.graph.artist_graph`` expects.

    §40 alias members are left out: they are the same artist as their canonical
    row, and a second node would split that artist's similarity edges in two.
    """
    owned: Set[str] = set()
    meta: Dict[str, Dict[str, Any]] = {}
    artists: List[Tuple[Any, ...]] = []
    for row in conn.execute(
        "SELECT id, name, image_url, genres FROM lib2_artists "
        " WHERE canonical_artist_id IS NULL "
        "   AND name IS NOT NULL AND name <> ''"
    ):
        name, image_url, genres = row[1], row[2], row[3]
        key = str(name or "").strip().lower()
        if not key:
            continue
        owned.add(key)
        meta[key] = {"thumb_url": image_url, "genres": genres, "id": row[0]}
        artists.append((name, genres, image_url, row[0], LIBRARY_NODE_SOURCE))
    return owned, meta, artists


__all__ = ["LIBRARY_NODE_SOURCE", "load_library_artists"]
