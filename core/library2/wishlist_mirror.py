"""Mirror Library-v2 track monitoring into the legacy Wishlist.

Shared by the Library v2 API (monitor toggles, bulk monitor, profile assigns,
manual upgrade scan) and the periodic ``monitoring_list_reconcile`` repair job
— one implementation so the queueing rules can't drift. There used to be a
second one: a dedicated upgrade job that queued the same candidates on its own
cadence, which is exactly the drift this module exists to prevent.

Key contract: ``add_to_wishlist(quality_profile_id=…)`` carries the app-wide
quality profile onto the wishlist row, which every pipeline stage resolves
live (``core/quality/selection.load_profile_by_id``). Under an upgrade policy
(``acceptable``/``until_top``/``until_cutoff``) a track that already HAS a file is only
queued when its file is a genuine upgrade candidate.
"""

from __future__ import annotations

import threading
from contextlib import closing
from typing import Any, Dict, List, Optional

from utils.logging_config import get_logger

logger = get_logger("library2.wishlist_mirror")


def _hand_tagged(conn, path: Any, keys: Optional[set] = None) -> bool:
    """Is this the file the user tagged by hand ("tag it yourself")? Its
    release is deliberately not the service's; an upgrade would fetch the
    studio cut and replace it."""
    if not path:
        return False
    from database.music_database import MusicDatabase
    key = MusicDatabase.manual_path_key(path)
    if keys is not None:
        return key in keys
    try:
        return conn.execute("SELECT 1 FROM manual_metadata_files WHERE path_key=?",
                            (key,)).fetchone() is not None
    except Exception:  # noqa: BLE001 - old schema: nothing is hand-tagged
        return False


def _explicit(value: Any) -> Optional[bool]:
    """the stored flag as a bool, None when the catalogue doesn't know."""
    if value is None or value == "":
        return None
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return None


def library_album_identity(conn, album_id: int, musicbrainz_id: Any) -> Dict[str, Any]:
    """what keeps a release apart from a same-named one at download time:
    the musicbrainz release id the folder lookup checks, the default
    edition's disambiguation (#1299 puts it on the folder) and disc count."""
    identity: Dict[str, Any] = {}
    if musicbrainz_id:
        identity["musicbrainz_release_id"] = str(musicbrainz_id)
    try:
        row = conn.execute(
            """SELECT disambiguation, disc_count FROM lib2_release_editions
                WHERE release_group_id = ?
                ORDER BY is_default DESC, id LIMIT 1""", (album_id,)).fetchone()
    except Exception:  # noqa: BLE001 - an install without editions yet
        row = None
    if row is not None:
        if str(row[0] or "").strip():
            identity["disambiguation"] = str(row[0]).strip()
        try:
            discs = int(row[1] or 0)
        except (TypeError, ValueError):
            discs = 0
        if discs > 1:
            identity["total_discs"] = discs
    return identity


def library_album_artist_names(conn, album_id: int, primary_artist_id: int) -> List[str]:
    """the album's own artists: its main artist, then a joint album's other
    headliners (never a featured guest). the download names the album folder
    and spots a various artists release from these; without them a wanted
    track of a compilation or a split release was filed under the track's
    own artist."""
    names: List[str] = []
    for row in conn.execute(
            """SELECT ar.name FROM lib2_artists ar WHERE ar.id = ?
               UNION ALL
               SELECT ar.name FROM lib2_album_artists aa
                 JOIN lib2_artists ar ON ar.id = aa.artist_id
                WHERE aa.album_id = ? AND aa.artist_id <> ?
                  AND COALESCE(aa.role, 'primary') IN ('primary', 'various')""",
            (primary_artist_id, album_id, primary_artist_id)):
        name = str(row[0] or "").strip()
        if name and name.casefold() not in {n.casefold() for n in names}:
            names.append(name)
    return names


def track_wishlist_payload(conn, track_id: int) -> Optional[Dict[str, Any]]:
    """Build the wishlist payload for a lib2 track (or None when unknown).

    "Has a file" and the file an upgrade is measured against are the ones of
    the library the caller works in (#1199): another library's copy neither
    satisfies this one nor gets replaced for it.
    """
    from core.library2.release_dates import album_release_date
    from core.library2.recording_links import owned_by_recording_sql
    from core.library2.sql_util import owner_clause
    t = conn.execute(
        """SELECT t.id AS track_id, t.spotify_id, t.musicbrainz_id,
                  t.external_ids, t.isrc, t.title, t.track_number,
                  t.disc_number, t.duration, t.explicit AS track_explicit,
                  t.recording_disambiguation,
                  al.explicit AS album_explicit,
                  al.id AS album_id, al.title album_title,
                  al.image_url album_image_url,
                  al.spotify_id album_spotify,
                  al.musicbrainz_id album_musicbrainz,
                  al.external_ids album_external_ids,
                  al.track_count, al.expected_track_count, al.album_type,
                  al.release_date album_release_date, al.year album_year,
                  al.primary_artist_id album_primary_artist_id,
                  EXISTS(SELECT 1 FROM lib2_track_files tf
                         WHERE tf.track_id = t.id
                           AND tf.path IS NOT NULL AND tf.path <> ''
                           AND COALESCE(tf.file_state,'active')
                               NOT IN ('missing_confirmed','deleted'){owner}) has_file,
                  {owned_elsewhere} AS owned_elsewhere
           FROM lib2_tracks t JOIN lib2_albums al ON al.id = t.album_id
           WHERE t.id = ?""".format(owner=owner_clause(column="tf.owner_profile_id"),
                                    owned_elsewhere=owned_by_recording_sql(conn, "t")),
        (track_id,),
    ).fetchone()
    if not t:
        return None
    artist_rows = conn.execute(
        """SELECT ar.name, ar.spotify_id, ar.musicbrainz_id, ar.external_ids
           FROM lib2_track_artists ta JOIN lib2_artists ar ON ar.id = ta.artist_id
           WHERE ta.track_id = ? ORDER BY ta.position""", (track_id,)
    ).fetchall()
    album_artist_names = library_album_artist_names(
        conn, t["album_id"], t["album_primary_artist_id"])
    from core.library2.provider_ids import (
        preferred_provider_identity,
        provider_only,
        source_ids_from_values,
    )
    from core.metadata.registry import get_primary_source, get_source_priority
    source_order = tuple(get_source_priority(get_primary_source()))

    track_provider_ids = provider_only(source_ids_from_values(
        spotify_id=t["spotify_id"],
        musicbrainz_id=t["musicbrainz_id"],
        external_ids=t["external_ids"],
        isrc=t["isrc"],
    ))
    album_provider_ids = provider_only(source_ids_from_values(
        spotify_id=t["album_spotify"],
        musicbrainz_id=t["album_musicbrainz"],
        external_ids=t["album_external_ids"],
    ))
    source_provider, provider_track_id = preferred_provider_identity(
        track_provider_ids, source_order,
    )
    artists = []
    for row in artist_rows:
        ids = provider_only(source_ids_from_values(
            spotify_id=row["spotify_id"],
            musicbrainz_id=row["musicbrainz_id"],
            external_ids=row["external_ids"],
        ))
        artists.append({"name": row["name"], "provider_ids": ids})
    # Provider-less rows use the persisted stable_id, never the rowid: a
    # library reset + reimport reproduces the same stable_id, so existing
    # wishlist rows keep matching instead of orphaning or double-queueing
    # against fresh rowids (audit P1-12).
    from core.library2.wishlist_art import album_images
    from core.library2.stable_ids import ensure_album_stable_id, ensure_track_stable_id
    source_track_id = provider_track_id or (
        f"lib2-track:{ensure_track_stable_id(conn, t['track_id'])}"
    )
    _album_provider, preferred_album_id = preferred_provider_identity(
        album_provider_ids, source_order,
    )
    source_album_id = (
        album_provider_ids.get(source_provider or "")
        or preferred_album_id
        or f"lib2-album:{ensure_album_stable_id(conn, t['album_id'])}"
    )
    # The PRIMARY file (ADR-03) is what upgrade decisions are made against —
    # never an arbitrary sibling copy of the recording.
    from core.library2.track_files import primary_file_row
    file_info = primary_file_row(conn, track_id, scoped=True)
    from core.library2.quality_eval import audio_quality_from_file, effective_track_profile
    profile_info = effective_track_profile(conn, track_id)
    original_quality = audio_quality_from_file(file_info)

    from core.library2.quality_eval import is_upgrade_policy
    # the same song already on disk under another release (the single of an
    # album track) is owned: Missing and the wanted queue say so, and the
    # wishlist must not download a second copy of it either
    should_queue = not bool(t["has_file"]) and not bool(t["owned_elsewhere"])
    quality_evaluation = "not_applicable"
    if t["has_file"] and _hand_tagged(conn, (file_info or {}).get("path")):
        quality_evaluation = "hand_tagged"
    elif t["has_file"] and is_upgrade_policy(profile_info["upgrade_policy"]):
        try:
            from core.library2.quality_eval import evaluate_file, profile_targets
            targets, upgrade_policy, cutoff = profile_targets(profile_info)
            evaluation = evaluate_file(file_info, targets, upgrade_policy, cutoff)
            candidate = evaluation["upgrade_candidate"]
            quality_evaluation = (
                "unknown" if candidate is None
                else "upgrade_candidate" if candidate
                else "satisfied"
            )
            # Unknown quality must enter the existing probe/upgrade pipeline;
            # silently treating it as satisfied would suppress re-evaluation.
            should_queue = candidate is not False
        except Exception as e:  # noqa: BLE001
            logger.debug("quality-profile upgrade check failed (track %s): %s", track_id, e)
            should_queue = False
            quality_evaluation = "error"

    return {
        "id": source_track_id, "name": t["title"],
        "provider": source_provider or "library_v2",
        "source": source_provider or "library_v2",
        "provider_ids": track_provider_ids,
        "artists": artists,
        "album": {
            "name": t["album_title"],
            "id": source_album_id,
            "provider_ids": album_provider_ids,
            "total_tracks": t["expected_track_count"] or t["track_count"] or 1,
            "album_type": t["album_type"],
            # the download names the album folder from this ([$year]). it
            # had none, so every wanted track landed in a yearless twin of
            # its album's folder (SeadogsBooty, Nov 5)
            "release_date": album_release_date(t["album_release_date"], t["album_year"]),
            "artists": [{"name": name} for name in album_artist_names],
            # $explicit / $contentrating in the naming template read these
            # (#1860); without them a wanted track lost its [E] and split off
            "explicit": _explicit(t["album_explicit"]),
            **library_album_identity(conn, t["album_id"], t["album_musicbrainz"]),
            # The wishlist UI reads `album.images[0].url`, and the import
            # pipeline reads the same slot for cover.jpg / embedded art. This
            # payload carried no `images` at all, so every Library-v2 row
            # rendered as a blank tile (373 of 611 rows in the production
            # report) and reached the importer with no cover to work from.
            # `album_images` puts a real provider CDN url first when the
            # catalogue has one and always appends Library v2's own artwork
            # endpoint, so a row is never image-less again.
            "images": album_images(
                conn, t["album_id"], stored_image_url=t["album_image_url"],
            ),
        },
        "track_number": t["track_number"],
        "disc_number": t["disc_number"],
        "duration_ms": t["duration"] or 0,
        "explicit": _explicit(t["track_explicit"]),
        # the recording comment ("acoustic", "live") a single's filename
        # carries (#1536), and the recording's own ids for the match checks
        **{key: value for key, value in (
            ("disambiguation", str(t["recording_disambiguation"] or "").strip()),
            ("musicbrainz_recording_id", str(t["musicbrainz_id"] or "").strip()),
            ("isrc", str(t["isrc"] or "").strip()),
        ) if value},
        "quality_profile_id": profile_info["id"],
        "quality_profile": profile_info,
        "_album_type": t["album_type"],
        "_has_file": bool(t["has_file"]),
        "_should_queue": should_queue,
        "_source_album_id": source_album_id,
        "_source_info": {
            "source": "library_v2",
            "lib2_track_id": t["track_id"],
            "lib2_album_id": t["album_id"],
            "quality_profile_id": profile_info["id"],
            "quality_profile_name": profile_info["name"],
            "quality_profile_source": profile_info["source"],
            "quality_profile_source_id": profile_info["source_id"],
            "upgrade_policy": profile_info["upgrade_policy"],
            "upgrade_check": bool(t["has_file"]),
            "quality_evaluation": quality_evaluation,
            "original_file_path": file_info.get("path") if file_info else None,
            "original_file_id": file_info.get("id") if file_info else None,
            "original_quality": original_quality.label() if original_quality else None,
            "metadata_source": source_provider,
            "track_provider_ids": track_provider_ids,
            "album_provider_ids": album_provider_ids,
        },
    }


def fill_catalogue_durations(db, tracks: List[Dict[str, Any]]) -> None:
    """Give queued download tracks without a length the catalogue's.

    A queue row can lose it (a failed attempt re-adds the row from the
    download's own track data). With no length the import skips its duration
    check, and the studio cut was imported for the slowed edit.
    """
    import json
    missing = [t for t in tracks if not t.get("duration_ms")]
    if not missing:
        return
    def lib2_id(track):
        info = track.get("source_info")
        try:
            info = json.loads(info) if isinstance(info, str) else info
            return int((info or {}).get("lib2_track_id") or 0) or None
        except (ValueError, TypeError, AttributeError):
            return None
    keys = [(t, lib2_id(t), str(t.get("id") or "").split("::", 1)[0]) for t in missing]
    ids = [k[1] for k in keys if k[1]] or [0]
    pids = [k[2] for k in keys if k[2]] or [""]
    with closing(db._get_connection()) as conn:
        rows = conn.execute(
            f"""SELECT id, spotify_id, duration FROM lib2_tracks WHERE duration > 0
                AND (id IN ({",".join("?" * len(ids))}) OR spotify_id IN ({",".join("?" * len(pids))}))""",
            (*ids, *pids)).fetchall()
    by_id = {r[0]: r[2] for r in rows}
    by_pid = {r[1]: r[2] for r in rows if r[1]}
    for track, tid, pid in keys:
        duration = by_id.get(tid) or by_pid.get(pid)
        if duration:
            track["duration_ms"] = duration
            if isinstance(track.get("track_data"), dict):
                track["track_data"]["duration_ms"] = duration


def track_direct_download_payload(conn, track_id: int) -> Optional[Dict[str, Any]]:
    """Build one transient pipeline input for a scoped track search.

    This intentionally does *not* write a Wishlist row.  It adapts the same
    server-resolved payload the Wishlist mirror uses into the shape consumed by
    ``run_full_missing_tracks_process`` so Automatic Search can run one track
    through the established candidate/retry/import pipeline without turning a
    one-shot user action into persistent monitoring state.
    """
    payload = track_wishlist_payload(conn, track_id)
    if not payload or not payload.get("_should_queue"):
        return None

    source_info = dict(payload.get("_source_info") or {})
    track_data = {
        key: value for key, value in payload.items()
        if not key.startswith("_")
    }
    album = dict(track_data.get("album") or {})
    artists = list(track_data.get("artists") or [])
    artist = artists[0] if artists else {"name": "Unknown Artist"}
    if not isinstance(artist, dict):
        artist = {"name": str(artist)}

    # The download worker consumes these top-level fields.  ``spotify_data``
    # remains a compatibility name for a provider-neutral, Spotify-shaped
    # payload; the provider/source fields inside it remain authoritative.
    return {
        "track_id": track_data.get("id"),
        "spotify_track_id": track_data.get("id"),
        "track_data": track_data,
        "spotify_data": track_data,
        "source_info": source_info,
        "quality_profile_id": track_data.get("quality_profile_id"),
        "id": track_data.get("id"),
        "name": track_data.get("name"),
        "artists": artists,
        "album": album,
        "duration_ms": track_data.get("duration_ms", 0),
        "track_number": track_data.get("track_number"),
        "disc_number": track_data.get("disc_number"),
        "_explicit_album_context": album,
        "_explicit_artist_context": artist,
        "_is_explicit_album_download": True,
        "_lib2_direct_search": True,
    }


def mirror_tracks_wishlist(db, conn, track_ids: List[int], monitored: bool,
                           *, profile_id: int = 1,
                           user_initiated: bool = False) -> int:
    """Add/remove the given lib2 tracks to/from the legacy Wishlist.

    Outbox-backed (audit P0-04): the intents are enqueued on ``conn``,
    committed, and drained against the legacy tables. A failing legacy write
    stays visible and retryable in ``lib2_mirror_outbox`` instead of being
    swallowed. Returns how many of THIS call's intents completed.

    NOTE: commits ``conn`` — callers must not be mid-transaction with
    unrelated pending writes (all current callers invoke this after their
    own commit; ``lib2_set_monitored`` enqueues inline instead, so its flag
    write and outbox rows share one transaction).

    ``profile_id`` is the legacy per-user profile scope of the wishlist, NOT a
    quality profile — the quality profile travels per item via
    ``quality_profile_id`` on the payload.

    ``user_initiated`` must only be True for a DIRECT user action on that
    specific track (the track-level monitor toggle). It bypasses the wishlist
    ignore-list AND clears a stale ignore. Cascades (album/artist toggles,
    bulk monitor, profile assignment) and scheduled jobs (upgrade scan,
    discography auto-monitor) must leave it False so a deliberate user
    cancel/remove keeps sticking (audit P1-11).
    """
    from core.library2.mirror_outbox import drain, enqueue_tracks

    outbox_ids = enqueue_tracks(conn, track_ids, monitored,
                                profile_id=profile_id,
                                user_initiated=user_initiated)
    if not outbox_ids:
        return 0
    conn.commit()
    drain(db, ids=outbox_ids)
    return _done_count(conn, outbox_ids)


def mirror_projected_tracks_wishlist(
    db,
    conn,
    track_ids: List[int],
    *,
    profile_id: int = 1,
    user_initiated: bool = False,
) -> int:
    """Mirror current wanted projection states for the requested tracks."""
    from core.library2.mirror_outbox import drain, enqueue_projected_tracks
    outbox_ids = enqueue_projected_tracks(
        conn,
        track_ids,
        profile_id=profile_id,
        user_initiated=user_initiated,
    )
    if not outbox_ids:
        return 0
    conn.commit()
    drain(db, ids=outbox_ids)
    return _done_count(conn, outbox_ids)


def upgrade_candidate_track_ids(conn, *, profile_id: int = 1,
                                track_ids: Optional[List[int]] = None,
                                quality_profile: Optional[Dict[str, Any]] = None) -> List[int]:
    """Wanted tracks with files whose profile permits upgrades -- or, given
    ``track_ids``, exactly those whatever their monitoring (Enhance Quality).
    ``quality_profile`` judges every track against that one profile instead
    of its own (Enhance Quality's "upgrade to").
    The per-track upgrade re-check happens in
    ``mirror_tracks_wishlist`` (only genuine candidates queue)."""
    import json
    from core.library2.wanted import PROJECTION_VERSION
    from core.library2.track_files import primary_order
    from core.library2.sql_util import owner_clause, separated
    from core.library_scope import library_scope_for_profile
    # the files of the library this profile's wishes fill (#1199)
    library = separated(library_scope_for_profile(profile_id))
    scope, scope_params = (
        ("JOIN lib2_wanted_tracks wt ON wt.track_id=t.id AND wt.profile_id=? AND wt.wanted=1"
         " WHERE wt.projection_version=?", (int(profile_id), PROJECTION_VERSION))
        if track_ids is None else
        ("WHERE t.id IN (SELECT value FROM json_each(?))",
         (json.dumps([int(t) for t in track_ids]),)))
    rows = conn.execute(
        f"""SELECT t.id,
                  (SELECT tf.path FROM lib2_track_files tf
                    WHERE tf.track_id=t.id AND tf.path IS NOT NULL AND tf.path<>''
                      AND COALESCE(tf.file_state,'active')
                          NOT IN ('missing_confirmed','deleted')
                      {owner_clause(library, column="tf.owner_profile_id")}
                    ORDER BY {primary_order('tf')} LIMIT 1) AS path
             FROM lib2_tracks t
           {scope}
            AND EXISTS (SELECT 1 FROM lib2_track_files f
                         WHERE f.track_id=t.id AND f.path IS NOT NULL AND f.path<>''
                           AND COALESCE(f.file_state,'active')
                               NOT IN ('missing_confirmed','deleted')
                           {owner_clause(library, column="f.owner_profile_id")})""",
        scope_params,
    ).fetchall()
    from core.library2.manual_skips import active_skip_paths
    from core.library2.quality_eval import effective_track_profile, is_upgrade_policy
    protected = active_skip_paths(
        conn, ("quality", "bit_depth"), profile_id=profile_id
    )
    try:
        hand_tagged = {r[0] for r in conn.execute("SELECT path_key FROM manual_metadata_files")}
    except Exception:  # noqa: BLE001 - old schema: nothing is hand-tagged
        hand_tagged = set()
    from core.library2.quality_eval import evaluate_file, profile_targets
    from core.library2.track_files import primary_file_rows
    primaries = primary_file_rows(conn, [int(row["id"]) for row in rows])
    targets_by_profile: Dict[Any, tuple] = {}
    candidates = set()
    for row in rows:
        if row["path"] in protected or _hand_tagged(conn, row["path"], hand_tagged):
            continue
        try:
            profile = quality_profile or effective_track_profile(conn, int(row["id"]))
        except (LookupError, ValueError):
            continue
        if not is_upgrade_policy(profile.get("upgrade_policy")):
            continue
        # Same verdict the payload reaches, from the stored quality columns:
        # a file that already meets its cutoff is no candidate (unknown is).
        try:
            key = (profile.get("id"), profile.get("ranked_targets"), profile.get("upgrade_policy"),
                   profile.get("upgrade_cutoff_index"))
            if key not in targets_by_profile:
                targets_by_profile[key] = profile_targets(profile)
            targets, policy, cutoff = targets_by_profile[key]
            verdict = evaluate_file(primaries.get(int(row["id"])), targets, policy, cutoff)
        except Exception:  # noqa: BLE001 - let the payload's own check decide
            verdict = {"upgrade_candidate": None}
        if verdict.get("upgrade_candidate") is not False:
            candidates.add(int(row["id"]))
    return sorted(candidates)


def _done_count(conn, outbox_ids: List[int]) -> int:
    """How many of ``outbox_ids`` the drain finished, in chunks: a profile
    refresh enqueues one row per wanted track, past SQLite's variable limit."""
    done = 0
    for start in range(0, len(outbox_ids), 900):
        chunk = outbox_ids[start:start + 900]
        marks = ",".join("?" for _ in chunk)
        row = conn.execute(
            f"SELECT COUNT(*) FROM lib2_mirror_outbox WHERE id IN ({marks}) AND status='done'",
            chunk).fetchone()
        done += int(row[0]) if row else 0
    return done


# a full projection walk, committed per chunk so the write lock is held a
# chunk at a time instead of for the whole library (iss32-M05)
_REFRESH_BATCH = 2000
# the Quality page's routes hand the refresh to a thread (RUN_INLINE: tests)
RUN_INLINE = False
_refresh_lock = threading.Lock()
_refresh_running: set = set()
_refresh_again: set = set()


def schedule_quality_profile_refresh(db, quality_profile_id: int, *, profile_id: int = 1) -> None:
    """Refresh the wishlist for an edited profile off the request thread.

    Saving, applying, renaming or deleting a profile used to run the whole
    projection walk and a wishlist payload per wanted track inside the HTTP
    request: minutes on a big library, with the write lock held. Asks while
    one is running for the same profile fold into one more pass after it."""
    key = (int(quality_profile_id), int(profile_id))
    if RUN_INLINE:
        refresh_quality_profile_wishlist(db, key[0], profile_id=key[1])
        return
    with _refresh_lock:
        if key in _refresh_running:
            _refresh_again.add(key)
            return
        _refresh_running.add(key)

    def _run():
        while True:
            try:
                refresh_quality_profile_wishlist(db, key[0], profile_id=key[1])
            except Exception:  # noqa: BLE001 - a background refresh logs, it never raises
                logger.exception("quality profile %s wishlist refresh failed", key[0])
            with _refresh_lock:
                if key in _refresh_again:
                    _refresh_again.discard(key)
                    continue
                _refresh_running.discard(key)
                return

    threading.Thread(target=_run, daemon=True, name="quality-profile-refresh").start()


def refresh_quality_profile_wishlist(db, quality_profile_id: int, *,
                                     profile_id: int = 1) -> int:
    """Re-evaluate every Library-v2 track using an edited profile."""
    from core.library2.wanted import recompute_wanted

    with closing(db._get_connection()) as conn:
        recompute_wanted(conn, profile_id=profile_id, batch_size=_REFRESH_BATCH,
                         on_batch=lambda _done, _total: conn.commit())
        rows = conn.execute(
            "SELECT track_id, wanted FROM lib2_wanted_tracks "
            "WHERE profile_id=? AND effective_profile_id=?",
            (int(profile_id), int(quality_profile_id)),
        ).fetchall()
        affected = {int(r[0]) for r in rows}
        track_ids = {int(r[0]) for r in rows if r[1]}
        from core.library2.monitor_sync import _wishlisted_lib2_track_ids
        track_ids.update(affected & set(
            _wishlisted_lib2_track_ids(conn, profile_id=profile_id)))
        conn.commit()
        return mirror_projected_tracks_wishlist(
            db, conn, sorted(track_ids), profile_id=profile_id, user_initiated=False)


__all__ = [
    "mirror_projected_tracks_wishlist",
    "mirror_tracks_wishlist",
    "track_direct_download_payload",
    "track_wishlist_payload",
    "upgrade_candidate_track_ids",
    "refresh_quality_profile_wishlist",
    "schedule_quality_profile_refresh",
]
