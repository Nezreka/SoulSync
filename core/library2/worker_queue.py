"""Batch selection for enrichment workers, from lib2 (docs §32.3.1 stage 2).

All sixteen workers pick their next item by the same rules: unattempted artists,
then albums, then tracks, then failures whose retry window has expired, with an
optional pinned entity type served first (Manage Enrichment Workers). Legacy drove
that from ``<service>_match_status`` / ``<service>_last_attempted``; this drives it
from :mod:`core.library2.provider_attempts`.

Written once here rather than sixteen times, and it returns the exact dict shape
the workers already consume, so the change inside each one stays small.

Both ``not_found`` and per-item ``error`` outcomes are retried after the configured
window. Source-wide outages are handled before an attempt is recorded and use the
workers' own backoff, so they cannot turn into a tight catalogue-wide retry loop.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from core.library2.provider_attempts import DEFAULT_RETRY_AFTER_DAYS
from core.library2.sql_util import owned_sql

ENTITY_ORDER = ("artist", "album", "track")

# name/title plus the artist name the provider query needs, per entity type.
def _provider_id_sql(alias: str, service: str) -> str:
    """The stored id for one service, column or JSON, as a SQL expression.

    ``json_extract`` rather than a LIKE: an id is a value, and matching it as a
    substring of the whole object would collide across services.
    """
    if service in ("spotify", "musicbrainz"):
        column = f"{alias}.{service}_id" if service == "spotify" else f"{alias}.musicbrainz_id"
        return (f"COALESCE(NULLIF({column},''), "
                f"json_extract({alias}.external_ids, '$.{service}'))")
    return f"json_extract({alias}.external_ids, '$.{service}')"


# The parent artist row a child item hangs off, so a child can be handed its
# artist's provider id. A track reaches it through its album — lib2 has no
# tracks.artist_id the way legacy did.
_PARENT_ALIAS = {"artist": None, "album": "ar", "track": "ar"}


def _sources(service: str) -> Dict[str, str]:
    """The per-entity SELECT, optionally carrying the parent artist's provider id."""
    parent = _provider_id_sql("ar", service)
    return {
        "artist": """
            SELECT e.id AS id, e.name AS name, NULL AS artist_name,
                   NULL AS parent_provider_id
              FROM lib2_artists e
        """,
        "album": f"""
            SELECT e.id AS id, e.title AS name, ar.name AS artist_name,
                   {parent} AS parent_provider_id
              FROM lib2_albums e
              JOIN lib2_artists ar ON ar.id = e.primary_artist_id
        """,
        "track": f"""
            SELECT e.id AS id, e.title AS name, ar.name AS artist_name,
                   {parent} AS parent_provider_id
              FROM lib2_tracks e
              JOIN lib2_albums al ON al.id = e.album_id
              JOIN lib2_artists ar ON ar.id = al.primary_artist_id
        """,
    }
_TABLES = {"artist": "lib2_artists", "album": "lib2_albums", "track": "lib2_tracks"}
_RETRYABLE = ("not_found", "error")
_STATUSES = frozenset({"matched", "not_found", "error", "skipped"})

# Similar Artists can source only these four provider namespaces.
_HAS_PROVIDER_ID = (
    "COALESCE(e.spotify_id,'') <> '' OR COALESCE(e.musicbrainz_id,'') <> '' "
    "OR COALESCE(json_extract(e.external_ids,'$.spotify'),'') <> '' "
    "OR COALESCE(json_extract(e.external_ids,'$.itunes'),'') <> '' "
    "OR COALESCE(json_extract(e.external_ids,'$.deezer'),'') <> '' "
    "OR COALESCE(json_extract(e.external_ids,'$.musicbrainz'),'') <> ''"
)


def _retryable_sql(retry_statuses: tuple) -> str:
    """Whitelisted against the ledger's own status vocabulary, because it is
    interpolated rather than bound — SQLite cannot parameterize an IN list."""
    wanted = [str(status).strip().lower() for status in retry_statuses]
    unknown = [status for status in wanted if status not in _STATUSES]
    if unknown:
        raise ValueError(f"Unknown attempt status(es): {sorted(unknown)}")
    return ", ".join(f"'{status}'" for status in wanted) or "''"


_PHASES = ("new", "retry")


def _pending_sql(entity_type: str, retry_statuses: tuple = _RETRYABLE,
                 require_provider_id: bool = False,
                 service: str = "spotify", phase: str = "new") -> str:
    """One of the queue's two disjoint halves: never attempted, or due again.

    Deliberately two queries. Ordering both together needs
    ``ORDER BY last_attempted_at IS NOT NULL, ...`` over a LEFT JOIN, which no
    index can serve — so SQLite built a temp b-tree over every artist/album/track
    row before taking the one row a worker asked for, three times per tick.
    Apart, each half stops early like legacy did: ``new`` walks entity ids,
    ``retry`` is driven by ``idx_lib2_provider_attempts_due``. Priority between
    them stays the caller's, which asks for ``new`` first.
    """
    universe = f"AND ({_HAS_PROVIDER_ID})" if require_provider_id else ""
    # The library the user owns, which is all legacy's tables could ever hold.
    # v2 keeps a watched artist's discography and the wishlist in the same
    # tables, and without this every worker enriched those too — work legacy
    # never did, re-asked every retry window, and counted into the denominator.
    owned = f"AND {owned_sql(entity_type, 'e')}"
    if phase == "new":
        return f"""
            {_sources(service)[entity_type]}
             WHERE NOT EXISTS (SELECT 1 FROM lib2_provider_attempts a
                                WHERE a.entity_type = :entity
                                  AND a.entity_id = e.id
                                  AND a.service = :service)
               {owned}
               {universe}
             ORDER BY e.id
        """
    return f"""
        {_sources(service)[entity_type]}
          JOIN lib2_provider_attempts a
                 ON a.entity_type = :entity AND a.entity_id = e.id
                AND a.service = :service
         WHERE a.status IN ({_retryable_sql(retry_statuses)})
           AND a.last_attempted_at <= datetime('now', :window)
           {owned}
           {universe}
         ORDER BY a.last_attempted_at, e.id
    """


def _params(entity_type: str, service: str, retry_after_days: int) -> Dict[str, Any]:
    return {"entity": entity_type, "service": str(service).strip().lower(),
            "window": f"-{max(0, int(retry_after_days))} days"}


def _fetch(conn, entity_type: str, service: str, retry_after_days: int,
           retry_statuses: tuple = _RETRYABLE,
           require_provider_id: bool = False,
           phase: str = "new") -> Optional[Any]:
    key = str(service).strip().lower()
    return conn.execute(
        _pending_sql(entity_type, retry_statuses, require_provider_id, key,
                     phase) + " LIMIT 1",
        _params(entity_type, key, retry_after_days)).fetchone()


def next_pending(
    conn, service: str, *,
    retry_after_days: int = DEFAULT_RETRY_AFTER_DAYS,
    pinned: Optional[str] = None,
    type_overrides: Optional[Mapping[str, str]] = None,
    entity_types: tuple = ENTITY_ORDER,
    retry_statuses: tuple = _RETRYABLE,
    require_provider_id: bool = False,
    include_parent_id: bool = False,
) -> Optional[Dict[str, Any]]:
    """The next item this provider should look at, or None when nothing is due.

    ``pinned`` puts one entity type at the front and then falls through to the
    normal order once it is exhausted — unset or exhausted behaves exactly like
    the default artist→album→track chain.

    ``retry_statuses`` defaults to both retryable terminal states. A definitive
    miss is ``not_found`` and provider/network/write failures are ``error``;
    freezing either forever would regress the legacy workers' retry contract.
    Callers may narrow the tuple for a provider with different semantics.
    ``require_provider_id`` narrows the universe to entities already
    matched to a metadata source, for work that is keyed by that id and has
    nothing to do without one.

    ``include_parent_id`` adds ``artist_<service>_id`` to an album or track item.
    Five workers compare a child's result against that id, because a track our
    library credits to one artist but which lives on another artist's album would
    otherwise stamp the wrong id onto our artist. It is None when the parent is
    unmatched, so the callers' ``if not parent_id: return True`` guard reads the same
    as it did on legacy.
    """
    overrides = dict(type_overrides or {})
    order = list(entity_types)
    if pinned in order:
        order.remove(pinned)
        order.insert(0, pinned)
    # Phase outside, entity type inside. Legacy ran priorities 1-3 (unattempted
    # artists, albums, tracks) before 4-6 (their expired retries), so a freshly
    # imported album was never queued behind a backlog of artist retries. Asking
    # per entity type instead inverted that; only the loop nesting says so.
    for phase in _PHASES:
        for entity_type in order:
            row = _fetch(conn, entity_type, service, retry_after_days,
                         retry_statuses, require_provider_id, phase)
            if row is None:
                continue
            item: Dict[str, Any] = {
                "type": overrides.get(entity_type, entity_type),
                "id": int(row["id"]),
                "name": row["name"],
            }
            if row["artist_name"] is not None:
                item["artist"] = row["artist_name"]
            if include_parent_id and entity_type != "artist":
                item[f"artist_{str(service).strip().lower()}_id"] = (
                    row["parent_provider_id"])
            return item
    return None


def pending_count(conn, service: str, *,
                  retry_after_days: int = DEFAULT_RETRY_AFTER_DAYS,
                  entity_types: tuple = ENTITY_ORDER,
                  retry_statuses: tuple = _RETRYABLE,
                  require_provider_id: bool = False) -> int:
    """How many items still need this provider looked at."""
    total = 0
    key = str(service).strip().lower()
    for entity_type in entity_types:
        params = _params(entity_type, key, retry_after_days)
        for phase in _PHASES:
            total += int(conn.execute(
                "SELECT COUNT(*) FROM ("
                + _pending_sql(entity_type, retry_statuses, require_provider_id,
                               key, phase) + ")", params).fetchone()[0])
    return total


def status_counts(conn, service: str, entity_type: str, *,
                  require_provider_id: bool = False) -> Dict[str, int]:
    """Persistent tallies over one entity type: each outcome, plus never-attempted.

    ``total`` counts the same population the queue picks from, so the two agree —
    a tally over a wider universe than the selection would show a percentage that
    never reaches 100. That is why the ownership predicate is not optional here
    (L2-016): v2 keeps discography, wishlist and provider-only rows in the same
    tables, and counting those reported pending work ``next_pending`` can never
    hand out, so the bar sat below 100% forever.
    """
    predicates = [owned_sql(entity_type, "e")]
    if require_provider_id:
        predicates.append(f"({_HAS_PROVIDER_ID})")
    universe = "WHERE " + " AND ".join(predicates)
    row = conn.execute(
        f"""
        SELECT
            SUM(CASE WHEN a.status='matched'   THEN 1 ELSE 0 END) AS matched,
            SUM(CASE WHEN a.status='not_found' THEN 1 ELSE 0 END) AS not_found,
            SUM(CASE WHEN a.status='error'     THEN 1 ELSE 0 END) AS error,
            SUM(CASE WHEN a.entity_id IS NULL  THEN 1 ELSE 0 END) AS pending,
            COUNT(*) AS total
          FROM (SELECT e.id, e.spotify_id, e.musicbrainz_id, e.external_ids
                  FROM {_TABLES[entity_type]} e {universe}) e
          LEFT JOIN lib2_provider_attempts a
                 ON a.entity_type=:entity AND a.entity_id=e.id AND a.service=:service
        """,
        {"entity": entity_type, "service": str(service).strip().lower()},
    ).fetchone()
    return {key: int(row[key] or 0)
            for key in ("matched", "not_found", "error", "pending", "total")}


def progress_breakdown(conn, service: str, *,
                       entity_types: tuple = ENTITY_ORDER) -> Dict[str, Dict[str, int]]:
    """Per-entity-type progress, keyed the way the UI already expects.

    Any recorded attempt counts as progress, including ``not_found``: legacy
    counted every non-NULL ``match_status`` the same way, and a bar that only
    counted successes would never reach 100% on a library with obscure releases.
    """
    out: Dict[str, Dict[str, int]] = {}
    key = str(service).strip().lower()
    for entity_type in entity_types:
        # Both halves are scoped to the OWNED library, matching the universe
        # the queue actually works on (`_pending_sql`). Counting every row in
        # the table meant a library with any discography or wishlist rows had a
        # denominator the worker would never reach, so the bar could not hit
        # 100% however long it ran.
        row = conn.execute(
            f"""
            SELECT (SELECT COUNT(*) FROM {_TABLES[entity_type]} e
                     WHERE {owned_sql(entity_type, "e")}) AS total,
                   (SELECT COUNT(*) FROM lib2_provider_attempts a
                     JOIN {_TABLES[entity_type]} e ON e.id = a.entity_id
                    WHERE a.entity_type=:entity AND a.service=:service
                      AND {owned_sql(entity_type, "e")}) AS processed
            """,
            {"entity": entity_type, "service": key},
        ).fetchone()
        total = int(row["total"] or 0)
        processed = min(int(row["processed"] or 0), total)
        out[f"{entity_type}s"] = {
            "matched": processed,
            "total": total,
            "percent": int((processed / total * 100) if total else 0),
        }
    return out


# --- Batch-first selection -------------------------------------------------
#
# Spotify and iTunes both have "every album by this artist" and "every track on
# this album" endpoints, and both build their queue around them: a matched artist
# with unattempted albums is worth ONE call for the whole set, where the individual
# path spends one per album. Falling back to individual lookups only when the parent
# is itself unmatched is what keeps the daily API budget survivable on a large
# library.
#
# That is a different shape from next_pending — it selects a *parent* and then works
# on its children — so it is its own function rather than a flag on that one.

_CHILD = {
    "album": {
        "table": "lib2_albums",
        "title": "title",
        "parent_type": "artist",
        "parent_join": "e.primary_artist_id = :parent",
        "extra": (),
    },
    "track": {
        "table": "lib2_tracks",
        "title": "title",
        "parent_type": "album",
        "parent_join": "e.album_id = :parent",
        "extra": ("track_number",),
    },
}


def _batch_parent(conn, service: str, child: str) -> Optional[Any]:
    """A settled parent, holding a provider id, with at least one child due."""
    # The ownership filter `_pending_sql` applies is needed here too. The
    # single-item path was owned-filtered and the BATCH path was not, so a
    # watchlisted artist with one owned album and sixty provider-only
    # discography releases handed the worker all sixty: it spent API budget
    # matching releases the user does not own, which then became `matched`
    # parents and seeded track_batch work of their own -- while the UI read the
    # owned-filtered pending_count and showed 0 pending / 100%.
    spec = _CHILD[child]
    parent_table = _TABLES[spec["parent_type"]]
    parent_id_sql = _provider_id_sql("p", service)
    if spec["parent_type"] == "artist":
        select = "p.id AS parent_id, p.name AS parent_name, NULL AS grandparent_name"
        child_link = "c.primary_artist_id = p.id"
    else:
        select = ("p.id AS parent_id, p.title AS parent_name, "
                  "ar.name AS grandparent_name")
        child_link = "c.album_id = p.id"
    grandparent = (
        "JOIN lib2_artists ar ON ar.id = p.primary_artist_id"
        if spec["parent_type"] == "album" else "")
    return conn.execute(
        f"""
        SELECT {select}, {parent_id_sql} AS provider_id
          FROM {parent_table} p
          {grandparent}
          JOIN lib2_provider_attempts a
                ON a.entity_type = :parent_type AND a.entity_id = p.id
               AND a.service = :service AND a.status = 'matched'
         WHERE COALESCE({parent_id_sql}, '') <> ''
           AND EXISTS (
               SELECT 1 FROM {spec["table"]} c
                 LEFT JOIN lib2_provider_attempts ca
                        ON ca.entity_type = :child_type AND ca.entity_id = c.id
                       AND ca.service = :service
                WHERE {child_link} AND ca.entity_id IS NULL
                  AND {owned_sql(child, "c")})
         ORDER BY p.id LIMIT 1
        """,
        {"parent_type": spec["parent_type"], "child_type": child,
         "service": str(service).strip().lower()},
    ).fetchone()


def next_batch_pending(conn, service: str, *,
                       retry_after_days: int = DEFAULT_RETRY_AFTER_DAYS,
                       pinned: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """The next item for a provider that fetches children in bulk.

    Order: a pinned group first, then unattempted artists, then an album batch, a
    track batch, and finally the individual fallbacks for children whose own parent
    never matched — which is the only case where a per-child lookup is unavoidable.
    Retries reuse the individual shapes.

    The returned dicts are exactly what the Spotify and iTunes workers already
    consume, including the service-prefixed id key (``spotify_artist_id`` /
    ``itunes_artist_id``), so their process methods are untouched.
    """
    key = str(service).strip().lower()
    overrides = {"album": "album_individual", "track": "track_individual"}

    if pinned:
        item = next_pending(conn, key, retry_after_days=retry_after_days,
                            entity_types=(pinned,), type_overrides=overrides)
        if item:
            return item

    artist = next_pending(conn, key, retry_after_days=retry_after_days,
                          entity_types=("artist",))
    if artist:
        return artist

    row = _batch_parent(conn, key, "album")
    if row is not None:
        return {
            "type": "album_batch",
            "artist_id": row["parent_id"],
            "artist_name": row["parent_name"],
            f"{key}_artist_id": row["provider_id"],
            "name": f"Albums for {row['parent_name']}",
        }

    row = _batch_parent(conn, key, "track")
    if row is not None:
        return {
            "type": "track_batch",
            "album_id": row["parent_id"],
            "album_name": row["parent_name"],
            f"{key}_album_id": row["provider_id"],
            "artist_name": row["grandparent_name"],
            "name": f"Tracks on {row['parent_name']}",
        }

    return next_pending(conn, key, retry_after_days=retry_after_days,
                        entity_types=("album", "track"),
                        type_overrides=overrides)


def pending_children(conn, service: str, parent_type: str, parent_id: Any, *,
                     child: str) -> list:
    """The children of one parent that this provider has not looked at yet.

    Owned children only -- see `_batch_parent`. Without this the batch path
    enriched a watched artist's whole discography.
    """
    spec = _CHILD[child]
    columns = ", ".join(("e.id", f"e.{spec['title']}", *(
        f"e.{extra}" for extra in spec["extra"])))
    rows = conn.execute(
        f"""
        SELECT {columns}
          FROM {spec["table"]} e
          LEFT JOIN lib2_provider_attempts a
                 ON a.entity_type = :child_type AND a.entity_id = e.id
                AND a.service = :service
         WHERE {spec["parent_join"]} AND a.entity_id IS NULL
           AND {owned_sql(child, "e")}
         ORDER BY e.id
        """,
        {"child_type": child, "service": str(service).strip().lower(),
         "parent": parent_id},
    ).fetchall()
    keys = ("id", "title", *spec["extra"])
    return [dict(zip(keys, row, strict=False)) for row in rows]


def record_children(conn, service: str, parent_type: str, parent_id: Any,
                    status: str, *, child: str) -> int:
    """Record one outcome for every child of a parent that is still unattempted.

    A bulk call that failed is a single outcome for the whole set. Children the
    provider has already settled are left alone — the batch was never about them.
    Returns how many were recorded.
    """
    from core.library2.provider_attempts import record_attempt

    children = pending_children(conn, service, parent_type, parent_id, child=child)
    for entry in children:
        record_attempt(conn, entity_type=child, entity_id=entry["id"],
                       service=service, status=status)
    return len(children)


__all__ = ["ENTITY_ORDER", "next_batch_pending", "next_pending", "pending_children",
           "pending_count", "progress_breakdown", "record_children",
           "status_counts"]
