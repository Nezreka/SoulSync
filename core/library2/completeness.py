"""Resolve an album's canonical tracklist so missing tracks show real titles.

Lidarr shows the full tracklist of an album (from metadata) and marks which tracks
are present vs missing. We fetch the canonical tracklist from a metadata provider
(Spotify by id, else Deezer by search — both reusing SoulSync's existing clients)
and cache it on ``lib2_albums.tracklist_json``. The read path (``queries.get_album``)
then fills missing-track placeholders with the real title instead of "Track N".

Resolution is best-effort and never raises — when no provider yields a tracklist,
the UI falls back to numbered missing slots.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Mapping, Optional, Tuple

from core.library2.provider_ids import parse_external_ids, provider_only
from utils.logging_config import get_logger

logger = get_logger("library2.completeness")


def _json_object(raw: Any) -> Dict[str, str]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(key).strip().lower(): str(item).strip()
        for key, item in value.items()
        if str(key).strip() and str(item).strip()
    }


def _track_artist_credits(raw: Any) -> List[Tuple[str, Optional[str]]]:
    """``(name, provider_id)`` per credit, in the order the provider credited.

    ``provider_id`` is ``None`` for caches written by an earlier parser, which
    stored names only. That is exactly the state this function exists to stop
    producing: an id-less credit becomes an id-less artist row, and the
    unmapped-artist reconciler then infers one from the album anchor — which
    resolves to the album's PRIMARY artist, not to the guest.
    """
    if not raw:
        return []
    if isinstance(raw, (str, bytes, Mapping)):
        raw = [raw]
    try:
        entries = list(raw)
    except TypeError:
        entries = [raw]

    credits: List[Tuple[str, Optional[str]]] = []
    seen = set()
    for entry in entries:
        provider_id = None
        if isinstance(entry, Mapping):
            nested = entry.get("artist")
            if isinstance(nested, Mapping):
                entry = nested
            name = entry.get("name") or entry.get("artist_name")
            provider_id = entry.get("id") or entry.get("artist_id")
        else:
            name = entry
        text = str(name or "").strip()
        key = text.casefold()
        if text and key != "unknown artist" and key not in seen:
            seen.add(key)
            identifier = str(provider_id).strip() if provider_id else None
            credits.append((text, identifier or None))
    return credits


def _credits_may_mint_artists(album_row: Any) -> bool:
    """May this release's credits create artists the library does not have yet?

    Only for a release the user owns or has asked for. A discography sync pulls
    an artist's entire back catalogue purely so it can be browsed, and
    resolving those tracklists used to mint a full artist row for every name
    credited on every one of them: on the production library 82 of 380 artists
    (22%) existed for no other reason, each listed as a library artist whose
    "My Library" is necessarily empty. Credits to artists the library already
    knows are still recorded either way — this governs creation, not linking.
    """
    try:
        origin = str(album_row["origin"] or "library")
    except (IndexError, KeyError, TypeError):
        origin = "library"
    if origin != "discography":
        return True
    try:
        return bool(album_row["monitored"])
    except (IndexError, KeyError, TypeError):
        return True


def _entry_credit_source(entry: Mapping[str, Any]) -> Optional[str]:
    """Which provider namespace this entry's credit ids belong to.

    The parser stamps ``provider`` onto every entry it writes; older cached
    entries only carry ``external_ids``, whose single provider key says the
    same thing.
    """
    provider = str(entry.get("provider") or "").strip().lower()
    if provider:
        return provider
    for source in provider_only(parse_external_ids(entry.get("external_ids"))):
        return source
    return None


def _persist_track_artist_credits(
    conn, track_id: int, album_id: int, album_primary_artist_id: int,
    raw_artists: Any, *, credit_source: Optional[str] = None,
    may_create_artists: bool = True,
) -> None:
    """Persist per-track credits and make their album appearance reachable.

    ``may_create_artists=False`` records the credits of artists the library
    already knows but mints no new rows — see :func:`_credits_may_mint_artists`.
    """
    names = _track_artist_credits(raw_artists)
    if not names:
        conn.execute(
            "INSERT OR IGNORE INTO lib2_track_artists"
            "(track_id, artist_id, role, position) VALUES(?,?, 'primary', 0)",
            (track_id, album_primary_artist_id),
        )
        return

    # Provider-only positions have no more authoritative local tag credit to
    # preserve. Replace the old album-primary fallback so a refreshed cache
    # heals rows created by earlier parser versions. Owned files remain
    # additive: their imported/tag-derived credit must not be discarded.
    owns_file = conn.execute(
        "SELECT 1 FROM lib2_track_files WHERE track_id=? "
        "AND COALESCE(file_state, 'active')<>'deleted' LIMIT 1",
        (track_id,),
    ).fetchone()
    if owns_file is None:
        conn.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (track_id,))

    from core.library2.autolink import find_or_create_artist
    for position, (name, provider_id) in enumerate(names):
        artist_id = find_or_create_artist(
            conn, name, spotify_id=provider_id, source=credit_source,
            create=may_create_artists,
        )
        if artist_id is None:
            continue
        conn.execute(
            "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
            "VALUES(?,?,?,?) ON CONFLICT(track_id, artist_id) DO UPDATE SET "
            "role=excluded.role, position=excluded.position",
            (track_id, artist_id, "primary" if position == 0 else "featured", position),
        )
        conn.execute(
            "INSERT INTO lib2_album_artists(album_id, artist_id, role) "
            "VALUES(?,?,?) ON CONFLICT(album_id, artist_id) DO NOTHING",
            (
                album_id,
                artist_id,
                "primary" if int(artist_id) == int(album_primary_artist_id) else "featured",
            ),
        )


def _album_tracklist_context(
    conn: Any, album_id: int,
) -> Optional[Tuple[Any, Dict[str, Any], Dict[str, str]]]:
    """Return album row, edition reference and provider IDs for cache binding."""
    row = conn.execute(
        """SELECT al.title, al.primary_artist_id, al.tracklist_json,
                  al.year AS album_year,
                  al.release_date AS album_release_date,
                  al.track_count AS album_track_count,
                  al.expected_track_count AS album_expected_track_count,
                  al.spotify_id AS album_spotify_id,
                  al.musicbrainz_id AS album_musicbrainz_id,
                  al.musicbrainz_release_group_id AS album_release_group_mbid,
                  al.external_ids AS album_external_ids,
                  ed.id AS release_edition_id,
                  ed.release_date AS edition_release_date,
                  ed.track_count AS edition_track_count,
                  ed.spotify_id AS edition_spotify_id,
                  ed.musicbrainz_id AS edition_musicbrainz_id,
                  ed.external_ids AS edition_external_ids
             FROM lib2_albums al
             LEFT JOIN lib2_release_editions ed
                    ON ed.release_group_id=al.id AND ed.is_default=1
            WHERE al.id=?""",
        (album_id,),
    ).fetchone()
    if row is None:
        return None

    source_ids = _json_object(row["album_external_ids"])
    source_ids.update(_json_object(row["edition_external_ids"]))
    spotify_id = row["edition_spotify_id"] or row["album_spotify_id"]
    musicbrainz_id = row["edition_musicbrainz_id"] or row["album_musicbrainz_id"]
    if spotify_id:
        source_ids["spotify"] = str(spotify_id)
    if musicbrainz_id:
        source_ids["musicbrainz"] = str(musicbrainz_id)
    elif row["album_release_group_mbid"]:
        # No concrete release is known for this row — a discography row never
        # has one. MusicBrainz's ``get_album_tracks`` resolves a release GROUP
        # too (it picks a release from the group), so the group id is a usable
        # tracklist key and the only one these rows have. It is deliberately
        # NOT stored under this key in the database: there it would be read as
        # a release id by the tag writer and the /release/ link.
        source_ids["musicbrainz"] = str(row["album_release_group_mbid"])
    release_date = (
        row["edition_release_date"]
        or row["album_release_date"]
        or (str(row["album_year"]) if row["album_year"] else None)
    )
    track_count = (
        row["edition_track_count"]
        or row["album_expected_track_count"]
        or row["album_track_count"]
    )
    reference = {
        "release_edition_id": row["release_edition_id"],
        "spotify_id": source_ids.get("spotify"),
        "musicbrainz_id": source_ids.get("musicbrainz"),
        "external_ids": dict(sorted(source_ids.items())),
        "release_date": release_date,
        "track_count": track_count,
    }
    return row, reference, source_ids


def _snapshot_tracks(snapshot: Any, reference: Mapping[str, Any]) -> Optional[List[dict]]:
    from core.library2.provider_adapters import TRACKLIST_PARSER_VERSION

    if snapshot is None or not snapshot.is_complete:
        return None
    if snapshot.parser_version != TRACKLIST_PARSER_VERSION:
        return None
    payload = snapshot.payload
    if not isinstance(payload, dict) or payload.get("reference") != dict(reference):
        return None
    tracks = payload.get("tracks")
    if not isinstance(tracks, list) or not tracks:
        return None
    return [track for track in tracks if isinstance(track, dict)] or None


def _delete_track_row(conn, track_id: int) -> None:
    """Remove one track row and its dependent rows (not the edition prune)."""
    conn.execute(
        "DELETE FROM lib2_monitor_rules WHERE entity_type='track' AND entity_id=?",
        (track_id,),
    )
    conn.execute("DELETE FROM lib2_wanted_tracks WHERE track_id=?", (track_id,))
    conn.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (track_id,))
    conn.execute("DELETE FROM lib2_tracks WHERE id=?", (track_id,))


def _trim_excess_fileless_tracks(conn, album_id: int, expected: int,
                                  protect_ids: Optional[set] = None) -> int:
    """Drop surplus provider-only rows when an old import over-materialized them.

    ``protect_ids`` (rows the current call's entries matched or inserted) are
    never dropped — the tracklist just reaffirmed those positions are real,
    even when the album's stored ``expected_track_count`` predates that
    knowledge and is now an undercount.
    """
    if expected <= 0:
        return 0
    protect_ids = protect_ids or set()
    rows = conn.execute(
        """SELECT t.id, t.legacy_track_id, t.monitored,
                  EXISTS(SELECT 1 FROM lib2_track_files f
                          WHERE f.track_id = t.id
                            AND COALESCE(f.file_state,'active')<>'deleted') AS has_file,
                  EXISTS(
                      SELECT 1 FROM lib2_monitor_rules r
                       WHERE r.entity_type='track' AND r.entity_id=t.id
                         AND r.monitored=1
                  ) AS has_positive_rule,
                  EXISTS(
                      SELECT 1 FROM lib2_wanted_tracks w
                       WHERE w.track_id=t.id AND w.wanted=1
                  ) AS is_wanted
             FROM lib2_tracks t
            WHERE t.album_id=?
            ORDER BY COALESCE(t.disc_number, 1), t.track_number, t.id""",
        (album_id,),
    ).fetchall()
    if len(rows) <= expected:
        return 0

    deleted = 0
    for idx, row in enumerate(rows):
        if idx < expected:
            continue
        if row["id"] in protect_ids:
            continue
        if (
            row["legacy_track_id"] is not None
            or row["has_file"]
            or row["monitored"]
            or row["has_positive_rule"]
            or row["is_wanted"]
        ):
            continue
        _delete_track_row(conn, row["id"])
        deleted += 1
    if deleted:
        from core.library2.editions import prune_orphaned_edition_rows
        prune_orphaned_edition_rows(conn.cursor())
    return deleted


def _norm_title(value: Any) -> str:
    """Casefold + collapse whitespace — a forgiving key for title matching."""
    return " ".join(str(value or "").split()).casefold()


def _unique_untouched_title_match(conn, album_id: int, title: str,
                                  touched_ids: set) -> Optional[int]:
    """A single not-yet-touched local track of this album with the same title.

    Returns its id only when the title unambiguously identifies ONE track to
    heal, so a duplicate title (remix/intro/outro name reused) never triggers
    a wrong heal. Used to repair corrupted track NUMBERS (§16.3): the title is
    the stable identity, the number is the field that got collapsed/
    duplicated, so matching on it re-keys the right row instead of confirming
    the corruption or inserting a duplicate.

    A title can also collide between a real (has-file) row and one or more
    fileless placeholders: an earlier resolve created the placeholder at the
    correct number before the file existed, then the file's own row got its
    number corrupted into colliding with something else (§17.2 — "DAISIES at
    number 1 AND 2"). Plain uniqueness would refuse to heal here (ambiguous),
    leaving the real row corrupted and the placeholder as a visible duplicate.
    When exactly one candidate has a file and the rest are safe-to-drop
    placeholders (no legacy link, not monitored, no positive monitor rule, not
    wanted), the real row is the one to heal and the redundant placeholder(s)
    are removed.
    """
    norm = _norm_title(title)
    if not norm:
        return None
    rows = [
        r for r in conn.execute(
            """SELECT t.id, t.title, t.legacy_track_id, t.monitored,
                      EXISTS(SELECT 1 FROM lib2_track_files f
                              WHERE f.track_id=t.id
                                AND COALESCE(f.file_state,'active')<>'deleted') AS has_file,
                      EXISTS(
                          SELECT 1 FROM lib2_monitor_rules r
                           WHERE r.entity_type='track' AND r.entity_id=t.id
                             AND r.monitored=1
                      ) AS has_positive_rule,
                      EXISTS(
                          SELECT 1 FROM lib2_wanted_tracks w
                           WHERE w.track_id=t.id AND w.wanted=1
                      ) AS is_wanted
                 FROM lib2_tracks t WHERE t.album_id=?""",
            (album_id,),
        ).fetchall()
        if _norm_title(r["title"]) == norm and r["id"] not in touched_ids
    ]
    if len(rows) == 1:
        return rows[0]["id"]
    if len(rows) < 2:
        return None
    with_file = [r for r in rows if r["has_file"]]
    without_file = [r for r in rows if not r["has_file"]]
    if len(with_file) != 1 or not without_file:
        return None
    if any(
        r["legacy_track_id"] is not None or r["monitored"]
        or r["has_positive_rule"] or r["is_wanted"]
        for r in without_file
    ):
        return None
    for r in without_file:
        _delete_track_row(conn, r["id"])
    from core.library2.editions import prune_orphaned_edition_rows
    prune_orphaned_edition_rows(conn.cursor())
    return with_file[0]["id"]


def _from_a_known_release_id(
    source_ids: Any, provider: Any, provider_entity_id: Any,
) -> bool:
    """Did this tracklist come from a release id the album already carried?

    Only then may a shorter list shrink ``expected_track_count``. The provider
    walk falls back to a Deezer NAME search for rows without release ids, and a
    title search can land on the wrong release — an EP's suite matching a
    one-track single would otherwise "correct" a 31-track expectation down to
    1 and hide thirty genuinely missing tracks. A direct id is a statement
    about THIS release; a search result is a guess about which release it is.
    """
    if not provider or not provider_entity_id:
        return False
    try:
        known = (source_ids or {}).get(str(provider))
    except AttributeError:
        return False
    return bool(known) and str(known) == str(provider_entity_id)


def _persist_tracklist_tracks(
    conn, album_id: int, tracks: List[dict], *, complete: bool = False,
) -> int:
    """Persist provider tracklist entries as fileless lib2 track rows.

    Missing rows must have real DB ids so they can be monitored individually,
    just like Lidarr's wanted track rows. Existing local/downloaded tracks are
    matched by disc+track number and left in place.

    ``complete`` says the provider answered for the WHOLE release. Only then may
    the stored expectation come down: an old, too-high count (a different
    edition, a deluxe total, a provider that has since corrected itself) would
    otherwise survive every refresh and leave the album permanently one track
    short — of a track that exists in no tracklist and no row. A truncated page
    is not evidence of a shorter album, and neither is an empty answer.
    """
    al = conn.execute(
        "SELECT primary_artist_id, monitored, quality_profile_id, "
        "       expected_track_count, origin FROM lib2_albums WHERE id=?",
        (album_id,),
    ).fetchone()
    if not al:
        return 0

    entries = [t for t in tracks if isinstance(t, dict)]
    try:
        expected = int(al["expected_track_count"] or 0)
    except (TypeError, ValueError):
        expected = 0
    # A provider-confirmed complete list wins over an old undercount. Never
    # slice real entries to a stale expected_track_count (P1-26).
    if len(entries) > expected:
        expected = len(entries)
        conn.execute(
            """UPDATE lib2_albums
                  SET expected_track_count=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (expected, album_id),
        )
    elif complete and entries and len(entries) < expected:
        # …and over an old OVERcount, which had no way to be corrected before:
        # the album kept reporting a missing track that no tracklist entry and
        # no row could account for. The convergence below still raises it back
        # if local rows outnumber the provider's list.
        expected = len(entries)
        conn.execute(
            """UPDATE lib2_albums
                  SET expected_track_count=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (expected, album_id),
        )
    has_explicit_disc = any(e.get("disc_number") not in (None, "", 1, "1") for e in entries)
    inferred_disc = 1
    previous_number: Optional[int] = None

    # Reserve every exact title match BEFORE any positional claim (§16.3 heal,
    # hoisted out of the loop below).
    #
    # A title match is the stronger claim on a local row, but it used to be
    # evaluated per entry, so an earlier entry's (disc, number) fallback could
    # take a row a later entry would have matched exactly. Production case: the
    # one owned file of "Peace Is The Mission (Extended)" was `04 - Lean On`,
    # while slot 4 of that edition is "Blaze Up the Fire (feat. Chronixx)".
    # The positional claim consumed the row, so the file got that other song's
    # provider ids and artist credits (Chronixx landed on "Lean On"), the real
    # slot 5 had to insert a duplicate "Lean On", and "Blaze Up the Fire" never
    # became a missing row at all. Reserving up front removes the ordering.
    reserved: Dict[int, int] = {}
    for idx, entry in enumerate(entries):
        entry_title = str(entry.get("title") or "").strip()
        if not entry_title:
            continue
        reservation = _unique_untouched_title_match(
            conn, album_id, entry_title, set(reserved.values()))
        if reservation is not None:
            reserved[idx] = reservation

    created = 0
    touched_ids: set = set()
    for idx, entry in enumerate(entries):
        title = str(entry.get("title") or "").strip()
        if not title:
            continue
        try:
            number = int(entry.get("track_number") or idx + 1)
        except (TypeError, ValueError):
            number = idx + 1
        if has_explicit_disc:
            try:
                disc = int(entry.get("disc_number") or 1)
            except (TypeError, ValueError):
                disc = 1
        else:
            if previous_number is not None and number <= previous_number:
                inferred_disc += 1
            disc = inferred_disc
            previous_number = number
        duration = entry.get("duration_ms")
        incoming_ids = parse_external_ids(entry.get("external_ids"))
        if entry.get("spotify_id"):
            incoming_ids.setdefault("spotify", str(entry["spotify_id"]))
        if entry.get("musicbrainz_id"):
            incoming_ids.setdefault("musicbrainz", str(entry["musicbrainz_id"]))
        if entry.get("isrc"):
            incoming_ids.setdefault("isrc", str(entry["isrc"]))
        spotify_id = incoming_ids.get("spotify")

        # §16.3 heal: prefer a unique, not-yet-touched local row with the SAME
        # title over the (disc, number) key. When track numbers got corrupted
        # (e.g. a whole album collapsed onto number 1, or duplicated), that key
        # IS the corrupt field, so it could only re-confirm the collapse or add
        # duplicate rows — which is exactly why "Update Discography" never
        # repaired it. Matching on the stable title lets a correctly-fetched
        # tracklist rewrite the numbers IN PLACE.
        heal_id = reserved.get(idx)
        existing = None if heal_id is not None else conn.execute(
            """SELECT id FROM lib2_tracks
               WHERE album_id=? AND COALESCE(disc_number, 1)=? AND track_number=?
                 AND id NOT IN (SELECT value FROM json_each(?))""",
            (album_id, disc, number, json.dumps(sorted(reserved.values()))),
        ).fetchone()
        if heal_id is not None:
            conn.execute(
                """UPDATE lib2_tracks
                      SET track_number=?, disc_number=?,
                          spotify_id=COALESCE(NULLIF(spotify_id, ''), ?),
                          duration=COALESCE(duration, ?),
                          updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (number, disc, spotify_id, duration, heal_id),
            )
            track_id = heal_id
            touched_ids.add(track_id)
        elif existing:
            conn.execute(
                """UPDATE lib2_tracks
                      SET title=COALESCE(NULLIF(title, ''), ?),
                          spotify_id=COALESCE(NULLIF(spotify_id, ''), ?),
                          duration=COALESCE(duration, ?),
                          updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (title, spotify_id, duration, existing["id"]),
            )
            track_id = existing["id"]
            touched_ids.add(track_id)
        else:
            from core.library2.profile_lookup import default_quality_profile_id
            conn.execute(
                """INSERT INTO lib2_tracks(album_id, title, track_number, disc_number,
                          duration, spotify_id, monitored, quality_profile_id)
                   VALUES(?,?,?,?,?,?,?,?)""",
                (album_id, title, number, disc, duration, spotify_id,
                 1 if al["monitored"] else 0,
                 al["quality_profile_id"] or default_quality_profile_id(conn)),
            )
            track_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            created += 1
            touched_ids.add(track_id)

        if incoming_ids:
            identity_row = conn.execute(
                "SELECT spotify_id, musicbrainz_id, isrc, external_ids "
                "FROM lib2_tracks WHERE id=?",
                (track_id,),
            ).fetchone()
            stored_ids = parse_external_ids(identity_row["external_ids"])
            for source, value in incoming_ids.items():
                stored_ids.setdefault(source, value)
            conn.execute(
                """UPDATE lib2_tracks
                      SET spotify_id=COALESCE(NULLIF(spotify_id,''), ?),
                          musicbrainz_id=COALESCE(NULLIF(musicbrainz_id,''), ?),
                          isrc=COALESCE(NULLIF(isrc,''), ?),
                          external_ids=?, updated_at=CURRENT_TIMESTAMP
                    WHERE id=?""",
                (
                    incoming_ids.get("spotify"),
                    incoming_ids.get("musicbrainz"),
                    incoming_ids.get("isrc"),
                    json.dumps(stored_ids, sort_keys=True, separators=(",", ":")),
                    track_id,
                ),
            )

        _persist_track_artist_credits(
            conn,
            track_id,
            album_id,
            al["primary_artist_id"],
            entry.get("artist_credits") or entry.get("artists"),
            credit_source=_entry_credit_source(entry),
            may_create_artists=_credits_may_mint_artists(al),
        )
    changed = created + _trim_excess_fileless_tracks(
        conn, album_id, expected, protect_ids=touched_ids
    )
    # Protected local/wanted rows can legitimately extend beyond the provider
    # count. Converge the stored expectation so precache does not retry the
    # same intentional mismatch forever.
    remaining_count = conn.execute(
        "SELECT COUNT(*) FROM lib2_tracks WHERE album_id=?", (album_id,)
    ).fetchone()[0]
    if remaining_count > expected:
        conn.execute(
            """UPDATE lib2_albums
                  SET expected_track_count=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (remaining_count, album_id),
        )
    # Canonical materialization can be the moment an imported release becomes
    # provably complete (every track covered by a file or Wishlist rule).  Keep
    # the derived parent state and every child's wanted projection coherent.
    from core.library2.importer import reconcile_import_monitoring
    reconcile_import_monitoring(conn.cursor(), album_ids=[album_id])
    from core.library2.wanted import recompute_wanted_for_entity
    recompute_wanted_for_entity(conn, "album", album_id)
    return changed


def resolve_tracklist(config_manager, conn, album_id: int) -> Optional[List[dict]]:
    """Return + cache the album's canonical tracklist. None when unavailable.

    **Commits ``conn``.** iss29-D05: this function calls ``conn.commit()``
    several times, and ``conn`` belongs to the CALLER. That is deliberate and
    necessary — it makes provider calls, and holding SQLite's single writer
    across one of those is the deadlock this project has already taken an
    outage on — but it means any uncommitted work the caller was carrying is
    committed here, at a point the caller does not choose.

    All four call sites are safe today only by position: two committed in the
    previous loop iteration, one has written nothing yet, and one owns a
    private connection. A refactor as small as hoisting a monitor UPDATE above
    the resolve call would silently commit a half-applied mutation. Pass a
    connection you are willing to have committed, or use your own.
    ``mirror_tracks_wishlist`` documents the same property for itself.
    """
    context = _album_tracklist_context(conn, album_id)
    if context is None:
        return None
    al, reference, source_ids = context

    from core.library2.provider_snapshots import (
        get_latest_provider_snapshot, record_provider_snapshot)
    snapshot = get_latest_provider_snapshot(
        conn, entity_type="album", entity_id=album_id, scope="tracklist")
    durable_tracks = _snapshot_tracks(snapshot, reference)
    cached: Optional[List[dict]] = None
    if al["tracklist_json"]:
        try:
            parsed = json.loads(al["tracklist_json"])
            if isinstance(parsed, list) and parsed:
                cached = [track for track in parsed if isinstance(track, dict)]
        except (ValueError, TypeError):
            pass
    if cached and snapshot is None:
        # Upgrade path: preserve an existing cache once, but bind it to the
        # current edition reference so a later edition switch invalidates it.
        from core.library2.provider_adapters import TRACKLIST_PARSER_VERSION
        record_provider_snapshot(
            conn,
            provider="legacy-cache",
            entity_type="album",
            entity_id=album_id,
            scope="tracklist",
            parser_version=TRACKLIST_PARSER_VERSION,
            payload={"reference": reference, "tracks": cached},
            is_complete=True,
        )
        durable_tracks = cached
    elif snapshot is not None and durable_tracks is None and cached:
        logger.info(
            "Invalidating tracklist cache for album %s after edition/provider change",
            album_id,
        )
        cached = None
        conn.execute(
            """UPDATE lib2_albums
                  SET tracklist_json=NULL, tracklist_status='idle',
                      tracklist_error=NULL, tracklist_retry_at=NULL
                WHERE id=?""",
            (album_id,),
        )
        conn.commit()

    reusable = durable_tracks or cached
    if reusable:
        # A durable snapshot carries whether the provider answered for the whole
        # release; the legacy-cache upgrade above records itself as complete.
        # Passing it through is what lets an album with a stale, too-high
        # expectation heal on the next reconcile without a provider call.
        reusable_complete = (
            bool(snapshot.is_complete)
            and _from_a_known_release_id(
                source_ids, snapshot.provider, snapshot.provider_entity_id)
            if durable_tracks and snapshot is not None else False
        )
        _persist_tracklist_tracks(
            conn, album_id, reusable, complete=reusable_complete,
        )
        conn.execute(
            """UPDATE lib2_albums
                  SET tracklist_json=?, tracklist_status='ready',
                      tracklist_attempts=0, tracklist_error=NULL,
                      tracklist_retry_at=NULL
                WHERE id=?""",
            (json.dumps(reusable), album_id),
        )
        conn.commit()
        return reusable

    artist = conn.execute(
        "SELECT name FROM lib2_artists WHERE id=?", (al["primary_artist_id"],)
    ).fetchone()
    artist_name = artist["name"] if artist else ""
    # Release the writer before the provider walk. The snapshot bookkeeping
    # above writes without committing, and the walk below is a chain of
    # blocking HTTP calls — holding SQLite's single write lock across them
    # stalled every other request until the 30s busy timeout fired.
    conn.commit()
    from core.library2.provider_adapters import fetch_album_tracklist
    provider_result = fetch_album_tracklist(
        al["title"],
        artist_name,
        source_album_ids=source_ids,
        release_date=reference["release_date"],
        expected_track_count=reference["track_count"],
    )
    if provider_result:
        tracks = provider_result.track_payloads()
        try:
            record_provider_snapshot(
                conn,
                provider=provider_result.provider,
                entity_type="album",
                entity_id=album_id,
                scope="tracklist",
                provider_entity_id=provider_result.provider_entity_id,
                parser_version=provider_result.parser_version,
                payload=provider_result.snapshot_payload(reference),
                is_complete=provider_result.is_complete,
            )
            conn.execute(
                """UPDATE lib2_albums
                      SET tracklist_json=?, tracklist_status='ready',
                          tracklist_attempts=0, tracklist_error=NULL,
                          tracklist_retry_at=NULL
                    WHERE id=?""",
                (json.dumps(tracks), album_id),
            )
            _persist_tracklist_tracks(
                conn, album_id, tracks,
                complete=bool(provider_result.is_complete) and _from_a_known_release_id(
                    source_ids, provider_result.provider,
                    provider_result.provider_entity_id),
            )
            conn.commit()
        except Exception as e:  # noqa: BLE001
            logger.debug("tracklist cache write failed (%s): %s", album_id, e)
        return tracks
    return None


def _partial_album_rows(conn, *, cached: Optional[bool] = None) -> List[Any]:
    """Albums whose expected provider track count is larger than known track rows,
    plus the ones whose size nothing has ever established.

    ``expected_track_count > known`` cannot select a row where the expectation
    is NULL — ``NULL > n`` is NULL — so a release nobody ever asked the provider
    about could never enter the precache. It sat at ``tracklist_status='idle'``
    indefinitely and only revealed its real tracklist when a user opened it,
    which is why a single that turned out to have two tracks looked like a
    one-track single until the click. An unknown size is not completeness; it is
    the thing this pass exists to resolve.

    Scoped to library releases: a discography row exists to be browsed, its size
    being unknown is not a gap in anyone's library, and resolving every one of
    them is a provider-call storm for nothing.
    """
    count_sql = "(SELECT COUNT(*) FROM lib2_tracks t WHERE t.album_id = al.id)"
    unverified_sql = (
        "(COALESCE(al.origin, 'library') = 'library' "
        " AND al.expected_track_count IS NULL)"
    )
    clauses = []
    if cached is True:
        clauses.append(f"al.expected_track_count IS NOT NULL AND al.expected_track_count <> {count_sql}")
        clauses.append("al.tracklist_json IS NOT NULL AND al.tracklist_json <> ''")
    else:
        clauses.append(f"(al.expected_track_count > {count_sql} OR {unverified_sql})")
    if cached is False:
        clauses.append("(al.tracklist_json IS NULL OR al.tracklist_json = '')")
    return conn.execute(
        "SELECT al.id FROM lib2_albums al WHERE " + " AND ".join(clauses) + " ORDER BY al.id"
    ).fetchall()


def _precache_max_workers(config_manager, default: int = 8) -> int:
    """Pool size for the metadata precache stages.

    This used to read ``auto_import.max_workers`` -- the knob that also governs
    DOWNLOAD concurrency -- with a default of 3. That conflation is why nobody
    ever raised it: three workers making one provider call per uncached album
    is `26,310 x 0.5 s / 3` = ~73 minutes on a large migrated library, which is
    the reported "grinds for 30+ minutes", and it was the DEFAULT rather than a
    pathological case (perf-audit PERF-12). Raising it meant also raising how
    many songs download at once, which is a different resource entirely.

    So it gets its own key. ``library_v2.precache_workers`` wins if set;
    otherwise the shared download knob is honoured only when the user raised it
    above this default, so an explicit high setting is never quietly lowered.
    """
    if config_manager is None:
        return default
    try:
        explicit = config_manager.get("library_v2.precache_workers", None)
        if explicit is not None:
            return max(1, int(explicit))
    except Exception as exc:  # noqa: BLE001 - a bad knob must not stop a precache
        logger.debug("precache_workers read failed, using default: %s", exc)
    try:
        shared = int(config_manager.get("auto_import.max_workers", default))
    except Exception:  # noqa: BLE001
        return default
    return max(1, default, shared)


def _resolve_stage(database, config_manager, album_ids: List[int], *,
                    stage: str, progress=None, progress_offset: int = 0,
                    progress_total: Optional[int] = None) -> int:
    """Resolve one precache stage's albums via a bounded ThreadPoolExecutor,
    each worker opening its own connection. Returns count resolved."""
    if not album_ids:
        return 0
    resolved = 0
    done = 0
    lock = threading.Lock()

    def _resolve_one(album_id: int) -> bool:
        try:
            thread_conn = database._get_connection()
        except Exception:  # noqa: BLE001
            return False
        try:
            return bool(resolve_tracklist(config_manager, thread_conn, album_id))
        except Exception as e:  # noqa: BLE001
            logger.debug("tracklist precache resolve failed (%s): %s", album_id, e)
            return False
        finally:
            thread_conn.close()

    max_workers = _precache_max_workers(config_manager)
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="Lib2Tracklist") as executor:
        futures = [executor.submit(_resolve_one, album_id) for album_id in album_ids]
        for future in as_completed(futures):
            if future.result():
                resolved += 1
            with lock:
                done += 1
                if progress and (done % 20 == 0 or done == len(album_ids)):
                    progress(
                        stage,
                        progress_offset + done,
                        progress_total if progress_total is not None else len(album_ids),
                    )
    return resolved


def precache_tracklists(database, config_manager, *, progress=None) -> int:
    """Resolve tracklists for every partial album (expected > present). Background.

    Cached tracklists are materialized first and without provider calls, so rows
    that already have canonical titles immediately become real, monitorable
    missing tracks in Library v2. Each stage's albums resolve through a bounded
    ThreadPoolExecutor (same pattern/config key as ``core.auto_import_worker``)
    instead of one album at a time — the provider-lookup stage in particular
    makes one network call per uncached album.
    """
    resolved = 0
    try:
        conn = database._get_connection()
    except Exception:  # noqa: BLE001
        return 0
    try:
        cached_ids = [r[0] for r in _partial_album_rows(conn, cached=True)]
        uncached_ids = [r[0] for r in _partial_album_rows(conn, cached=False)]
    except Exception as e:  # noqa: BLE001
        logger.debug("tracklist precache error: %s", e)
        return 0
    finally:
        conn.close()

    total = len(cached_ids) + len(uncached_ids)
    if progress:
        progress("tracklists", 0, total)

    try:
        resolved += _resolve_stage(database, config_manager, cached_ids,
                                    stage="tracklists", progress=progress,
                                    progress_offset=0, progress_total=total)
        resolved += _resolve_stage(database, config_manager, uncached_ids,
                                    stage="tracklists", progress=progress,
                                    progress_offset=len(cached_ids), progress_total=total)
    except Exception as e:  # noqa: BLE001
        logger.debug("tracklist precache error: %s", e)
    if progress:
        progress("tracklists", total, total)
    logger.info("Library v2 tracklist precache: %d resolved", resolved)
    return resolved


__all__ = ["resolve_tracklist", "precache_tracklists"]
