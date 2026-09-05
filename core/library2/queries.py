"""Read queries for the Library v2 API.

All functions take an open sqlite3 connection (``row_factory = sqlite3.Row``) and
return plain dicts/lists ready to serialize. Roll-up counts go through the
``lib2_album_artists`` / ``lib2_track_artists`` junctions so a release or track that
credits multiple artists is counted under *each* of them (a song by two artists
shows under both, but is stored once).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .metadata_overrides import project_metadata, project_metadata_many
from .paths import library_relative_path
from .status import compute_metadata_gaps, file_status, metadata_scan_status, quality_tier
from .track_files import primary_order

_SORTS = {
    "name": "a.sort_name COLLATE NOCASE, a.name COLLATE NOCASE",
    "added": "a.added_at DESC",
    "albums": "album_count DESC, a.name COLLATE NOCASE",
    "tracks": "track_count DESC, a.name COLLATE NOCASE",
}


def _media_server_sources_many(conn, entity_type: str, entity_ids: List[int]
                               ) -> Dict[int, List[str]]:
    """Positive Plex/Jellyfin/Navidrome recognitions for API projections."""
    ids = sorted({int(value) for value in entity_ids if value is not None})
    if not ids:
        return {}
    marks = ",".join("?" for _ in ids)
    if entity_type == "artist":
        rows = conn.execute(
            f"""SELECT COALESCE(a.canonical_artist_id,a.id) AS entity_id,
                       m.server_source
                  FROM lib2_media_server_mappings m
                  JOIN lib2_artists a ON a.id=m.entity_id
                 WHERE m.entity_type='artist'
                   AND COALESCE(a.canonical_artist_id,a.id) IN ({marks})
                   AND m.match_status='recognized'
                 GROUP BY COALESCE(a.canonical_artist_id,a.id),m.server_source
                 ORDER BY m.server_source""",
            ids,
        ).fetchall()
    else:
        rows = conn.execute(
            f"""SELECT entity_id,server_source
                  FROM lib2_media_server_mappings
                 WHERE entity_type=? AND entity_id IN ({marks})
                   AND match_status='recognized'
                 GROUP BY entity_id,server_source ORDER BY server_source""",
            [entity_type, *ids],
        ).fetchall()
    result = {entity_id: [] for entity_id in ids}
    for row in rows:
        result.setdefault(int(row[0]), []).append(str(row[1]))
    return result


# The two count-based artist sorts read their ordering key from
# `lib2_artist_rollup` (see core/library2/artist_rollup.py for the measurements
# that forced that design). Both used to be a correlated scalar subquery
# injected straight into ORDER BY, so SQLite re-ran them per artist row:
# `sort=albums` was measured at 11.5 s and (on the audit's bigger fixture)
# 46.6 s, with no timeout guard, for one click on a column header
# (perf-audit PERF-01/PERF-04).
_ORDER_ROLLUP_COLUMNS = {"albums": "album_count", "tracks": "track_count"}


def _artist_page_order(sort: str) -> Tuple[str, str, str, str]:
    """How to order the artist page, and what it costs to compute.

    Returns ``(page_join, page_order, outer_order, needs_rollup)``:

    - ``page_join``    -- join added to the page-id selection.
    - ``page_order``   -- ORDER BY used while choosing the page's artists.
    - ``outer_order``  -- ORDER BY on the final projection, which can only
      reference columns carried through ``page_artists``.
    - ``needs_rollup`` -- the roll-up column, or "" for the cheap sorts.
    """
    column = _ORDER_ROLLUP_COLUMNS.get(sort)
    if column:
        return (
            "LEFT JOIN lib2_artist_rollup ar ON ar.artist_id=a.id",
            f"COALESCE(ar.{column}, 0) DESC, a.name COLLATE NOCASE, a.id",
            "a._order_count DESC, a.name COLLATE NOCASE, a.id",
            column,
        )
    plain = _SORTS.get(sort, _SORTS["name"]) + ", a.id"
    return "", plain, plain, ""


def _json_dict(raw: Any) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (ValueError, TypeError):
        return {}


def _quality_profile_dict(row: Any) -> Optional[Dict[str, Any]]:
    """Shape an app-wide ``quality_profiles`` row for the Library v2 UI."""
    if row is None:
        return None
    keys = set(row.keys())

    def _ranked(raw: Any) -> List[Any]:
        try:
            val = json.loads(raw) if isinstance(raw, str) else (raw or [])
            return val if isinstance(val, list) else []
        except (ValueError, TypeError):
            return []

    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"] if "description" in keys else None,
        "upgrade_policy": row["upgrade_policy"] or "none",
        "upgrade_cutoff_index": int(row["upgrade_cutoff_index"] or 0) if "upgrade_cutoff_index" in keys else 0,
        "ranked_targets": _ranked(row["ranked_targets"] if "ranked_targets" in keys else None),
        "repair_job_id": row["repair_job_id"] if "repair_job_id" in keys else "quality_upgrade",
        "repair_settings": _json_dict(row["repair_settings"] if "repair_settings" in keys else None),
        "is_default": bool(row["is_default"]),
    }


def _quality_profile_assignment(conn: Any, entity: str, entity_id: int) -> Dict[str, Any]:
    """Shared API projection for §52.2 effective-profile provenance."""
    from core.library2.profile_lookup import effective_quality_profile

    return effective_quality_profile(conn, entity, int(entity_id))


def _json_list(raw: Any) -> List[str]:
    if not raw:
        return []
    if isinstance(raw, list):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, list) else []
    except (ValueError, TypeError):
        return []


def _artist_provider_ids(row: Any) -> Dict[str, str]:
    """Every qualified provider id stored on an artist row (guide §2.5): the
    dedicated ``spotify_id`` column plus the ``external_ids`` namespaces."""
    from core.library2.provider_ids import parse_external_ids
    ids = parse_external_ids(row["external_ids"] if "external_ids" in row.keys() else None)
    if row["spotify_id"]:
        ids.setdefault("spotify", str(row["spotify_id"]))
    # Both promoted columns, not just Spotify: an MBID written by the importer
    # lives only in the column, and a reader that missed it (the ported
    # Concerts section asks setlist.fm by MBID) sees an artist with no
    # MusicBrainz identity at all.
    if "musicbrainz_id" in row.keys() and row["musicbrainz_id"]:
        ids.setdefault("musicbrainz", str(row["musicbrainz_id"]))
    return ids


_LEGACY_API_SOURCE_COLUMNS = {
    "spotify": "spotify", "musicbrainz": "musicbrainz", "deezer": "deezer",
    "discogs": "discogs", "audiodb": "audiodb", "itunes": "itunes",
    "lastfm": "lastfm", "genius": "genius", "tidal": "tidal", "qobuz": "qobuz",
    "amazon": "amazon", "jiosaavn": "jiosaavn",
}


class _ProfileWatchlist:
    """The calling profile's legacy watchlist, as one rule used twice.

    Guide §2.6: the global lib2 ``monitored`` flag is the *admin's* intent, and
    other household profiles keep their own watchlist. So membership is decided
    by ``watchlist_artists`` rows for one ``profile_id``, matched exactly the way
    ``MusicDatabase.get_library_artists`` matches them: Spotify id, iTunes id, or
    lowercased name.

    The page filter has to run in SQL or pagination would count the wrong rows,
    while ``is_watched`` is decided per returned row. Both come from this one
    object so the two answers cannot drift apart.
    """

    def __init__(self, conn, profile_id: int) -> None:
        self.spotify: set = set()
        self.itunes: set = set()
        self.names: set = set()
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='watchlist_artists'"
        ).fetchone()
        if not exists:
            # A fresh install can read the catalogue before the legacy watchlist
            # table exists. Falling back to `monitored` here is exactly the
            # substitution that lost profile scoping in the first place, so the
            # honest reading of "no rows" is "nothing is watched".
            return
        for row in conn.execute(
            "SELECT spotify_artist_id, itunes_artist_id, LOWER(artist_name) AS name_lower "
            "FROM watchlist_artists WHERE profile_id = ?", (int(profile_id),)
        ):
            if row["spotify_artist_id"]:
                self.spotify.add(str(row["spotify_artist_id"]))
            if row["itunes_artist_id"]:
                self.itunes.add(str(row["itunes_artist_id"]))
            if row["name_lower"]:
                self.names.add(str(row["name_lower"]))

    def __bool__(self) -> bool:
        return bool(self.spotify or self.itunes or self.names)

    def sql(self, params: Dict[str, Any]) -> str:
        """A predicate over ``lib2_artists a``, binding into ``params``.

        Returns ``"0"`` for an empty watchlist: nothing can match, and the
        legacy reader says the same.
        """
        parts = []
        for prefix, values, columns in (
            ("wlsp", self.spotify, ("a.spotify_id", "json_extract(a.external_ids,'$.spotify')")),
            ("wlit", self.itunes, ("json_extract(a.external_ids,'$.itunes')",)),
        ):
            if not values:
                continue
            keys = []
            for index, value in enumerate(sorted(values)):
                key = f"{prefix}_{index}"
                params[key] = value
                keys.append(f":{key}")
            joined = ", ".join(keys)
            parts.append("(" + " OR ".join(
                f"({column} IS NOT NULL AND {column} IN ({joined}))"
                for column in columns) + ")")
        if self.names:
            keys = []
            for index, value in enumerate(sorted(self.names)):
                key = f"wlnm_{index}"
                params[key] = value
                keys.append(f":{key}")
            parts.append(f"LOWER(a.name) IN ({', '.join(keys)})")
        return "(" + " OR ".join(parts) + ")" if parts else "0"

    def contains(self, *, name: Any, provider_ids: Mapping[str, str]) -> bool:
        if str(provider_ids.get("spotify") or "") in self.spotify and self.spotify:
            return True
        if str(provider_ids.get("itunes") or "") in self.itunes and self.itunes:
            return True
        return str(name or "").strip().casefold() in self.names


def legacy_api_artists_page(conn, *, search_query: str = "", letter: str = "all",
                            page: int = 1, limit: int = 75,
                            watchlist_filter: str = "all",
                            source_filter: str = "",
                            profile_id: int = 1) -> Dict[str, Any]:
    """``/api/library/artists`` served from lib2 instead of the legacy tables.

    iss32-E03. The endpoint read ``database.get_library_artists`` — the legacy
    table — so metadata edits and enrichment performed in the Library-v2 UI
    were invisible to it. The response shape is reproduced field for field,
    because the shape is the contract: ``findExactArtist`` in the tools page
    and the finding→artist link both consume it.

    **The ``id`` stays the legacy artist id.** Consumers hand it straight to
    ``navigateToArtistDetail``, and the artist-detail page resolves a bare
    numeric id against the legacy table by contract (see the tool-integration
    audit). Returning a lib2 id here would look correct and navigate to
    nothing. ``lib2_artist_id`` is added alongside for callers that want the
    native identity.

    **Artists with no legacy row are omitted.** They are invisible to this
    endpoint today too, and giving them an id that navigation cannot resolve
    would be a regression dressed as a feature. They stop being a special case
    in Stufe 2, when the producers write lib2 directly and the artist-detail
    page can take a native id (docs §32.3.1).
    """
    from core.library2.provider_ids import parse_external_ids

    page = max(1, int(page))
    limit = max(1, min(int(limit), 500))
    offset = (page - 1) * limit

    clauses = ["a.canonical_artist_id IS NULL", "a.legacy_artist_id IS NOT NULL"]
    params: Dict[str, Any] = {}
    if search_query:
        clauses.append("a.name LIKE :like ESCAPE '\\'")
        params["like"] = f"%{str(search_query).replace(chr(92), chr(92) * 2).replace('%', chr(92) + '%').replace('_', chr(92) + '_')}%"
    if letter and letter != "all":
        if letter == "#":
            clauses.append("UPPER(SUBSTR(a.name, 1, 1)) NOT GLOB '[A-Z]'")
        else:
            clauses.append("UPPER(SUBSTR(a.name, 1, 1)) = UPPER(:letter)")
            params["letter"] = letter
    watchlist = _ProfileWatchlist(conn, profile_id)
    if watchlist_filter == "watched":
        clauses.append(watchlist.sql(params))
    elif watchlist_filter == "unwatched":
        clauses.append(f"NOT {watchlist.sql(params)}")

    # Provider match filter. lib2 keeps provider identity in external_ids (plus
    # the promoted spotify_id/musicbrainz_id columns), so the legacy
    # column-per-provider test becomes a JSON containment test.
    negate = str(source_filter or "").startswith("!")
    source_key = str(source_filter or "").lstrip("!").strip().lower()
    if source_key in _LEGACY_API_SOURCE_COLUMNS:
        namespace = _LEGACY_API_SOURCE_COLUMNS[source_key]
        promoted = {"spotify": "a.spotify_id", "musicbrainz": "a.musicbrainz_id"}.get(namespace)
        has_it = f"json_extract(a.external_ids, '$.{namespace}') IS NOT NULL"
        if promoted:
            has_it = f"({has_it} OR ({promoted} IS NOT NULL AND {promoted} <> ''))"
        clauses.append(f"NOT ({has_it})" if negate else has_it)

    where = " AND ".join(clauses)
    total_count = int(conn.execute(
        f"SELECT COUNT(*) FROM lib2_artists a WHERE {where}", params).fetchone()[0])

    rows = conn.execute(
        f"""SELECT a.id, a.legacy_artist_id, a.name, a.image_url, a.genres,
                   a.external_ids, a.spotify_id, a.musicbrainz_id, a.soul_id,
                   a.monitored,
                   (SELECT COUNT(*) FROM lib2_albums al
                     WHERE al.primary_artist_id = a.id
                       AND al.origin = 'library') AS album_count,
                   (SELECT COUNT(*) FROM lib2_tracks t
                      JOIN lib2_albums al2 ON al2.id = t.album_id
                     WHERE al2.primary_artist_id = a.id) AS track_count
              FROM lib2_artists a
             WHERE {where}
             ORDER BY a.name COLLATE NOCASE
             LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset}).fetchall()

    artists: List[Dict[str, Any]] = []
    for row in rows:
        ids = parse_external_ids(row["external_ids"])
        if row["spotify_id"]:
            ids.setdefault("spotify", str(row["spotify_id"]))
        if row["musicbrainz_id"]:
            ids.setdefault("musicbrainz", str(row["musicbrainz_id"]))
        artists.append({
            "id": row["legacy_artist_id"],
            "lib2_artist_id": row["id"],
            "name": row["name"],
            "image_url": row["image_url"],
            "genres": _json_list(row["genres"]),
            "musicbrainz_id": ids.get("musicbrainz"),
            "spotify_artist_id": ids.get("spotify"),
            "itunes_artist_id": ids.get("itunes"),
            "deezer_id": ids.get("deezer"),
            "audiodb_id": ids.get("audiodb"),
            "discogs_id": ids.get("discogs"),
            "lastfm_url": ids.get("lastfm"),
            "genius_url": ids.get("genius"),
            "tidal_id": ids.get("tidal"),
            "qobuz_id": ids.get("qobuz"),
            # The column is where the SoulID worker writes (docs §50.4.4.12).
            # The two external_ids keys stay as fallbacks: `soul` is what the
            # typed-metadata converter emits for an importer-supplied id, and a
            # row that only ever passed through it has nothing in the column.
            "soul_id": row["soul_id"] or ids.get("soulid") or ids.get("soul"),
            "amazon_id": ids.get("amazon"),
            "album_count": int(row["album_count"] or 0),
            "track_count": int(row["track_count"] or 0),
            # Not `row["monitored"]`: that is the admin's global lib2 intent,
            # and telling a guest profile it owns the admin's monitoring is the
            # regression this reproduces the legacy meaning to avoid.
            "is_watched": watchlist.contains(name=row["name"], provider_ids=ids),
        })

    total_pages = (total_count + limit - 1) // limit
    return {
        "artists": artists,
        "pagination": {
            "page": page,
            "limit": limit,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        },
    }


def find_artists_by_name(conn, name: str, *, limit: int = 5) -> List[Dict[str, Any]]:
    """Name lookup for non-UI consumers, with the two fields they need.

    The metadata-update worker pushes genres into Plex/Jellyfin and uses a
    stored Spotify id to skip a provider search. It read those from the legacy
    ``artists`` row through ``MusicDatabase.search_artists`` /
    ``api_get_artist``; both fields exist on the lib2 row.

    Deliberately not ``list_artists``: that one carries the artist page's whole
    roll-up — album/single/track counts, quality-profile resolution and a
    window function over every file for the size column. A worker asking "do we
    know this name?" should not pay for any of it.

    The filter matches the stored name and overrides are projected afterwards,
    exactly as ``list_artists`` does. Alias members are folded away (§40) so a
    caller cannot push the same genres twice.
    """
    text = str(name or "").strip()
    if not text:
        return []
    rows = conn.execute(
        """
        SELECT id, name, genres, spotify_id, external_ids
          FROM lib2_artists
         WHERE canonical_artist_id IS NULL AND name LIKE :pattern
         ORDER BY LENGTH(name), name
         LIMIT :limit
        """,
        {"pattern": f"%{text}%", "limit": max(1, min(int(limit), 50))},
    ).fetchall()
    projected = project_metadata_many(
        conn,
        entity_type="artist",
        provider_fields={int(row["id"]): dict(row) for row in rows},
    )
    found = []
    for row in rows:
        effective, _overrides = projected[int(row["id"])]
        found.append({
            "id": row["id"],
            "name": effective["name"],
            "genres": _json_list(effective["genres"]),
            "spotify_id": _artist_provider_ids(row).get("spotify"),
        })
    return found


def list_artists(conn, *, search: str = "", sort: str = "name", monitored: str = "all",
                 page: int = 1, limit: int = 75,
                 include_size: bool = True) -> Tuple[List[Dict[str, Any]], int]:
    """Paginated artist overview with per-artist roll-up stats.

    ``monitored`` filters the list: ``'all'`` (default), ``'monitored'``, or
    ``'unmonitored'``.

    ``include_size`` (perf25-03) controls the disk-space roll-up, which needs a
    window function over every file of the page's artists plus a SUM on top of
    it — by far the heaviest part of this query.  The size column is opt-in in
    the artist table (default off), so the caller may switch it off and get
    ``total_size_bytes = 0`` for a value nothing renders.
    """
    page_join, page_order, outer_order, rollup_column = _artist_page_order(sort)
    if rollup_column:
        # Rebuilt only when missing or stale; a few minutes of drift moves an
        # artist by a row, which is the whole reason a cache is acceptable for
        # an ordering key but not for a rendered number.
        from core.library2.artist_rollup import ensure_fresh_artist_rollup
        ensure_fresh_artist_rollup(conn)
    page = max(1, int(page))
    limit = max(1, min(int(limit), 500))
    offset = (page - 1) * limit
    # §40: alias-member rows are folded into their canonical artist's entry
    # (get_artist merges their albums in) and never listed on their own.
    clauses, params = ["a.canonical_artist_id IS NULL"], {}
    if search:
        # iss29-D04: spell the alias-membership test so an index can serve it.
        #
        # `COALESCE(member.canonical_artist_id, member.id) = a.id` is not
        # sargable — no index can answer it, and the only artist indexes are
        # `idx_lib2_artists_canonical(canonical_artist_id)` and
        # `idx_lib2_artists_name`. Combined with a leading-wildcard LIKE that
        # made `GET /artists?search=a` a full cross product: ~10^8 row
        # comparisons on a 10k-artist library, evaluated TWICE (the same WHERE
        # is reused by the count and by the page_artists CTE), on the request
        # thread, on every keystroke.
        #
        # The two branches below are exactly equivalent to the COALESCE — the
        # outer query already restricts `a` to canonical rows — and each one is
        # an index lookup.
        # ...and the two branches are kept APART. Written as one EXISTS with an
        # `OR` inside, SQLite could use neither index and fell back to scanning
        # lib2_artists once per candidate artist: 21.7 s on a 12k-artist
        # catalogue for a search matching ten of them (the PERF-08 shape).
        #
        # The second branch is not a subquery at all. `member.canonical_artist_id
        # IS NULL AND member.id = a.id` can only be satisfied by `a` itself,
        # because the outer query already restricts `a` to canonical rows -- so
        # it is a plain column test on the row being examined.
        # The alias branch is an `IN (...)` over a subquery that has no
        # correlation, so SQLite evaluates it ONCE. Written as a correlated
        # `EXISTS (... WHERE member.canonical_artist_id = a.id ...)` it is
        # re-run per candidate artist, and whether that is a seek or a scan
        # depends entirely on how selective ANALYZE believes
        # `idx_lib2_artists_canonical` to be -- on a library with few aliases
        # SQLite sees one distinct value, picks the scan, and the search
        # becomes 12,000 x 12,000: measured at 7.5 s for the count alone,
        # doubled because the same WHERE also drives the page query.
        clauses.append(
            "(a.name LIKE :like ESCAPE '\\' "
            " OR a.id IN (SELECT member.canonical_artist_id FROM lib2_artists member "
            "              WHERE member.canonical_artist_id IS NOT NULL "
            "                AND member.name LIKE :like ESCAPE '\\'))"
        )
        # ...and escape the wildcards. Without ESCAPE, a user typing `%` or `_`
        # was writing pattern syntax rather than searching for the character.
        escaped = (
            str(search)
            .replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )
        params["like"] = f"%{escaped}%"
    if monitored == "monitored":
        clauses.append("a.monitored = 1")
    elif monitored == "unmonitored":
        clauses.append("a.monitored = 0")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    total = conn.execute(
        f"SELECT COUNT(*) AS c FROM lib2_artists a {where}", params
    ).fetchone()["c"]

    # I8: disk-space roll-up, kept separate from track_stats below — that CTE's
    # plain (unranked) tf join fans out per historical file row, which would
    # inflate a SUM(size) sharing the same join. This one joins each track's
    # single ADR-03 primary file exactly once.  perf25-03: the window function
    # over every file of the page plus the SUM on top of it is the heaviest
    # part of the statement, so it is only assembled when the caller wants it.
    # The scoping used to be an `EXISTS (...)` on a bare `FROM lib2_track_files`,
    # which the planner served by SCANNING the whole file table and evaluating
    # the EXISTS per row -- 21.7 s on a 288k-track library for a search that
    # matched ten artists (perf-audit PERF-03's shape, here in list_artists).
    # Resolving the page's track ids first and CROSS JOINing from them forces
    # the small side to lead. DISTINCT matters: a track credited to two artists
    # on the page would otherwise enter twice and split its own ROW_NUMBER
    # partition, double-counting the file in the SUM below.
    size_cte = f""",
        page_tracks AS (
            SELECT DISTINCT ta.track_id
              FROM canonical_members cm
              CROSS JOIN lib2_track_artists ta ON ta.artist_id=cm.member_id
        ),
        track_primary_files AS (
            SELECT tf.track_id, tf.size,
                   ROW_NUMBER() OVER (
                       PARTITION BY tf.track_id ORDER BY {primary_order('tf')}
                   ) AS rank
              FROM page_tracks pt
              CROSS JOIN lib2_track_files tf ON tf.track_id=pt.track_id
             WHERE COALESCE(tf.file_state, 'active') <> 'deleted'
        ),
        artist_size AS (
            SELECT cm.canonical_id AS artist_id,
                   COALESCE(SUM(pf.size), 0) AS total_size_bytes
              FROM canonical_members cm
              CROSS JOIN lib2_track_artists ta ON ta.artist_id=cm.member_id
              JOIN track_primary_files pf ON pf.track_id=ta.track_id AND pf.rank=1
             GROUP BY cm.canonical_id
        )""" if include_size else ""
    size_select = "COALESCE(asz.total_size_bytes, 0)" if include_size else "0"
    size_join = "LEFT JOIN artist_size asz ON asz.artist_id=a.id" if include_size else ""
    order_count_col = f"COALESCE(ar.{rollup_column}, 0)" if rollup_column else "0"

    rows = conn.execute(
        f"""
        WITH page_artists AS MATERIALIZED (
            SELECT a.*, {order_count_col} AS _order_count
              FROM lib2_artists a
              {page_join}
              {where}
             ORDER BY {page_order}
             LIMIT :limit OFFSET :offset
        ),
        -- perf25-03: only alias members that fold into an artist ON THIS PAGE
        -- matter; materializing the whole artist table here made every list
        -- request scale with library size instead of page size.
        canonical_members AS MATERIALIZED (
            SELECT member.id AS member_id,
                   COALESCE(member.canonical_artist_id, member.id) AS canonical_id
              FROM lib2_artists member
             WHERE COALESCE(member.canonical_artist_id, member.id)
                   IN (SELECT id FROM page_artists)
        ),
        -- perf25-03 scoped these to `page_artists`, but the PLANNER IGNORED
        -- it: `canonical_members` is a MATERIALIZED CTE with no index, so
        -- SQLite estimated its cardinality high and drove the join from
        -- lib2_track_artists instead -- a full scan of the largest junction
        -- table, twice, plus one of lib2_track_files, on every artist page.
        -- CROSS JOIN is an explicit join-order constraint in SQLite, so the
        -- ~78-row page CTE leads and the junction is SEEKed through
        -- idx_lib2_track_artists_artist. `page_artists` is dropped from these
        -- joins because `canonical_members` is already page-scoped.
        artist_albums AS (
            SELECT cm.canonical_id AS artist_id, aa.album_id
              FROM canonical_members cm
              CROSS JOIN lib2_album_artists aa ON aa.artist_id=cm.member_id
            UNION
            SELECT cm.canonical_id AS artist_id, t.album_id
              FROM canonical_members cm
              CROSS JOIN lib2_track_artists ta ON ta.artist_id=cm.member_id
              JOIN lib2_tracks t ON t.id=ta.track_id
        ),
        album_stats AS (
            SELECT aa.artist_id,
                   COUNT(DISTINCT CASE
                       WHEN al.album_type <> 'single'
                        AND (al.origin='library' OR al.monitored=1)
                       THEN al.id END) AS album_count,
                   COUNT(DISTINCT CASE
                       WHEN al.album_type = 'single'
                        AND (al.origin='library' OR al.monitored=1)
                       THEN al.id END) AS single_count
              FROM artist_albums aa
              JOIN lib2_albums al ON al.id=aa.album_id
             GROUP BY aa.artist_id
        ),
        track_stats AS (
            SELECT cm.canonical_id AS artist_id,
                   COUNT(DISTINCT CASE
                       WHEN COALESCE(w.wanted, t.monitored)=1 OR tf.id IS NOT NULL
                       THEN t.id END) AS track_count,
                   COUNT(DISTINCT CASE
                       WHEN tf.id IS NOT NULL
                        AND COALESCE(tf.file_state, 'active')
                            NOT IN ('missing_confirmed','deleted')
                       THEN t.id END) AS track_files_present
              FROM canonical_members cm
              CROSS JOIN lib2_track_artists ta ON ta.artist_id=cm.member_id
              JOIN lib2_tracks t ON t.id=ta.track_id
              LEFT JOIN lib2_wanted_tracks w
                     ON w.track_id=t.id AND w.profile_id=1
              LEFT JOIN lib2_track_files tf ON tf.track_id=t.id
             GROUP BY cm.canonical_id
        ){size_cte}
        SELECT a.id, a.name, a.sort_name, a.image_url, a.genres,
               a.monitored, a.monitor_new_items, a.quality_profile_id,
               a.quality_profile_explicit, a.added_at,
               COALESCE(als.album_count, 0) AS album_count,
               COALESCE(als.single_count, 0) AS single_count,
               COALESCE(ts.track_count, 0) AS track_count,
               COALESCE(ts.track_files_present, 0) AS track_files_present,
               {size_select} AS total_size_bytes
        FROM page_artists a
        LEFT JOIN album_stats als ON als.artist_id=a.id
        LEFT JOIN track_stats ts ON ts.artist_id=a.id
        {size_join}
        ORDER BY {outer_order}
        """,
        {**params, "limit": limit, "offset": offset},
    ).fetchall()

    projected = project_metadata_many(
        conn,
        entity_type="artist",
        provider_fields={int(row["id"]): dict(row) for row in rows},
    )
    media_sources = _media_server_sources_many(
        conn, "artist", [int(row["id"]) for row in rows])
    artists = []
    for r in rows:
        effective, overrides = projected[int(r["id"])]
        track_count = r["track_count"] or 0
        present = r["track_files_present"] or 0
        artists.append({
            "id": r["id"],
            "name": effective["name"],
            "image_url": effective["image_url"],
            "genres": _json_list(effective["genres"]),
            "monitored": bool(r["monitored"]),
            "monitor_new_items": r["monitor_new_items"],
            "quality_profile_id": r["quality_profile_id"],
            "quality_profile_source": (
                "artist" if bool(r["quality_profile_explicit"]) else "global"
            ),
            "quality_profile_source_id": (
                r["id"] if bool(r["quality_profile_explicit"]) else None
            ),
            "quality_profile_explicit": bool(r["quality_profile_explicit"]),
            "added_at": r["added_at"],
            "album_count": r["album_count"] or 0,
            "single_count": r["single_count"] or 0,
            "track_count": track_count,
            "tracks_present": present,
            "tracks_missing": max(0, track_count - present),
            "total_size_bytes": r["total_size_bytes"] or 0,
            "media_server_sources": media_sources.get(int(r["id"]), []),
            "user_overrides": overrides,
        })
    return artists, total


def list_artist_track_files(conn, artist_id: int, *, search: str = "",
                            page: int = 1, limit: int = 100
                            ) -> Tuple[List[Dict[str, Any]], int]:
    """Paginated flat file list for one artist (C2: Lidarr "Manage Track
    Files"). Mirrors ``core.library2.file_delete._scope_snapshot``'s artist
    scope exactly (alias-group ``primary_artist_id``, non-deleted files) so a selection
    made from this list lines up with what the ADR-05 preview/execute
    endpoints will actually see for the same file ids.
    """
    page = max(1, int(page))
    limit = max(1, min(int(limit), 500))
    offset = (page - 1) * limit
    from core.library2.artist_aliases import resolve_alias_group

    artist_ids = resolve_alias_group(conn, artist_id)
    artist_marks = ",".join(f":artist_id_{i}" for i in range(len(artist_ids)))
    clauses = [f"al.primary_artist_id IN ({artist_marks})", "tf.file_state <> 'deleted'"]
    params: Dict[str, Any] = {
        f"artist_id_{i}": value for i, value in enumerate(artist_ids)
    }
    if search:
        clauses.append("(t.title LIKE :like OR al.title LIKE :like)")
        params["like"] = f"%{search}%"
    where = "WHERE " + " AND ".join(clauses)

    total = conn.execute(
        f"""SELECT COUNT(*) AS c FROM lib2_track_files tf
             JOIN lib2_tracks t ON t.id = tf.track_id
             JOIN lib2_albums al ON al.id = t.album_id
            {where}""",
        params,
    ).fetchone()["c"]

    rows = conn.execute(
        f"""SELECT tf.id AS file_id, tf.track_id, tf.path, tf.size, tf.format,
                   tf.bitrate, tf.sample_rate, tf.bit_depth, tf.quality_tier,
                   tf.file_state, tf.is_primary, tf.primary_manual,
                   tf.file_role, tf.derived_from_file_id,
                   tf.acquired_quality_json, tf.retention_json, tf.added_at,
                   t.title AS track_title, t.track_number, t.disc_number,
                   al.id AS album_id, al.title AS album_title
              FROM lib2_track_files tf
              JOIN lib2_tracks t ON t.id = tf.track_id
              JOIN lib2_albums al ON al.id = t.album_id
             {where}
             ORDER BY al.title, t.disc_number, t.track_number, tf.id
             LIMIT :limit OFFSET :offset""",
        {**params, "limit": limit, "offset": offset},
    ).fetchall()

    files = [
        {
            "file_id": r["file_id"],
            "track_id": r["track_id"],
            "track_title": r["track_title"],
            "track_number": r["track_number"],
            "disc_number": r["disc_number"],
            "album_id": r["album_id"],
            "album_title": r["album_title"],
            "path": r["path"],
            "size": r["size"],
            "format": r["format"],
            "bitrate": r["bitrate"],
            "sample_rate": r["sample_rate"],
            "bit_depth": r["bit_depth"],
            "quality_tier": r["quality_tier"],
            "file_state": r["file_state"],
            "is_primary": bool(r["is_primary"]),
            "primary_manual": bool(r["primary_manual"]),
            "file_role": r["file_role"] or "master",
            "derived_from_file_id": r["derived_from_file_id"],
            "acquired_quality_json": r["acquired_quality_json"],
            "retention_json": r["retention_json"],
            "added_at": r["added_at"],
        }
        for r in rows
    ]
    return files, total


def list_artist_playback_files(conn, artist_id: int, *, page: int = 1,
                               limit: int = 100
                               ) -> Tuple[List[Dict[str, Any]], int]:
    """Paginated play queue for one artist: one playable file per track.

    Deliberately NOT ``list_artist_track_files``. That one answers "which files
    does this artist's Manage-Track-Files selection cover", and its scope is
    ``lib2_albums.primary_artist_id`` on purpose, so a selection lines up with
    what the ADR-05 delete preview will see. Playback asks a different
    question: ``get_artist`` shows a release the artist only guests on — it
    reaches it through ``lib2_track_artists`` — and a Play button that omitted
    those songs would contradict the page it sits on.

    Each row also carries the track's OWN primary credit. Without it a
    compilation queue labels every song with the page's artist, which is wrong
    on exactly the releases this scope was widened to include.

    One file per track (primary first, then quality) is done here rather than
    left to the caller: a lossless master and its retained lossy companion are
    two rows for ONE recording, and pagination would otherwise split the pair
    across pages where no client-side dedupe can see both.
    """
    from core.library2.artist_aliases import resolve_alias_group
    from core.library2.track_files import primary_order

    page = max(1, int(page))
    limit = max(1, min(int(limit), 500))
    offset = (page - 1) * limit
    artist_ids = resolve_alias_group(conn, artist_id)
    marks = ",".join("?" for _ in artist_ids)
    params = [*artist_ids, *artist_ids]

    scope = f"""
        WITH scope_tracks AS (
            SELECT t.id AS track_id
              FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id
             WHERE al.primary_artist_id IN ({marks})
            UNION
            SELECT ta.track_id
              FROM lib2_track_artists ta
             WHERE ta.artist_id IN ({marks})
        ),
        ranked AS (
            SELECT tf.id AS file_id, tf.track_id, tf.path, tf.format, tf.bitrate,
                   ROW_NUMBER() OVER (
                       PARTITION BY tf.track_id ORDER BY {primary_order('tf')}
                   ) AS rank
              FROM scope_tracks s
              JOIN lib2_track_files tf ON tf.track_id = s.track_id
             WHERE COALESCE(tf.file_state, 'active') = 'active'
               AND COALESCE(tf.path, '') <> ''
        )"""

    total = conn.execute(
        f"{scope} SELECT COUNT(*) AS c FROM ranked WHERE rank = 1", params
    ).fetchone()["c"]

    rows = conn.execute(
        f"""{scope}
        SELECT r.file_id, r.track_id, r.path, r.format, r.bitrate,
               t.title AS track_title, t.track_number, t.disc_number, t.duration,
               al.id AS album_id, al.title AS album_title,
               al.image_url AS album_image_url,
               ar.id AS artist_id, ar.name AS artist_name
          FROM ranked r
          JOIN lib2_tracks t ON t.id = r.track_id
          JOIN lib2_albums al ON al.id = t.album_id
          LEFT JOIN lib2_artists ar ON ar.id = (
              SELECT ta.artist_id FROM lib2_track_artists ta
               WHERE ta.track_id = t.id
               ORDER BY CASE WHEN ta.role = 'primary' THEN 0 ELSE 1 END,
                        ta.position, ta.artist_id
               LIMIT 1)
         WHERE r.rank = 1
         ORDER BY al.title, al.id, t.disc_number, t.track_number, t.id
         LIMIT ? OFFSET ?""",
        [*params, limit, offset],
    ).fetchall()

    files = [
        {
            "file_id": r["file_id"],
            "track_id": r["track_id"],
            "track_title": r["track_title"],
            "track_number": r["track_number"],
            "disc_number": r["disc_number"],
            "duration": r["duration"],
            "album_id": r["album_id"],
            "album_title": r["album_title"],
            "album_image_url": r["album_image_url"],
            "artist_id": r["artist_id"],
            "artist_name": r["artist_name"],
            "path": r["path"],
            "format": r["format"],
            "bitrate": r["bitrate"],
            # The client filters on these the same way it does for the Files
            # tab; both are already guaranteed by the query above.
            "file_state": "active",
            "is_primary": True,
        }
        for r in rows
    ]
    return files, total


def get_artist(conn, artist_id: int) -> Optional[Dict[str, Any]]:
    """Artist detail: header + albums and singles grouped separately.

    §40: resolves ``artist_id``'s alias group first — works whether it is the
    canonical row or one of its linked aliases, so an old deep link to an
    alias id still resolves. Albums/EPs/singles are the UNION of every group
    member's own releases (each keeps its own ``lib2_albums`` rows, nothing
    is reassigned); the header fields (bio/image/genres/...) always come from
    the CANONICAL row.
    """
    from core.library2.artist_aliases import resolve_alias_group
    group = resolve_alias_group(conn, artist_id)
    canonical_id = group[0]
    a = conn.execute("SELECT * FROM lib2_artists WHERE id = ?", (canonical_id,)).fetchone()
    if a is None:
        return None
    artist_effective, artist_overrides = project_metadata(
        conn,
        entity_type="artist",
        entity_id=a["id"],
        provider_fields=dict(a),
    )
    artist_profile = _quality_profile_assignment(conn, "artists", a["id"])
    qp = conn.execute(
        "SELECT * FROM quality_profiles WHERE id = ?", (artist_profile["id"],)
    ).fetchone()

    group_marks = ",".join("?" for _ in group)
    from core.library2.recording_links import owned_by_recording_sql
    owned_elsewhere = owned_by_recording_sql(conn, "t")
    album_rows = conn.execute(
        f"""
        WITH artist_albums AS (
            SELECT aa.album_id
              FROM lib2_album_artists aa
             WHERE aa.artist_id IN ({group_marks})
            UNION
            SELECT t.album_id
              FROM lib2_track_artists ta
              JOIN lib2_tracks t ON t.id=ta.track_id
             WHERE ta.artist_id IN ({group_marks})
        ),
        -- Scoped to THIS artist's albums. Neither this CTE nor album_size
        -- below used to be, so opening any artist page ranked every file row
        -- in the library and grouped every album in the database: 491 ms for a
        -- ONE-album artist at 320k tracks, and the same 491 ms for a
        -- 105-album artist -- the cost was entirely library-wide (PERF-03).
        -- `list_artists` had the identical defect and it was fixed there
        -- (perf25-03); this one was missed.
        track_primary_files AS (
            SELECT tf.track_id, tf.size,
                   ROW_NUMBER() OVER (
                       PARTITION BY tf.track_id ORDER BY {primary_order('tf')}
                   ) AS rank
              FROM artist_albums aa2
              JOIN lib2_tracks t2 ON t2.album_id=aa2.album_id
              JOIN lib2_track_files tf ON tf.track_id=t2.id
             WHERE COALESCE(tf.file_state, 'active') <> 'deleted'
        ),
        -- I8: disk-space roll-up per album, computed separately from the
        -- files_present fan-out below (that join isn't restricted to one row
        -- per track, so a SUM(size) sharing it would double-count).
        album_size AS (
            SELECT t.album_id, COALESCE(SUM(pf.size), 0) AS total_size_bytes
              FROM artist_albums aa3
              JOIN lib2_tracks t ON t.album_id=aa3.album_id
              JOIN track_primary_files pf ON pf.track_id=t.id AND pf.rank=1
             GROUP BY t.album_id
        )
        SELECT al.id, al.title, al.album_type, al.release_date, al.year,
               al.image_url, al.monitored, al.quality_profile_id,
               al.quality_profile_explicit, al.track_count,
               al.expected_track_count, al.origin, al.spotify_id,
               al.primary_artist_id,
               pa.quality_profile_id AS artist_quality_profile_id,
               pa.quality_profile_explicit AS artist_quality_profile_explicit,
               al.explicit, al.label, al.style, al.mood,
               COUNT(DISTINCT t.id) AS db_track_count,
               COUNT(DISTINCT CASE
                   WHEN (tf.id IS NOT NULL
                         AND COALESCE(tf.file_state, 'active')
                             NOT IN ('missing_confirmed','deleted'))
                     OR ({owned_elsewhere})
                   THEN t.id END) AS files_present,
               -- Guide §5: "My Library" means `origin='library' OR monitored`.
               -- A wanted TRACK on an otherwise unowned release satisfies that
               -- just as much as a monitored album does — without this count
               -- bookmarking a single top track wrote a wishlist row the user
               -- could then not find anywhere in their library.
               COUNT(DISTINCT CASE WHEN t.monitored=1 THEN t.id END) AS monitored_tracks,
               -- What "missing" is allowed to mean: a track you still want and
               -- do not have. Unmonitoring the two interludes you never intend
               -- to own used to leave the album reading "2 missing" forever,
               -- with no way to reach zero except downloading music you had
               -- explicitly said no to.
               -- `wanted` first, `monitored` as the fallback: that is exactly
               -- what the album detail projects per row, and the two views
               -- disagreeing about the same number would be its own bug.
               COUNT(DISTINCT CASE
                   WHEN COALESCE(w.wanted, t.monitored)=1 AND NOT EXISTS (
                       SELECT 1 FROM lib2_track_files f
                        WHERE f.track_id=t.id
                          AND COALESCE(f.file_state,'active')
                              NOT IN ('missing_confirmed','deleted'))
                    -- §49.6(c): and it is not already on disk under another
                    -- release. The album detail draws that row as present, so
                    -- counting it as a gap here would be the same disagreement
                    -- the comment above exists to prevent.
                    AND NOT ({owned_elsewhere})
                   THEN t.id END) AS monitored_missing,
               COALESCE(asz.total_size_bytes, 0) AS total_size_bytes
        FROM artist_albums aa
        JOIN lib2_albums al ON al.id = aa.album_id
        JOIN lib2_artists pa ON pa.id=al.primary_artist_id
        LEFT JOIN lib2_tracks t ON t.album_id=al.id
        LEFT JOIN lib2_track_files tf ON tf.track_id=t.id
        LEFT JOIN lib2_wanted_tracks w ON w.track_id=t.id AND w.profile_id=1
        LEFT JOIN album_size asz ON asz.album_id=al.id
        GROUP BY al.id
        ORDER BY al.year DESC, al.title COLLATE NOCASE
        """,
        (*tuple(group), *tuple(group)),
    ).fetchall()

    projected_albums = project_metadata_many(
        conn,
        entity_type="release_group",
        provider_fields={int(row["id"]): dict(row) for row in album_rows},
    )
    albums, eps, singles = [], [], []
    for r in album_rows:
        effective, overrides = projected_albums[int(r["id"])]
        album_owns_profile = bool(r["quality_profile_explicit"])
        artist_owns_profile = bool(r["artist_quality_profile_explicit"])
        album_profile = {
            "source": "album" if album_owns_profile else (
                "artist" if artist_owns_profile else "global"
            ),
            "source_id": r["id"] if album_owns_profile else (
                r["primary_artist_id"] if artist_owns_profile else None
            ),
            "explicit": album_owns_profile,
        }
        present = r["files_present"] or 0
        # Total = the metadata's true track count when known, so partial albums
        # show "have / total" and the missing count is visible (Lidarr-style).
        total = max(r["expected_track_count"] or 0, r["db_track_count"] or 0,
                    r["track_count"] or 0, present)
        entry = {
            "id": r["id"],
            "title": effective["title"],
            "album_type": effective["album_type"],
            "release_date": effective["release_date"],
            "year": effective["year"],
            "image_url": effective["image_url"],
            "monitored": bool(r["monitored"]),
            "quality_profile_id": r["quality_profile_id"],
            "quality_profile_source": album_profile["source"],
            "quality_profile_source_id": album_profile["source_id"],
            "quality_profile_explicit": album_profile["explicit"],
            "origin": r["origin"] or "library",
            "spotify_id": r["spotify_id"],
            "explicit": (bool(effective["explicit"]) if effective["explicit"] is not None else None),
            "label": effective["label"],
            "style": effective["style"],
            "mood": effective["mood"],
            "track_count": total,
            "tracks_present": present,
            # Rows the provider promised but that do not exist yet always
            # count: a slot with no row is not a track anyone said no to, and
            # `lib2_albums.monitored` cannot stand in for intent here — the
            # importer clears it precisely BECAUSE a release is incomplete, so
            # gating on it would hide the gaps on exactly the albums that have
            # them.
            "tracks_missing": (r["monitored_missing"] or 0)
            + max(0, total - (r["db_track_count"] or 0)),
            "monitored_tracks": r["monitored_tracks"] or 0,
            "total_size_bytes": r["total_size_bytes"] or 0,
            "user_overrides": overrides,
        }
        if effective["album_type"] == "single":
            singles.append(entry)
        elif effective["album_type"] == "ep":
            eps.append(entry)
        else:
            albums.append(entry)

    def _in_library(entries):
        return sum(1 for e in entries
                   if e["origin"] == "library" or e["monitored"] or e["monitored_tracks"])

    return {
        "id": a["id"],
        "name": artist_effective["name"],
        "image_url": artist_effective["image_url"],
        "summary": artist_effective["summary"],
        "style": artist_effective["style"],
        "mood": artist_effective["mood"],
        "label": artist_effective["label"],
        "genres": _json_list(artist_effective["genres"]),
        # ldp-05: the rich artist header asks the shared provider endpoints
        # (top tracks) for this artist — those key off a provider id, and a
        # lib2 row id is not one. Expose what the row already stores instead
        # of making the client take a second round trip to /match-status.
        "provider_ids": _artist_provider_ids(a),
        "media_server_sources": _media_server_sources_many(
            conn, "artist", [int(a["id"])]).get(int(a["id"]), []),
        # iss32-E02: "both show as matched so you can't tell them apart."
        # An artist with a legacy counterpart is walked by the twelve metadata
        # workers and can carry provider bios; one born inside lib2 is served
        # by the native path, which resolves provider ids, artwork, genres and
        # the descriptive columns but not the Last.fm/Genius/Discogs bios
        # (those workers still write legacy rows — Stufe 2). Say which, instead
        # of letting the chips imply parity.
        "enrichment_depth": "full" if a["legacy_artist_id"] is not None else "native",
        "monitored": bool(a["monitored"]),
        "monitor_new_items": a["monitor_new_items"],
        "quality_profile": _quality_profile_dict(qp),
        "quality_profile_source": artist_profile["source"],
        "quality_profile_source_id": artist_profile["source_id"],
        "quality_profile_explicit": artist_profile["explicit"],
        "albums": albums,
        "eps": eps,
        "singles": singles,
        "album_count": _in_library(albums) + _in_library(eps),
        "single_count": _in_library(singles),
        "discography_count": sum(1 for e in albums + eps + singles if e["origin"] == "discography"),
        # I8: sum of each release's own total_size_bytes above — one source
        # of truth, no separate artist-wide aggregate query needed.
        "total_size_bytes": sum(e["total_size_bytes"] for e in albums + eps + singles),
        "user_overrides": artist_overrides,
    }


def _track_artists(conn, track_id: int) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT ar.id, ar.name, ta.role, ta.position
        FROM lib2_track_artists ta
        JOIN lib2_artists ar ON ar.id = ta.artist_id
        WHERE ta.track_id = ?
        ORDER BY ta.position
        """,
        (track_id,),
    ).fetchall()
    result = []
    for r in rows:
        effective, overrides = project_metadata(
            conn,
            entity_type="artist",
            entity_id=r["id"],
            provider_fields=dict(r),
        )
        result.append({
            "id": r["id"],
            "name": effective["name"],
            "role": r["role"],
            "user_overrides": overrides,
        })
    return result


def _track_artists_many(
    conn, track_ids: List[int],
) -> Dict[int, List[Dict[str, Any]]]:
    """Load and project track credits once for an album result set."""
    if not track_ids:
        return {}
    marks = ",".join("?" for _ in track_ids)
    rows = conn.execute(
        f"""SELECT ta.track_id, ar.id, ar.name, ta.role, ta.position
              FROM lib2_track_artists ta
              JOIN lib2_artists ar ON ar.id=ta.artist_id
             WHERE ta.track_id IN ({marks})
             ORDER BY ta.track_id, ta.position""",
        track_ids,
    ).fetchall()
    projected = project_metadata_many(
        conn,
        entity_type="artist",
        provider_fields={int(row["id"]): dict(row) for row in rows},
    )
    result: Dict[int, List[Dict[str, Any]]] = {
        int(track_id): [] for track_id in track_ids
    }
    for row in rows:
        effective, overrides = projected[int(row["id"])]
        result[int(row["track_id"])].append({
            "id": row["id"],
            "name": effective["name"],
            "role": row["role"],
            "user_overrides": overrides,
        })
    return result


def _download_provenance_for_path(conn, path: Optional[str], *,
                                  track: Any = None,
                                  album: Optional[Dict[str, Any]] = None,
                                  artists: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Most recent quality/provenance row for a file path, if the old table exists."""
    try:
        row = None
        if path:
            row = conn.execute(
                "SELECT * FROM track_downloads WHERE file_path = ? ORDER BY id DESC LIMIT 1",
                (path,),
            ).fetchone()
            fname = str(path).replace("\\", "/").rsplit("/", 1)[-1]
            if row is None and fname:
                row = conn.execute(
                    "SELECT * FROM track_downloads WHERE file_path LIKE ? OR file_path LIKE ? "
                    "ORDER BY id DESC LIMIT 1",
                    (f"%/{fname}", f"%\\{fname}"),
                ).fetchone()
        if row is not None:
            return dict(row)

        if track is not None:
            for column, value in (
                ("spotify_track_id", track["spotify_id"] if "spotify_id" in track.keys() else None),
                ("musicbrainz_recording_id",
                 track["musicbrainz_id"] if "musicbrainz_id" in track.keys() else None),
                ("isrc", track["isrc"] if "isrc" in track.keys() else None),
            ):
                if not value:
                    continue
                row = conn.execute(
                    f"SELECT * FROM track_downloads WHERE {column} = ? ORDER BY id DESC LIMIT 1",
                    (value,),
                ).fetchone()
                if row is not None:
                    return dict(row)

        title = track["title"] if track is not None and "title" in track.keys() else None
        album_title = album.get("title") if album else None
        artist_names = [a.get("name") for a in (artists or []) if a.get("name")]
        if album and album.get("primary_artist_name"):
            artist_names.append(album["primary_artist_name"])
        unique_artist_names = []
        seen_artists = set()
        for name in artist_names:
            folded = name.casefold()
            if folded not in seen_artists:
                seen_artists.add(folded)
                unique_artist_names.append(name)
        if title:
            candidates: List[List[Tuple[str, Any]]] = []
            for artist_name in unique_artist_names:
                if album_title:
                    candidates.append([
                        ("lower(track_title) = lower(?)", title),
                        ("lower(track_artist) = lower(?)", artist_name),
                        ("lower(track_album) = lower(?)", album_title),
                    ])
                candidates.append([
                    ("lower(track_title) = lower(?)", title),
                    ("lower(track_artist) = lower(?)", artist_name),
                ])
            if album_title:
                candidates.append([
                    ("lower(track_title) = lower(?)", title),
                    ("lower(track_album) = lower(?)", album_title),
                ])
            for candidate in candidates:
                clauses = [part[0] for part in candidate]
                params = [part[1] for part in candidate]
                row = conn.execute(
                    "SELECT * FROM track_downloads WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY id DESC LIMIT 1",
                    params,
                ).fetchone()
                if row is not None:
                    return dict(row)

        return dict(row) if row else {}
    except Exception:
        return {}


def _download_provenance_many(
    conn,
    tracks: List[Any],
    files: Mapping[int, Dict[str, Any]],
    album: Optional[Dict[str, Any]],
    artists: Mapping[int, List[Dict[str, Any]]],
) -> Dict[int, Dict[str, Any]]:
    """Resolve legacy provenance candidates once for an album track set."""
    if not tracks:
        return {}

    def _values(column: str) -> List[str]:
        values = []
        for track in tracks:
            if column in track.keys() and track[column] not in (None, ""):
                values.append(str(track[column]))
        return sorted(set(values))

    paths = sorted({
        str(file_row["path"])
        for file_row in files.values()
        if file_row.get("path")
    })
    filenames = sorted({
        path.replace("\\", "/").rsplit("/", 1)[-1]
        for path in paths
        if path.replace("\\", "/").rsplit("/", 1)[-1]
    })
    predicates: List[str] = []
    params: List[Any] = []

    def _in(column: str, values: List[str], *, lower: bool = False) -> None:
        if not values:
            return
        marks = ",".join("?" for _ in values)
        predicates.append(
            f"lower({column}) IN ({marks})" if lower else f"{column} IN ({marks})"
        )
        params.extend(value.lower() if lower else value for value in values)

    _in("file_path", paths)
    for filename in filenames:
        predicates.append("(file_path LIKE ? OR file_path LIKE ?)")
        params.extend((f"%/{filename}", f"%\\{filename}"))
    _in("spotify_track_id", _values("spotify_id"))
    _in("musicbrainz_recording_id", _values("musicbrainz_id"))
    _in("isrc", _values("isrc"))
    _in("track_title", _values("title"), lower=True)
    if album and album.get("title"):
        predicates.append("lower(track_album)=lower(?)")
        params.append(album["title"])
    if not predicates:
        return {}
    try:
        candidates = [dict(row) for row in conn.execute(
            "SELECT * FROM track_downloads WHERE "
            + " OR ".join(predicates)
            + " ORDER BY id DESC",
            params,
        ).fetchall()]
    except Exception:
        return {}

    def _fold(value: Any) -> str:
        return str(value or "").casefold()

    result: Dict[int, Dict[str, Any]] = {}
    for track in tracks:
        track_id = int(track["id"])
        file_row = files.get(track_id) or {}
        path = str(file_row.get("path") or "")
        filename = path.replace("\\", "/").rsplit("/", 1)[-1]
        match = next(
            (row for row in candidates if path and row.get("file_path") == path),
            None,
        )
        if match is None and filename:
            match = next(
                (
                    row for row in candidates
                    if str(row.get("file_path") or "").replace("\\", "/").endswith(
                        f"/{filename}"
                    )
                ),
                None,
            )
        if match is None:
            for track_column, download_column in (
                ("spotify_id", "spotify_track_id"),
                ("musicbrainz_id", "musicbrainz_recording_id"),
                ("isrc", "isrc"),
            ):
                value = track[track_column] if track_column in track.keys() else None
                if value:
                    match = next(
                        (
                            row for row in candidates
                            if str(row.get(download_column) or "") == str(value)
                        ),
                        None,
                    )
                if match is not None:
                    break
        if match is None:
            title = _fold(track["title"] if "title" in track.keys() else None)
            album_title = _fold(album.get("title") if album else None)
            artist_names = []
            for artist in artists.get(track_id, []):
                if artist.get("name"):
                    artist_names.append(_fold(artist["name"]))
            if album and album.get("primary_artist_name"):
                artist_names.append(_fold(album["primary_artist_name"]))
            artist_names = list(dict.fromkeys(artist_names))
            for artist_name in artist_names:
                match = next(
                    (
                        row for row in candidates
                        if _fold(row.get("track_title")) == title
                        and _fold(row.get("track_artist")) == artist_name
                        and _fold(row.get("track_album")) == album_title
                    ),
                    None,
                ) if album_title else None
                if match is None:
                    match = next(
                        (
                            row for row in candidates
                            if _fold(row.get("track_title")) == title
                            and _fold(row.get("track_artist")) == artist_name
                        ),
                        None,
                    )
                if match is not None:
                    break
            if match is None and album_title:
                match = next(
                    (
                        row for row in candidates
                        if _fold(row.get("track_title")) == title
                        and _fold(row.get("track_album")) == album_title
                    ),
                    None,
                )
        if match is not None:
            result[track_id] = match
    return result


def _first_present(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", 0):
            return value
    return None


def _bitrate_kbps(value: Any) -> Any:
    if value in (None, "", 0):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return value
    if numeric > 10000:
        numeric = numeric / 1000
    return int(round(numeric))


_NOT_LOADED = object()


def _serialize_track(
    conn,
    t,
    album=None,
    *,
    file_row: Any = _NOT_LOADED,
    artists: Optional[List[Dict[str, Any]]] = None,
    projection: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None,
    provenance: Optional[Dict[str, Any]] = None,
    media_server_sources: Optional[List[str]] = None,
    linked_from: Any = _NOT_LOADED,
) -> Dict[str, Any]:
    """Build a track dict with linked artists, primary file, and computed status."""
    if file_row is _NOT_LOADED:
        from core.library2.track_files import primary_file_row
        file_row = primary_file_row(conn, t["id"])
    # §49.6(c): a position with no file of its own may still be on disk — under
    # another release that carries the same recording. Borrow that file rather
    # than drawing a gap the user is invited to re-download.
    if linked_from is _NOT_LOADED:
        linked_from = None
        if not file_row:
            from core.library2.recording_links import reference_owner
            linked_from = reference_owner(conn, t["id"])
            if linked_from:
                borrowed = conn.execute(
                    "SELECT * FROM lib2_track_files WHERE id=?",
                    (linked_from["file_id"],),
                ).fetchone()
                file_row = dict(borrowed) if borrowed else None
    if artists is None:
        artists = _track_artists(conn, t["id"])
    if projection is None:
        projection = project_metadata(
            conn,
            entity_type="track",
            entity_id=t["id"],
            provider_fields=dict(t),
        )
    effective, overrides = projection
    keys = set(t.keys())
    if "effective_wanted" in keys:
        wanted = bool(t["effective_wanted"])
    else:
        wanted_row = conn.execute(
            "SELECT wanted FROM lib2_wanted_tracks "
            "WHERE profile_id=1 AND track_id=?",
            (t["id"],),
        ).fetchone()
        wanted = bool(wanted_row["wanted"]) if wanted_row else bool(t["monitored"])
    gaps = compute_metadata_gaps(file_row)
    scan_status = metadata_scan_status(file_row)
    fstat = file_status(file_row, t["canonical_track_id"])
    if linked_from and fstat == "present":
        fstat = "linked"
    file_info = None
    if file_row:
        prov = provenance or {}
        if provenance is None and (
            not file_row["bitrate"]
            or not file_row["sample_rate"]
            or not file_row["bit_depth"]
        ):
            prov = _download_provenance_for_path(
                conn, file_row["path"], track=t, album=album, artists=artists
            )
        bitrate = _bitrate_kbps(_first_present(file_row["bitrate"], prov.get("bitrate")))
        sample_rate = _first_present(file_row["sample_rate"], prov.get("sample_rate"))
        bit_depth = _first_present(file_row["bit_depth"], prov.get("bit_depth"))
        source = _first_present(file_row["source"], prov.get("source_service"))
        has_rg = False
        has_lyrics = False
        if file_row.get("tags_json"):
            try:
                tags_data = json.loads(file_row["tags_json"]) or {}
                has_rg = any(
                    k in tags_data
                    for k in (
                        "replaygain_track_gain",
                        "replaygain_track_peak",
                        "replaygain_album_gain",
                        "replaygain_album_peak",
                    )
                )
                has_lyrics = bool(tags_data.get("lyrics") or tags_data.get("unsyncedlyrics"))
            except (AttributeError, TypeError, ValueError):
                has_rg = False
                has_lyrics = False
        pipeline_result = {}
        if file_row.get("pipeline_result_json"):
            try:
                pipeline_result = json.loads(file_row["pipeline_result_json"]) or {}
            except Exception:
                pipeline_result = {}
        file_info = {
            "file_id": file_row["id"],
            "path": file_row["path"],
            # Where the file sits IN THE LIBRARY. `path` stays the authority —
            # it is what the tooltip and the copy button hand back — but the
            # column leading with a root the user configured themselves says
            # nothing, and the same root reaches this table written three ways
            # ('./Transfer', 'Transfer', '/app/Transfer'), so the noise was not
            # even consistent between rows of one album.
            "display_path": library_relative_path(file_row["path"]),
            "format": file_row["format"],
            "bitrate": bitrate,
            "sample_rate": sample_rate,
            "bit_depth": bit_depth,
            "size": file_row["size"],
            "quality_tier": quality_tier(file_row["format"], bitrate, bit_depth),
            "import_status": file_row["import_status"],
            "verification_status": file_row["verification_status"],
            # Deep-dive A7/C4: AcoustID outcome + compact pipeline detail
            # (AcoustID reason, quality-profile fallback) for the Info-tab
            # lifecycle section — populated by the autolink import callback.
            "acoustid_status": file_row["acoustid_status"],
            "pipeline_result": pipeline_result,
            "source": source,
            "file_state": file_row["file_state"],
            "is_primary": bool(file_row.get("is_primary")),
            "primary_manual": bool(file_row.get("primary_manual")),
            "file_role": file_row.get("file_role") or "master",
            "derived_from_file_id": file_row.get("derived_from_file_id"),
            "acquired_quality_json": file_row.get("acquired_quality_json"),
            "retention_json": file_row.get("retention_json"),
            "has_replaygain": has_rg,
            "has_lyrics": has_lyrics,
        }
    return {
        "id": t["id"],
        "lib2_track_id": t["id"],
        "legacy_track_id": t["legacy_track_id"] if "legacy_track_id" in keys else None,
        # Legacy ids originate from the media-server-backed tracks table and
        # are therefore also the only safe server stream id available here.
        "server_track_id": t["legacy_track_id"] if "legacy_track_id" in keys else None,
        "title": effective["title"],
        "track_number": effective["track_number"],
        "disc_number": effective["disc_number"],
        "duration": effective["duration"],
        "bpm": effective["bpm"],
        "explicit": (bool(effective["explicit"]) if effective["explicit"] is not None else None),
        "style": effective["style"],
        "mood": effective["mood"],
        "isrc": t["isrc"],
        "monitored": wanted,
        "quality_profile_id": (
            t["quality_profile_id"] if bool(t["quality_profile_explicit"])
            else (album or {}).get("quality_profile_id")
        ),
        "quality_profile_source": (
            "track" if bool(t["quality_profile_explicit"])
            else (album or {}).get("quality_profile_source", "global")
        ),
        "quality_profile_source_id": (
            t["id"] if bool(t["quality_profile_explicit"])
            else (album or {}).get("quality_profile_source_id")
        ),
        "quality_profile_explicit": bool(t["quality_profile_explicit"]),
        "canonical_track_id": t["canonical_track_id"],
        "artists": artists,
        "file": file_info,
        "file_status": fstat,
        "linked_from": linked_from or None,
        "metadata_gaps": gaps,
        "metadata_scan_status": scan_status,
        "media_server_sources": media_server_sources or [],
        "user_overrides": overrides,
    }


def _borrowed_row(links: Dict[int, Dict[str, Any]],
                  borrowed_files: Dict[int, Dict[str, Any]],
                  track_id: int) -> Optional[Dict[str, Any]]:
    """The file row a fileless position borrows, if it borrows one."""
    link = links.get(track_id)
    return borrowed_files.get(int(link["file_id"])) if link else None


def _serialize_tracks(conn, tracks: List[Any], album=None) -> List[Dict[str, Any]]:
    """Serialize an album track set with bounded shared reads."""
    if not tracks:
        return []
    track_ids = [int(track["id"]) for track in tracks]
    from core.library2.track_files import primary_file_rows
    files = primary_file_rows(conn, track_ids)
    # One resolve for the whole table instead of one per fileless row.
    from core.library2.recording_links import reference_owners
    links = reference_owners(conn, [tid for tid in track_ids if not files.get(tid)])
    borrowed_files: Dict[int, Dict[str, Any]] = {}
    if links:
        file_ids = sorted({int(link["file_id"]) for link in links.values()})
        marks = ",".join("?" for _ in file_ids)
        borrowed_files = {
            int(row["id"]): dict(row)
            for row in conn.execute(
                f"SELECT * FROM lib2_track_files WHERE id IN ({marks})",
                tuple(file_ids),
            ).fetchall()
        }
    artists = _track_artists_many(conn, track_ids)
    projections = project_metadata_many(
        conn,
        entity_type="track",
        provider_fields={int(track["id"]): dict(track) for track in tracks},
    )
    needs_provenance = [
        track for track in tracks
        if (file_row := files.get(int(track["id"])))
        and (
            not file_row.get("bitrate")
            or not file_row.get("sample_rate")
            or not file_row.get("bit_depth")
        )
    ]
    provenance = _download_provenance_many(
        conn,
        needs_provenance,
        files,
        album,
        artists,
    )
    media_sources = _media_server_sources_many(conn, "track", track_ids)
    serialized = [
        _serialize_track(
            conn,
            track,
            album,
            file_row=(files.get(int(track["id"]))
                      or _borrowed_row(links, borrowed_files, int(track["id"]))),
            linked_from=(None if files.get(int(track["id"]))
                         else links.get(int(track["id"]))),
            artists=artists.get(int(track["id"]), []),
            projection=projections[int(track["id"])],
            provenance=provenance.get(int(track["id"]), {}),
            media_server_sources=media_sources.get(int(track["id"]), []),
        )
        for track in tracks
    ]
    marks = ",".join("?" for _ in track_ids)
    file_counts = {
        int(row["track_id"]): int(row["file_count"])
        for row in conn.execute(
            f"""SELECT track_id, COUNT(*) AS file_count
                  FROM lib2_track_files
                 WHERE track_id IN ({marks})
                   AND COALESCE(file_state,'active')<>'deleted'
                 GROUP BY track_id""",
            tuple(track_ids),
        ).fetchall()
    }
    for item in serialized:
        item["file_count"] = file_counts.get(int(item["id"]), 0) if item.get("id") else 0
    return serialized


def _missing_track_placeholder(track_number: int, *, disc_number: int = 1,
                               album=None, title: Optional[str] = None) -> Dict[str, Any]:
    """Expected-but-not-owned track row, mirroring Lidarr's missing rows."""
    artists = []
    if album and album.get("primary_artist_id") and album.get("primary_artist_name"):
        artists.append({
            "id": album["primary_artist_id"],
            "name": album["primary_artist_name"],
            "role": "primary",
        })
    return {
        "id": None,
        "title": title,
        "track_number": track_number,
        "disc_number": disc_number,
        "duration": None,
        "bpm": None,
        "explicit": None,
        "style": None,
        "mood": None,
        "isrc": None,
        "monitored": bool(album["monitored"]) if album and "monitored" in album else False,
        "quality_profile_id": album["quality_profile_id"] if album and "quality_profile_id" in album else None,
        "quality_profile_source": (
            album.get("quality_profile_source", "global") if album else "global"
        ),
        "quality_profile_source_id": (
            album.get("quality_profile_source_id") if album else None
        ),
        "quality_profile_explicit": False,
        "canonical_track_id": None,
        "artists": artists,
        "file": None,
        "file_status": "missing",
        "metadata_gaps": [],
        "media_server_sources": [],
        "is_missing": True,
    }


def get_album(conn, album_id: int) -> Optional[Dict[str, Any]]:
    """Album/single detail: header + track table with per-track status."""
    al = conn.execute("SELECT * FROM lib2_albums WHERE id = ?", (album_id,)).fetchone()
    if al is None:
        return None
    album_effective, album_overrides = project_metadata(
        conn,
        entity_type="release_group",
        entity_id=al["id"],
        provider_fields=dict(al),
    )
    album_profile = _quality_profile_assignment(conn, "albums", al["id"])
    qp = conn.execute(
        "SELECT * FROM quality_profiles WHERE id = ?", (album_profile["id"],)
    ).fetchone()
    artist = conn.execute(
        "SELECT id, name FROM lib2_artists WHERE id = ?", (al["primary_artist_id"],)
    ).fetchone()
    track_rows = conn.execute(
        """SELECT t.*, COALESCE(w.wanted, t.monitored) AS effective_wanted
             FROM lib2_tracks t
             LEFT JOIN lib2_wanted_tracks w
                    ON w.track_id=t.id AND w.profile_id=1
            WHERE t.album_id = ?
            ORDER BY t.disc_number, t.track_number, t.id""",
        (album_id,),
    ).fetchall()
    album_for_tracks = album_effective
    album_for_tracks["quality_profile_id"] = album_profile["id"]
    album_for_tracks["quality_profile_source"] = album_profile["source"]
    album_for_tracks["quality_profile_source_id"] = album_profile["source_id"]
    if artist:
        artist_effective, _artist_overrides = project_metadata(
            conn,
            entity_type="artist",
            entity_id=artist["id"],
            provider_fields=dict(artist),
        )
        album_for_tracks["primary_artist_name"] = artist_effective["name"]
        album_for_tracks["primary_artist_id"] = artist["id"]
    tracks = _serialize_tracks(conn, track_rows, album_for_tracks)
    present_count = sum(1 for t in tracks if t["file_status"] != "missing")

    # Evaluate each present file against its effective Track→Album→Artist→
    # Global profile.  A track override must affect both the profile shown in
    # the row and its upgrade badge; evaluating the entire album against the
    # album profile made those two UI statements contradict each other.
    from core.library2.quality_eval import evaluate_file, profile_targets
    profile_ids = sorted({
        int(t["quality_profile_id"])
        for t in tracks if t.get("quality_profile_id") is not None
    })
    profile_rows: Dict[int, Dict[str, Any]] = {}
    if profile_ids:
        marks = ",".join("?" for _ in profile_ids)
        profile_rows = {
            int(row["id"]): dict(row)
            for row in conn.execute(
                f"SELECT * FROM quality_profiles WHERE id IN ({marks})",
                tuple(profile_ids),
            ).fetchall()
        }
    upgrades_available = 0
    for t in tracks:
        if t.get("file") and t["file_status"] != "missing":
            targets, upgrade_policy, cutoff_index = profile_targets(
                profile_rows.get(int(t["quality_profile_id"]))
                if t.get("quality_profile_id") is not None else None
            )
            ev = evaluate_file(t["file"], targets, upgrade_policy, cutoff_index)
            t["meets_profile"] = ev["meets_profile"]
            candidate = ev["upgrade_candidate"]
            t["upgrade_candidate"] = (
                None if candidate is None
                else bool(t["monitored"] and candidate)
            )
            if t["upgrade_candidate"] is True:
                upgrades_available += 1
        else:
            t["meets_profile"] = None
            t["upgrade_candidate"] = False

    # Lidarr keeps expected missing recordings visible in the track table. When
    # we only know the album's expected size, expose those slots as missing rows
    # without pretending we know their title or tag gaps.
    expected = al["expected_track_count"] or 0
    known_count = len(tracks)
    total = max(expected, known_count, present_count)
    known_numbers = {
        (t.get("disc_number") or 1, t.get("track_number"))
        for t in tracks
        if t.get("track_number") is not None
    }
    # Slots for the missing tracks. When the album's canonical tracklist is
    # cached (core/library2/completeness.py) the slots come from it — with the
    # real title AND disc number, so multi-disc albums don't get colliding
    # disc-1 placeholders. Without a tracklist, fall back to a numeric loop.
    tl_entries: List[Dict[str, Any]] = []
    try:
        tl_raw = al["tracklist_json"] if "tracklist_json" in al.keys() else None
        for entry in (json.loads(tl_raw) if tl_raw else []):
            num = entry.get("track_number")
            if num:
                tl_entries.append({
                    "track_number": int(num),
                    "disc_number": int(entry.get("disc_number") or 1),
                    "title": entry.get("title"),
                })
    except (ValueError, TypeError):
        tl_entries = []
    if total > known_count:
        if tl_entries:
            for entry in tl_entries:
                key = (entry["disc_number"], entry["track_number"])
                if key not in known_numbers:
                    tracks.append(_missing_track_placeholder(
                        entry["track_number"], disc_number=entry["disc_number"],
                        album=album_for_tracks, title=entry.get("title")))
        else:
            for number in range(1, total + 1):
                if (1, number) not in known_numbers:
                    tracks.append(_missing_track_placeholder(number, album=album_for_tracks))
    tracks.sort(key=lambda t: (t.get("disc_number") or 1, t.get("track_number") or 0,
                              t.get("id") or 0))

    origin = "library"
    try:
        origin = al["origin"] or "library"
    except (IndexError, KeyError):
        pass

    return {
        "id": al["id"],
        "title": album_effective["title"],
        "album_type": album_effective["album_type"],
        "release_date": album_effective["release_date"],
        "year": album_effective["year"],
        "image_url": album_effective["image_url"],
        "genres": _json_list(album_effective["genres"]),
        "explicit": (
            bool(album_effective["explicit"]) if album_effective["explicit"] is not None else None
        ),
        "label": album_effective["label"],
        "style": album_effective["style"],
        "mood": album_effective["mood"],
        "monitored": bool(al["monitored"]),
        "origin": origin,
        "quality_profile": _quality_profile_dict(qp),
        "quality_profile_source": album_profile["source"],
        "quality_profile_source_id": album_profile["source_id"],
        "quality_profile_explicit": album_profile["source"] == "album",
        "primary_artist": {
            "id": artist["id"],
            "name": album_for_tracks["primary_artist_name"],
        } if artist else None,
        "tracks": tracks,
        "track_count": total,
        "tracks_present": present_count,
        # Same rule as the album list: only a track you still want counts as
        # missing, plus the slots the provider promised that have no row yet
        # (those have no monitored flag of their own — the album's answers).
        #
        # ARCH-02: the placeholders for those promised slots were appended to
        # `tracks` above and inherit the album's monitored flag, so on a
        # monitored album the sum already counted them and `total - known_count`
        # counted them a second time — two expected tracks with no rows yet
        # reported four missing, more missing than the album has. `id is None`
        # is what makes a row a placeholder; only real rows belong in the sum.
        "tracks_missing": sum(
            1 for t in tracks
            if t.get("id") is not None
            and t["file_status"] == "missing" and t.get("monitored")
        ) + max(0, total - known_count),
        # I8: disk-space roll-up — sum of each present track's primary file.
        "total_size_bytes": sum(
            t["file"]["size"] or 0 for t in tracks if t.get("file") and t["file"].get("size")
        ),
        "upgrades_available": upgrades_available,
        "tracklist_sync": {
            "status": al["tracklist_status"],
            "attempts": al["tracklist_attempts"],
            "error": al["tracklist_error"],
            "retry_at": al["tracklist_retry_at"],
        },
        "user_overrides": album_overrides,
    }


def get_track(conn, track_id: int) -> Optional[Dict[str, Any]]:
    """Single-track detail incl. linked album + artists + file + status."""
    t = conn.execute(
        """SELECT t.*, COALESCE(w.wanted, t.monitored) AS effective_wanted
             FROM lib2_tracks t
             LEFT JOIN lib2_wanted_tracks w
                    ON w.track_id=t.id AND w.profile_id=1
            WHERE t.id = ?""",
        (track_id,),
    ).fetchone()
    if t is None:
        return None
    album = conn.execute("SELECT * FROM lib2_albums WHERE id = ?", (t["album_id"],)).fetchone()
    album_effective = None
    album_overrides: Dict[str, Any] = {}
    if album:
        album_effective, album_overrides = project_metadata(
            conn,
            entity_type="release_group",
            entity_id=album["id"],
            provider_fields=dict(album),
        )
    data = _serialize_track(conn, t, album_effective)
    data["album"] = {
        "id": album["id"],
        "title": album_effective["title"],
        "album_type": album_effective["album_type"],
        "user_overrides": album_overrides,
    } if album else None
    return data


def list_quality_profiles(conn) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM quality_profiles ORDER BY is_default DESC, id"
    ).fetchall()
    return [_quality_profile_dict(row) for row in rows if row is not None]


__all__ = ["legacy_api_artists_page", "list_artists", "list_artist_track_files",
           "get_artist", "get_album", "get_track", "list_quality_profiles"]
