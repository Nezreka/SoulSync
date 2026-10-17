"""Shared native artist selection, filters, paging and page-scoped rollups.

Response adapters live in queries/MusicDatabase. Upgrade selection uses active
owned file facts and the same live profile cascade/evaluator as acquisition.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple
from .sql_util import intent_profile_id, monitored_sql, owner_clause, scope_visibility_sql
from .track_files import primary_order

def artist_pagination(page: int, limit: int, total: int) -> Dict[str, Any]:
    page = max(1, int(page))
    limit = max(1, min(int(limit), 500))
    pages = (int(total) + limit - 1) // limit
    return {"page": page, "limit": limit, "total_count": int(total),
            "total_pages": pages, "has_prev": page > 1, "has_next": page < pages}


def _upgradable_artist_ids(conn, predicate: str, params: Dict[str, Any]) -> set[int]:
    """Evaluate each candidate's scoped primary active file with the live kernel.

    A manual primary choice still wins within the selected library. Historical,
    missing and fileless rows are not evidence of an owned upgrade candidate.
    Profiles and file facts are batched; no Findings or stale wanted quality
    projections decide this filter.
    """
    from .profile_lookup import default_quality_profile_id, resolve_profile_cascade
    from .quality_eval import evaluate_file, profile_targets

    owner = owner_clause(column="f.owner_profile_id")
    rows = conn.execute(f"""
        WITH candidates AS MATERIALIZED (
            SELECT a.id FROM lib2_artists a WHERE {predicate}
        ), members AS MATERIALIZED (
            SELECT a.id, COALESCE(a.canonical_artist_id,a.id) AS canonical_id
              FROM lib2_artists a
             WHERE COALESCE(a.canonical_artist_id,a.id) IN (SELECT id FROM candidates)
        ), artist_tracks AS MATERIALIZED (
            SELECT m.canonical_id AS artist_id, t.id AS track_id
              FROM members m CROSS JOIN lib2_albums al ON al.primary_artist_id=m.id
              JOIN lib2_tracks t ON t.album_id=al.id
            UNION
            SELECT m.canonical_id, ta.track_id
              FROM members m CROSS JOIN lib2_track_artists ta ON ta.artist_id=m.id
        ), ranked_files AS MATERIALIZED (
            SELECT f.*, ROW_NUMBER() OVER (
                PARTITION BY f.track_id ORDER BY {primary_order('f')}) AS file_rank
              FROM (SELECT DISTINCT track_id FROM artist_tracks) at
              CROSS JOIN lib2_track_files f ON f.track_id=at.track_id
             WHERE COALESCE(f.file_state,'active')='active'
               AND f.path IS NOT NULL AND TRIM(f.path)<>''{owner}
        )
        SELECT f.*, at.artist_id, t.quality_profile_id AS tp,
               t.quality_profile_explicit AS te, t.album_id,
               al.quality_profile_id AS ap, al.quality_profile_explicit AS ae,
               al.primary_artist_id, ar.quality_profile_id AS rp,
               ar.quality_profile_explicit AS re
          FROM artist_tracks at JOIN ranked_files f ON f.track_id=at.track_id AND f.file_rank=1
          JOIN lib2_tracks t ON t.id=at.track_id
          JOIN lib2_albums al ON al.id=t.album_id
          LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id
    """, params).fetchall()
    default_id = default_quality_profile_id(conn)
    profiles = {int(row['id']): dict(row) for row in conn.execute("SELECT * FROM quality_profiles")}
    settings = {}
    verdicts = {}
    result = set()
    for row in rows:
        tid = int(row['track_id'])
        if tid not in verdicts:
            resolved = resolve_profile_cascade((
                ('track', tid, row['tp'], row['te']),
                ('album', row['album_id'], row['ap'], row['ae']),
                ('artist', row['primary_artist_id'], row['rp'], row['re']),
            ), default_id)
            pid = resolved['id']
            if pid not in settings:
                settings[pid] = profile_targets(profiles.get(pid))
            targets, policy, cutoff = settings[pid]
            verdicts[tid] = evaluate_file(dict(row), targets, policy, cutoff)['upgrade_candidate'] is True
        if verdicts[tid]:
            result.add(int(row['artist_id']))
    return result


def _alpha_key(column: str) -> str:
    """Upstream's A-Z key (33da6be49): leading punctuation is skipped, so
    '"Weird Al" Yankovic' files under W and '*NSYNC' under N. One definition,
    shared with the compatibility API in ``database/music_database.py``."""
    from database.music_database import _library_sort_key_sql

    return _library_sort_key_sql(column)


_SORTS = {
    # An empty or NULL sort_name sorts by the name rather than ahead of A.
    "name": (_alpha_key("COALESCE(NULLIF(a.sort_name, ''), a.name)")
             + ", a.name COLLATE NOCASE"),
    "added": "a.added_at DESC",
    "recent": "a.added_at DESC, a.id DESC",
    "albums": "album_count DESC, a.name COLLATE NOCASE",
    "tracks": "track_count DESC, a.name COLLATE NOCASE",
}


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


class _ProfileWatchlist:
    """The calling profile's legacy watchlist, as one rule used twice.

    Guide §2.6: the global lib2 ``monitored`` flag is the *admin's* intent, and
    other household profiles keep their own watchlist. So membership is decided
    by ``watchlist_artists`` rows for one ``profile_id``, matched exactly the way
    ``MusicDatabase.get_library_artists`` matches them: Spotify id, iTunes id, or
    lowercased name.

    The page filter and ``is_watched`` projection use the identical SQL
    predicate, including SQLite's case folding, so their answers cannot drift.
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


def read_artist_page(conn, *, search: str = "", sort: str = "name", monitored: str = "all",
                 page: int = 1, limit: int = 75,
                 include_size: bool = True, letter: str = "all",
                 watchlist_filter: str = "all", source_filter: str = "",
                 quality_filter: str = "", profile_id: int = 1,
                 require_legacy_id: bool = False) -> Tuple[List[Dict[str, Any]], int]:
    """Paginated artist overview with per-artist roll-up stats.

    ``monitored`` filters the list: ``'all'`` (default), ``'monitored'``, or
    ``'unmonitored'``.

    ``include_size`` (perf25-03) controls the disk-space roll-up, which needs a
    window function over every file of the page's artists plus a SUM on top of
    it — by far the heaviest part of this query.  The size column is opt-in in
    the artist table (default off), so the caller may switch it off and get
    ``total_size_bytes = 0`` for a value nothing renders.
    """
    # whose library this page is (#1199). Built per call: the scope belongs
    # to whoever is asking, and while SCOPE_PARKED is true it is empty, so
    # every query here is byte for byte the one that ran before.
    tf_owner = owner_clause(column="tf.owner_profile_id")
    album_visible = scope_visibility_sql("album", "al") or "1=1"
    album_monitored = monitored_sql("album", "al")
    track_monitored = monitored_sql("track", "t")
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
    from core.library2.sql_util import library_artist_sql
    clauses, params = ["a.canonical_artist_id IS NULL", library_artist_sql('a')], {}
    # ...and, once directories exist, only the ones this library may see: a
    # file of ours hangs off it, or we have monitoring intent on it. Empty
    # while the scope is every library, so an install without own directories
    # -- and every install while SCOPE_PARKED is true -- lists what it listed
    # before (E-03).
    visible = scope_visibility_sql("artist", "va")
    if visible:
        # Across the ALIAS GROUP, not just the canonical row. §40 folds member
        # rows into their canonical entry and get_artist merges their albums in
        # afterwards, so a canonical artist whose owned files all hang off an
        # alias would otherwise be judged fileless and dropped from its owner's
        # own list.
        clauses.append(
            "EXISTS (SELECT 1 FROM lib2_artists va"
            "  WHERE COALESCE(va.canonical_artist_id, va.id) = a.id"
            f"   AND {visible})")
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
    # the monitored flag THIS library sees: the shared library's global
    # column, or an own library's rules (#1199)
    artist_monitored = monitored_sql("artist", "a")
    if monitored == "monitored":
        clauses.append(f"{artist_monitored} = 1")
    elif monitored == "unmonitored":
        clauses.append(f"{artist_monitored} = 0")
    if require_legacy_id:
        clauses.append("a.legacy_artist_id IS NOT NULL")
    if letter and letter != "all":
        from database.music_database import _library_stripped_sql
        stripped = _library_stripped_sql("a.name")
        if letter == "#":
            clauses.append(f"SUBSTR(UPPER({stripped}),1,1) NOT GLOB '[A-Z]'")
        else:
            clauses.append(f"UPPER(SUBSTR({stripped},1,1)) = UPPER(:letter)")
            params["letter"] = letter
    watchlist = _ProfileWatchlist(conn, profile_id)
    watchlist_predicate = watchlist.sql(params)
    if watchlist_filter == "watched":
        clauses.append(watchlist_predicate)
    elif watchlist_filter == "unwatched":
        clauses.append(f"NOT {watchlist_predicate}")
    from .provider_ids import normalize_provider_name
    source = normalize_provider_name(str(source_filter or "").lstrip("!"))
    if source:
        parts = [f"COALESCE(json_extract(a.external_ids, '$.{source}'),'') <> ''"]
        if source in ("spotify", "musicbrainz"):
            parts.append(f"COALESCE(a.{source}_id,'') <> ''")
        predicate = "(" + " OR ".join(parts) + ")"
        clauses.append(f"NOT {predicate}" if str(source_filter).startswith("!") else predicate)
    if quality_filter == "upgradable":
        ids = _upgradable_artist_ids(conn, " AND ".join(clauses), params)
        params["upgrade_artists"] = json.dumps(sorted(ids))
        clauses.append("a.id IN (SELECT value FROM json_each(:upgrade_artists))")
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
             WHERE COALESCE(tf.file_state, 'active') <> 'deleted'{tf_owner}
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
                        AND (al.origin='library' OR {album_monitored}=1)
                       THEN al.id END) AS album_count,
                   COUNT(DISTINCT CASE
                       WHEN al.album_type = 'single'
                        AND (al.origin='library' OR {album_monitored}=1)
                       THEN al.id END) AS single_count
              FROM artist_albums aa
              JOIN lib2_albums al ON al.id=aa.album_id
             -- the same library as the track counters beside them, or the row
             -- reads "41 albums, 1 track present" from two different scopes
             WHERE {album_visible}
             GROUP BY aa.artist_id
        ),
        track_stats AS (
            SELECT cm.canonical_id AS artist_id,
                   COUNT(DISTINCT CASE
                       WHEN COALESCE(w.wanted, {track_monitored})=1 OR tf.id IS NOT NULL
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
                     ON w.track_id=t.id AND w.profile_id={intent_profile_id()}
              LEFT JOIN lib2_track_files tf ON tf.track_id=t.id{tf_owner}
             GROUP BY cm.canonical_id
        ){size_cte}
        SELECT a.id, a.legacy_artist_id, a.name, a.sort_name, a.image_url, a.genres,
               a.spotify_id, a.musicbrainz_id, a.external_ids, a.soul_id, a.server_source,
               {watchlist_predicate} AS is_watched,
               {artist_monitored} AS monitored, a.monitor_new_items, a.quality_profile_id,
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

    result = []
    for row in rows:
        data = dict(row)
        data["is_watched"] = bool(data["is_watched"])
        result.append(data)
    return result, int(total)
