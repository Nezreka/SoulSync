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


def _native_chips(
    conn: Any,
    canonical: str,
    entity_id: int,
    row: Any,
    available_services: Optional[set],
) -> List[Dict[str, Any]]:
    ids = _source_ids(row)
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
        chips.append({
            "service": service,
            "label": label,
            "status": "matched" if external_id else "pending",
            "external_id": external_id,
            "last_attempted": provenance.get("matched_at") if provenance_matches else None,
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
    rows = conn.execute(
        f"""SELECT DISTINCT t.id, t.spotify_id, t.musicbrainz_id, t.external_ids
              FROM lib2_tracks t
              LEFT JOIN lib2_track_artists ta ON ta.track_id = t.id
              LEFT JOIN lib2_albums al ON al.id = t.album_id
             WHERE ta.artist_id IN ({marks}) OR al.primary_artist_id IN ({marks})""",
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
    for row in rows:
        track_id = int(row["id"])
        result["tracks"][track_id] = _native_chips(
            conn, "track", track_id, row, available_services,
        )
    return result


def _sync_attempt_ledger(
    conn: Any, canonical: str, entity_id: int, service: str, value: Optional[str],
) -> None:
    """Keep ``lib2_provider_attempts`` in step with a manually set/cleared id.

    Setting an id settles that (entity, service) pair — the user has answered
    the question the worker would ask. Clearing it makes the pair unanswered
    again, which is a *deleted* row rather than a failure status: the queue's
    "never attempted" half is what should pick it up next, ahead of the retry
    backlog, and no retry window should have to expire first.
    """
    try:
        from core.library2 import provider_attempts

        if not provider_attempts._table_exists(conn, "lib2_provider_attempts"):
            return
        if value:
            provider_attempts.record_attempt(
                conn, entity_type=canonical, entity_id=entity_id,
                service=service, status="matched", detail="manual match",
            )
        else:
            conn.execute(
                "DELETE FROM lib2_provider_attempts "
                "WHERE entity_type=? AND entity_id=? AND service=?",
                (canonical, entity_id, service),
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
) -> None:
    """Set or clear one explicitly qualified provider identity.

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
    value = str(external_id).strip() if external_id not in (None, "") else None
    if value and _identity_must_be_unique(canonical, service):
        owner = provider_id_owner(conn, canonical, service, value)
        if owner is not None and owner != int(entity_id):
            if not steal:
                raise ProviderIdentityConflict(service, value, owner, canonical)
            # Clear the previous claim first, in this same transaction — an
            # id that exists on two rows for even one statement is a state no
            # reader should ever be able to observe.
            set_library_v2_match(
                conn, canonical, owner, service, None, actor=actor,
            )
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
    _sync_attempt_ledger(conn, canonical, int(entity_id), service, value)

    provenance_type = f"lib2_{canonical}"
    if value:
        try:
            from core.enrichment.match_provenance import record_manual_match
            record_manual_match(
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
