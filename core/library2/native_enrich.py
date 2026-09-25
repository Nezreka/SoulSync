"""Resolve + enrich native Library-v2 entities.

Artists born inside lib2 — featured credits (``_featured_names_for_import``),
wishlist rows and discography discoveries — may have no legacy back-reference.
Provider enrichment therefore resolves and updates their native catalogue row
directly.

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
import re
from typing import Any, Callable, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.native_enrich")

# resolver(name) -> {"source", "artist_id", "name", "image_url"?, "genres"?} | None
ArtistResolver = Callable[[str], Optional[Dict[str, Any]]]
# anchor_resolver(source, kind ['album'|'track'], provider_id) -> identity dict | None
AnchorResolver = Callable[[str, str, str], Optional[Dict[str, Any]]]
ENRICH_AMBIGUITY_MARGIN = 0.08


def _context_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or "").strip()
    if isinstance(value, (list, tuple)):
        return ", ".join(filter(None, (_context_text(item) for item in value)))
    return str(value or "").strip()


def _candidate_context(candidate: Dict[str, Any], entity_type: str) -> tuple[str, str]:
    artist = _context_text(
        candidate.get("artist_name")
        or candidate.get("artist")
        or candidate.get("artists")
    )
    album = _context_text(
        candidate.get("album_title")
        or candidate.get("album_name")
        or candidate.get("album")
    )
    parts = [part.strip() for part in str(candidate.get("extra") or "").split("·")]
    parts = [part for part in parts if part]
    if not artist and parts:
        artist = parts[0]
    if entity_type == "track" and not album and len(parts) > 1:
        possible_album = parts[1]
        if not re.match(
            r"^(?:score|listeners?|fans?|popularity|\d{4}(?:-\d{2})?)\b",
            possible_album,
            re.IGNORECASE,
        ):
            album = possible_album
    return artist, album


def _artist_context_matches(wanted: str, candidate: str) -> bool:
    from core.worker_utils import artist_name_matches

    if artist_name_matches(wanted, candidate):
        return True
    components = re.split(
        r"\s*(?:,|;|&|/|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b)\s*",
        candidate,
        flags=re.IGNORECASE,
    )
    return any(artist_name_matches(wanted, component) for component in components)


# Words that mark a genuinely DIFFERENT recording, not another edition of the
# same one. `normalize_artist_name` deletes parentheticals and " - ..." tails
# before comparing, which is what lets a fuzzy title match work at all — but it
# also erases exactly these words, so "Memory Reboot (Slowed)" normalized to
# "memory reboot" and scored 1.00 against the ORIGINAL "Memory Reboot". A
# production report found 20 album provider-id groups shared between originals
# and their slowed/sped-up editions for precisely this reason.
#
# Edition words (remastered, deluxe, expanded, anniversary, mono …) are
# deliberately NOT here: those describe the same recordings, folding them is
# what makes provider matching useful, and treating them as variants would cost
# real coverage.
_RECORDING_VARIANT_WORDS = frozenset({
    "slowed", "sped", "speed", "spedup", "reverb", "nightcore", "daycore",
    "remix", "remixed", "rmx", "bootleg", "mashup", "flip", "vip", "edit",
    "instrumental", "instrumentals", "acapella", "acappella", "acoustic",
    "unplugged", "live", "demo", "karaoke", "cover", "rerecorded",
    "reimagined", "orchestral", "piano", "8d", "reverbed",
})

_SEQUENCE_SUFFIX_RE = re.compile(r"(\d+)\s*$")


def _plain_title_tokens(title: Any) -> list:
    """Every word in a title, with punctuation and case removed and NOTHING
    dropped — the counterpart to ``normalize_artist_name``'s lossy fold."""
    text = re.sub(r"[^\w\s]", " ", str(title or "").lower())
    return [token for token in text.split() if token]


def _title_variant_signature(title: Any) -> tuple:
    """What distinguishes this title from its fuzzy-matched base form.

    Returns ``(variant words, trailing sequence number)``. Two titles may only
    be treated as the same release when their signatures agree:

    * ``"Memory Reboot (Slowed)"`` -> ``({"slowed"}, None)`` vs
      ``"Memory Reboot"`` -> ``(frozenset(), None)``  → different.
    * ``"NEON BLADE 2"`` -> ``(frozenset(), "2")`` vs
      ``"NEON BLADE"`` -> ``(frozenset(), None)``     → different.
    * ``"Abbey Road (Remastered)"`` and ``"Abbey Road"`` both ->
      ``(frozenset(), None)``                          → same (edition, not variant).
    """
    full = _plain_title_tokens(title)
    # Scanned over the FULL token list, not only over what the fuzzy fold
    # discards: `normalize_artist_name` strips `(...)` but not `[...]`, so
    # "GigaChad Theme (Phonk House Version) [Slowed]" keeps its "slowed" and
    # would otherwise look identical to the non-slowed release.
    variants = frozenset(token for token in full if token in _RECORDING_VARIANT_WORDS)
    match = _SEQUENCE_SUFFIX_RE.search(" ".join(full))
    return variants, (match.group(1) if match else None)


def titles_are_same_release(wanted: Any, candidate: Any) -> bool:
    """False when two titles differ by a recording variant or a sequence number.

    Applied on top of the existing fuzzy score, never instead of it: the score
    still decides whether the titles are *close*, this decides whether the
    thing that makes them differ is allowed to be folded away.
    """
    return _title_variant_signature(wanted) == _title_variant_signature(candidate)


def _title_context_matches(wanted: str, candidate: str) -> bool:
    from difflib import SequenceMatcher
    from core.worker_utils import normalize_artist_name

    left = normalize_artist_name(wanted)
    right = normalize_artist_name(candidate)
    return bool(left and right) and SequenceMatcher(None, left, right).ratio() >= 0.85


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
        assignments.append(
            "image_url=CASE WHEN COALESCE(art_locked,0)=1 THEN image_url ELSE ? END"
        )
        params.append(str(image_url))
    if genres:
        assignments.append("genres=?")
        params.append(genres)

    params.append(int(artist_id))
    conn.execute(
        f"UPDATE lib2_artists SET {', '.join(assignments)} WHERE id=?", params
    )


def identity_is_free(conn, artist_id: int, source: str, provider_id: str) -> bool:
    """Is ``provider_id`` unclaimed by any artist outside this alias group?

    Two catalogue rows are two artists. Whatever a resolver claims, one
    provider identity may not end up on both — that fan-out is what put one
    artist's photo, genres and whole discography on a dozen unrelated pages.
    Alias members legitimately share their canonical row's identity (§40), so
    the group is resolved before the test.
    """
    namespace = str(source or "").strip().lower()
    identifier = str(provider_id or "").strip()
    if not namespace or not identifier:
        return False
    from core.library2.artist_aliases import resolve_alias_group
    group = resolve_alias_group(conn, int(artist_id)) or [int(artist_id)]
    group = set(group) | {int(artist_id)}
    if namespace == "spotify":
        where = "spotify_id = ?"
    elif namespace == "musicbrainz":
        where = "musicbrainz_id = ?"
    else:
        where = f"TRIM(CAST(json_extract(external_ids, '$.{namespace}') AS TEXT)) = ?"
    placeholders = ",".join("?" for _ in group)
    row = conn.execute(
        f"SELECT 1 FROM lib2_artists WHERE {where} "
        f"  AND id NOT IN ({placeholders}) LIMIT 1",
        (identifier, *sorted(group)),
    ).fetchone()
    return row is None


def _artist_catalog_anchors(conn, artist_id: int) -> Dict[str, tuple]:
    """``source -> ('album'|'track', provider_id)`` for every strong provider
    id already confirmed on a catalog row this artist is the PRIMARY of.

    These ids came from a real album/track match, not a name guess, so they
    are strong anchors (Guide §2.5, issues.md §16 Finding 1). Album anchors
    win over track anchors for the same source when both exist —
    ``get_album_for_source`` is supported uniformly across every registered
    provider, while single-track fetch isn't (album rows are checked first
    and a source is never overwritten once found).

    A FEATURED credit is deliberately not an anchor. The resolvers behind
    these ids (``get_album_artist_identity_for_source`` /
    ``get_track_artist_identity_for_source``) answer with the release's
    *primary* artist — that is their contract — so anchoring a guest on a
    release they merely appear on resolves to somebody else every time. On the
    production library that handed twelve guests of one Major Lazer release
    his Spotify id, artwork, genres and discography, and ten more Sawano
    Hiroyuki's. A guest has no anchor; the name search is the honest answer.
    """
    anchors: Dict[str, tuple] = {}

    def _consume(row, kind: str) -> None:
        if row["spotify_id"] and "spotify" not in anchors:
            anchors["spotify"] = (kind, str(row["spotify_id"]))
        if row["musicbrainz_id"] and "musicbrainz" not in anchors:
            anchors["musicbrainz"] = (kind, str(row["musicbrainz_id"]))
        try:
            ext = json.loads(row["external_ids"] or "{}")
        except (TypeError, ValueError):
            ext = {}
        if isinstance(ext, dict):
            for source, value in ext.items():
                if value and source not in anchors:
                    anchors[str(source)] = (kind, str(value))

    album_rows = conn.execute(
        "SELECT DISTINCT al.spotify_id, al.musicbrainz_id, al.external_ids "
        "FROM lib2_albums al WHERE al.primary_artist_id=? "
        "   OR al.id IN (SELECT album_id FROM lib2_album_artists "
        "                 WHERE artist_id=? AND role='primary')",
        (artist_id, artist_id),
    ).fetchall()
    for row in album_rows:
        _consume(row, "album")

    track_rows = conn.execute(
        "SELECT DISTINCT t.spotify_id, t.musicbrainz_id, t.external_ids "
        "FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id "
        "WHERE al.primary_artist_id=? "
        "   OR t.id IN (SELECT track_id FROM lib2_track_artists "
        "                WHERE artist_id=? AND role='primary')",
        (artist_id, artist_id),
    ).fetchall()
    for row in track_rows:
        _consume(row, "track")

    return anchors


def default_anchor_resolver(source: str, kind: str, provider_id: str) -> Optional[Dict[str, Any]]:
    """Production adapter over ``core.metadata.album_tracks`` — imported
    lazily so tests (which inject a fake anchor_resolver) never pull the
    metadata stack, same seam as :func:`default_artist_resolver`."""
    from core.metadata.album_tracks import (
        get_album_artist_identity_for_source,
        get_track_artist_identity_for_source,
    )

    if kind == "album":
        return get_album_artist_identity_for_source(source, provider_id)
    return get_track_artist_identity_for_source(source, provider_id)


def resolve_and_enrich_native_artist(
    conn,
    artist_id: int,
    *,
    resolver: Optional[ArtistResolver] = None,
    anchor_resolver: Optional[AnchorResolver] = None,
) -> Dict[str, Any]:
    """Resolve one native artist and persist id + artwork onto its row.

    Tries every strong catalog anchor first (an already-confirmed provider id
    sitting on one of this artist's own albums/tracks) — one lookup per
    anchored source, all of them persisted, not just the first (issues.md §16
    Finding 1). Only when the artist has no anchor anywhere does this fall
    back to the original single-source name search.

    Returns ``{"success": True, "source", "provider_id", "image_url"}`` on a
    match — with an additional ``"anchor_sources"`` list when the match came
    from catalog anchors — or ``{"success": False, "attempted": True,
    "reason": "not_found"}`` when nothing resolved (the expected outcome for
    genuine collaboration names like "Ian Asher & Galantis").
    """
    if resolver is None:
        resolver = default_artist_resolver
    if anchor_resolver is None:
        anchor_resolver = default_anchor_resolver

    row = conn.execute(
        "SELECT id, name, spotify_id, musicbrainz_id, external_ids "
        "FROM lib2_artists WHERE id=?",
        (int(artist_id),),
    ).fetchone()
    if row is None:
        raise LookupError(f"Library v2 artist {artist_id} not found")

    anchors = _artist_catalog_anchors(conn, int(artist_id))
    if anchors:
        matched_sources: List[str] = []
        first_match: Optional[Dict[str, Any]] = None

        # iss29-D01: resolve EVERY anchor before persisting any of them.
        #
        # `_persist_identity` is a bare UPDATE and `isolation_level=""` opens an
        # implicit transaction on DML, so writing anchor n and then resolving
        # anchor n+1 held the SQLite writer across a blocking provider call.
        # That is the self-deadlock this file describes for
        # `enrich_native_entity_for_service` below: the provider clients cache
        # their responses in the same database through their own connection, so
        # the held writer waits on itself for the full busy timeout and every
        # other writer in the process queues behind it.
        #
        # Splitting the loop keeps the network phase entirely free of DML — the
        # reads above start no write transaction — and the write phase entirely
        # free of I/O. Same shape as `enrich_native_entity_for_service`.
        resolved_anchors: List[tuple] = []
        for source, (kind, provider_id) in anchors.items():
            try:
                identity = anchor_resolver(source, kind, provider_id)
            except Exception as exc:  # noqa: BLE001 — one bad source must not abort the rest
                logger.debug(
                    "anchor resolve failed for artist %s source %s: %s",
                    artist_id, source, exc,
                )
                continue
            resolved_id = str((identity or {}).get("artist_id") or "").strip()
            if not identity or not resolved_id:
                continue
            if not identity_is_free(conn, int(artist_id), source, resolved_id):
                logger.debug(
                    "artist %s: anchor %s/%s is already another artist's identity",
                    artist_id, source, resolved_id,
                )
                continue
            resolved_anchors.append((source, resolved_id, identity))

        # Re-read rather than reusing the row fetched before the provider walk:
        # another writer may have merged into the same blob while we were on the
        # network, and that walk is the long part of this function.
        existing_external_ids = conn.execute(
            "SELECT external_ids FROM lib2_artists WHERE id=?", (int(artist_id),)
        ).fetchone()["external_ids"]
        for source, resolved_id, identity in resolved_anchors:
            _persist_identity(
                conn, int(artist_id),
                source=source,
                provider_id=resolved_id,
                image_url=identity.get("image_url"),
                genres=_normalize_genres(identity.get("genres")),
                existing_external_ids=existing_external_ids,
            )
            # Re-read so the next anchor's merge sees this write — several
            # non-dedicated-column sources share the same external_ids blob.
            existing_external_ids = conn.execute(
                "SELECT external_ids FROM lib2_artists WHERE id=?", (int(artist_id),)
            ).fetchone()["external_ids"]
            matched_sources.append(source)
            if first_match is None:
                first_match = {
                    "source": source, "provider_id": resolved_id,
                    "image_url": identity.get("image_url"),
                }

        if matched_sources:
            return {
                "success": True, "artist_id": int(artist_id),
                "source": first_match["source"],
                "provider_id": first_match["provider_id"],
                "image_url": first_match.get("image_url"),
                "anchor_sources": matched_sources,
            }
        # Anchors existed but none resolved (album delisted upstream, network
        # hiccup, ...) — fall through to the name-based path below.

    name = str(row["name"] or "").strip()
    identity = resolver(name) if name else None
    source = str((identity or {}).get("source") or "").strip().lower()
    provider_id = str((identity or {}).get("artist_id") or "").strip()
    if not identity or not source or not provider_id:
        return {
            "success": False, "attempted": True, "artist_id": int(artist_id),
            "reason": "not_found",
        }
    if not identity_is_free(conn, int(artist_id), source, provider_id):
        return {
            "success": False, "attempted": True, "artist_id": int(artist_id),
            "reason": "identity_taken", "source": source,
            "provider_id": provider_id,
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
        "UPDATE lib2_artists SET "
        "image_url=CASE WHEN COALESCE(art_locked,0)=1 THEN image_url ELSE ? END, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (str(url), int(artist_id)),
    )
    return True


def schedule_native_artist_artwork(database: Any, artist_id: int):
    """Run :func:`enrich_native_artist_artwork` off the caller's thread.

    manualmatch25-01: the artwork walk is a chain of *blocking* provider HTTP
    calls over every id stored on the row (same sequential shape as
    perf25-02, but on a request-critical path).  Running it inside
    ``PUT /manual-match`` — and, worse, before that route's ``conn.commit()`` —
    pushed the response past the web client's 10s default timeout for every
    artist and every source, so a match that had in fact succeeded looked
    like "Request timed out".

    Callers commit their own transaction first and then schedule here; the
    worker opens its own connection, so it sees the committed identity and
    cannot hold the caller's write lock.  Returns the :class:`threading.Thread`
    (tests join it); never raises — artwork is presentation data.
    """
    import threading

    thread = threading.Thread(
        target=_run_native_artist_artwork,
        args=(database, int(artist_id)),
        name="lib2-native-artist-artwork",
        daemon=True,
    )
    thread.start()
    return thread


def _run_native_artist_artwork(database: Any, artist_id: int) -> None:
    conn = None
    try:
        try:
            conn = database._get_connection()
        except Exception as exc:  # noqa: BLE001
            logger.debug("native artist artwork connection failed: %s", exc)
            return
        # Module-level lookup on purpose: keeps the seam patchable and picks up
        # the same function the synchronous callers use.
        if enrich_native_artist_artwork(conn, int(artist_id)):
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("native artist artwork fetch failed (%s): %s", artist_id, exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception as exc:  # noqa: BLE001
                logger.debug("native artist artwork close failed: %s", exc)


def default_artwork_fetcher(name: str, source_ids: Dict[str, str]) -> Optional[str]:
    """Resolve an artist image URL from stored provider ids (production adapter)."""
    from core.library2.provider_adapters import fetch_artwork_url

    result = fetch_artwork_url("artist", artist_name=name, source_ids=source_ids)
    return result.url if result is not None else None


def _apply_descriptive_metadata(
    conn: Any, entity_type: str, entity_id: int, metadata: Any,
) -> Optional[str]:
    """Persist only normalized, non-null provider fields onto one native row."""

    columns = {
        "artist": {
            "image_url": "image_url", "genres": "genres", "summary": "summary",
            "style": "style", "mood": "mood", "label": "label",
            "banner_url": "banner_url",
        },
        "album": {
            "image_url": "image_url", "genres": "genres", "year": "year",
            "release_date": "release_date", "label": "label", "upc": "upc",
            "style": "style", "mood": "mood", "explicit": "explicit",
        },
        "track": {
            "duration_ms": "duration", "bpm": "bpm", "explicit": "explicit",
            "lyrics": "genius_lyrics", "copyright": "copyright",
            "style": "style", "mood": "mood",
        },
    }[entity_type]
    assignments = []
    values: List[Any] = []
    for attribute, column in columns.items():
        value = getattr(metadata, attribute, None)
        if value is None:
            continue
        if attribute == "genres":
            value = json.dumps(list(value))
        if column == "image_url" and entity_type in {"artist", "album"}:
            assignments.append(
                "image_url=CASE WHEN COALESCE(art_locked,0)=1 THEN image_url ELSE ? END"
            )
        else:
            assignments.append(f"{column}=?")
        values.append(value)
    if assignments:
        values.append(int(entity_id))
        conn.execute(
            f"UPDATE lib2_{entity_type}s SET {', '.join(assignments)}, "
            "updated_at=CURRENT_TIMESTAMP WHERE id=?",
            values,
        )
    return getattr(metadata, "image_url", None)


def _musicbrainz_artist_identity(conn, artist_id: int, artist_name: str) -> Optional[str]:
    """MusicBrainz's own answer for an artist, instead of a name-similarity guess.

    The generic path below ranks a provider's search results by normalised-name
    similarity. For an artist whose MusicBrainz name is written in another
    script that similarity is 0.0, so the RIGHT entity is discarded — while a
    same-script near-namesake sails through: "SawanoHiroyuki[nZk]" against
    "Sawano Hiroyuki" normalises to 0.88, comfortably over the 0.85 gate. The
    Enrich button and the provider backfill that shares this function therefore
    could not match a cross-script artist at all, and could confidently match
    the wrong one — writing that id onto the row, where the AcoustID alias
    bridge then reads it.

    ``match_artist`` already decides this properly, including the owned-catalogue
    evidence that survives a script difference. Returns None to fall through to
    the generic path.
    """
    try:
        from core.musicbrainz_service import get_musicbrainz_service
        from core.library2.worker_support import owned_album_titles
    except Exception as exc:  # noqa: BLE001 — optional dependency, never fatal
        logger.debug("musicbrainz artist match unavailable: %s", exc)
        return None
    try:
        owned = owned_album_titles(conn, int(artist_id))
    except Exception:  # noqa: BLE001 — no catalogue is not an error
        owned = []
    try:
        result = get_musicbrainz_service().match_artist(
            artist_name, owned_titles=owned) or {}
    except Exception as exc:  # noqa: BLE001 — one provider, not the run
        logger.debug("musicbrainz artist match failed for %r: %s", artist_name, exc)
        return None
    return str(result.get("mbid") or "").strip() or None


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
    if not provider_id and canonical == "artist" and service == "musicbrainz":
        provider_id = _musicbrainz_artist_identity(
            conn, int(entity_id), str(row["name"] or ""))
        if provider_id:
            actual_source = service

    if not provider_id:
        if searcher is None:
            from core.library.service_search import _search_service
            searcher = _search_service
        query = str(row["name"] or "")
        if canonical != "artist" and artist_name:
            query = f"{artist_name} - {query}"
        candidates = searcher(service, canonical, query) or []

        # Use the project-wide Unicode-aware normalizer for every entity. The
        # artist threshold remains deliberately stricter to reject
        # "Blance/Blanke"-style near misses, while album/track title matching
        # keeps its existing tolerance without collapsing CJK to an empty key.
        from core.worker_utils import normalize_artist_name
        if canonical == "artist":
            from core.worker_utils import ARTIST_NAME_MATCH_THRESHOLD
            normalize_fn = normalize_artist_name
            threshold = ARTIST_NAME_MATCH_THRESHOLD
        else:
            normalize_fn = normalize_artist_name
            threshold = 0.72

        wanted = normalize_fn(row["name"])
        ranked = []
        seen_candidates = set()
        for candidate in candidates:
            if not isinstance(candidate, dict) or not candidate.get("id"):
                continue
            candidate_key = (
                str(candidate.get("provider") or service).strip().lower(),
                str(candidate.get("id")).strip(),
            )
            if candidate_key in seen_candidates:
                continue
            seen_candidates.add(candidate_key)
            candidate_name = normalize_fn(candidate.get("name"))
            if not wanted or not candidate_name:
                continue
            if canonical != "artist":
                # The fuzzy fold above erased whatever separates a slowed /
                # sped-up / sequel release from its original, so the score alone
                # cannot tell them apart. Reject before scoring.
                if not titles_are_same_release(row["name"], candidate.get("name")):
                    continue
                candidate_artist, candidate_album = _candidate_context(
                    candidate, canonical,
                )
                if not candidate_artist or not _artist_context_matches(
                    artist_name, candidate_artist,
                ):
                    continue
                if canonical == "track" and (
                    not candidate_album
                    or not _title_context_matches(album_title, candidate_album)
                ):
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
        if (
            len(ranked) > 1
            and ranked[0][0] - ranked[1][0] < ENRICH_AMBIGUITY_MARGIN
        ):
            return {
                "success": False, "attempted": True,
                "entity_type": canonical, "entity_id": int(entity_id),
                "reason": "ambiguous", "source": service,
            }
        hit = ranked[0][1]
        provider_id = str(hit["id"]).strip()
        actual_source = str(hit.get("provider") or service).strip().lower()

    from core.library2.match_status import (
        ProviderIdentityConflict, set_library_v2_match,
    )
    try:
        set_library_v2_match(
            conn, canonical, int(entity_id), actual_source, provider_id,
            actor="native_enrichment",
        )
    except ProviderIdentityConflict as conflict:
        # Another entity already IS this provider release. Leaving this one
        # unmatched is recoverable (the chip stays pending, a user can match it
        # by hand); writing the id anyway is not — it silently makes two local
        # releases the same release everywhere downstream.
        logger.info(
            "Refusing %s id %s for %s %s: already held by %s %s",
            actual_source, provider_id, canonical, entity_id,
            canonical, conflict.owner_id,
        )
        return {
            "success": False, "attempted": True,
            "entity_type": canonical, "entity_id": int(entity_id),
            "reason": "identity_conflict", "source": actual_source,
            "provider_id": provider_id, "conflicting_entity_id": conflict.owner_id,
        }

    # Release the writer before the provider walk (same rule as
    # completeness.resolve_tracklist). Holding it here deadlocked this thread
    # against itself: the provider clients cache their responses in the SAME
    # database through their own connection, so `fetch_descriptive_metadata`
    # below opens a second connection and writes — which cannot proceed while
    # this one still has an open transaction. It then waited out the full 30s
    # busy timeout, and every other writer in the process waited with it. That
    # is the "database is locked" storm: notifications, automations, repair
    # jobs and UI preferences all failing while one enrichment thread blocked
    # on its own uncommitted match write.
    conn.commit()

    from core.library2.provider_adapters import fetch_descriptive_metadata
    metadata = fetch_descriptive_metadata(
        canonical,
        {actual_source: provider_id},
        source_order=(actual_source,),
    )
    metadata_image = (
        _apply_descriptive_metadata(conn, canonical, int(entity_id), metadata)
        if metadata is not None else None
    )
    image_url = metadata_image or str(hit.get("image") or "").strip() or None
    if canonical == "artist":
        if not image_url:
            conn.commit()  # artwork lookup is another provider call
            image_url = default_artwork_fetcher(
                artist_name, {actual_source: provider_id},
            )
        if image_url:
            conn.execute(
                "UPDATE lib2_artists SET "
                "image_url=CASE WHEN COALESCE(art_locked,0)=1 THEN image_url ELSE ? END, "
                "updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (image_url, int(entity_id)),
            )
    elif canonical == "album":
        if not image_url:
            conn.commit()  # artwork lookup is another provider call
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
                "UPDATE lib2_albums SET "
                "image_url=CASE WHEN COALESCE(art_locked,0)=1 THEN image_url ELSE ? END, "
                "updated_at=CURRENT_TIMESTAMP "
                "WHERE id=?", (image_url, int(entity_id)),
            )
    else:
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


def enrich_native_entity_all_services(
    conn, entity_type: str, entity_id: int, *, commit: bool = False,
    services: Optional[set] = None,
) -> Dict[str, str]:
    """Resolve an entity against EVERY provider that supports it.

    A newly created catalogue row starts with at most the one provider id
    whoever created it happened to know. One id is enough to exist and not
    enough to work: tracklist resolution, artwork and matching all walk the
    stored ids in priority order, so an album that only Spotify knows falls
    back to a name search the moment Spotify has nothing. Each service is
    independent and best-effort — one provider being down or having no match
    must not cost the others. Returns ``{service: external_id}`` for what
    resolved.
    """
    from core.library2.match_status import SERVICES

    resolved: Dict[str, str] = {}
    for service, _label, supported in SERVICES:
        if entity_type not in supported:
            continue
        # Only providers this instance has actually configured. Walking an
        # unconfigured one is not merely wasted: Tidal's client starts an
        # interactive login, so a background enrich popped an OAuth tab in the
        # user's browser seconds after they clicked Bookmark.
        if services is not None and service not in services:
            continue
        try:
            result = enrich_native_entity_for_service(conn, entity_type, entity_id, service)
        except Exception as exc:  # noqa: BLE001
            logger.debug("enrich %s %s via %s failed: %s", entity_type, entity_id, service, exc)
            if commit and conn is not None:
                conn.rollback()
            continue
        finally:
            # Commit (or release) between providers, never across them. Each
            # service does a blocking provider call and then writes on this
            # connection; leaving the write transaction open across the NEXT
            # service's network call held SQLite's single writer lock for the
            # whole walk, and every other request — including the monitor POST
            # this walk was started by — died on "database is locked" after
            # the 30s busy timeout.
            if commit and conn is not None:
                conn.commit()
        # iss29-D06: the key is `provider_id`. `enrich_native_entity_for_service`
        # has never returned `external_id`, so this dict was unconditionally
        # empty — contradicting the docstring above. Both of today's callers
        # discard the value, which is why nothing surfaced it.
        if result.get("success") and result.get("provider_id"):
            resolved[str(result.get("source") or service)] = str(result["provider_id"])
    return resolved


def schedule_native_entity_enrich(
    database: Any, targets: List[tuple], *, services: Optional[set] = None,
) -> Any:
    """Run :func:`enrich_native_entity_all_services` off the caller's thread.

    Same contract as :func:`schedule_native_artist_artwork`: the caller
    commits first, the worker opens its own connection, and nothing here can
    fail the request. ``targets`` is a list of ``(entity_type, entity_id)``;
    ``services`` restricts the walk to the providers this instance configured.
    """
    import threading

    def _run() -> None:
        conn = None
        try:
            conn = database._get_connection()
            for entity_type, entity_id in targets:
                enrich_native_entity_all_services(
                    conn, str(entity_type), int(entity_id), commit=True,
                    services=services)
        except Exception as exc:  # noqa: BLE001
            logger.debug("native entity enrich failed (%s): %s", targets, exc)
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("native entity enrich close failed: %s", exc)

    thread = threading.Thread(target=_run, name="lib2-native-entity-enrich", daemon=True)
    thread.start()
    return thread


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

    from core.library2.importer import normalize_name

    cur = conn.execute(
        "INSERT INTO lib2_artists(name, name_key, sort_name, monitored) VALUES(?, ?, ?, ?)",
        (name, normalize_name(name), name, monitored),
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


def _has_attempt_column(conn) -> bool:
    """``lib2_artists.unmapped_last_attempted_at`` present? (pre-migration DBs)."""
    try:
        return any(
            str(row[1]) == "unmapped_last_attempted_at"
            for row in conn.execute("PRAGMA table_info(lib2_artists)").fetchall()
        )
    except Exception as exc:  # noqa: BLE001 — a missing column only costs backoff
        logger.debug("attempt-column probe failed: %s", exc)
        return False


def _mark_reconcile_attempted(conn, artist_id: int) -> None:
    """Stamp the backoff marker for one artist (issues.md §16 Finding 2).

    Written for *every* attempt, matched or not: a matched artist drops out of
    the pending query anyway, while an unmatched or erroring one must not be
    re-asked at every automated trigger. A row deleted meanwhile (smart split)
    simply updates nothing.
    """
    try:
        conn.execute(
            "UPDATE lib2_artists SET unmapped_last_attempted_at=CURRENT_TIMESTAMP "
            "WHERE id=?",
            (int(artist_id),),
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("could not mark reconcile attempt for artist %s: %s", artist_id, exc)


def _pending_unmapped_artists(
    conn, limit: Optional[int], *, cooldown_hours: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Artists (both native and legacy) that still carry no catalog provider id.

    ``cooldown_hours`` skips rows attempted inside that window — the backoff an
    automated (post-import) run needs so a permanently unresolvable name does
    not re-hit every configured provider on every trigger. A manual run passes
    ``None`` and gets the full backlog.
    """
    sql = (
        "SELECT id, legacy_artist_id, external_ids FROM lib2_artists "
        "WHERE (spotify_id IS NULL OR spotify_id='') "
        "  AND (musicbrainz_id IS NULL OR musicbrainz_id='') "
    )
    params: List[Any] = []
    if cooldown_hours is not None and _has_attempt_column(conn):
        # Compared inside SQLite so both sides are the same UTC clock the
        # CURRENT_TIMESTAMP marker is written with.
        sql += (
            "  AND (unmapped_last_attempted_at IS NULL "
            "       OR unmapped_last_attempted_at < datetime('now', ?)) "
        )
        params.append(f"-{float(cooldown_hours)} hours")
    sql += "ORDER BY id"
    rows = conn.execute(sql, params).fetchall()

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


def _artist_is_a_release_primary(conn, artist_id: int) -> bool:
    """Does this artist front at least one release in the catalogue?

    That is the evidence that an anchor could legitimately have produced its
    identity. A row that only ever appears as a guest never had a claim to one.
    """
    row = conn.execute(
        "SELECT 1 FROM lib2_albums WHERE primary_artist_id=? "
        " UNION ALL "
        "SELECT 1 FROM lib2_album_artists WHERE artist_id=? AND role='primary' "
        "LIMIT 1",
        (int(artist_id), int(artist_id)),
    ).fetchone()
    return row is not None


def _clear_borrowed_identity(conn, artist_id: int, namespace: str,
                             owner_row: Any) -> None:
    """Drop one borrowed provider id, and the artwork/genres that came with it.

    ``_persist_identity`` writes the id, the image and the genres of the same
    provider answer in one statement, so a borrowed row whose image and genres
    are byte-identical to the owner's got them from that same wrong answer.
    Anything that differs was written by something else and is left alone.
    """
    assignments = ["updated_at=CURRENT_TIMESTAMP"]
    params: List[Any] = []
    if namespace == "spotify":
        assignments.append("spotify_id=NULL")
    elif namespace == "musicbrainz":
        assignments.append("musicbrainz_id=NULL")
    else:
        assignments.append("external_ids=json_remove(external_ids, ?)")
        params.append(f"$.{namespace}")
    row = conn.execute(
        "SELECT image_url, genres, art_locked FROM lib2_artists WHERE id=?",
        (int(artist_id),),
    ).fetchone()
    if row is not None:
        if (not row["art_locked"] and row["image_url"]
                and row["image_url"] == owner_row["image_url"]):
            assignments.append("image_url=NULL")
        if row["genres"] and row["genres"] == owner_row["genres"]:
            assignments.append("genres='[]'")
    params.append(int(artist_id))
    conn.execute(
        f"UPDATE lib2_artists SET {', '.join(assignments)} WHERE id=?", params
    )


def release_borrowed_artist_identities(conn) -> Dict[str, Any]:
    """Take back provider identities that ended up on more than one artist.

    The backlog healer for the featured-credit anchor defect: whichever member
    of a sharing group actually fronts a release keeps the id; every guest that
    merely inherited it is cleared, so the next reconcile pass resolves it by
    name instead. A group where nothing in the catalogue names an owner (two
    co-composers of one soundtrack, neither the primary of any release) is
    reported and left untouched — guessing there would only move the error.

    Returns ``{"released", "ambiguous", "artist_ids"}``; ``artist_ids`` are the
    rows whose identity was taken back, so the caller can drop their cached
    artwork.
    """
    from core.library2.artist_aliases import resolve_alias_group

    holders: Dict[tuple, List[int]] = {}
    rows = conn.execute(
        "SELECT id, spotify_id, musicbrainz_id, external_ids, image_url, genres "
        "FROM lib2_artists"
    ).fetchall()
    by_id = {int(row["id"]): row for row in rows}
    for row in rows:
        identity: Dict[str, str] = {}
        if row["spotify_id"]:
            identity["spotify"] = str(row["spotify_id"]).strip()
        if row["musicbrainz_id"]:
            identity["musicbrainz"] = str(row["musicbrainz_id"]).strip()
        try:
            extra = json.loads(row["external_ids"] or "{}")
        except (TypeError, ValueError):
            extra = {}
        if isinstance(extra, dict):
            for namespace, value in extra.items():
                key = str(namespace).strip().lower()
                text = str(value or "").strip()
                if key and text:
                    identity.setdefault(key, text)
        for namespace, value in identity.items():
            if value:
                holders.setdefault((namespace, value), []).append(int(row["id"]))

    stats: Dict[str, Any] = {"released": 0, "ambiguous": 0, "artist_ids": []}
    for (namespace, value), artist_ids in holders.items():
        if len(artist_ids) < 2:
            continue
        # §40 alias members legitimately share their canonical row's identity.
        groups = {frozenset(resolve_alias_group(conn, aid) or [aid]) | {aid}
                  for aid in artist_ids}
        if len(groups) < 2:
            continue
        owners = [aid for aid in artist_ids
                  if _artist_is_a_release_primary(conn, aid)]
        if len(owners) != 1:
            stats["ambiguous"] += 1
            logger.info(
                "borrowed identity %s/%s shared by %s and no catalogue owner",
                namespace, value, artist_ids,
            )
            continue
        owner_row = by_id[owners[0]]
        for aid in artist_ids:
            if aid == owners[0]:
                continue
            _clear_borrowed_identity(conn, aid, namespace, owner_row)
            stats["released"] += 1
            if aid not in stats["artist_ids"]:
                stats["artist_ids"].append(aid)
    return stats


def clear_placeholder_artist_images(conn) -> Dict[str, Any]:
    """Drop stored artist photos that are really a provider's "no photo" asset.

    Last.fm hands out one generic grey star for every artist it cannot picture,
    and it is a perfectly valid image URL — so it was stored, cached and served
    as a portrait for seven different artists. Clearing it lets the next lookup
    try again and, failing that, show the placeholder the UI has for exactly
    this. An artwork lock means the user chose the picture; that is never
    touched.
    """
    from core.metadata.artist_image import is_placeholder_artist_image

    rows = conn.execute(
        "SELECT id, image_url FROM lib2_artists "
        " WHERE image_url IS NOT NULL AND image_url <> '' "
        "   AND COALESCE(art_locked, 0) = 0"
    ).fetchall()
    artist_ids = [int(row["id"]) for row in rows
                  if is_placeholder_artist_image(row["image_url"])]
    for artist_id in artist_ids:
        conn.execute(
            "UPDATE lib2_artists SET image_url=NULL, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=?",
            (artist_id,),
        )
    return {"cleared": len(artist_ids), "artist_ids": artist_ids}


def prune_browse_only_artists(conn) -> Dict[str, Any]:
    """Remove artists that only ever existed because a browse-only release
    credited them.

    A discography sync pulls an artist's whole back catalogue so it can be
    browsed; resolving those tracklists minted a full artist row for every name
    credited on every one of them. On the production library that was 82 of 380
    artists (22%), each listed as a library artist whose "My Library" is
    necessarily empty — there is nothing of theirs to show, and never was.

    Only rows with nothing but a name and browse-only credits go: not
    monitored, never fronting a RELEASE, no link to anything the user owns or
    wants, no file anywhere, no artwork lock and no metadata override. A row
    with NO credits at all is left alone — a watchlisted or hand-created artist
    has an empty page too, but somebody asked for it.

    Fronting a *track* deliberately does not count. Position 0 of every credit
    list is written as ``role='primary'``, so the lead of any browse-only
    single would qualify — and that is the same ghost: three such rows survived
    an earlier cut of this query on the production library purely because the
    single they lead happens to be filed under the guest whose discography
    surfaced it.
    """
    rows = conn.execute(
        """
        SELECT a.id
          FROM lib2_artists a
         WHERE COALESCE(a.monitored, 0) = 0
           AND COALESCE(a.art_locked, 0) = 0
           AND a.canonical_artist_id IS NULL
           AND NOT EXISTS (SELECT 1 FROM lib2_artists m
                            WHERE m.canonical_artist_id = a.id)
           AND NOT EXISTS (SELECT 1 FROM lib2_albums al
                            WHERE al.primary_artist_id = a.id)
           AND NOT EXISTS (SELECT 1 FROM lib2_album_artists aa
                            WHERE aa.artist_id = a.id AND aa.role = 'primary')
           AND NOT EXISTS (SELECT 1 FROM lib2_metadata_overrides o
                            WHERE o.entity_type = 'artist' AND o.entity_id = a.id)
           AND NOT EXISTS (
                 SELECT 1 FROM lib2_track_artists ta
                   JOIN lib2_track_files f ON f.track_id = ta.track_id
                  WHERE ta.artist_id = a.id
                    AND COALESCE(f.file_state, 'active') <> 'deleted')
           AND NOT EXISTS (
                 SELECT 1 FROM lib2_album_artists aa2
                   JOIN lib2_albums al2 ON al2.id = aa2.album_id
                  WHERE aa2.artist_id = a.id
                    AND (al2.origin <> 'discography' OR al2.monitored = 1))
           AND NOT EXISTS (
                 SELECT 1 FROM lib2_track_artists ta2
                   JOIN lib2_tracks t2 ON t2.id = ta2.track_id
                   JOIN lib2_albums al3 ON al3.id = t2.album_id
                  WHERE ta2.artist_id = a.id
                    AND (al3.origin <> 'discography' OR al3.monitored = 1
                         OR COALESCE(t2.monitored, 0) = 1))
           AND (
                 EXISTS (SELECT 1 FROM lib2_album_artists aa3 WHERE aa3.artist_id = a.id)
              OR EXISTS (SELECT 1 FROM lib2_track_artists ta3 WHERE ta3.artist_id = a.id)
           )
         ORDER BY a.id
        """
    ).fetchall()
    artist_ids = [int(row["id"]) for row in rows]
    for artist_id in artist_ids:
        conn.execute("DELETE FROM lib2_artists WHERE id=?", (artist_id,))
    if artist_ids:
        logger.info("pruned %d browse-only artist rows", len(artist_ids))
    return {"pruned": len(artist_ids), "artist_ids": artist_ids}


def reconcile_unmapped_native_artists(
    conn,
    *,
    resolver: Optional[ArtistResolver] = None,
    limit: Optional[int] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    cooldown_hours: Optional[float] = None,
) -> Dict[str, Any]:
    """Resolve every still-unmapped artist by name (the backlog healer).

    This is the on-demand maintenance pass behind the "Reconcile unmapped
    artists" action: it walks artists with no provider id and tries to
    resolve+enrich (for native) or split them. Collaboration names that no provider
    models as one entity simply stay unmatched — counted, never fabricated.

    ``cooldown_hours`` is the automated caller's backoff (issues.md §16
    Finding 2): rows attempted inside the window are left alone. Every attempt
    is stamped regardless of outcome, so the window also covers rows whose
    provider call errored.
    """
    if resolver is None:
        resolver = default_artist_resolver

    # Take back borrowed identities FIRST: a row cleared here has no provider
    # id any more, so it joins this same run's pending set and gets resolved by
    # name instead of keeping the guest-anchor answer forever.
    released = release_borrowed_artist_identities(conn)
    pruned = prune_browse_only_artists(conn)
    placeholders = clear_placeholder_artist_images(conn)
    conn.commit()

    artists = _pending_unmapped_artists(conn, limit, cooldown_hours=cooldown_hours)
    total = len(artists)
    stats = {
        "scanned": 0, "matched": 0, "split": 0, "unmatched": 0, "errors": 0,
        "identities_released": released["released"],
        "identities_ambiguous": released["ambiguous"],
        "released_artist_ids": released["artist_ids"] + placeholders["artist_ids"],
        "browse_only_pruned": pruned["pruned"],
        "placeholder_images_cleared": placeholders["cleared"],
    }
    for index, art in enumerate(artists):
        artist_id = art["id"]
        try:
            stats["scanned"] += 1
            result = resolve_and_enrich_native_artist(conn, artist_id, resolver=resolver)
            if result.get("success"):
                stats["matched"] += 1
                _mark_reconcile_attempted(conn, artist_id)
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

            _mark_reconcile_attempted(conn, artist_id)
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
            # After the rollback, never inside it: a failing provider must
            # still earn its backoff, otherwise an automated trigger retries
            # the same broken row at full frequency.
            _mark_reconcile_attempted(conn, artist_id)
            try:
                conn.commit()
            except Exception as commit_err:  # noqa: BLE001
                logger.debug("reconcile attempt-marker commit failed: %s", commit_err)
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



def backfill_missing_provider_ids(
    conn,
    *,
    services: Any,
    limit: int,
    enricher: Optional[Callable[..., Dict[str, Any]]] = None,
    next_item: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
    should_stop: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Fill in per-provider gaps across owned artists, albums and tracks.

    ``reconcile_unmapped_native_artists`` asks "does this artist have ANY
    catalog id", which is the right question for an artist nothing has ever
    resolved and the wrong one for an artist that Spotify matched and
    MusicBrainz did not — the shape behind the user's `Sawano Hiroyuki`, whose
    missing MBID left the AcoustID verifier without the alias bridge that makes
    a romaji name comparable to a kanji one. The gap is per-provider, so the
    backlog query has to be too.

    Work comes from ``lib2_provider_attempts`` — the same ledger the twelve
    enrichment workers drain — via :func:`core.library2.worker_queue.next_pending`,
    which already knows the owned-library predicate, the artist→album→track
    order and the retry window for a previous miss. That shared ledger is also
    what makes this safe to run alongside those workers: whoever reaches a row
    first records the attempt, and the other one is handed the next row instead.

    ``should_stop`` is consulted between items. Without it a full budget against
    MusicBrainz's one-request-per-second limiter was minutes of work that a stop
    could not interrupt, because the job only checked before the phase began.

    ``limit`` is a whole-run budget spent round-robin over ``services``, so one
    provider's backlog cannot starve the rest. Every outcome is recorded,
    including a failure, because a row whose attempt went unrecorded is handed
    straight back on the next iteration.
    """
    from core.library2.provider_attempts import record_attempt
    from core.library2.worker_queue import next_pending

    if enricher is None:
        enricher = enrich_native_entity_for_service
    if next_item is None:
        next_item = next_pending

    queue = [str(s).strip().lower() for s in (services or []) if str(s).strip()]
    stats: Dict[str, Any] = {
        "scanned": 0, "matched": 0, "not_found": 0, "errors": 0,
        "by_service": {},
    }
    budget = max(0, int(limit))
    exhausted: set = set()

    while budget > 0 and len(exhausted) < len(queue):
        progressed = False
        for service in queue:
            if budget <= 0:
                break
            # A 100-item budget against MusicBrainz's one-request-per-second
            # limiter is minutes of work; checking only before the phase made
            # "stop" mean "stop eventually".
            if callable(should_stop) and should_stop():
                budget = 0
                break
            if service in exhausted:
                continue
            try:
                item = next_item(conn, service)
            except Exception as exc:  # noqa: BLE001 — one provider, not the run
                logger.debug("provider backfill: queue read for %s failed: %s",
                             service, exc)
                exhausted.add(service)
                continue
            if not item:
                exhausted.add(service)
                continue
            progressed = True
            budget -= 1
            stats["scanned"] += 1
            entity_type = str(item.get("type") or "")
            entity_id = int(item.get("id"))
            try:
                result = enricher(conn, entity_type, entity_id, service) or {}
                status = "matched" if result.get("success") else "not_found"
            except Exception as exc:  # noqa: BLE001 — one entity, not the run
                logger.debug("provider backfill: %s %s via %s failed: %s",
                             entity_type, entity_id, service, exc)
                status = "error"
            stats["errors" if status == "error" else status] += 1
            stats["by_service"].setdefault(service, {"matched": 0, "not_found": 0,
                                                     "errors": 0})
            stats["by_service"][service][
                "errors" if status == "error" else status] += 1
            try:
                record_attempt(conn, entity_type=entity_type, entity_id=entity_id,
                               service=service, status=status)
                conn.commit()
            except Exception as exc:  # noqa: BLE001
                # An unrecorded attempt means the queue hands this row straight
                # back, so the run would spin on it. Drop the provider instead.
                logger.debug("provider backfill: could not record %s attempt for "
                             "%s %s: %s", service, entity_type, entity_id, exc)
                exhausted.add(service)
        if not progressed:
            break
    return stats


__all__ = [
    "backfill_missing_provider_ids",
    "enrich_native_entity_all_services",
    "schedule_native_entity_enrich",
    "enrich_native_entity_for_service",
    "resolve_and_enrich_native_artist",
    "reconcile_unmapped_native_artists",
    "release_borrowed_artist_identities",
    "prune_browse_only_artists",
    "clear_placeholder_artist_images",
    "identity_is_free",
    "smart_split_combined_artist",
    "enrich_native_artist_artwork",
    "default_artist_resolver",
    "default_artwork_fetcher",
]
