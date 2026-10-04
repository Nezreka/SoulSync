"""Every artist a metadata source credits on a track or release (upstream 3.4.6).

A track is filed under one album artist, so "calvin harris feat. rihanna" did
not show on rihanna's page, and "watch the throne" not on kanye's. Spotify and
Deezer hand back the full credit list, with ids, when a worker matches a track
or an album. Library v2 already has the junctions for it -- ``lib2_track_artists``
and ``lib2_album_artists`` -- and every artist page reads through them, so a
credit is one more junction row.

Only for artists already in the library: a featured artist nobody owns a
release of does not become an artist row (the grid and the enrichment workers
would fill up with them). The credit list itself is kept as a snapshot in
``lib2_provider_credits`` -- provider, provider artist id, name, position --
the way upstream keeps its side table, and the junction is materialized the
moment that artist gains the provider id (feature-parity A04). Before this a
guest who joined the library later never got the credits of the tracks matched
before them.
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.provider_credits")


LIB2_PROVIDER_CREDITS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_provider_credits (
    entity_type TEXT NOT NULL,                        -- 'track' | 'album'
    entity_id INTEGER NOT NULL,
    source TEXT NOT NULL,                             -- 'spotify' | 'deezer'
    position INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    source_artist_id TEXT,
    PRIMARY KEY (entity_type, entity_id, source, position)
) WITHOUT ROWID
"""

_SOURCES = ("spotify", "deezer")


def ensure_provider_credits_schema(cursor: Any) -> None:
    """The snapshot table, its provider-id index and the cleanup triggers."""
    cursor.execute(LIB2_PROVIDER_CREDITS_DDL)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_lib2_provider_credits_artist "
                   "ON lib2_provider_credits(source, source_artist_id)")
    for entity, table in (("track", "lib2_tracks"), ("album", "lib2_albums")):
        cursor.execute(f"""
            CREATE TRIGGER IF NOT EXISTS trg_lib2_provider_credits_{entity}_gone
            AFTER DELETE ON {table}
            BEGIN
                DELETE FROM lib2_provider_credits
                 WHERE entity_type = '{entity}' AND entity_id = OLD.id;
            END""")


def _artist_id_sql(service: str) -> Optional[str]:
    """How a source's artist id is found on a lib2 artist row -- through the
    same expression the index is built on (a hand-written json_extract scans
    the table and misses ids stored as JSON numbers). LIMIT 2: an id on two
    artists is a corrupt mapping, and a credit must not guess."""
    if service == "spotify":
        return "SELECT id FROM lib2_artists WHERE spotify_id = ? LIMIT 2"
    if service == "deezer":
        from core.library2.provider_ids import external_id_sql
        return (f"SELECT id FROM lib2_artists"
                f" WHERE {external_id_sql('external_ids', 'deezer')} = ? LIMIT 2")
    return None


def _credited(artists: Optional[Iterable[Any]]) -> list:
    """``[(provider id, name)]`` in credit order, artists without an id left out."""
    out = []
    for artist in artists or []:
        if isinstance(artist, dict):
            pid = artist.get("id")
            if pid not in (None, ""):
                out.append((str(pid), str(artist.get("name") or "")))
    return out


def _store_snapshot(conn: Any, entity_type: str, entity_id: int, service: str,
                    credited: list) -> None:
    """Replace this source's credit snapshot for the entity. A source's credits
    belong to its current match, so the previous list goes."""
    conn.execute(
        "DELETE FROM lib2_provider_credits WHERE entity_type=? AND entity_id=? AND source=?",
        (entity_type, int(entity_id), service))
    conn.executemany(
        "INSERT INTO lib2_provider_credits(entity_type, entity_id, source, position,"
        " name, source_artist_id) VALUES(?,?,?,?,?,?)",
        [(entity_type, int(entity_id), service, position, name, pid)
         for position, (pid, name) in enumerate(credited)])


def _insert_credit(conn: Any, entity_type: str, entity_id: int, artist_id: int,
                   position: int) -> int:
    if entity_type == "track":
        cur = conn.execute(
            "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position)"
            " SELECT ?, ?, 'featured', ? WHERE EXISTS (SELECT 1 FROM lib2_tracks WHERE id=?)",
            (int(entity_id), int(artist_id), position, int(entity_id)))
    else:
        cur = conn.execute(
            "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role)"
            " SELECT ?, ?, 'featured' WHERE EXISTS (SELECT 1 FROM lib2_albums WHERE id=?)",
            (int(entity_id), int(artist_id), int(entity_id)))
    return max(int(cur.rowcount or 0), 0)


def materialize_artist_credits(conn: Any, artist_id: int) -> int:
    """Credit ``artist_id`` on every track and album whose stored provider
    credits name one of its provider ids. Called when an artist gains a Spotify
    or Deezer id; one indexed lookup per id. Returns how many junction rows were
    added. Never raises."""
    added = 0
    try:
        row = conn.execute("SELECT spotify_id, external_ids FROM lib2_artists WHERE id=?",
                           (int(artist_id),)).fetchone()
        if row is None:
            return 0
        from core.library2.provider_ids import parse_external_ids
        ids = {"spotify": str(row[0] or "").strip(),
               "deezer": str(parse_external_ids(row[1]).get("deezer") or "").strip()}
        for source, provider_id in ids.items():
            if not provider_id:
                continue
            for entity_type, entity_id, position in conn.execute(
                    "SELECT entity_type, entity_id, position FROM lib2_provider_credits"
                    " WHERE source=? AND source_artist_id=?", (source, provider_id)).fetchall():
                added += _insert_credit(conn, entity_type, entity_id, artist_id, position)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.debug("credits for artist %s not materialized: %s", artist_id, exc)
    return added


def link_credited_artists(conn: Any, entity_type: str, entity_id: int,
                          service: str, artists: Optional[Iterable[Any]]) -> int:
    """Add a featured credit for every artist ``service`` credits on this
    track/album who is a library artist, and keep the whole credit list for
    the ones who join later. Returns how many rows were added. Never removes a
    credit and never raises: a credit is decoration on a match that already
    succeeded."""
    service = str(service or "").lower()
    sql = _artist_id_sql(service)
    credited = _credited(artists)
    if not sql or len(credited) < 2 or entity_type not in ("track", "album"):
        return 0  # a sole artist is the one it is filed under
    added = 0
    try:
        try:
            _store_snapshot(conn, entity_type, entity_id, service, credited)
        except Exception as exc:  # noqa: BLE001 - a schema without the table
            logger.debug("credit snapshot for %s %s skipped: %s", entity_type, entity_id, exc)
        for position, (provider_id, _name) in enumerate(credited):
            rows = conn.execute(sql, (provider_id,)).fetchall()
            if len(rows) != 1:
                continue  # not in the library, or ambiguous
            added += _insert_credit(conn, entity_type, entity_id, rows[0][0], position)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.debug("credits for %s %s from %s skipped: %s", entity_type, entity_id, service, exc)
    return added


__all__ = ["ensure_provider_credits_schema", "link_credited_artists",
           "materialize_artist_credits"]
