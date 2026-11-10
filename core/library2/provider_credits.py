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

# a manual match fetches the new release's credits from the source. tests turn
# it off (conftest) so none asks a provider; the tests of it turn it back on.
ONLINE_REFRESH = True


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
    # an artist that loses (or changes) a spotify/deezer id loses the guest
    # credits that id brought, unless its current ids still name it there.
    # 3.6.1 linked credits at read time through the artist's current ids, so a
    # cleared borrowed id unlinked them at once; materialized rows stayed for
    # good. a trigger catches every path that changes the id (a match, the
    # borrowed-identity healer, Dedupe Source IDs).
    deezer_new = "TRIM(CAST(json_extract(NEW.external_ids, '$.deezer') AS TEXT))"
    deezer_old = "TRIM(CAST(json_extract(OLD.external_ids, '$.deezer') AS TEXT))"
    still = (f"(source = 'spotify' AND source_artist_id = NEW.spotify_id) OR "
             f"(source = 'deezer' AND source_artist_id = {deezer_new})")
    for source, column, old_id, new_id in (
            ("spotify", "spotify_id", "OLD.spotify_id", "NEW.spotify_id"),
            ("deezer", "external_ids", deezer_old, deezer_new)):
        cursor.execute(f"""
            CREATE TRIGGER IF NOT EXISTS trg_lib2_artist_{source}_credits_unlink
            AFTER UPDATE OF {column} ON lib2_artists
            WHEN COALESCE({old_id}, '') <> '' AND COALESCE({old_id}, '') <> COALESCE({new_id}, '')
            BEGIN
                DELETE FROM lib2_track_artists
                 WHERE artist_id = NEW.id AND role = 'featured'
                   AND track_id IN (SELECT entity_id FROM lib2_provider_credits
                                     WHERE entity_type = 'track' AND source = '{source}'
                                       AND source_artist_id = {old_id})
                   AND track_id NOT IN (SELECT entity_id FROM lib2_provider_credits
                                         WHERE entity_type = 'track' AND ({still}));
                DELETE FROM lib2_album_artists
                 WHERE artist_id = NEW.id AND role = 'featured'
                   AND album_id IN (SELECT entity_id FROM lib2_provider_credits
                                     WHERE entity_type = 'album' AND source = '{source}'
                                       AND source_artist_id = {old_id})
                   AND album_id NOT IN (SELECT entity_id FROM lib2_provider_credits
                                         WHERE entity_type = 'album' AND ({still}));
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
        canon = conn.execute("SELECT COALESCE(canonical_artist_id, id) FROM lib2_artists WHERE id=?",
                             (int(artist_id),)).fetchone()[0]
        for source, provider_id in ids.items():
            if not provider_id:
                continue
            # an id two different artists hold names neither for sure (the
            # linker's own rule): no credits from it
            from core.library2.provider_ids import external_id_sql
            column = ("a.spotify_id" if source == "spotify"
                      else external_id_sql("a.external_ids", "deezer"))
            shared = conn.execute(
                f"SELECT 1 FROM lib2_artists a WHERE {column} = ? "
                "AND COALESCE(a.canonical_artist_id, a.id) <> ? LIMIT 1",
                (provider_id, canon)).fetchone()
            if shared:
                continue
            for entity_type, entity_id, position in conn.execute(
                    "SELECT entity_type, entity_id, position FROM lib2_provider_credits"
                    " WHERE source=? AND source_artist_id=?", (source, provider_id)).fetchall():
                added += _insert_credit(conn, entity_type, entity_id, artist_id, position)
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.debug("credits for artist %s not materialized: %s", artist_id, exc)
    return added


def _resolve_credit_ids(conn: Any, service: str, provider_ids: Iterable[str]) -> set:
    """lib2 artist ids for a source's credited provider ids (unambiguous only)."""
    sql = _artist_id_sql(service)
    out: set = set()
    if not sql:
        return out
    for provider_id in provider_ids:
        if not provider_id:
            continue
        rows = conn.execute(sql, (str(provider_id),)).fetchall()
        if len(rows) == 1:
            out.add(int(rows[0][0]))
    return out


def _still_credited_elsewhere(conn: Any, entity_type: str, entity_id: int,
                              service: str) -> set:
    """Artists this entity credits for a reason other than ``service``'s list:
    another source's snapshot, its own tags (track artist / title ``feat.``),
    or, for an album, any of its tracks."""
    keep: set = set()
    for other in _SOURCES:
        if other == service:
            continue
        ids = [r[0] for r in conn.execute(
            "SELECT source_artist_id FROM lib2_provider_credits"
            " WHERE entity_type=? AND entity_id=? AND source=?",
            (entity_type, int(entity_id), other)).fetchall()]
        keep |= _resolve_credit_ids(conn, other, ids)
    if entity_type == "track":
        row = conn.execute("SELECT title, track_artist FROM lib2_tracks WHERE id=?",
                           (int(entity_id),)).fetchone()
        if row is not None:
            from core.library2.importer import (featured_from_title, normalize_name,
                                                split_artist_credits)
            keys = {normalize_name(n) for n in
                    split_artist_credits(row[1] or "") + featured_from_title(row[0] or "")}
            keys.discard("")
            if keys:
                marks = ",".join("?" for _ in keys)
                keep |= {int(r[0]) for r in conn.execute(
                    f"SELECT ta.artist_id FROM lib2_track_artists ta"
                    f" JOIN lib2_artists a ON a.id = ta.artist_id"
                    f" WHERE ta.track_id=? AND a.name_key IN ({marks})",
                    (int(entity_id), *sorted(keys))).fetchall()}
    else:
        keep |= {int(r[0]) for r in conn.execute(
            "SELECT DISTINCT ta.artist_id FROM lib2_tracks t"
            " JOIN lib2_track_artists ta ON ta.track_id = t.id WHERE t.album_id=?",
            (int(entity_id),)).fetchall()}
    return keep


def drop_source_credits(conn: Any, entity_type: str, entity_id: int,
                        service: str, *, keep_provider_ids: Iterable[str] = ()) -> int:
    """Forget ``service``'s credit list for this track/album, and the featured
    rows that list put there. A source's credits belong to the release it
    matched: after a rematch or a cleared match they name the wrong people
    (3.6.1 dropped them the same way). A featured row the entity still owes to
    another source, to its own tags or (albums) to one of its tracks stays,
    and so does every primary credit. ``keep_provider_ids``: the new list's
    ids, so a replacement keeps the guests both lists share. Returns how many
    featured rows went. Never raises."""
    service = str(service or "").lower()
    if service not in _SOURCES or entity_type not in ("track", "album"):
        return 0
    removed = 0
    try:
        old = [r[0] for r in conn.execute(
            "SELECT source_artist_id FROM lib2_provider_credits"
            " WHERE entity_type=? AND entity_id=? AND source=?",
            (entity_type, int(entity_id), service)).fetchall()]
        if not old:
            return 0
        kept = {str(k) for k in keep_provider_ids if k not in (None, "")}
        gone = _resolve_credit_ids(conn, service, [i for i in old if str(i) not in kept])
        gone -= _resolve_credit_ids(conn, service, kept)
        if gone:
            gone -= _still_credited_elsewhere(conn, entity_type, entity_id, service)
        for artist_id in sorted(gone):
            if entity_type == "track":
                cur = conn.execute(
                    "DELETE FROM lib2_track_artists WHERE track_id=? AND artist_id=?"
                    " AND role='featured'", (int(entity_id), artist_id))
            else:
                cur = conn.execute(
                    "DELETE FROM lib2_album_artists WHERE album_id=? AND artist_id=?"
                    " AND role='featured'", (int(entity_id), artist_id))
            removed += max(int(cur.rowcount or 0), 0)
        conn.execute(
            "DELETE FROM lib2_provider_credits WHERE entity_type=? AND entity_id=? AND source=?",
            (entity_type, int(entity_id), service))
    except Exception as exc:  # noqa: BLE001 - a schema without the table
        logger.debug("old %s credits for %s %s not dropped: %s", service, entity_type,
                     entity_id, exc)
    return removed


def link_credited_artists(conn: Any, entity_type: str, entity_id: int,
                          service: str, artists: Optional[Iterable[Any]]) -> int:
    """Add a featured credit for every artist ``service`` credits on this
    track/album who is a library artist, and keep the whole credit list for
    the ones who join later. The source's previous list is replaced: a guest
    it no longer credits loses the featured row that list gave them (see
    :func:`drop_source_credits`). ``artists`` None means the credits are
    unknown this time, and the stored ones stay. Returns how many rows were
    added. Never raises: a credit is decoration on a match that already
    succeeded."""
    service = str(service or "").lower()
    sql = _artist_id_sql(service)
    if not sql or entity_type not in ("track", "album") or artists is None:
        return 0
    credited = _credited(artists)
    drop_source_credits(conn, entity_type, entity_id, service,
                        keep_provider_ids=[pid for pid, _name in credited])
    if len(credited) < 2:
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


def _fetch_credits(service: str, entity_type: str, provider_id: str) -> Optional[list]:
    """The new match's credited artists, straight from the source. None when
    the source can't say (not configured, rate limited, nothing back). Never
    the fallback source: its artist ids are another provider's."""
    if service == "spotify":
        from core.metadata.registry import get_spotify_client
        client = get_spotify_client()
        if client is None:
            return None
        if entity_type == "album":
            data = client.get_album(provider_id, allow_fallback=False)
            return (data or {}).get("artists") if data else None
        data = client.get_track_details(provider_id, allow_fallback=False)
        if not data:
            return None
        return (data.get("raw_data") or data).get("artists")
    if service == "deezer":
        from core.metadata.registry import get_deezer_client
        client = get_deezer_client()
        if client is None:
            return None
        fetch = client.get_album_raw if entity_type == "album" else client.get_track_raw
        data = fetch(int(provider_id))
        return (data or {}).get("contributors") if data else None
    return None


def refresh_match_credits(database: Any, entity_type: str, entity_id: int,
                          service: str, provider_id: str) -> int:
    """Fetch and link the credits of a match the user just set. The enrichment
    worker treats a manual match as settled and never revisits it, so without
    this the new release's guests never showed (3.6.1 queued a refetch).
    Writes only if the entity still holds ``provider_id`` when the fetch is
    back. Returns how many rows were added. Never raises."""
    service = str(service or "").lower()
    if service not in _SOURCES or entity_type not in ("track", "album") or not provider_id:
        return 0
    try:
        artists = _fetch_credits(service, entity_type, str(provider_id))
    except Exception as exc:  # noqa: BLE001 - a credit is decoration
        logger.debug("credit fetch for %s %s (%s %s) failed: %s", entity_type, entity_id,
                     service, provider_id, exc)
        return 0
    if artists is None:
        return 0
    conn = None
    try:
        from core.library2.worker_support import stored_provider_id
        conn = database._get_connection()
        if str(stored_provider_id(conn, entity_type, entity_id, service) or "") != str(provider_id):
            return 0  # matched again while the fetch ran
        added = link_credited_artists(conn, entity_type, int(entity_id), service, artists)
        conn.commit()
        return added
    except Exception as exc:  # noqa: BLE001 - see docstring
        logger.debug("credits for %s %s not refreshed: %s", entity_type, entity_id, exc)
        return 0
    finally:
        if conn is not None:
            conn.close()


def schedule_match_credits(database: Any, entity_type: str, entity_id: int,
                           service: str, provider_id: Optional[str]):
    """:func:`refresh_match_credits` off the request thread, after the caller
    committed (a provider call inside a request is how manual matches timed
    out, manualmatch25-01). Returns the thread, or None when there is nothing
    to fetch. Never raises."""
    entity_type = {"tracks": "track", "albums": "album"}.get(str(entity_type), str(entity_type))
    service = str(service or "").lower()
    if (not ONLINE_REFRESH or service not in _SOURCES
            or entity_type not in ("track", "album") or not provider_id):
        return None
    try:
        import threading
        thread = threading.Thread(
            target=refresh_match_credits,
            args=(database, entity_type, int(entity_id), service, str(provider_id)),
            name="lib2-match-credits", daemon=True)
        thread.start()
        return thread
    except Exception as exc:  # noqa: BLE001
        logger.debug("credit refresh not scheduled: %s", exc)
        return None


__all__ = ["drop_source_credits", "ensure_provider_credits_schema",
           "link_credited_artists", "materialize_artist_credits",
           "refresh_match_credits", "schedule_match_credits"]
