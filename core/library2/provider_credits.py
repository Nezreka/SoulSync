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

from typing import Any, Iterable, List, Optional

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

# every source whose track lookup gives a real artist list (ids, in credit
# order). A source that only has a string ("A & B": iTunes, Last.fm, AudioDB)
# or one name (Amazon's first artist, Qobuz's performer) is no evidence of
# who is on a track, so it is not one of them.
_SOURCES = ("spotify", "deezer", "musicbrainz", "tidal", "discogs", "jiosaavn")


def credit_services(configured: Optional[Iterable[str]] = None) -> set:
    """The credit sources this instance can ask: every one of ``_SOURCES``
    it configured (MusicBrainz always is). ``configured`` None reads the
    app's configured services; when nothing registered an answer, all."""
    if configured is None:
        try:
            from core.library2.match_status import configured_services
            configured = configured_services()
        except Exception as exc:  # noqa: BLE001
            logger.debug("configured credit services unavailable: %s", exc)
            configured = None
    if configured is None:
        return set(_SOURCES)
    return set(_SOURCES) & {str(s).lower() for s in configured}

# a manual match fetches the new release's credits from the source. tests turn
# it off (conftest) so none asks a provider; the tests of it turn it back on.
ONLINE_REFRESH = True


# what a credit refresh learned about a provider track id, one-artist answers
# included (the snapshot keeps only lists of two or more), so a reader that
# asked once never makes the refresh ask again
LIB2_CREDIT_CHECKS_DDL = """
CREATE TABLE IF NOT EXISTS lib2_credit_checks (
    source TEXT NOT NULL,                             -- 'spotify' | 'deezer'
    provider_id TEXT NOT NULL,                        -- the track's id there
    names_json TEXT NOT NULL DEFAULT '[]',            -- its credited artists
    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (source, provider_id)
) WITHOUT ROWID
"""
# a provider can re-credit a track; an answer this old is asked again
CREDIT_CHECK_TTL_DAYS = 180
# a track no search found at a source is not searched there again for this long
UNMATCHED_RETRY_DAYS = 30
# an empty answer (no results at all) may be an outage: asked again sooner
EMPTY_RETRY_DAYS = 3
# MusicBrainz and Tidal searches omit the album needed for a match.
# Discogs has no track search; its credits come from a release's tracklist.
_NO_TEXT_SEARCH = frozenset({"musicbrainz", "tidal", "discogs"})


def ensure_provider_credits_schema(cursor: Any) -> None:
    """The snapshot table, its provider-id index and the cleanup triggers."""
    cursor.execute(LIB2_PROVIDER_CREDITS_DDL)
    cursor.execute(LIB2_CREDIT_CHECKS_DDL)
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
    if service == "musicbrainz":
        return "SELECT id FROM lib2_artists WHERE musicbrainz_id = ? LIMIT 2"
    if service in _SOURCES:
        from core.library2.provider_ids import external_id_sql
        return (f"SELECT id FROM lib2_artists"
                f" WHERE {external_id_sql('external_ids', service)} = ? LIMIT 2")
    return None


def _credited(artists: Optional[Iterable[Any]], service: Optional[str] = None) -> list:
    """``[(provider id, name)]`` in credit order, artists without an id left out."""
    from core.various_artists_ids import is_various_artists_id
    out = []
    for artist in artists or []:
        if isinstance(artist, dict):
            pid = artist.get("id")
            # "Various Artists" is no artist: left out like one with no id,
            # or every compilation went to whoever held its id (#2229)
            if pid not in (None, "") and not is_various_artists_id(
                    pid, artist.get("source") or service):
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


def _credited_already(conn: Any, entity_type: str, entity_id: int, artist_id: int) -> bool:
    """another artist row with this artist's name, spelling aside, is
    already credited here: "The Chamber Orchestra Of London" from the source
    beside the "Chamber Orchestra of London" the files named. a second credit
    is the same orchestra twice in ARTIST (#2217)"""
    from core.text.normalize import credit_name_key
    junction, column = (("lib2_track_artists", "track_id") if entity_type == "track"
                        else ("lib2_album_artists", "album_id"))
    row = conn.execute("SELECT name FROM lib2_artists WHERE id=?", (int(artist_id),)).fetchone()
    key = credit_name_key(row[0] if row else "")
    if not key:
        return False
    return any(credit_name_key(name) == key for (name,) in conn.execute(
        f"SELECT a.name FROM {junction} j JOIN lib2_artists a ON a.id = j.artist_id"
        f" WHERE j.{column} = ? AND j.artist_id <> ?", (int(entity_id), int(artist_id))))


def _insert_credit(conn: Any, entity_type: str, entity_id: int, artist_id: int,
                   position: int) -> int:
    if _credited_already(conn, entity_type, entity_id, artist_id):
        return 0
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
    credits name one of its provider ids. Called when an artist gains an id at
    a credit source; one lookup per id. A one-artist list adds nothing (its
    artist is the one the entity is filed under). Returns how many junction
    rows were added. Never raises."""
    added = 0
    try:
        row = conn.execute(
            "SELECT spotify_id, musicbrainz_id, external_ids FROM lib2_artists WHERE id=?",
            (int(artist_id),)).fetchone()
        if row is None:
            return 0
        from core.library2.provider_ids import external_id_sql, parse_external_ids
        external = parse_external_ids(row[2])
        ids = {source: str((row[0] if source == "spotify" else row[1] if source == "musicbrainz"
                            else external.get(source)) or "").strip() for source in _SOURCES}
        canon = conn.execute("SELECT COALESCE(canonical_artist_id, id) FROM lib2_artists WHERE id=?",
                             (int(artist_id),)).fetchone()[0]
        for source, provider_id in ids.items():
            if not provider_id:
                continue
            # an id two different artists hold names neither for sure (the
            # linker's own rule): no credits from it
            column = ("a.spotify_id" if source == "spotify" else "a.musicbrainz_id"
                      if source == "musicbrainz" else external_id_sql("a.external_ids", source))
            shared = conn.execute(
                f"SELECT 1 FROM lib2_artists a WHERE {column} = ? "
                "AND COALESCE(a.canonical_artist_id, a.id) <> ? LIMIT 1",
                (provider_id, canon)).fetchone()
            if shared:
                continue
            for entity_type, entity_id, position in conn.execute(
                    "SELECT p.entity_type, p.entity_id, p.position FROM lib2_provider_credits p"
                    " WHERE p.source=? AND p.source_artist_id=?"
                    " AND (SELECT COUNT(*) FROM lib2_provider_credits q"
                    "      WHERE q.entity_type=p.entity_type AND q.entity_id=p.entity_id"
                    "        AND q.source=p.source) > 1", (source, provider_id)).fetchall():
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
    credited = _credited(artists, service)
    drop_source_credits(conn, entity_type, entity_id, service,
                        keep_provider_ids=[pid for pid, _name in credited])
    if not credited:
        return 0
    # the list is kept whole, a one-artist list too: "Above & Beyond" credited
    # alone is the evidence that keeps a band whole
    try:
        _store_snapshot(conn, entity_type, entity_id, service, credited)
    except Exception as exc:  # noqa: BLE001 - a schema without the table
        logger.debug("credit snapshot for %s %s skipped: %s", entity_type, entity_id, exc)
    if len(credited) < 2:
        return 0  # a sole artist is the one it is filed under
    added = 0
    try:
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
    if service == "musicbrainz" and entity_type == "album":
        from core.musicbrainz_client import artist_credit_list
        client = _credit_client("musicbrainz")
        return artist_credit_list(client.get_release(provider_id, includes=["artist-credits"])) \
            if client is not None else None
    if service in ("musicbrainz", "tidal", "discogs", "jiosaavn") and entity_type == "track":
        # each client reads its own format: [{"id", "name"}] in credit order
        client = _credit_client(service)
        return client.track_artist_credits(provider_id) if client is not None else None
    return None


def _credit_client(service: str) -> Any:
    """The client a credit source answers through, or None."""
    from core.metadata import registry
    if service == "musicbrainz":
        # the registry hands out the search facade; the lookups by id are on
        # the MusicBrainz client it wraps (one rate limit for both)
        facade = registry.get_musicbrainz_client()
        return getattr(facade, "_client", facade)
    if service == "discogs":
        return registry.get_discogs_client()
    if service == "jiosaavn":
        return registry.get_jiosaavn_client()
    if service == "tidal":
        # Tidal needs the signed-in client the enrichment worker holds
        from core.library import service_search
        worker = getattr(service_search, "tidal_enrichment_worker", None)
        return getattr(worker, "client", None)
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


def _has_snapshot(database: Any, entity_type: str, entity_id: int, service: str) -> bool:
    conn = None
    try:
        conn = database._get_connection()
        return conn.execute(
            "SELECT 1 FROM lib2_provider_credits WHERE entity_type=? AND entity_id=? AND source=? "
            "LIMIT 1", (entity_type, int(entity_id), service)).fetchone() is not None
    except Exception:  # noqa: BLE001 - unreadable: fetch
        return False
    finally:
        if conn is not None:
            conn.close()


def keep_match_credits(database: Any, entity_type: str, entity_id: int, service: str,
                       provider_id: Optional[str], artists: Optional[list] = None) -> int:
    """An enrichment worker matched a track at a credit source: keep that
    source's artist list for it, as the evidence every credit decision reads.
    ``artists``: the list the match already carries (no request); otherwise
    it is fetched by id. Only while the entity still holds that id. Returns
    how many credits were added. Never raises."""
    service = str(service or "").lower()
    if service not in _SOURCES or entity_type not in ("track", "album") or not provider_id:
        return 0
    if artists is None:
        if not ONLINE_REFRESH or _has_snapshot(database, entity_type, entity_id, service):
            # a list for this match is kept already (a rematch drops it first)
            return 0
        return refresh_match_credits(database, entity_type, entity_id, service, str(provider_id))
    conn = None
    try:
        from core.library2.worker_support import stored_provider_id
        conn = database._get_connection()
        if str(stored_provider_id(conn, entity_type, entity_id, service) or "") != str(provider_id):
            return 0
        added = link_credited_artists(conn, entity_type, int(entity_id), service, artists)
        conn.commit()
        return added
    except Exception as exc:  # noqa: BLE001 - a credit list is evidence, never a blocker
        logger.debug("%s credits for %s %s not kept: %s", service, entity_type, entity_id, exc)
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


def checked_track_credits(conn: Any, track_id: Any) -> list:
    """``[(source, names)]`` the credit refresh recorded for this track's
    current provider ids, fresh ones only. Never raises."""
    out = []
    try:
        from core.library2.worker_support import stored_provider_id
        for service in _SOURCES:
            provider_id = str(stored_provider_id(conn, "track", track_id, service) or "")
            if not provider_id:
                continue
            row = conn.execute(
                "SELECT names_json FROM lib2_credit_checks WHERE source=? AND provider_id=? "
                "AND checked_at >= datetime('now', ?)",
                (service, provider_id, f"-{CREDIT_CHECK_TTL_DAYS} days")).fetchone()
            if row is not None:
                import json
                names = [str(n) for n in json.loads(row[0] or "[]") if str(n or "").strip()]
                out.append((service, names))
    except Exception as exc:  # noqa: BLE001 - a reader never fails on the ledger
        logger.debug("credit checks for track %s not read: %s", track_id, exc)
    return out


def _record_check(conn: Any, service: str, provider_id: str, artists: list) -> None:
    import json
    names = [str(a.get("name") if isinstance(a, dict) else a or "").strip() for a in artists]
    conn.execute(LIB2_CREDIT_CHECKS_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO lib2_credit_checks(source, provider_id, names_json, checked_at)"
        " VALUES(?, ?, ?, CURRENT_TIMESTAMP)",
        (service, str(provider_id), json.dumps([n for n in names if n], ensure_ascii=False)))


def _unmatched_key(conn: Any, track_id: Any, hints: Iterable[str]) -> str:
    """The identity of one search for a track: its title, album and album
    artist and the names it is looked up under. lib2_credit_checks is keyed
    by a source's own id; a search that found nothing has none, so this
    stands in, marked as such. A corrected title or another proposed name is
    another search, asked again."""
    import hashlib
    row = conn.execute(
        "SELECT t.title, al.title, ar.name FROM lib2_tracks t "
        "LEFT JOIN lib2_albums al ON al.id=t.album_id "
        "LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id WHERE t.id=?",
        (int(track_id),)).fetchone()
    parts = [str(v or "").strip().casefold() for v in (row or ())]
    parts += [h.casefold() for h in _hint_names(hints)]
    digest = hashlib.sha1("\x1f".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"unmatched:track:{int(track_id)}:{digest}"


def _searched_lately(conn: Any, service: str, key: str) -> bool:
    try:
        row = conn.execute(
            "SELECT names_json FROM lib2_credit_checks WHERE source=? AND provider_id=? "
            "AND checked_at >= datetime('now', ?)",
            (service, key, f"-{UNMATCHED_RETRY_DAYS} days")).fetchone()
        if row is None:
            return False
        if row[0] != '"empty"':
            return True
        return conn.execute(
            "SELECT 1 FROM lib2_credit_checks WHERE source=? AND provider_id=? "
            "AND checked_at >= datetime('now', ?)",
            (service, key, f"-{EMPTY_RETRY_DAYS} days")).fetchone() is not None
    except Exception:  # noqa: BLE001 - no ledger yet: search
        return False


def _record_unmatched(conn: Any, service: str, key: str, *, empty: bool = False) -> None:
    conn.execute(LIB2_CREDIT_CHECKS_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO lib2_credit_checks(source, provider_id, names_json, checked_at)"
        " VALUES(?, ?, ?, CURRENT_TIMESTAMP)", (service, key, '"empty"' if empty else '[]'))


# a combined artist's parts a track is looked up under, at most
_MAX_HINTS = 4


def _hint_names(hints: Iterable[str]) -> List[str]:
    """distinct, non-empty, in order, capped"""
    out: List[str] = []
    for hint in hints or ():
        name = str(hint or "").strip()
        if name and name.casefold() not in {o.casefold() for o in out}:
            out.append(name)
    return out[:_MAX_HINTS]


def ensure_track_credits(database: Any, track_id: int, services: Optional[Iterable[str]] = None,
                         *, match: bool = True, artist_hints: Iterable[str] = ()) -> bool:
    """Make sure the library holds a track's credit list from each of
    ``services`` (Spotify, Deezer; default both), for readers that need it
    (the Split Artists job, the credit healers). Uses the writers that exist:

    - a track with no id at a service is matched there first by the native
      enrichment (``enrich_native_entity_for_service``), when ``match``:
      under its album's artist, then under each of ``artist_hints`` (the
      parts of a combined artist: "ODESZA; RY X" matches no artist there,
      "ODESZA" does)
    - a track with an id gets its credits fetched by id and stored as its
      snapshot (``link_credited_artists``, which also credits every guest
      the library has); the answer, a one-artist one too, is recorded in
      ``lib2_credit_checks``

    A service the track already has a snapshot or a fresh check for is not
    asked. Returns whether anything new was stored. Never raises: a reader
    without the data decides from what it has."""
    if not ONLINE_REFRESH:
        return False
    from core.library2 import locks

    allowed = None if services is None else set(services)
    wanted = [s for s in _SOURCES if allowed is None or s in allowed]
    stored_any = False
    for service in wanted:
        conn = None
        exact = None   # (recording id, credits) MusicBrainz gave for the ISRC
        try:
            from core.library2.worker_support import stored_provider_id
            conn = database._get_connection()
            if locks.blocks_entity("track", track_id, "metadata", "Credit refresh", conn=conn):
                continue
            provider_id = str(stored_provider_id(conn, "track", track_id, service) or "")
            has_snapshot = conn.execute(
                "SELECT 1 FROM lib2_provider_credits WHERE entity_type='track' AND entity_id=?"
                " AND source=? LIMIT 1", (int(track_id), service)).fetchone() is not None
            if has_snapshot or (provider_id and any(
                    s == service for s, _n in checked_track_credits(conn, track_id))):
                continue
            search_key = (_unmatched_key(conn, track_id, artist_hints)
                          if not provider_id and match and service not in _NO_TEXT_SEARCH else "")
            if search_key and _searched_lately(conn, service, search_key):
                continue    # this very lookup found nothing there lately
            if not provider_id and service == "musicbrainz":
                # an ISRC names the recording exactly, no text search
                row = conn.execute("SELECT isrc FROM lib2_tracks WHERE id=?",
                                   (int(track_id),)).fetchone()
                isrc = str((row[0] if row else None) or "").strip()
                isrc_key = f"unmatched:isrc:{isrc.upper().replace('-', '')}"
                if not isrc or _searched_lately(conn, service, isrc_key):
                    continue
                client = _credit_client("musicbrainz")
                if client is not None:
                    try:
                        exact = client.track_artist_credits_by_isrc(isrc)
                    except Exception as exc:  # noqa: BLE001 - an outage: ask again next time
                        logger.debug("isrc lookup for track %s failed: %s", track_id, exc)
                        continue
                    if not exact:
                        _record_unmatched(conn, service, isrc_key)   # it answered: none
                        conn.commit()
            if search_key:
                from core.library2.native_enrich import enrich_native_entity_for_service
                answered = False
                for artist in (None, *_hint_names(artist_hints)):
                    result = enrich_native_entity_for_service(conn, "track", int(track_id),
                                                              service, artist_name=artist)
                    conn.commit()
                    answered = answered or bool((result or {}).get("candidates"))
                    if (result or {}).get("success") and \
                            (result or {}).get("source", service) == service:
                        provider_id = str(stored_provider_id(conn, "track", track_id, service) or "")
                    if provider_id or (result or {}).get("locked"):
                        break
                # an answer with results none of which fit (or several did)
                # is remembered for the full time; an empty one for a few
                # days only, it can be an outage the search helpers folded
                if not provider_id and not (result or {}).get("locked"):
                    _record_unmatched(conn, service, search_key, empty=not answered)
                    conn.commit()
            if not provider_id and not exact:
                continue
        except Exception as exc:  # noqa: BLE001 - see docstring
            logger.debug("credit refresh for track %s via %s skipped: %s", track_id, service, exc)
            continue
        finally:
            if conn is not None:
                conn.close()
        # the provider call runs with no connection open
        if exact:
            checked_id, artists = exact
        else:
            checked_id = provider_id
            try:
                artists = _fetch_credits(service, "track", provider_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("credit fetch for track %s via %s failed: %s", track_id, service, exc)
                artists = None
        if artists is None:
            continue  # unreachable: nothing recorded, asked again next time
        conn = None
        try:
            from core.library2.worker_support import stored_provider_id
            conn = database._get_connection()
            if locks.blocks_entity("track", track_id, "metadata", "Credit refresh", conn=conn):
                continue
            if str(stored_provider_id(conn, "track", track_id, service) or "") != provider_id:
                continue  # matched again while the fetch ran
            link_credited_artists(conn, "track", int(track_id), service, artists)
            _record_check(conn, service, checked_id, artists)
            conn.commit()
            stored_any = True
        except Exception as exc:  # noqa: BLE001
            logger.debug("credits for track %s via %s not stored: %s", track_id, service, exc)
        finally:
            if conn is not None:
                conn.close()
    return stored_any


CREDIT_SOURCES = _SOURCES

__all__ = ["CREDIT_SOURCES", "checked_track_credits", "credit_services", "drop_source_credits",
           "ensure_provider_credits_schema", "keep_match_credits",
           "ensure_track_credits",
           "link_credited_artists", "materialize_artist_credits",
           "refresh_match_credits", "schedule_match_credits"]
