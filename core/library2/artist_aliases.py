"""§40 artist-alias registry: soft-link two artist rows as the same person.

Handles the case §38's provider-id-based ``_ArtistResolver`` merge cannot
catch: the same real-world artist appears as two SEPARATE ``lib2_artists``
rows because the providers themselves carry no shared identifier (e.g. a
kanji vs. romaji name are distinct Deezer/Spotify catalog entries). Linking
is a soft link only — both rows keep their own albums/tracks; nothing is
reassigned or deleted. See docs/library-v2.md §24 for the full design.
"""

from __future__ import annotations

from typing import Any, List


class AliasLinkError(ValueError):
    """A proposed artist-alias link is missing or would create an invalid structure."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _artist_row(conn: Any, artist_id: int) -> Any:
    return conn.execute(
        "SELECT id, canonical_artist_id FROM lib2_artists WHERE id=?",
        (int(artist_id),),
    ).fetchone()


def link_artist_alias(conn: Any, artist_id: int, alias_of_id: int) -> None:
    """Mark ``artist_id`` as an alias of the canonical ``alias_of_id`` row.

    ``alias_of_id`` must itself be canonical (``canonical_artist_id IS NULL``)
    and ``artist_id`` must not already be the canonical root of its own group
    — both rules together keep the structure provably one level deep, so
    ``resolve_alias_group`` never needs to recurse. A group merge (linking two
    already-canonical roots that each have their own aliases) is out of scope
    for v1: unlink the alias rows first, then relink them individually.
    """
    artist_id = int(artist_id)
    alias_of_id = int(alias_of_id)
    if artist_id == alias_of_id:
        raise AliasLinkError("An artist cannot be linked as its own alias")

    canonical = _artist_row(conn, alias_of_id)
    if canonical is None:
        raise AliasLinkError("Canonical artist not found", status=404)
    if canonical["canonical_artist_id"] is not None:
        raise AliasLinkError(
            "Target is itself an alias — link to its canonical artist instead"
        )

    artist = _artist_row(conn, artist_id)
    if artist is None:
        raise AliasLinkError("Artist not found", status=404)
    has_own_aliases = conn.execute(
        "SELECT 1 FROM lib2_artists WHERE canonical_artist_id=? LIMIT 1",
        (artist_id,),
    ).fetchone()
    if has_own_aliases:
        raise AliasLinkError(
            "Artist already has aliases of its own — unlink them before merging groups"
        )

    conn.execute(
        "UPDATE lib2_artists SET canonical_artist_id=?, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=?",
        (alias_of_id, artist_id),
    )


def unlink_artist_alias(conn: Any, artist_id: int) -> None:
    """Detach ``artist_id`` from its canonical artist, if any. Idempotent —
    unlinking an already-standalone row is a no-op, not an error."""
    artist_id = int(artist_id)
    if _artist_row(conn, artist_id) is None:
        raise AliasLinkError("Artist not found", status=404)
    conn.execute(
        "UPDATE lib2_artists SET canonical_artist_id=NULL, updated_at=CURRENT_TIMESTAMP "
        "WHERE id=?",
        (artist_id,),
    )


def resolve_alias_group(conn: Any, artist_id: int) -> List[int]:
    """Return the full alias group for ``artist_id``, canonical id first.

    Accepts either a canonical or an alias id and resolves to the same group
    either way. A standalone artist (no known aliases) resolves to a
    single-element list containing just its own id. Used by discography
    fan-out (24.3) and the merged listing/detail reads (24.4).
    """
    artist_id = int(artist_id)
    row = conn.execute(
        "SELECT id, canonical_artist_id FROM lib2_artists WHERE id=?",
        (artist_id,),
    ).fetchone()
    if row is None:
        return [artist_id]
    canonical_id = (
        int(row["canonical_artist_id"])
        if row["canonical_artist_id"] is not None
        else int(row["id"])
    )
    alias_rows = conn.execute(
        "SELECT id FROM lib2_artists WHERE canonical_artist_id=? ORDER BY id",
        (canonical_id,),
    ).fetchall()
    return [canonical_id] + [int(r["id"]) for r in alias_rows]


def artist_album_scope_ids(conn: Any, artist_id: int) -> List[int]:
    """Return every release shown for an artist's complete alias group."""

    group = resolve_alias_group(conn, artist_id)
    marks = ",".join("?" for _ in group)
    return [int(row["album_id"]) for row in conn.execute(
        f"""SELECT album_id FROM (
                SELECT aa.album_id
                  FROM lib2_album_artists aa
                 WHERE aa.artist_id IN ({marks})
                UNION
                SELECT t.album_id
                  FROM lib2_track_artists ta
                  JOIN lib2_tracks t ON t.id=ta.track_id
                 WHERE ta.artist_id IN ({marks})
            ) ORDER BY album_id""",
        [*group, *group],
    )]


def artist_track_scope_ids(conn: Any, artist_id: int) -> List[int]:
    """Return all tracks on releases shown for an alias-group artist."""

    album_ids = artist_album_scope_ids(conn, artist_id)
    if not album_ids:
        return []
    marks = ",".join("?" for _ in album_ids)
    return [int(row["id"]) for row in conn.execute(
        f"SELECT id FROM lib2_tracks WHERE album_id IN ({marks}) "
        "ORDER BY album_id, COALESCE(disc_number, 1), track_number, id",
        album_ids,
    )]


__all__ = [
    "AliasLinkError",
    "artist_album_scope_ids",
    "artist_track_scope_ids",
    "link_artist_alias",
    "unlink_artist_alias",
    "resolve_alias_group",
]
