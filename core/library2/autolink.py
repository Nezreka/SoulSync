"""Auto-link freshly imported downloads into the Library v2 tables.

Called from the post-processing side effects (``core/imports/side_effects.py``)
once a download has its final processed path. Best-effort and strictly additive:
it never raises into the pipeline and follows the native catalogue cutover.

This closes the wanted-loop: monitor a discography release → tracks mirror into
the wishlist → the download pipeline fetches a file → the file appears in
Library v2 immediately (no full re-import needed).

Matching prefers existing rows (including fileless rows materialized from a
provider tracklist — attaching a file to one flips it from "missing" to
"present") and only creates artist/album/track rows when genuinely new.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from utils.logging_config import get_logger

from .importer import (
    dedup_title_key,
    looks_like_foreign_provider_id,
    normalize_name,
    release_title_key,
)
from .path_drift import split_track_numbering
from .tag_cache import read_tag_snapshot

logger = get_logger("library2.autolink")

# Sentinel used by the same-named legacy/provider fallbacks across the app;
# reusing the string keeps a later real match able to fold these rows away.
UNKNOWN_ARTIST = "Unknown Artist"

_PROVIDER_ID_PLACEHOLDERS = {
    "auto_import", "explicit_album", "explicit_artist", "from_sync_modal",
    "library_v2", "staging", "wishlist_album",
}
_PROVIDER_ID_PLACEHOLDER_PREFIXES = ("lib2-album:", "lib2-artist:", "lib2-track:")


def _clean_provider_id(value: Any) -> Optional[str]:
    """Return a real provider id, never an internal compatibility token."""
    text = str(value or "").strip()
    lowered = text.casefold()
    if (not text or lowered in _PROVIDER_ID_PLACEHOLDERS
            or lowered.startswith(_PROVIDER_ID_PLACEHOLDER_PREFIXES)):
        return None
    return text


def _metadata_source(*values: Any) -> Optional[str]:
    for value in values:
        source = str(value or "").strip().lower()
        if source and source not in _PROVIDER_ID_PLACEHOLDERS:
            return source
    return None


def _get(ti: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        val = ti.get(key)
        if isinstance(val, dict):
            val = val.get("name")
        if val:
            return str(val)
    return ""


def _primary_artist_name(ti: Dict[str, Any]) -> str:
    artists = ti.get("artists")
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict) and first.get("name"):
            return str(first["name"])
        if isinstance(first, str) and first:
            return first
    return _get(ti, "artist")


def _primary_artist_provider_id(ti: Dict[str, Any]) -> Optional[str]:
    # `artists[0]["id"]` is populated by every client with its provider-local
    # id. §62.4: the namespace decision (spotify column vs. external_ids vs.
    # match-only) lives in `_provider_namespace`, driven by ti["provider"] and
    # the id's shape — non-Spotify ids never reach the spotify_id column.
    artists = ti.get("artists")
    if isinstance(artists, list) and artists:
        first = artists[0]
        if isinstance(first, dict) and first.get("id"):
            return str(first["id"])
    return None


def _provider_namespace(provider_id: Optional[str],
                        source: Optional[str]) -> Optional[str]:
    """Which external-id namespace an incoming id belongs to.

    - a provider marker is authoritative for its own id;
    - an unmarked id is used for compatibility matching only (``None``),
      never guessed into a namespace;
    - even an explicitly Spotify-marked id must pass the shape guard (numeric
      = Deezer/iTunes, UUID = MusicBrainz — §62.4's poison).
    """
    provider_id = _clean_provider_id(provider_id)
    if not provider_id:
        return None
    src = str(source or "").strip().lower() or None
    if src and src != "spotify":
        return src
    if src != "spotify":
        return None
    if looks_like_foreign_provider_id(provider_id):
        return None
    return "spotify"


def _row_external_ids(raw: Any) -> Dict[str, str]:
    if isinstance(raw, dict):
        value = raw
    else:
        try:
            value = json.loads(raw or "{}")
        except (TypeError, ValueError):
            return {}
    if not isinstance(value, dict):
        return {}
    return {
        str(source).strip().lower(): str(pid).strip()
        for source, pid in value.items()
        if str(source).strip() and str(pid).strip()
    }


def _qualified_provider_id(source: Optional[str], *mappings: Any) -> Optional[str]:
    if not source:
        return None
    for raw in mappings:
        provider_id = _clean_provider_id(_row_external_ids(raw).get(source))
        if provider_id:
            return provider_id
    return None


def _adopt_external_id(conn, table: str, row_id: int, namespace: str,
                       provider_id: str) -> None:
    """setdefault-style: record the id under its namespace, never overwrite."""
    provider_id = _clean_provider_id(provider_id)
    if not provider_id:
        return
    row = conn.execute(
        f"SELECT external_ids FROM {table} WHERE id=?", (row_id,)).fetchone()
    if row is None:
        return
    ids = _row_external_ids(row["external_ids"])
    if ids.get(namespace):
        return
    ids[namespace] = provider_id
    conn.execute(
        f"UPDATE {table} SET external_ids=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
        (json.dumps(ids, sort_keys=True, separators=(",", ":")), row_id))


def _find_or_create_artist(conn, name: str, *, spotify_id: Optional[str] = None,
                           source: Optional[str] = None,
                           create: bool = True) -> Optional[int]:
    """``create=False`` makes this a pure lookup: no id backfill, no INSERT,
    ``None`` when the artist has no catalogue row yet. ldp-02's discovery mode
    needs to ask "do we already have this artist?" on a plain GET without
    turning a read into a write."""
    # ID match first: cheap (indexed) and — unlike name matching — survives
    # name-spelling variants of the same provider identity (e.g. a kanji vs.
    # romaji release credit), the case G8's alias-awareness gap calls out.
    provider_id = _clean_provider_id(spotify_id)
    namespace = _provider_namespace(provider_id, source)
    if provider_id:
        if namespace == "spotify":
            row = conn.execute(
                "SELECT id FROM lib2_artists WHERE spotify_id = ? LIMIT 1",
                (provider_id,)).fetchone()
            if row:
                return row["id"]
        elif namespace is not None:
            # A real qualified id wins over the old poisoned-column fallback.
            for candidate in conn.execute(
                    "SELECT id, external_ids FROM lib2_artists WHERE external_ids LIKE ?",
                    (f"%{provider_id}%",)):
                ids = _row_external_ids(candidate["external_ids"])
                if ids.get(namespace) == provider_id:
                    return candidate["id"]
            row = conn.execute(
                "SELECT id FROM lib2_artists WHERE spotify_id = ? LIMIT 1",
                (provider_id,),
            ).fetchone()
            if row:
                return row["id"]
        else:
            # Unknown namespaces may only hit the legacy poisoned column;
            # scanning every external_ids namespace can cross-link providers
            # that happen to use the same opaque id.
            row = conn.execute(
                "SELECT id FROM lib2_artists WHERE spotify_id = ? LIMIT 1",
                (provider_id,),
            ).fetchone()
            if row:
                return row["id"]

    key = normalize_name(name)
    if not key:
        return None
    # iss29-D13: an index seek on the stored normalized key. The previous
    # "fast path" was `WHERE lower(name) = ?`, which EXPLAIN reports as SCAN —
    # no index can cover the expression — and SQLite's `lower()` is ASCII-only,
    # so Cyrillic/Greek/Turkish names missed it and paid a second, Python-side
    # scan of the whole table on top. Measured at 100k artists: ~169 ms per
    # finished download, against ~0.004 ms here.
    row = conn.execute(
        "SELECT id FROM lib2_artists WHERE name_key = ? LIMIT 1", (key,)
    ).fetchone()
    if row is None:
        # Backstop for rows no keyed write path produced (direct SQL inserts,
        # ad-hoc repair). Scoped to exactly those, so the index answers it as a
        # seek over an empty set on a migrated library — and the startup
        # backfill hands any stragglers a key, keeping it that way.
        for candidate in conn.execute(
                "SELECT id, name FROM lib2_artists WHERE name_key IS NULL"):
            if normalize_name(candidate["name"]) == key:
                row = candidate
                break
    if row is not None:
        if not create:
            return row["id"]
        if namespace == "spotify":
            # Backfill so the next finished download for this artist can take
            # the indexed ID path above instead of falling through to here.
            conn.execute(
                "UPDATE lib2_artists SET spotify_id=?, updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND spotify_id IS NULL", (provider_id, row["id"]))
        elif namespace is not None:
            _adopt_external_id(conn, "lib2_artists", row["id"], namespace, provider_id)
        return row["id"]

    if not create:
        return None

    from core.library2.profile_lookup import default_quality_profile_id
    from core.library2.monitor_sync import artist_is_watchlisted
    external_json = (json.dumps({namespace: provider_id})
                     if namespace not in (None, "spotify") else "{}")
    provider_ids = {namespace: provider_id} if namespace and provider_id else {}
    monitored = int(artist_is_watchlisted(conn, name, provider_ids, profile_id=1))
    cur = conn.execute(
        "INSERT INTO lib2_artists(name, name_key, sort_name, spotify_id, external_ids, "
        "quality_profile_id, monitored) VALUES(?, ?, ?, ?, ?, ?, ?)",
        (name, key, name, provider_id if namespace == "spotify" else None,
         external_json, default_quality_profile_id(conn), monitored))
    return cur.lastrowid


def _find_or_create_album(conn, artist_id: int, title: str, *,
                          album_type: str, spotify_album_id: Optional[str] = None,
                          source: Optional[str] = None,
                          monitored: Optional[int] = None) -> int:
    provider_id = _clean_provider_id(spotify_album_id)
    namespace = _provider_namespace(provider_id, source)
    key = release_title_key(title)
    # dd28-09: ``_find_or_create_artist`` happily resolves an ALIAS row, but
    # the album lookup used to scope to that one artist id. A download booked
    # on the rōmaji spelling while the album sits under the kanji canonical
    # therefore created a SECOND lib2_albums row under the alias: the artist
    # page showed the album twice, the file landed on the duplicate, and the
    # original stayed fileless-but-monitored, so the wanted projection kept
    # proposing it for download forever. Searching the whole alias group is
    # exactly what the alias feature (§24/§40) exists for.
    from core.library2.artist_aliases import resolve_alias_group
    scope_ids = resolve_alias_group(conn, artist_id) or [artist_id]
    placeholders = ",".join("?" for _ in scope_ids)
    rows = conn.execute(
        f"""SELECT DISTINCT al.id, al.title, al.spotify_id, al.external_ids
            FROM lib2_album_artists aa
            JOIN lib2_albums al ON al.id = aa.album_id
            WHERE aa.artist_id IN ({placeholders})""",
        tuple(scope_ids),
    ).fetchall()
    if provider_id:
        if namespace is not None and namespace != "spotify":
            for row in rows:
                if _row_external_ids(row["external_ids"]).get(namespace) == provider_id:
                    return row["id"]
        for row in rows:
            if row["spotify_id"] == provider_id:
                return row["id"]
    for row in rows:
        if release_title_key(row["title"]) == key:
            if namespace is not None and namespace != "spotify" and provider_id:
                _adopt_external_id(conn, "lib2_albums", row["id"], namespace,
                                   provider_id)
            return row["id"]
    # New albums inherit the artist's quality-profile assignment (cascade),
    # mirroring what the explicit assign endpoint does.
    from core.library2.profile_lookup import default_quality_profile_id
    artist_profile = conn.execute(
        "SELECT quality_profile_id FROM lib2_artists WHERE id=?", (artist_id,)
    ).fetchone()
    profile_id = ((artist_profile["quality_profile_id"] if artist_profile else None)
                  or default_quality_profile_id(conn))
    external_json = (json.dumps({namespace: provider_id})
                     if namespace not in (None, "spotify") else "{}")
    cur = conn.execute(
        """INSERT INTO lib2_albums(primary_artist_id, title, album_type, spotify_id,
               external_ids, quality_profile_id, monitored)
           VALUES(?,?,?,?,?,?, COALESCE(?, 1))""",
        (artist_id, title, album_type,
         provider_id if namespace == "spotify" else None,
         external_json, profile_id, monitored),
    )
    album_id = cur.lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO lib2_album_artists(album_id, artist_id, role) "
        "VALUES(?,?, 'primary')", (album_id, artist_id))
    return album_id


def _find_or_create_track(conn, album_id: int, artist_id: int, title: str, *,
                          track_number: Optional[int],
                          spotify_track_id: Optional[str] = None,
                          disc_number: Optional[int] = None,
                          source: Optional[str] = None,
                          monitored: Optional[int] = None) -> int:
    provider_id = _clean_provider_id(spotify_track_id)
    namespace = _provider_namespace(provider_id, source)
    key = dedup_title_key(title)
    rows = conn.execute(
        "SELECT id, title, track_number, disc_number, spotify_id, external_ids "
        "FROM lib2_tracks WHERE album_id=?",
        (album_id,),
    ).fetchall()
    if provider_id and namespace not in (None, "spotify"):
        for row in rows:
            if _row_external_ids(row["external_ids"]).get(namespace) == provider_id:
                return row["id"]
    if provider_id:
        for row in rows:
            if row["spotify_id"] == provider_id:
                return row["id"]
    for row in rows:
        # dedup_title_key (§39) drops feat.-annotations so a finished
        # download's title matches a fileless wanted-row that spells the
        # credit differently — without it, a bare exact-title match misses
        # this (the most common real-world case) and creates a duplicate
        # track row whose wanted-row keeps re-downloading forever (G4).
        if dedup_title_key(row["title"]) == key:
            if namespace == "spotify" and provider_id and not row["spotify_id"]:
                conn.execute(
                    "UPDATE lib2_tracks SET spotify_id=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?", (provider_id, row["id"]),
                )
            elif namespace not in (None, "spotify") and provider_id:
                _adopt_external_id(
                    conn, "lib2_tracks", row["id"], namespace, provider_id,
                )
            return row["id"]
    if track_number is not None:
        wanted_disc = disc_number if disc_number is not None else 1
        for row in rows:
            row_disc = row["disc_number"] if row["disc_number"] is not None else 1
            if row["track_number"] == track_number and row_disc == wanted_disc:
                return row["id"]
    from core.library2.profile_lookup import default_quality_profile_id
    album_profile = conn.execute(
        "SELECT quality_profile_id FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()
    profile_id = ((album_profile["quality_profile_id"] if album_profile else None)
                  or default_quality_profile_id(conn))
    cur = conn.execute(
        """INSERT INTO lib2_tracks(album_id, title, track_number, disc_number,
               spotify_id, external_ids, quality_profile_id, monitored)
           VALUES(?,?,?,?,?,?,?, COALESCE(?, 1))""",
        (
            album_id,
            title,
            track_number,
            disc_number if disc_number is not None else 1,
            provider_id if namespace == "spotify" else None,
            json.dumps({namespace: provider_id})
            if namespace not in (None, "spotify") else "{}",
            profile_id,
            monitored,
        ),
    )
    track_id = cur.lastrowid
    conn.execute(
        "INSERT OR IGNORE INTO lib2_track_artists(track_id, artist_id, role, position) "
        "VALUES(?,?, 'primary', 0)", (track_id, artist_id))
    # dd28-10: without an edition row the new track is invisible to every
    # edition-scoped consumer (bundle matching, acquisition catalog) until the
    # next schema-ensure backfill happens to run — and that backfill then pins
    # it to the default edition permanently.
    try:
        from core.library2.editions import attach_track_to_edition
        attach_track_to_edition(conn, track_id)
    except Exception as exc:  # noqa: BLE001 - never fail track creation
        logger.debug("edition attachment failed (track %s): %s", track_id, exc)
    return track_id


# Public aliases for reuse outside this module (§52.8 early materialization,
# core/library2/materialize.py) — same resolve-or-create semantics the
# post-download autolink path above already relies on.
find_or_create_artist = _find_or_create_artist
find_or_create_album = _find_or_create_album
find_or_create_track = _find_or_create_track


def _acoustid_status_for(raw: Any) -> Optional[str]:
    """Map the pipeline's raw AcoustID outcome to the schema's narrower
    ``acoustid_status`` vocabulary. 'disabled'/'error'/unset make no claim
    either way (None) — a hard FAIL never reaches here: it quarantines the
    file and returns before this callback runs."""
    return raw if raw in ("pass", "skip") else None


def _pipeline_result_json(context: Dict[str, Any]) -> str:
    """Deep-dive A7/C4: compact per-file detail that has no dedicated column
    — the AcoustID reason and whether a quality-profile fallback (downsample /
    lossy copy) fired for this file. Built from context keys the pipeline
    already sets; empty when none apply so most rows stay `'{}'`."""
    result: Dict[str, Any] = {}
    message = context.get("_acoustid_message")
    if message:
        result["acoustid_message"] = str(message)
    # The recording identity the import-time check judged, so a later scan can
    # recognise the same recording instead of re-arguing about title/artist
    # strings it reads from different sources than the download did.
    mbids = context.get("_acoustid_recording_mbids")
    if mbids:
        result["acoustid_recording_mbids"] = [str(m) for m in mbids if m]
    version = context.get("_version_mismatch_fallback")
    if version:
        result["version_mismatch_fallback"] = str(version)
    fallbacks = [
        name for name, key in (
            ("downsample", "_quality_fallback_downsample"),
            ("lossy_copy", "_quality_fallback_lossy_copy"),
        ) if context.get(key)
    ]
    if fallbacks:
        result["quality_fallback"] = fallbacks
    return json.dumps(result) if result else "{}"


def _warm_new_artwork(database, conn, album_id: Optional[int]) -> None:
    """Queue artwork for the album this download landed in and its artist."""
    if not album_id:
        return
    try:
        from core.settings import config_manager
        from core.library2.artwork import schedule_missing_artwork
        targets = [("album", int(album_id))]
        row = conn.execute(
            "SELECT primary_artist_id FROM lib2_albums WHERE id=?", (album_id,)
        ).fetchone()
        if row and row["primary_artist_id"]:
            targets.append(("artist", int(row["primary_artist_id"])))
        schedule_missing_artwork(database, config_manager, targets)
    except Exception as e:  # noqa: BLE001
        logger.debug("autolink artwork warm-up skipped: %s", e)


def _split_artist_title(stem: str) -> tuple[Optional[str], str]:
    """Read ``Artist - Title`` out of a bare filename stem.

    A leading track/disc numbering is peeled first, so ``01 - Song`` yields a
    title and no artist rather than an artist literally called "01".
    """
    _number, rest = split_track_numbering(stem)
    rest = rest.strip()
    for separator in (" - ", " – ", " — "):
        if separator in rest:
            left, right = rest.split(separator, 1)
            left, right = left.strip(), right.strip()
            if left and right:
                return left, right
    return None, rest


def _fallback_identity(context: Dict[str, Any],
                       file_path: str) -> Optional[Dict[str, Any]]:
    """Best-effort identity for a download that carried no track metadata.

    Simple Downloads (``search_result.is_simple_download``) never have a
    ``track_info`` with title/artist and never a Library-v2 entity id, so
    autolink used to skip them entirely: the file imported, the legacy history
    recorded it, and lib2 never learned it existed — which is precisely what
    later made the orphan detector flag a perfectly legitimate file
    (issues §7).  The 26 July 2026 decision is to materialize instead of
    skipping, so this derives an identity from what the file itself says:

    1. the embedded tags the import pipeline just wrote — ground truth;
    2. the download's own filename, parsed as ``Artist - Title``;
    3. the bare filename stem under :data:`UNKNOWN_ARTIST`, because a visible
       row with a weak name is still better than an invisible file.

    Returns ``None`` only when there is no filename at all to work from.
    """
    tags: Dict[str, Any] = {}
    try:
        tags = read_tag_snapshot(file_path) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("fallback tag read failed (%s): %s", file_path, exc)
    title = str(tags.get("title") or "").strip()
    artist = str(tags.get("artist") or tags.get("album_artist") or "").strip()
    album = str(tags.get("album") or "").strip()

    if not title or not artist:
        search_result = context.get("search_result") or {}
        name = ""
        if isinstance(search_result, dict):
            name = str(search_result.get("filename") or "").strip()
        name = os.path.basename((name or str(file_path or "")).replace("\\", "/"))
        stem = os.path.splitext(name)[0].strip()
        if not stem:
            return None
        parsed_artist, parsed_title = _split_artist_title(stem)
        title = title or parsed_title or stem
        artist = artist or parsed_artist or UNKNOWN_ARTIST

    if not title:
        return None
    return {
        "title": title,
        "artist": artist,
        "album": album or None,
        "track_number": tags.get("track_number"),
        "disc_number": tags.get("disc_number"),
    }


def _link_companion_file(
    conn,
    track_id: int,
    file_path: str,
    *,
    derived_from_file_id: Optional[int] = None,
    acquired_quality_json: Optional[str] = None,
    retention_json: Optional[str] = None,
) -> Optional[int]:
    """Record an extra on-disk file that belongs to an already-linked track.

    A generated lossy companion is a derivative of the retained acquisition,
    not an unrelated duplicate.  Persisting that relationship is what lets
    the UI group versions honestly and prevents maintenance from treating it
    as an upgrade candidate of its own.  Idempotent per ``(track_id, path)``;
    does not commit.
    """
    if not file_path or not os.path.exists(file_path):
        return None
    existing = conn.execute(
        "SELECT id FROM lib2_track_files WHERE track_id=? AND path=?",
        (track_id, file_path),
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE lib2_track_files
                  SET file_role='derivative',
                      derived_from_file_id=COALESCE(?, derived_from_file_id),
                      acquired_quality_json=COALESCE(?, acquired_quality_json),
                      retention_json=COALESCE(?, retention_json),
                      updated_at=CURRENT_TIMESTAMP
                WHERE id=?""",
            (derived_from_file_id, acquired_quality_json, retention_json,
             existing["id"]),
        )
        return existing["id"]
    fmt = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else None
    bitrate = sample_rate = bit_depth = tier = None
    try:
        from core.imports.file_ops import probe_audio_quality
        from core.library2.status import quality_tier
        quality = probe_audio_quality(file_path)
        if quality:
            fmt = quality.format or fmt
            bitrate = quality.bitrate
            sample_rate = quality.sample_rate
            bit_depth = quality.bit_depth
            tier = quality_tier(fmt, bitrate, bit_depth)
    except Exception as exc:  # noqa: BLE001
        logger.debug("companion quality probe failed (%s): %s", file_path, exc)
    try:
        size = os.path.getsize(file_path)
    except OSError:
        size = None
    cur = conn.execute(
        """INSERT INTO lib2_track_files(track_id, path, size, bitrate, sample_rate,
               bit_depth, format, quality_tier, source, import_status,
               file_role, derived_from_file_id, acquired_quality_json,
               retention_json)
           VALUES(?,?,?,?,?,?,?,?,'companion','imported','derivative',?,?,?)""",
        (track_id, file_path, size, bitrate, sample_rate, bit_depth, fmt, tier,
         derived_from_file_id, acquired_quality_json, retention_json),
    )
    return cur.lastrowid


def link_download_into_library_v2(context: Dict[str, Any], *,
                                  raise_on_error: bool = False) -> Optional[int]:
    """Link a finished download's file into ``lib2_*``. Returns the file-row id.

    Idempotent: an existing path on the same track is updated, not duplicated.
    Background repair callers keep the historical best-effort default;
    completion paths pass ``raise_on_error=True`` so an import cannot be
    reported successful while its Library-v2 row is missing.
    """
    try:
        from core.settings import config_manager
        # The `library_v2_enabled(config_manager)` call that followed this
        # import was a side-effect-only no-op: the function returns True
        # unconditionally (the cutover is not reversible through config) and
        # its one-time deprecation warning only ever fires when the key is set
        # FALSY -- which on a default install it never is, so the flag stays
        # unset and the call paid for a config read forever, on a path that
        # runs per completed download. One call at boot
        # (core/library2/bootstrap.py) delivers 100% of the warning.
        # The import itself stays: `config_manager` is used further down.

        file_path = context.get("_final_processed_path") or context.get("_final_path")
        if not file_path:
            return None

        # Consume the same source-neutral import contract as every legacy
        # writer.  Auto Import deliberately keeps the canonical release and
        # provider at context level (historically under ``spotify_album`` /
        # ``spotify_artist`` even for non-Spotify providers); treating the
        # narrower track payload as authoritative creates one album per track
        # and guesses foreign ids into Spotify columns.
        from core.imports.context import (
            get_import_context_album,
            get_import_context_artist,
            get_import_source,
            get_import_source_ids,
        )

        raw_track_info = context.get("track_info")
        has_track_info = isinstance(raw_track_info, dict) and bool(raw_track_info)
        ti = raw_track_info if has_track_info else (context.get("search_result") or {})
        if not isinstance(ti, dict):
            ti = {}
        canonical_album = get_import_context_album(context)
        canonical_artist = get_import_context_artist(context)
        source_ids = get_import_source_ids(context)

        # A grab that started from Library v2 carries the server-resolved
        # entity (audit P1-16) — the file links to that exact row, no
        # heuristic re-matching. Scheduled Wishlist downloads carry the same
        # ids in source_info; that object survives into this pipeline context.
        lib2_ctx = context.get("lib2_entity") or ti.get("lib2_entity") or {}
        if not isinstance(lib2_ctx, dict):
            lib2_ctx = {}
        from core.downloads.origin import _parse_source_info
        source_info = _parse_source_info(ti.get("source_info"))
        nested_track = ti.get("track_data") or ti.get("spotify_data") or {}
        if not isinstance(nested_track, dict):
            nested_track = {}
        identity_source = _metadata_source(
            ti.get("provider"), ti.get("source"), nested_track.get("provider"),
            nested_track.get("source"), source_info.get("metadata_source"),
            context.get("provider"), get_import_source(context),
        )
        direct_track_id = lib2_ctx.get("track_id") or source_info.get("lib2_track_id")
        direct_album_id = lib2_ctx.get("album_id") or source_info.get("lib2_album_id")

        title = _get(ti, "name", "title")
        artist_name = _primary_artist_name(ti) or _get(canonical_artist, "name")
        fallback: Dict[str, Any] = {}
        if not direct_track_id and not direct_album_id and (not title or not artist_name):
            # issues §7 / status §18: materialize instead of skipping. Without
            # a catalogue row the file is invisible to every native subject
            # query — including the orphan detector, which then reports a file
            # the user legitimately downloaded.
            derived = _fallback_identity(context, file_path)
            if derived is None:
                return None
            fallback = derived
            title = title or fallback["title"]
            artist_name = artist_name or fallback["artist"]
        # Context-level album metadata is canonical.  In particular, the
        # AutoImportWorker puts only ``album_id`` on each track and keeps the
        # real title/type/total under ``spotify_album``.
        album_name = (
            _get(canonical_album, "name", "title")
            or _get(ti, "album")
            or fallback.get("album")
            or title
        )

        embedded = context.get("_embedded_id_tags") or {}
        embedded_spotify_id = str(embedded.get("SPOTIFY_TRACK_ID") or "") or None
        # On the fallback path ``ti`` IS the raw search result, whose ``id`` is
        # the *source's* result id, not a music-provider identity — adopting it
        # would write a Soulseek/usenet token into a provider namespace (the
        # §62.4 poisoning the guide forbids). An embedded Spotify tag read off
        # the file itself is a real qualified identity and still counts.
        is_simple_download = bool(ti.get("is_simple_download"))
        # Unqualified ids are still useful to match pre-existing/poisoned
        # compatibility rows, but `_provider_namespace` will never persist
        # them without an authoritative source.
        use_provider_ids = bool(not fallback and not is_simple_download)
        qualified_track_id = _qualified_provider_id(
            identity_source, source_info.get("track_provider_ids"),
            ti.get("provider_ids"), nested_track.get("provider_ids"),
        )
        spotify_track_id = embedded_spotify_id or (
            qualified_track_id or _clean_provider_id(source_ids.get("track_id"))
            if use_provider_ids
            else None
        )
        album_raw = ti.get("album") if isinstance(ti.get("album"), dict) else {}
        qualified_album_id = _qualified_provider_id(
            identity_source, source_info.get("album_provider_ids"),
            canonical_album.get("provider_ids"), canonical_album.get("external_ids"),
            album_raw.get("provider_ids"), album_raw.get("external_ids"),
        )
        spotify_album_id = (
            qualified_album_id
            or _clean_provider_id(source_ids.get("album_id") or album_raw.get("id"))
            if use_provider_ids
            else None
        )
        total_tracks = canonical_album.get("total_tracks") or album_raw.get("total_tracks")
        album_type = str(
            canonical_album.get("album_type") or album_raw.get("album_type") or ""
        ).lower() or (
            "single" if (normalize_name(album_name) == normalize_name(title)
                         or total_tracks in (1, "1")) else "album")
        track_number = ti.get("track_number") or fallback.get("track_number")
        try:
            track_number = int(track_number) if track_number else None
        except (TypeError, ValueError):
            track_number = None
        disc_number = ti.get("disc_number") or fallback.get("disc_number")
        try:
            disc_number = int(disc_number) if disc_number else None
        except (TypeError, ValueError):
            disc_number = None

        from database.music_database import get_database
        db = get_database()
        conn = db._get_connection()
        try:
            track_id = album_id = None
            track_identity_source = "spotify" if embedded_spotify_id else identity_source
            if direct_track_id:
                row = conn.execute(
                    "SELECT id, album_id FROM lib2_tracks WHERE id=?",
                    (direct_track_id,)).fetchone()
                if row:
                    track_id, album_id = row["id"], row["album_id"]
            elif direct_album_id:
                row = conn.execute(
                    "SELECT id, primary_artist_id FROM lib2_albums WHERE id=?",
                    (direct_album_id,)).fetchone()
                if row and title:
                    album_id = row["id"]
                    track_id = _find_or_create_track(
                        conn, album_id, row["primary_artist_id"], title,
                        track_number=track_number, spotify_track_id=spotify_track_id,
                        disc_number=disc_number, source=track_identity_source)
            if track_id is None:
                # Entity gone or absent — heuristic name matching as before.
                if not title or not artist_name:
                    return None
                artists = ti.get("artists") if isinstance(ti.get("artists"), list) else []
                primary_artist = artists[0] if artists and isinstance(artists[0], dict) else {}
                qualified_artist_id = _qualified_provider_id(
                    identity_source, canonical_artist.get("provider_ids"),
                    canonical_artist.get("external_ids"), primary_artist.get("provider_ids"),
                    primary_artist.get("external_ids"),
                )
                artist_id = _find_or_create_artist(
                    conn,
                    artist_name,
                    spotify_id=(
                        qualified_artist_id
                        or _clean_provider_id(source_ids.get("artist_id"))
                        or _primary_artist_provider_id(ti)
                        or None
                    ) if use_provider_ids else None,
                    source=identity_source,
                )
                if artist_id is None:
                    return None
                # A derived identity is an observation, never an acquisition
                # intent: rows minted from tags/filename start unmonitored so
                # a guessed "Unknown Artist" title can't enter the wanted
                # projection and send the pipeline hunting for it. Matching an
                # existing row leaves that row's own monitoring untouched.
                derived_monitored = 0 if fallback else None
                album_id = _find_or_create_album(
                    conn, artist_id, album_name,
                    album_type=album_type, spotify_album_id=spotify_album_id,
                    source=identity_source, monitored=derived_monitored)
                track_id = _find_or_create_track(
                    conn, album_id, artist_id, title,
                    track_number=track_number, spotify_track_id=spotify_track_id,
                    disc_number=disc_number, source=track_identity_source,
                    monitored=derived_monitored)

            fmt = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else None
            bitrate = sample_rate = bit_depth = None
            tier = None
            try:
                from core.imports.file_ops import probe_audio_quality
                from core.library2.status import quality_tier
                quality = probe_audio_quality(file_path)
                if quality:
                    fmt = quality.format or fmt
                    bitrate = quality.bitrate
                    sample_rate = quality.sample_rate
                    bit_depth = quality.bit_depth
                    tier = quality_tier(fmt, bitrate, bit_depth)
            except Exception as e:  # noqa: BLE001
                logger.debug("autolink quality probe failed (%s): %s", file_path, e)
            try:
                size = os.path.getsize(file_path)
            except OSError:
                size = None
            source = str(context.get("username") or "") or None
            # Deep-dive A7/C4: the ONE callback every finished download (grabbed
            # via wishlist, manual search, or watchlist) passes through — the
            # verification badge and AcoustID/quality-fallback detail were
            # computed upstream this same pipeline run but never made it onto
            # the file row for autolink-created files, leaving the Info-tab
            # lifecycle UI permanently empty for "the normal case today".
            verification_status = context.get("_verification_status")
            acoustid_status = _acoustid_status_for(context.get("_acoustid_result"))
            pipeline_result_json = _pipeline_result_json(context)
            from core.quality.model import AudioQuality
            from core.quality.retention import (
                ACQUIRED_QUALITY_CONTEXT_KEY,
                RETENTION_CONTEXT_KEY,
                quality_json,
                transforms_json,
            )
            raw_acquired = context.get(ACQUIRED_QUALITY_CONTEXT_KEY)
            try:
                acquired_quality_json = quality_json(
                    AudioQuality.from_dict(raw_acquired)
                    if isinstance(raw_acquired, dict) else raw_acquired
                )
            except (AttributeError, TypeError, ValueError):
                acquired_quality_json = None
            retention_json = transforms_json(context.get(RETENTION_CONTEXT_KEY))
            provenance_present = (
                raw_acquired is not None
                or isinstance(context.get(RETENTION_CONTEXT_KEY), list)
            )
            destructive_retention = any(
                isinstance(step, dict) and bool(step.get("source_replaced"))
                for step in (context.get(RETENTION_CONTEXT_KEY) or [])
            )
            main_file_role = "derivative" if destructive_retention else "master"

            existing = conn.execute(
                "SELECT id, COALESCE(file_state,'active') AS file_state"
                "  FROM lib2_track_files WHERE track_id=? AND path=?",
                (track_id, file_path),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE lib2_track_files SET size=COALESCE(?, size),
                           bitrate=COALESCE(?, bitrate), sample_rate=COALESCE(?, sample_rate),
                           bit_depth=COALESCE(?, bit_depth), format=COALESCE(?, format),
                           quality_tier=COALESCE(?, quality_tier),
                           verification_status=COALESCE(?, verification_status),
                           acoustid_status=COALESCE(?, acoustid_status),
                           pipeline_result_json=?,
                           file_role=CASE WHEN ? THEN ? ELSE file_role END,
                           derived_from_file_id=CASE WHEN ? THEN NULL
                                                    ELSE derived_from_file_id END,
                           acquired_quality_json=CASE WHEN ? THEN ?
                                                      ELSE acquired_quality_json END,
                           retention_json=CASE WHEN ? THEN ? ELSE retention_json END,
                           updated_at=CURRENT_TIMESTAMP WHERE id=?""",
                    (size, bitrate, sample_rate, bit_depth, fmt, tier,
                     verification_status, acoustid_status, pipeline_result_json,
                     provenance_present, main_file_role,
                     provenance_present,
                     provenance_present, acquired_quality_json,
                     provenance_present, retention_json,
                     existing["id"]),
                )
                file_id = existing["id"]
                # FI-02: a re-import onto a path whose row was retired (deleted
                # by the file-delete flow, or marked missing by a scan) updated
                # the quality columns and left the row retired. The bytes were
                # at the destination, but the registration gate found no active
                # file, `primary_file_row` did not read the row at all, and the
                # library scan excluded it — while the exception recovery took
                # the returned id as proof of success. `retire_replaced_files`
                # skips the keep_path and the primary triggers only choose
                # among live rows, so nothing else brings it back. The other
                # writer (`media_server_sync._upsert_file`) has always
                # reactivated here; this is the same rule for the writer every
                # non-SoulSync install actually uses.
                if str(existing["file_state"] or "active") != "active":
                    from core.library2.track_files import set_file_state
                    conn.execute(
                        "UPDATE lib2_track_files"
                        "   SET missing_since=NULL, missing_scan_count=0"
                        " WHERE id=?", (file_id,))
                    set_file_state(conn, int(file_id), "active")
                    logger.info(
                        "Library v2: reactivated file row %s for re-imported "
                        "path %s (was %s)",
                        file_id, file_path, existing["file_state"])
            else:
                cur = conn.execute(
                    """INSERT INTO lib2_track_files(track_id, path, size, bitrate,
                           sample_rate, bit_depth, format, quality_tier, source,
                           verification_status, acoustid_status, pipeline_result_json,
                           file_role, acquired_quality_json, retention_json,
                           import_status)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'imported')""",
                    (track_id, file_path, size, bitrate, sample_rate, bit_depth,
                     fmt, tier, source,
                     verification_status, acoustid_status, pipeline_result_json,
                     main_file_role, acquired_quality_json, retention_json),
                )
                file_id = cur.lastrowid
            # dd28-40: a retained lossless original next to a generated lossy
            # copy is a second file of the SAME track, not an orphan.
            for companion in (context.get("_companion_file_paths") or []):
                try:
                    _link_companion_file(
                        conn,
                        track_id,
                        str(companion),
                        derived_from_file_id=file_id,
                        acquired_quality_json=acquired_quality_json,
                        retention_json=retention_json,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("companion file link failed (%s): %s", companion, exc)
            # dd28-08: an upgrade/enhance/redownload writes a NEW path and
            # deletes the old file. Without this, the stale row stayed
            # is_primary/active and every lib2 read path kept working against a
            # file that no longer exists.
            try:
                from core.library2.track_files import retire_replaced_files
                replaced = context.get("_replaced_file_paths") or []
                retire_replaced_files(
                    conn, track_id,
                    keep_path=file_path,
                    removed_paths=replaced if isinstance(replaced, (list, tuple, set)) else [],
                    config_manager=config_manager,
                )
            except Exception as exc:  # noqa: BLE001 - never fail the link
                logger.debug("replaced-file retirement failed (track %s): %s", track_id, exc)
            # The album now owns a real file — a provider-only discography row
            # must graduate to the library, or "My Library" (which filters on
            # origin/monitored) would hide an album whose file exists.
            conn.execute(
                "UPDATE lib2_albums SET origin='library', updated_at=CURRENT_TIMESTAMP "
                "WHERE id=? AND origin='discography'", (album_id,))
            # Heuristic auto-link can create a catalog track outside importer/
            # tracklist flows; materialize its wanted state before commit so
            # projection consumers never silently miss the new row.
            #
            # SYNC-04: `recompute_wanted`'s `profile_id` is the USER profile
            # that owns the monitoring intent — `ADMIN_PROFILE_ID`, what every
            # regular Library-v2 consumer reads. It was being handed the
            # default QUALITY profile id, a different namespace entirely. With a
            # default quality profile of anything but 1 the new track got its
            # projection filed under a profile nobody queries: the admin status
            # reported it missing, `track_wanted_states` raised "stale", and
            # `list_cutoff_unmet` never saw an MP3 that needed upgrading. The
            # quality profile stays where it belongs — in the quality cascade
            # `effective_profile_id` resolves.
            from core.library2 import ADMIN_PROFILE_ID
            from core.library2.wanted import recompute_wanted
            recompute_wanted(conn, profile_id=ADMIN_PROFILE_ID,
                             track_ids=[track_id])
            conn.commit()
            # perf25-04: an artist/album born from a finished download is not
            # covered by the last precache run, so warm its artwork now instead
            # of leaving the first browse on the cold path.
            _warm_new_artwork(db, conn, album_id)
            logger.info("Library v2 auto-linked download: %s → track %s (file %s)",
                        os.path.basename(str(file_path)), track_id, file_id)
            return file_id
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.debug("library v2 autolink failed: %s", e)
        if raise_on_error:
            raise
        return None


__all__ = [
    "find_or_create_album",
    "find_or_create_artist",
    "find_or_create_track",
    "link_download_into_library_v2",
]
