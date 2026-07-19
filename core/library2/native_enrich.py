"""Resolve + enrich native Library-v2 entities.

Artists born inside lib2 — featured credits (``_featured_names_for_import``),
wishlist rows, discography discoveries — carry ``legacy_artist_id = NULL``. The
whole metadata/enrichment machine is legacy-row-based (``web_server.
_run_single_enrichment`` writes the legacy ``artists`` row, then
``core.library2.enrich.resync_entity_from_legacy`` mirrors it back), so a native
artist can never be reached by it: the Enrich endpoint rejects it and a manual
match records an id but pulls no artwork. Result: every provider chip is stuck
``pending`` and no cover art loads.

This module gives native artists the missing path. It resolves the provider
identity *by name* through SoulSync's existing source-priority search
(``core.metadata.album_tracks``), then writes the resolved id + artwork/genres
STRAIGHT onto the lib2 row — no legacy row required. Once the id is stored the
match-status chips flip to ``matched`` (``match_status`` synthesizes them from
the row's own ``spotify_id``/``external_ids``) and the artist becomes eligible
for the normal discography/artwork pipeline.

P3 always writes the Library-v2 row. Legacy back-references may still exist
during the rollback window, but they are not an enrichment authority.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.native_enrich")

# resolver(name) -> {"source", "artist_id", "name", "image_url"?, "genres"?} | None
ArtistResolver = Callable[[str], Optional[Dict[str, Any]]]


def _normalize_genres(raw: Any) -> Optional[str]:
    """Coerce a genre list/string into lib2's JSON-array storage, or None."""
    if not raw:
        return None
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
    else:
        parts = [str(g).strip() for g in raw if str(g).strip()]
    return json.dumps(parts) if parts else None


def _persist_identity(
    conn,
    artist_id: int,
    *,
    source: str,
    provider_id: str,
    image_url: Optional[str],
    genres: Optional[str],
    existing_external_ids: Any,
) -> None:
    """Write the resolved provider id (namespace-correct) + artwork onto the row.

    Spotify/MusicBrainz ids live in their dedicated columns (the chip synth and
    the rest of lib2 read them there); every other provider id is merged into
    ``external_ids`` without disturbing ids other providers already left.
    """
    assignments = ["updated_at=CURRENT_TIMESTAMP"]
    params: List[Any] = []

    if source == "spotify":
        assignments.append("spotify_id=?")
        params.append(provider_id)
    elif source == "musicbrainz":
        assignments.append("musicbrainz_id=?")
        params.append(provider_id)
    else:
        try:
            ids = json.loads(existing_external_ids or "{}")
        except (TypeError, ValueError):
            ids = {}
        if not isinstance(ids, dict):
            ids = {}
        ids[source] = provider_id
        assignments.append("external_ids=?")
        params.append(json.dumps(ids, sort_keys=True, separators=(",", ":")))

    if image_url:
        assignments.append("image_url=?")
        params.append(str(image_url))
    if genres:
        assignments.append("genres=?")
        params.append(genres)

    params.append(int(artist_id))
    conn.execute(
        f"UPDATE lib2_artists SET {', '.join(assignments)} WHERE id=?", params
    )


def resolve_and_enrich_native_artist(
    conn,
    artist_id: int,
    *,
    resolver: Optional[ArtistResolver] = None,
) -> Dict[str, Any]:
    """Resolve one native artist by name and persist id + artwork onto its row.

    Returns ``{"success": True, "source", "provider_id", "image_url"}`` on a
    match, or ``{"success": False, "attempted": True, "reason": "not_found"}``
    when no provider had the artist as a single entity (the expected outcome for
    genuine collaboration names like "Ian Asher & Galantis").
    """
    if resolver is None:
        resolver = default_artist_resolver

    row = conn.execute(
        "SELECT id, name, spotify_id, musicbrainz_id, external_ids "
        "FROM lib2_artists WHERE id=?",
        (int(artist_id),),
    ).fetchone()
    if row is None:
        raise LookupError(f"Library v2 artist {artist_id} not found")
    name = str(row["name"] or "").strip()
    identity = resolver(name) if name else None
    source = str((identity or {}).get("source") or "").strip().lower()
    provider_id = str((identity or {}).get("artist_id") or "").strip()
    if not identity or not source or not provider_id:
        return {
            "success": False, "attempted": True, "artist_id": int(artist_id),
            "reason": "not_found",
        }

    _persist_identity(
        conn, int(artist_id),
        source=source,
        provider_id=provider_id,
        image_url=identity.get("image_url"),
        genres=_normalize_genres(identity.get("genres")),
        existing_external_ids=row["external_ids"],
    )
    return {
        "success": True, "artist_id": int(artist_id), "source": source,
        "provider_id": provider_id, "image_url": identity.get("image_url"),
    }


ArtworkFetcher = Callable[[str, Dict[str, str]], Optional[str]]


def _stored_source_ids(row: Any) -> Dict[str, str]:
    ids: Dict[str, str] = {}
    if row["spotify_id"]:
        ids["spotify"] = str(row["spotify_id"])
    if row["musicbrainz_id"]:
        ids["musicbrainz"] = str(row["musicbrainz_id"])
    try:
        extra = json.loads(row["external_ids"] or "{}")
        if isinstance(extra, dict):
            for source, value in extra.items():
                if source and value:
                    ids[str(source)] = str(value)
    except (TypeError, ValueError):
        pass
    return ids


def enrich_native_artist_artwork(
    conn,
    artist_id: int,
    *,
    artwork_fetcher: Optional[ArtworkFetcher] = None,
) -> bool:
    """Pull artwork for a native artist that already has provider id(s).

    Setting an id (e.g. via a manual match) flips the chip but does not fetch a
    cover — this closes that gap: it reads the row's stored provider ids and
    asks the artwork engine for an image, writing it onto the row. No-op (returns
    False) when the row has no provider id or the engine finds nothing.
    """
    row = conn.execute(
        "SELECT id, name, image_url, spotify_id, musicbrainz_id, external_ids "
        "FROM lib2_artists WHERE id=?",
        (int(artist_id),),
    ).fetchone()
    if row is None:
        return False
    source_ids = _stored_source_ids(row)
    if not source_ids:
        return False
    fetch = artwork_fetcher or default_artwork_fetcher
    url = fetch(str(row["name"] or ""), source_ids)
    if not url:
        return False
    conn.execute(
        "UPDATE lib2_artists SET image_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (str(url), int(artist_id)),
    )
    return True


def default_artwork_fetcher(name: str, source_ids: Dict[str, str]) -> Optional[str]:
    """Resolve an artist image URL from stored provider ids (production adapter)."""
    from core.library2.provider_adapters import fetch_artwork_url

    result = fetch_artwork_url("artist", artist_name=name, source_ids=source_ids)
    return result.url if result is not None else None


def enrich_native_entity_for_service(
    conn: Any,
    entity_type: str,
    entity_id: int,
    service: str,
    *,
    searcher: Optional[Callable[[str, str, str], List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """Refresh one entity from one requested provider, without a legacy row.

    Search results may explicitly report a different provider when a client
    fell back. That provider namespace is persisted and returned; a Deezer or
    iTunes ID can therefore never enter the Spotify slot merely because the
    Spotify facade initiated the request.
    """

    from difflib import SequenceMatcher
    import re

    canonical = {
        "artist": "artist", "artists": "artist",
        "album": "album", "albums": "album",
        "track": "track", "tracks": "track",
    }.get(str(entity_type))
    if canonical is None:
        raise ValueError(f"Unsupported entity type: {entity_type}")
    service = str(service or "").strip().lower()

    if canonical == "artist":
        row = conn.execute(
            "SELECT id, name, spotify_id, musicbrainz_id, external_ids "
            "FROM lib2_artists WHERE id=?", (int(entity_id),),
        ).fetchone()
        artist_name = str(row["name"] or "") if row else ""
        album_title = None
    elif canonical == "album":
        row = conn.execute(
            """SELECT al.id, al.title AS name, al.spotify_id,
                      al.musicbrainz_id, al.external_ids, ar.name AS artist_name
                 FROM lib2_albums al
                 LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                WHERE al.id=?""",
            (int(entity_id),),
        ).fetchone()
        artist_name = str(row["artist_name"] or "") if row else ""
        album_title = str(row["name"] or "") if row else ""
    else:
        row = conn.execute(
            """SELECT t.id, t.title AS name, t.spotify_id,
                      t.musicbrainz_id, t.external_ids, ar.name AS artist_name,
                      al.title AS album_title, t.album_id
                 FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id
                 LEFT JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                WHERE t.id=?""",
            (int(entity_id),),
        ).fetchone()
        artist_name = str(row["artist_name"] or "") if row else ""
        album_title = str(row["album_title"] or "") if row else ""
    if row is None:
        raise LookupError(f"Library v2 {canonical} {entity_id} not found")

    source_ids = _stored_source_ids(row)
    provider_id = source_ids.get(service)
    actual_source = service
    hit: Dict[str, Any] = {}
    if not provider_id:
        if searcher is None:
            from core.library.service_search import _search_service
            searcher = _search_service
        query = str(row["name"] or "")
        if canonical != "artist" and artist_name:
            query = f"{artist_name} - {query}"
        candidates = searcher(service, canonical, query) or []

        # Artists use the same dedicated, project-wide name gate every other
        # worker match uses (core.worker_utils.artist_name_matches, threshold
        # 0.85 chosen specifically to reject "Blance/Blanke"-style near
        # misses) instead of a locally re-derived, looser 0.72 threshold —
        # review A12. Its normalizer also keeps CJK characters (Python's
        # \w is Unicode-aware) where the old ASCII-only [^a-z0-9]+ filter
        # collapsed any CJK name to '', making SequenceMatcher('', '').ratio()
        # == 1.0 always accept the first candidate.
        if canonical == "artist":
            from core.worker_utils import ARTIST_NAME_MATCH_THRESHOLD, normalize_artist_name
            normalize_fn = normalize_artist_name
            threshold = ARTIST_NAME_MATCH_THRESHOLD
        else:
            def normalize_fn(value: Any) -> str:
                return re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
            threshold = 0.72

        wanted = normalize_fn(row["name"])
        ranked = []
        for candidate in candidates:
            if not isinstance(candidate, dict) or not candidate.get("id"):
                continue
            candidate_name = normalize_fn(candidate.get("name"))
            if not wanted or not candidate_name:
                continue
            score = SequenceMatcher(None, wanted, candidate_name).ratio()
            if score >= threshold:
                ranked.append((score, candidate))
        if not ranked:
            return {
                "success": False, "attempted": True,
                "entity_type": canonical, "entity_id": int(entity_id),
                "reason": "not_found", "source": service,
            }
        ranked.sort(key=lambda item: item[0], reverse=True)
        hit = ranked[0][1]
        provider_id = str(hit["id"]).strip()
        actual_source = str(hit.get("provider") or service).strip().lower()

    from core.library2.match_status import set_library_v2_match
    set_library_v2_match(
        conn, canonical, int(entity_id), actual_source, provider_id,
        actor="native_enrichment",
    )

    image_url = str(hit.get("image") or "").strip() or None
    if canonical == "artist":
        if not image_url:
            image_url = default_artwork_fetcher(
                artist_name, {actual_source: provider_id},
            )
        if image_url:
            conn.execute(
                "UPDATE lib2_artists SET image_url=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (image_url, int(entity_id)),
            )
    elif canonical == "album":
        if not image_url:
            from core.library2.provider_adapters import fetch_artwork_url
            artwork = fetch_artwork_url(
                "album",
                artist_name=artist_name,
                album_title=album_title,
                source_ids={actual_source: provider_id},
                source_order=(actual_source,),
            )
            image_url = artwork.url if artwork else None
        if image_url:
            conn.execute(
                "UPDATE lib2_albums SET image_url=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (image_url, int(entity_id)),
            )
    else:
        from core.library2.provider_adapters import fetch_track_metadata
        metadata = fetch_track_metadata(
            {actual_source: provider_id}, source_order=(actual_source,),
        )
        if metadata and metadata.duration_ms is not None:
            conn.execute(
                "UPDATE lib2_tracks SET duration=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (metadata.duration_ms, int(entity_id)),
            )
        image_url = metadata.image_url if metadata else image_url
        if image_url and row["album_id"]:
            conn.execute(
                "UPDATE lib2_albums SET image_url=COALESCE(image_url, ?), "
                "updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (image_url, int(row["album_id"])),
            )

    return {
        "success": True,
        "entity_type": canonical,
        "entity_id": int(entity_id),
        "requested_source": service,
        "source": actual_source,
        "provider_id": provider_id,
        "image_url": image_url,
    }


def _get_or_create_component_artist(
    conn, name: str, identity: Dict[str, Any], *, monitored: int = 0
) -> int:
    """Resolve a split component to a lib2 artist id, creating + enriching it.

    Reuses an existing row (matched case-insensitively by name) so a split never
    duplicates an artist the library already has. A brand-new component is
    created native and enriched from the resolved identity; an existing *native,
    still-unmapped* row is enriched too. A legacy-backed match is left untouched
    (it keeps the authoritative legacy path).
    """
    existing = conn.execute(
        "SELECT id, legacy_artist_id, spotify_id, musicbrainz_id, external_ids "
        "FROM lib2_artists WHERE name=? COLLATE NOCASE ORDER BY id LIMIT 1",
        (name,),
    ).fetchone()
    if existing is not None:
        cid = int(existing["id"])
        unmapped = (
            existing["legacy_artist_id"] is None
            and not existing["spotify_id"]
            and not existing["musicbrainz_id"]
            and (existing["external_ids"] or "{}") in ("", "{}")
        )
        if unmapped:
            _persist_identity(
                conn, cid,
                source=str(identity["source"]).strip().lower(),
                provider_id=str(identity["artist_id"]).strip(),
                image_url=identity.get("image_url"),
                genres=_normalize_genres(identity.get("genres")),
                existing_external_ids=existing["external_ids"],
            )
        return cid

    cur = conn.execute(
        "INSERT INTO lib2_artists(name, sort_name, monitored) VALUES(?, ?, ?)",
        (name, name, monitored),
    )
    cid = int(cur.lastrowid)
    _persist_identity(
        conn, cid,
        source=str(identity["source"]).strip().lower(),
        provider_id=str(identity["artist_id"]).strip(),
        image_url=identity.get("image_url"),
        genres=_normalize_genres(identity.get("genres")),
        existing_external_ids="{}",
    )
    return cid


def _rehome_and_delete_combined(
    conn, combined_id: int, primary_id: int, component_ids: List[int]
) -> None:
    """Move every reference off the ghost combined artist, then delete/alias it.

    ORDER IS SAFETY-CRITICAL: ``lib2_albums.primary_artist_id`` is
    ``ON DELETE CASCADE``, so the ghost's primary albums (and their tracks/files)
    would be destroyed if it were deleted while still their primary. We reassign
    primaries and rewrite junctions FIRST; the final action then finds nothing
    depending on the ghost.
    """
    # 1) Reassign albums where the ghost is primary → the first component.
    primary_album_ids = [
        int(r["id"]) for r in conn.execute(
            "SELECT id FROM lib2_albums WHERE primary_artist_id=?", (combined_id,)
        )
    ]
    for album_id in primary_album_ids:
        conn.execute(
            "UPDATE lib2_albums SET primary_artist_id=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=?",
            (primary_id, album_id),
        )

    # 2) Credit every component on every album the ghost was on (incl. reassigned).
    album_ids = set(primary_album_ids)
    for r in conn.execute(
        "SELECT album_id FROM lib2_album_artists WHERE artist_id=?", (combined_id,)
    ):
        album_ids.add(int(r["album_id"]))
    for album_id in album_ids:
        for cid in component_ids:
            role = "primary" if cid == primary_id else "featured"
            conn.execute(
                "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
                "VALUES(?, ?, ?)",
                (album_id, cid, role),
            )
    conn.execute("DELETE FROM lib2_album_artists WHERE artist_id=?", (combined_id,))

    # 3) Same for track credits — preserve the ghost's role, fan out to components.
    track_rows = conn.execute(
        "SELECT track_id, role, position FROM lib2_track_artists WHERE artist_id=?",
        (combined_id,),
    ).fetchall()
    for tr in track_rows:
        base_pos = int(tr["position"] or 0)
        for offset, cid in enumerate(component_ids):
            conn.execute(
                "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position) "
                "VALUES(?, ?, ?, ?)",
                (int(tr["track_id"]), cid, tr["role"], base_pos + offset),
            )
    conn.execute("DELETE FROM lib2_track_artists WHERE artist_id=?", (combined_id,))

    # 4) Drop the ghost's monitor rules so they don't linger.
    try:
        conn.execute(
            "DELETE FROM lib2_monitor_rules WHERE entity_type='artist' AND entity_id=?",
            (combined_id,),
        )
    except Exception as exc:  # noqa: BLE001 — table optional/absent on minimal DBs
        logger.debug("ghost monitor-rule cleanup skipped (%s): %s", combined_id, exc)

    # 5) The ghost is now unreferenced by any primary or junction.
    # If it is legacy-backed, keep it as an alias so future legacy imports
    # don't recreate it; otherwise delete it.
    row = conn.execute(
        "SELECT legacy_artist_id FROM lib2_artists WHERE id=?", (combined_id,)
    ).fetchone()
    is_legacy = row and row["legacy_artist_id"] is not None

    if is_legacy:
        conn.execute(
            "UPDATE lib2_artists SET canonical_artist_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (primary_id, combined_id),
        )
    else:
        conn.execute("DELETE FROM lib2_artists WHERE id=?", (combined_id,))


def smart_split_combined_artist(
    conn,
    artist_id: int,
    *,
    resolver: Optional[ArtistResolver] = None,
) -> Optional[Dict[str, Any]]:
    """Split a combined-name artist ("A & B") into its real components.

    Runs only as a fallback for an artist that no provider recognizes as a
    single entity (the caller tries a single-entity resolve first). It splits the
    display name and requires **every** component to resolve to a real provider
    artist — a strong guard: a genuine band name like "Hall & Oates" is
    recognized as one entity upstream and never reaches here, so this only ever
    fires on true concatenations. When all components resolve, each becomes a
    real (enriched) artist, the release re-homes to the first component with the
    rest credited, and the ghost combined row is deleted/aliased (see
    ``_rehome_and_delete_combined`` for the cascade-safe ordering).

    Returns a summary dict on a split, or ``None`` when it declines (not a
    combined name or a component didn't resolve).
    """
    if resolver is None:
        resolver = default_artist_resolver

    row = conn.execute(
        "SELECT id, name, legacy_artist_id, monitored FROM lib2_artists WHERE id=?",
        (int(artist_id),),
    ).fetchone()
    if row is None:
        return None

    from core.library2.importer import split_artist_credits

    components = split_artist_credits(row["name"])
    if len(components) < 2:
        return None

    identities: List[tuple] = []
    for component in components:
        identity = resolver(component)
        if not identity or not str(identity.get("artist_id") or "").strip():
            return None  # not confident — leave the combined row intact
        identities.append((component, identity))

    ghost_monitored = int(row["monitored"] if row["monitored"] is not None else 1)

    component_ids: List[int] = []
    for component, identity in identities:
        cid = _get_or_create_component_artist(
            conn, component, identity, monitored=ghost_monitored
        )
        if cid not in component_ids:
            component_ids.append(cid)
    if len(component_ids) < 2 or int(artist_id) in component_ids:
        return None

    primary_id = component_ids[0]
    _rehome_and_delete_combined(conn, int(artist_id), primary_id, component_ids)
    return {
        "combined_id": int(artist_id),
        "primary_id": primary_id,
        "component_ids": component_ids,
    }


def _pending_unmapped_artists(conn, limit: Optional[int]) -> List[Dict[str, Any]]:
    """Artists (both native and legacy) that still carry no catalog provider id."""
    sql = (
        "SELECT id, legacy_artist_id, external_ids FROM lib2_artists "
        "WHERE (spotify_id IS NULL OR spotify_id='') "
        "  AND (musicbrainz_id IS NULL OR musicbrainz_id='') "
        "ORDER BY id"
    )
    rows = conn.execute(sql).fetchall()

    catalog_providers = {
        "spotify",
        "musicbrainz",
        "deezer",
        "itunes",
        "tidal",
        "qobuz",
        "amazon",
        "jiosaavn",
        "bandcamp",
    }

    pending = []
    for r in rows:
        row_dict = dict(r)
        ext_ids = {}
        if row_dict.get("external_ids"):
            try:
                ext_ids = json.loads(row_dict["external_ids"])
            except (json.JSONDecodeError, TypeError):
                ext_ids = {}

        # If they are matched on any catalog provider, they are not pending.
        has_catalog_id = any(p in ext_ids for p in catalog_providers)
        if not has_catalog_id:
            pending.append(row_dict)

    if limit is not None:
        pending = pending[:limit]
    return pending


def reconcile_unmapped_native_artists(
    conn,
    *,
    resolver: Optional[ArtistResolver] = None,
    limit: Optional[int] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> Dict[str, Any]:
    """Resolve every still-unmapped artist by name (the backlog healer).

    This is the on-demand maintenance pass behind the "Reconcile unmapped
    artists" action: it walks artists with no provider id and tries to
    resolve+enrich (for native) or split them. Collaboration names that no provider
    models as one entity simply stay unmatched — counted, never fabricated.
    """
    if resolver is None:
        resolver = default_artist_resolver

    artists = _pending_unmapped_artists(conn, limit)
    total = len(artists)
    stats = {"scanned": 0, "matched": 0, "split": 0, "unmatched": 0, "errors": 0}
    for index, art in enumerate(artists):
        artist_id = art["id"]
        try:
            stats["scanned"] += 1
            result = resolve_and_enrich_native_artist(conn, artist_id, resolver=resolver)
            if result.get("success"):
                stats["matched"] += 1
                if progress is not None:
                    progress(index + 1, total)
                else:
                    conn.commit()
                continue

            # Single-entity match failed: try a conservative collaboration split.
            if smart_split_combined_artist(conn, artist_id, resolver=resolver):
                stats["split"] += 1
            else:
                stats["unmatched"] += 1

            if progress is not None:
                progress(index + 1, total)
            else:
                conn.commit()
        except Exception as exc:  # noqa: BLE001 — one bad row must not abort the pass
            stats["errors"] += 1
            logger.debug("reconcile failed for artist %s: %s", artist_id, exc)
            try:
                conn.rollback()
            except Exception as rollback_err:
                logger.debug("reconcile rollback failed: %s", rollback_err)
            if progress is not None:
                try:
                    progress(index + 1, total)
                except Exception as progress_exc:
                    logger.debug("reconcile progress callback failed: %s", progress_exc)
    return stats


def default_artist_resolver(name: str) -> Optional[Dict[str, Any]]:
    """Resolve an artist name to a provider identity via source-priority search.

    Thin production adapter over ``core.metadata.album_tracks`` — imported lazily
    so tests (which inject a fake resolver) never pull the metadata stack.
    """
    from core.metadata.album_tracks import resolve_artist_identity

    return resolve_artist_identity(name)


__all__ = [
    "enrich_native_entity_for_service",
    "resolve_and_enrich_native_artist",
    "reconcile_unmapped_native_artists",
    "smart_split_combined_artist",
    "enrich_native_artist_artwork",
    "default_artist_resolver",
    "default_artwork_fetcher",
]
