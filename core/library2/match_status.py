"""Provider-qualified match state for native Library-v2 entities.

P3 makes the Library-v2 row the only catalogue authority.  Provider chips,
manual matches and clears therefore read/write dedicated Spotify/MusicBrainz
columns plus the provider-keyed ``external_ids`` mapping.  Legacy backrefs may
remain during the rollback window, but never participate in match decisions.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from core.enrichment.match_provenance import load_match_provenance
from core.library2.provider_ids import parse_external_ids
from utils.logging_config import get_logger


logger = get_logger("library2.match_status")


SERVICES: List[tuple] = [
    ("spotify", "Spotify", {"artist": "spotify_artist_id", "album": "spotify_album_id", "track": "spotify_track_id"}),
    ("musicbrainz", "MusicBrainz", {"artist": "musicbrainz_id", "album": "musicbrainz_release_id", "track": "musicbrainz_recording_id"}),
    ("deezer", "Deezer", {"artist": "deezer_id", "album": "deezer_id", "track": "deezer_id"}),
    ("itunes", "iTunes", {"artist": "itunes_artist_id", "album": "itunes_album_id", "track": "itunes_track_id"}),
    ("audiodb", "AudioDB", {"artist": "audiodb_id", "album": "audiodb_id", "track": "audiodb_id"}),
    ("discogs", "Discogs", {"artist": "discogs_id", "album": "discogs_id"}),
    ("lastfm", "Last.fm", {"artist": "lastfm_url", "album": "lastfm_url", "track": "lastfm_url"}),
    ("genius", "Genius", {"artist": "genius_id", "track": "genius_id"}),
    ("tidal", "Tidal", {"artist": "tidal_id", "album": "tidal_id", "track": "tidal_id"}),
    ("qobuz", "Qobuz", {"artist": "qobuz_id", "album": "qobuz_id", "track": "qobuz_id"}),
    ("amazon", "Amazon", {"artist": "amazon_id", "album": "amazon_id", "track": "amazon_id"}),
    ("jiosaavn", "JioSaavn", {"artist": "jiosaavn_id", "album": "jiosaavn_id", "track": "jiosaavn_id"}),
    ("bandcamp", "Bandcamp", {"album": "bandcamp_url", "track": "bandcamp_url"}),
]

_TABLES = {
    "artist": "lib2_artists",
    "album": "lib2_albums",
    "track": "lib2_tracks",
}
_NORMALIZE = {
    "artist": "artist", "artists": "artist",
    "album": "album", "albums": "album",
    "track": "track", "tracks": "track",
}


def _canonical(entity_type: str) -> str:
    value = _NORMALIZE.get(str(entity_type))
    if value is None:
        raise ValueError(f"Unknown entity type: {entity_type}")
    return value


def _available(service: str, available_services: Optional[set]) -> bool:
    return available_services is None or service in available_services


def _source_ids(row: Any) -> Dict[str, str]:
    ids = parse_external_ids(row["external_ids"] if "external_ids" in row.keys() else None)
    if "spotify_id" in row.keys() and row["spotify_id"]:
        ids["spotify"] = str(row["spotify_id"])
    if "musicbrainz_id" in row.keys() and row["musicbrainz_id"]:
        ids["musicbrainz"] = str(row["musicbrainz_id"])
    return ids


def _ledger(conn: Any, canonical: str, entity_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """what the workers last heard per (entity, service): third #1062 review
    P2B-3. 3.6.1's chips read ``{service}_match_status`` and showed "not
    found" with a date; v2 had the same answer in the ledger and said
    "pending" forever, which hid that a manual match was needed."""
    out: Dict[int, Dict[str, Any]] = {}
    try:
        for start in range(0, len(entity_ids), 500):
            chunk = entity_ids[start:start + 500]
            marks = ",".join("?" for _ in chunk)
            for r in conn.execute(
                    f"SELECT entity_id, service, status, last_attempted_at "
                    f"FROM lib2_provider_attempts WHERE entity_type=? AND entity_id IN ({marks})",
                    (canonical, *chunk)):
                out.setdefault(int(r[0]), {})[str(r[1])] = (r[2], r[3])
    except Exception as exc:  # noqa: BLE001 - no ledger yet: chips as before
        logger.debug("no attempt ledger for chips: %s", exc)
    return out


# no id, and the ledger says why
_UNMATCHED_STATUS = {"not_found": "not_found", "error": "error", "manual": "cleared"}


def _native_chips(
    conn: Any,
    canonical: str,
    entity_id: int,
    row: Any,
    available_services: Optional[set],
    ledger: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    ids = _source_ids(row)
    if ledger is None:
        ledger = _ledger(conn, canonical, [int(entity_id)]).get(int(entity_id), {})
    origins = load_match_provenance(
        conn, f"lib2_{canonical}", [int(entity_id)]
    ).get(str(entity_id), {})
    chips: List[Dict[str, Any]] = []
    for service, label, supported in SERVICES:
        if canonical not in supported:
            continue
        external_id = ids.get(service)
        provenance = origins.get(service) or {}
        provenance_matches = bool(
            external_id
            and str(provenance.get("external_id") or "") == str(external_id)
        )
        attempt_status, attempted_at = ledger.get(service) or (None, None)
        status = ("matched" if external_id
                  else _UNMATCHED_STATUS.get(str(attempt_status or ""), "pending"))
        chips.append({
            "service": service,
            "label": label,
            "status": status,
            "external_id": external_id,
            "last_attempted": (provenance.get("matched_at") if provenance_matches else None)
                              or attempted_at,
            # Kept only as a response-shape compatibility field. P3 clients
            # always use library_v2_entity_id for mutation.
            "legacy_entity_id": None,
            "library_v2_entity_id": int(entity_id),
            "available": _available(service, available_services),
            "match_origin": provenance.get("origin") if provenance_matches else None,
            "matched_at": provenance.get("matched_at") if provenance_matches else None,
        })
    return chips


def artist_enrichment_coverage(conn: Any, artist_id: int) -> Dict[str, Any]:
    """Share of this artist's tracks that carry a qualified id per provider.

    The legacy artist hero showed this as a ring per service; it answers a
    question the per-entity chips cannot ("the artist is matched to Spotify,
    but are its TRACKS?"). Counting reuses ``_source_ids`` — the same
    detection the chips use — so a row can never count as matched in one
    place and unmatched in the other.
    """
    from core.library2.artist_aliases import resolve_alias_group

    group = resolve_alias_group(conn, int(artist_id)) or [int(artist_id)]
    marks = ",".join("?" for _ in group)
    # two indexed halves, not one OR across two joined tables: the OR made
    # sqlite walk every track in the catalogue, 20s an artist page on a big
    # library. UNION keeps the DISTINCT.
    rows = conn.execute(
        f"""SELECT t.id, t.spotify_id, t.musicbrainz_id, t.external_ids
              FROM lib2_track_artists ta
              JOIN lib2_tracks t ON t.id = ta.track_id
             WHERE ta.artist_id IN ({marks})
            UNION
            SELECT t.id, t.spotify_id, t.musicbrainz_id, t.external_ids
              FROM lib2_albums al
              JOIN lib2_tracks t ON t.album_id = al.id
             WHERE al.primary_artist_id IN ({marks})""",
        tuple(group) * 2,
    ).fetchall()
    total = len(rows)
    coverage: Dict[str, Any] = {"total_tracks": total}
    if not total:
        return coverage
    counts: Dict[str, int] = {}
    for row in rows:
        for service in _source_ids(row):
            counts[service] = counts.get(service, 0) + 1
    for service, matched in counts.items():
        coverage[service] = round(matched / total * 100, 1)
    return coverage


def entity_match_status(
    conn: Any,
    entity_type: str,
    entity_id: int,
    *,
    available_services: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Return provider chips from one authoritative native entity row."""

    canonical = _canonical(entity_type)
    row = conn.execute(
        f"SELECT id, spotify_id, musicbrainz_id, external_ids "
        f"FROM {_TABLES[canonical]} WHERE id=?",
        (int(entity_id),),
    ).fetchone()
    if row is None:
        return []
    return _native_chips(conn, canonical, int(entity_id), row, available_services)


def album_match_bundle(
    conn: Any,
    album_id: int,
    *,
    available_services: Optional[set] = None,
) -> Dict[str, Any]:
    """Album chips plus per-track native chips."""

    result: Dict[str, Any] = {
        "album": entity_match_status(
            conn, "album", album_id, available_services=available_services,
        ),
        "tracks": {},
    }
    rows = conn.execute(
        "SELECT id, spotify_id, musicbrainz_id, external_ids "
        "FROM lib2_tracks WHERE album_id=? ORDER BY id",
        (int(album_id),),
    ).fetchall()
    ledger = _ledger(conn, "track", [int(r["id"]) for r in rows])
    for row in rows:
        track_id = int(row["id"])
        result["tracks"][track_id] = _native_chips(
            conn, "track", track_id, row, available_services,
            ledger.get(track_id, {}),
        )
    return result


def _sync_attempt_ledger(
    conn: Any, canonical: str, entity_id: int, service: str, value: Optional[str],
    *, ask_again: bool = False, detail: str = "manual match",
) -> None:
    """Keep ``lib2_provider_attempts`` in step with a manually set/cleared id.

    ``ask_again`` is for a repair that drops a bad id it stored itself: the
    pair goes back to never attempted so the worker looks it up again.

    Setting an id settles that (entity, service) pair — the user has answered
    the question the worker would ask. Clearing it settles it too: the user
    said the match was wrong, and the worker must not search it back.
    """
    try:
        from core.library2 import provider_attempts

        if not provider_attempts._table_exists(conn, "lib2_provider_attempts"):
            return
        if value:
            provider_attempts.record_attempt(
                conn, entity_type=canonical, entity_id=entity_id,
                service=service, status="matched", detail=detail,
            )
        elif ask_again:
            conn.execute(
                "DELETE FROM lib2_provider_attempts "
                "WHERE entity_type=? AND entity_id=? AND service=?",
                (canonical, entity_id, service),
            )
        else:
            # a clear is the user saying "that match was wrong". deleting the
            # row made the pair "never attempted", the worker searched by name
            # again and stored the same wrong id back. 3.6.1 left a cleared
            # match cleared until the user matched it by hand: a settled
            # "manual" answer does that, and a manual match overwrites it
            provider_attempts.record_attempt(
                conn, entity_type=canonical, entity_id=entity_id,
                service=service, status="manual", detail="cleared by user",
            )
    except ValueError:
        # A provider the ledger does not know (it also covers derived workers
        # that carry no id). The id write above still stands.
        logger.debug("no attempt ledger for provider %s", service)


# Track ids that several catalogue rows may legitimately share, because the
# provider keys them by RECORDING or by content rather than by release: one
# MusicBrainz recording MBID covers the album version and the greatest-hits
# version, and Last.fm/AudioDB/Genius key on artist+title. Enforcing uniqueness
# there would refuse correct matches. Every other provider issues a per-release
# track id, so two rows holding one is always a mistake.
_SHARED_TRACK_ID_SERVICES = frozenset({"musicbrainz", "lastfm", "audiodb", "genius"})


def _identity_must_be_unique(canonical: str, service: str) -> bool:
    """Whether one entity of this kind may be the only holder of this id.

    Artist and album ids name exactly one thing at every provider. Track ids
    do not — see ``_SHARED_TRACK_ID_SERVICES``.
    """
    if canonical == "track":
        return service not in _SHARED_TRACK_ID_SERVICES
    return True


class ProviderIdentityConflict(Exception):
    """Another Library-v2 entity of the same kind already claims this id.

    One provider release is one local entity. When two rows carry the same
    provider id everything keyed on it collapses: the wishlist writes one row
    for what the user sees as two releases, artwork and tracklists are fetched
    for the wrong edition, and the dedupe/twin scans see a false duplicate. A
    production report found 20 album provider-id groups shared across distinct
    Library-v2 albums — original vs. slowed/sped-up editions of the same track,
    and in one case two unrelated albums by two different artists.
    """

    def __init__(self, service: str, external_id: str, owner_id: int, entity_type: str):
        self.service = service
        self.external_id = external_id
        self.owner_id = owner_id
        self.entity_type = entity_type
        super().__init__(
            f"{service} id {external_id!r} already belongs to {entity_type} {owner_id}"
        )


def provider_id_owner(
    conn: Any, entity_type: str, service: str, external_id: str,
) -> Optional[int]:
    """The Library-v2 entity that already claims ``external_id``, if any."""
    canonical = _canonical(entity_type)
    table = _TABLES[canonical]
    service = str(service or "").strip().lower()
    value = str(external_id or "").strip()
    if not value:
        return None
    if service == "spotify":
        predicate, params = "spotify_id = ?", (value,)
    elif service == "musicbrainz":
        predicate, params = "musicbrainz_id = ?", (value,)
    else:
        # a literal path in the indexed form: a bound path can never use the
        # external_ids expression indexes, so it scanned the whole table
        from core.library2.provider_ids import external_id_sql
        try:
            predicate, params = f"{external_id_sql('external_ids', service)} = ?", (value,)
        except ValueError:  # not a provider the indexes know
            predicate = "json_extract(external_ids, '$.' || ?) = ?"
            params = (service, value)
    row = conn.execute(
        f"SELECT id FROM {table} WHERE {predicate} LIMIT 1", params,
    ).fetchone()
    return int(row["id"]) if row else None


def provider_id_conflicts(conn: Any, entity_type: str) -> List[Dict[str, Any]]:
    """Every provider id currently held by MORE than one entity of this kind.

    Read-only. The write-time guard above stops NEW conflicts; databases that
    predate it still carry the old ones, and cleaning those means merging or
    re-matching real catalogue rows — a decision for the user, not for a
    startup migration. This reports them so that decision can be made with the
    actual list in hand.
    """
    canonical = _canonical(entity_type)
    table = _TABLES[canonical]
    conflicts: List[Dict[str, Any]] = []
    services = [
        name for name, _label, entity_types in SERVICES
        if canonical in entity_types and _identity_must_be_unique(canonical, name)
    ]
    for service in services:
        if service == "spotify":
            expression = "spotify_id"
        elif service == "musicbrainz":
            expression = "musicbrainz_id"
        else:
            expression = f"json_extract(external_ids, '$.{service}')"
        try:
            rows = conn.execute(
                f"""SELECT {expression} AS external_id,
                           GROUP_CONCAT(id) AS entity_ids, COUNT(*) AS holders
                      FROM {table}
                     WHERE {expression} IS NOT NULL AND {expression} <> ''
                     GROUP BY {expression}
                    HAVING COUNT(*) > 1""",
            ).fetchall()
        except Exception as exc:  # noqa: BLE001 — reporting must not raise
            logger.debug("conflict scan failed for %s/%s: %s", canonical, service, exc)
            continue
        for row in rows:
            conflicts.append({
                "entity_type": canonical,
                "service": service,
                "external_id": row["external_id"],
                "entity_ids": [int(x) for x in str(row["entity_ids"]).split(",") if x],
                "holders": int(row["holders"]),
            })
    return conflicts


def set_library_v2_match(
    conn: Any,
    entity_type: str,
    entity_id: int,
    service: str,
    external_id: Optional[str],
    *,
    actor: str = "admin",
    steal: bool = False,
    ask_again: bool = False,
    origin: str = "manual",
) -> None:
    """Set or clear one explicitly qualified provider identity.

    ``origin='automatic'``: a background lookup made the match. It is labelled
    as such (not as the user's own choice), and never relabels a manual match
    of the same id.

    ``steal`` decides what happens when another entity already holds the id.
    Automated callers leave it False and get a :class:`ProviderIdentityConflict`
    — a missing id is recoverable, a wrong one silently corrupts everything
    downstream. A deliberate user match passes True, which MOVES the id: the
    previous owner's claim is cleared in the same transaction, so the "one
    provider release, one local entity" invariant holds either way.
    """

    canonical = _canonical(entity_type)
    service = str(service or "").strip().lower()
    supported = {
        name for name, _label, entity_types in SERVICES
        if canonical in entity_types
    }
    if service not in supported:
        raise ValueError(f"Provider {service!r} does not support {canonical}")
    table = _TABLES[canonical]
    row = conn.execute(
        f"SELECT id, external_ids FROM {table} WHERE id=?", (int(entity_id),)
    ).fetchone()
    if row is None:
        raise LookupError(f"Library v2 {canonical} {entity_id} not found")

    ids = parse_external_ids(row["external_ids"])
    previous = str(ids.get(service) or "").strip()
    value = str(external_id).strip() if external_id not in (None, "") else None
    if value and service == "spotify":
        from core.library2.importer import looks_like_foreign_provider_id
        if looks_like_foreign_provider_id(value):
            # a Spotify fallback once handed iTunes ids over as Spotify's (§62.4)
            raise ValueError(f"{value!r} is not a Spotify id")
    if value and _identity_must_be_unique(canonical, service):
        owner = provider_id_owner(conn, canonical, service, value)
        if owner is not None and owner != int(entity_id):
            if not steal:
                raise ProviderIdentityConflict(service, value, owner, canonical)
            # Clear the previous claim first, in this same transaction — an
            # id that exists on two rows for even one statement is a state no
            # reader should ever be able to observe.
            # the old holder didn't say its match was wrong: the id is simply
            # someone else's. it goes back to the queue (not "cleared by
            # user"), and a release pin naming the id goes with it
            set_library_v2_match(
                conn, canonical, owner, service, None, actor=actor, ask_again=True,
            )
            if canonical == "album" and "canonical_source" in {
                    r[1] for r in conn.execute("PRAGMA table_info(lib2_albums)")}:
                from core.library2.editions import pin_album_release
                pin_album_release(conn, owner, service, None)
            logger.info(
                "Moved %s id %s from %s %s to %s %s",
                service, value, canonical, owner, canonical, entity_id,
            )
        ids[service] = value
    else:
        ids.pop(service, None)
    assignments = ["external_ids=?", "updated_at=CURRENT_TIMESTAMP"]
    params: List[Any] = [
        json.dumps(ids, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    ]
    if service == "spotify":
        assignments.append("spotify_id=?")
        params.append(value)
    elif service == "musicbrainz":
        assignments.append("musicbrainz_id=?")
        params.append(value)
    params.append(int(entity_id))
    conn.execute(
        f"UPDATE {table} SET {', '.join(assignments)} WHERE id=?", params,
    )

    # The provider-attempt ledger is what the enrichment queue reads, and it is
    # a separate table from the id we just wrote. Leaving it behind made the two
    # disagree in both directions (L2-004): a manual PUT showed a matched chip
    # with no ledger row, so ``next_pending()`` handed the freshly matched entity
    # straight back to the worker; a manual DELETE cleared the id but left a
    # stale ``matched`` row, so the queue never offered that entity again. Same
    # transaction as the id, so the two can never be half-applied.
    _sync_attempt_ledger(conn, canonical, int(entity_id), service, value, ask_again=ask_again,
                         detail=("native enrichment" if origin == "automatic" else "manual match"))

    # A source's credit list belongs to the release it matched: a rematch or a
    # cleared match takes that list's featured artists away with it (3.6.1
    # did the same). The new match's credits are fetched after the commit
    # (provider_credits.schedule_match_credits). Same transaction as the id.
    if (canonical in ("track", "album") and service in ("spotify", "deezer")
            and (value or "") != previous):
        from core.library2.provider_credits import drop_source_credits
        drop_source_credits(conn, canonical, int(entity_id), service)

    # The default edition names the release the album row stands for, or the
    # chip says one release while completeness and track-number repair read
    # another (feature-parity A01). Same transaction as the id.
    if canonical == "artist" and value and service in ("spotify", "deezer"):
        # credits stored before this artist had the id (A04)
        from core.library2.provider_credits import materialize_artist_credits
        materialize_artist_credits(conn, int(entity_id))
    if canonical == "album":
        try:
            from core.library2.editions import sync_default_edition
            sync_default_edition(conn, int(entity_id))
        except Exception as exc:  # noqa: BLE001 - a schema without editions
            logger.debug("default edition sync skipped for album %s: %s", entity_id, exc)

    provenance_type = f"lib2_{canonical}"
    if value:
        try:
            from core.enrichment.match_provenance import (
                record_automatic_match, record_manual_match,
            )
            (record_automatic_match if origin == "automatic" else record_manual_match)(
                conn,
                entity_type=provenance_type,
                entity_id=entity_id,
                service=service,
                external_id=value,
                actor=actor,
            )
        except Exception as exc:  # provenance is supplemental to the native id
            logger.debug("could not record Library-v2 match provenance: %s", exc)
    else:
        try:
            conn.execute(
                "DELETE FROM metadata_match_provenance "
                "WHERE entity_type=? AND entity_id=? AND service=?",
                (provenance_type, str(entity_id), service),
            )
        except Exception as exc:  # older databases may not have provenance yet
            logger.debug("could not clear Library-v2 match provenance: %s", exc)


__all__ = [
    "SERVICES", "album_match_bundle", "entity_match_status",
    "ProviderIdentityConflict",
    "provider_id_conflicts",
    "provider_id_owner",
    "set_library_v2_match",
]


class WatchlistMismatch(ValueError):
    """The supplied Watchlist row belongs to another artist."""


def apply_manual_match(conn: Any, entity_type: str, entity_id: int, service: str,
                       service_id: Optional[str], *, actor: str,
                       watchlist_row_id: Any = None,
                       profile_id: Optional[int] = None) -> Dict[str, List[str]]:
    """A user's manual match (``service_id`` None clears it), uncommitted.

    steal=True: a deliberate match MOVES an id another entity holds -- the
    automated matcher's mistakes are what someone comes here to correct.

    #1901: a new artist match also moves ``profile_id``'s watchlist entry for
    that artist, found by the artist's ids from before the change. Answers
    which watched artists followed and which were skipped.
    """
    canonical = _canonical(entity_type)
    follow = (canonical == "artist" and watchlist_row_id is None
              and profile_id is not None and bool(service_id))
    followed: Dict[str, List[str]] = {"updated": [], "skipped": []}
    if follow:
        from core.watchlist.library_match import library_artist_ids
        before = library_artist_ids(conn.cursor(), entity_id)
    set_library_v2_match(conn, entity_type, entity_id, service, service_id,
                         actor=actor, steal=True)
    # #758: a manual ALBUM match also pins and locks that release; clearing the
    # match lifts a pin it made.
    from core.metadata.canonical_version import should_pin_manual_canonical
    if should_pin_manual_canonical(canonical, service):
        from core.library2.editions import pin_album_release
        pin_album_release(conn, entity_id, service, service_id)
    if follow and before:
        # the watchlist follows, but never fails the match itself (upstream)
        from core.watchlist.library_match import follow_library_artist_match
        try:
            followed = follow_library_artist_match(
                conn.cursor(), before=before, service=service, new_id=str(service_id),
                profile_id=int(profile_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Watchlist didn't follow the match for artist %s: %s", entity_id, exc)
    if watchlist_row_id is None:
        return followed
    # Artist settings reuse the Watchlist: keep an identity-checked row in sync.
    if canonical != "artist":
        raise ValueError("Watchlist sync is only valid for artists")
    from core.library2.artist_settings import WATCHLIST_PROVIDER_FIELDS
    column = WATCHLIST_PROVIDER_FIELDS.get(service)
    if not column:
        raise ValueError(f"Watchlist does not support provider {service!r}")
    artist = conn.execute("SELECT name FROM lib2_artists WHERE id=?", (entity_id,)).fetchone()
    watchlist = conn.execute("SELECT id, artist_name FROM watchlist_artists WHERE id=?",
                             (int(watchlist_row_id),)).fetchone()
    if not artist or not watchlist or str(artist["name"] or "").casefold() \
            != str(watchlist["artist_name"] or "").casefold():
        raise WatchlistMismatch("Watchlist artist does not match this Library v2 artist")
    if column not in {row[1] for row in conn.execute("PRAGMA table_info(watchlist_artists)")}:
        raise ValueError(f"Watchlist column {column!r} is unavailable")
    conn.execute(f"UPDATE watchlist_artists SET {column}=? WHERE id=?",
                 (service_id, int(watchlist_row_id)))
    return followed


_configured_services_getter = None


def set_configured_services_getter(getter) -> None:
    """The app knows which providers are configured; jobs ask here instead of
    importing web_server (which starts the whole app in a job's process)."""
    global _configured_services_getter
    _configured_services_getter = getter


def configured_services() -> Optional[set]:
    """Configured provider ids, or None when nothing registered an answer."""
    return set(_configured_services_getter()) if _configured_services_getter else None
