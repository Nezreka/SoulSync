"""Preview/apply adapters over native exact-ID enrichment and provider writes.

Preview stages one native row in memory because the active refresh kernel
commits before provider I/O. Its commits must never commit the caller's work.
Track identity belongs to the native reconciler; overrides belong to the
metadata projection. This module only shapes their answers for the refresh UI.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from contextlib import closing
from typing import Any, Dict, Optional, Tuple

from core.library2 import provider_adapters
from core.library2.metadata_overrides import project_metadata
from core.library2.provider_writes import write_provider_enrichment
from utils.logging_config import get_logger

logger = get_logger("library2.catalogue_refresh")

_TRACK_FIELDS = ("title", "track_number", "disc_number")
_ALBUM_FIELDS = ("title", "release_date", "year")


def album_source(conn, album_id: int, *, source: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Resolve a usable provider and its OWN selected-release ID."""
    ids = provider_adapters.album_refresh_source_ids(conn, album_id)
    order = provider_adapters.configured_entity_source_order("album", ids)
    if source is not None:
        requested = str(source).strip().lower()
        order = (requested,) if requested in order else ()
    return next(((provider, ids[provider]) for provider in order if ids.get(provider)), (None, None))


def _staged_refresh(conn, row, source: str, external_id: str):
    """Run the active album kernel on a scoped native row, with isolated commits."""
    from core.library2.native_enrich import refresh_native_entity_metadata
    from core.metadata.cache import refresh_cached_entity

    with closing(sqlite3.connect(":memory:")) as staged:
        staged.row_factory = sqlite3.Row
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='lib2_albums'").fetchone()[0]
        staged.execute(ddl)
        fields = list(row.keys())
        staged.execute(
            f"INSERT INTO lib2_albums ({','.join(fields)}) VALUES ({','.join('?' for _ in fields)})",
            tuple(row[field] for field in fields),
        )
        with refresh_cached_entity(source, "album", external_id):
            metadata = refresh_native_entity_metadata(staged, "album", row["id"], {source: external_id})
        updated = staged.execute("SELECT * FROM lib2_albums WHERE id=?", (row["id"],)).fetchone()
        return metadata, dict(updated)


def _effective_album(conn, row):
    fields = dict(row)
    overrides = {}
    edition = conn.execute(
        "SELECT id FROM lib2_release_editions WHERE release_group_id=? AND is_default=1", (row["id"],),
    ).fetchone()
    if edition:
        fields, overrides = project_metadata(conn, entity_type="release_edition", entity_id=edition["id"], provider_fields=fields)
    fields, group_overrides = project_metadata(conn, entity_type="release_group", entity_id=row["id"], provider_fields=fields)
    return fields, {**overrides, **group_overrides}


def _change(field, current, proposed, overrides):
    if proposed in (None, "") or str(proposed) == str(current if current is not None else ""):
        return None
    return {"field": field, "current": current, "proposed": proposed, "manual": field in overrides}


def _outcome(status, source=None):
    return {"success": False, "status": status, "source": source,
            "album": None, "tracks": [], "has_manual_conflict": False}


def refresh_preview(conn, album_id: int, *, source: Optional[str] = None) -> Dict[str, Any]:
    """Compute the existing refresh contract without writes or caller commits."""
    from core.library2.importer import dedup_title_key
    from core.library2.track_identity_reconcile import _choose_track
    from core.metadata.cache import refresh_cached_entity

    album_row = conn.execute("SELECT * FROM lib2_albums WHERE id=?", (int(album_id),)).fetchone()
    if album_row is None:
        return _outcome("no_album")
    resolved_source, external_id = album_source(conn, album_id, source=source)
    if resolved_source is None:
        return _outcome("no_source")

    metadata, proposed_album = _staged_refresh(conn, album_row, resolved_source, external_id)
    with refresh_cached_entity(resolved_source, "album", external_id):
        tracklist = provider_adapters.fetch_album_release_tracklist(resolved_source, external_id)
    # An adapter/facade must never smuggle another release's facts into a pin.
    if tracklist is not None and (tracklist.provider != resolved_source or str(tracklist.provider_entity_id) != external_id):
        tracklist = None
    album_effective, album_overrides = _effective_album(conn, album_row)
    album_changes = [] if metadata is None else [
        change for field in _ALBUM_FIELDS
        if (change := _change(field, album_effective.get(field), proposed_album.get(field), album_overrides))
    ]
    rows = [dict(row) for row in conn.execute(
        "SELECT * FROM lib2_tracks WHERE album_id=? ORDER BY COALESCE(disc_number,1), track_number, id", (int(album_id),),
    )]
    source_tracks = tracklist.tracks if tracklist else ()
    local_counts = Counter(dedup_title_key(row["title"]) for row in rows)
    provider_counts = Counter(dedup_title_key(track.title) for track in source_tracks)
    used = set()
    matched = {}
    for provider_track in source_tracks:
        target = _choose_track(rows, provider_track, provider=resolved_source,
                               provider_title_counts=provider_counts, local_title_counts=local_counts,
                               used_track_ids=used)
        if target is not None:
            used.add(target["id"])
            matched[target["id"]] = provider_track
    tracks = []
    for row in rows:
        effective, overrides = project_metadata(conn, entity_type="track", entity_id=row["id"], provider_fields=row)
        provider_track = matched.get(row["id"])
        changes = [] if provider_track is None else [
            change for field in _TRACK_FIELDS
            if (change := _change(field, effective.get(field), getattr(provider_track, field), overrides))
        ]
        tracks.append({"track_id": row["id"], "title": effective.get("title"),
                       "track_number": effective.get("track_number"), "matched": provider_track is not None,
                       "changes": changes})
    return {"success": True, "status": "planned", "source": resolved_source,
            "album": {"album_id": album_row["id"], "changes": album_changes}, "tracks": tracks,
            "has_manual_conflict": any(c["manual"] for c in album_changes) or any(c["manual"] for t in tracks for c in t["changes"])}


def apply_refresh(conn, album_id: int, *, source: Optional[str] = None,
                  overwrite_manual: bool = False) -> Dict[str, Any]:
    """Apply protected proposals through the shared writer; caller commits."""
    from core.library2.metadata_overrides import clear_field_override, get_field_overrides

    plan = refresh_preview(conn, album_id, source=source)
    stats = {"status": plan["status"], "source": plan.get("source"),
             "tracks_updated": 0, "album_updated": 0, "kept_manual": 0}
    if not plan["success"]:
        return stats

    def apply(kind, entity_id, changes):
        allowed = {}
        override_kind = "release_group" if kind == "album" else "track"
        for change in changes:
            field = change["field"]
            if change["manual"]:
                if not overwrite_manual:
                    stats["kept_manual"] += 1
                    continue
                if kind == "album":
                    edition = conn.execute("SELECT id FROM lib2_release_editions WHERE release_group_id=? AND is_default=1", (entity_id,)).fetchone()
                    if edition and field in get_field_overrides(conn, entity_type="release_edition", entity_id=edition["id"]):
                        clear_field_override(conn, entity_type="release_edition", entity_id=edition["id"], field_name=field)
                clear_field_override(conn, entity_type=override_kind, entity_id=entity_id, field_name=field)
            allowed[field] = change["proposed"]
        if allowed:
            write_provider_enrichment(conn, entity_type=kind, entity_id=entity_id, service=plan["source"], columns=allowed)
        return bool(allowed)

    for track in plan["tracks"]:
        stats["tracks_updated"] += int(apply("track", track["track_id"], track["changes"]))
    stats["album_updated"] = int(apply("album", album_id, plan["album"]["changes"]))
    return stats


def fill_album_metadata_gaps(conn, album_id: int) -> Optional[str]:
    """Fill empty album facts using the same exact-ID refresh and service policy.

    Unmatched releases still use native identity resolution. Once any edition
    is known, gap fill never searches for a replacement release in another
    provider; only that edition's qualified IDs are eligible.
    """
    from core.library2.native_enrich import enrich_native_entity_for_service

    ids = provider_adapters.album_refresh_source_ids(conn, album_id)
    for source in provider_adapters.configured_entity_source_order("album", ids):
        if ids and source not in ids:
            continue
        try:
            if not ids:
                result = enrich_native_entity_for_service(conn, "album", album_id, source)
                if result.get("success"):
                    conn.commit()
                    return str(result.get("source") or source)
                conn.rollback()
                continue
            row = conn.execute("SELECT * FROM lib2_albums WHERE id=?", (int(album_id),)).fetchone()
            metadata, proposed = _staged_refresh(conn, row, source, ids[source])
            _effective, overrides = _effective_album(conn, row)
            fields = ("title", "image_url", "genres", "year", "release_date", "label", "upc", "style", "mood", "explicit")
            missing = {field: proposed[field] for field in fields
                       if field not in overrides and row[field] in (None, "", "[]", "{}") and proposed[field] not in (None, "", "[]", "{}")}
            if row["art_locked"]:
                missing.pop("image_url", None)
            elif not row["image_url"] and "image_url" not in overrides and "image_url" not in missing:
                artist = conn.execute("SELECT name FROM lib2_artists WHERE id=?", (row["primary_artist_id"],)).fetchone()
                artwork = provider_adapters.fetch_artwork_url(
                    "album", artist_name=artist["name"] if artist else "", album_title=row["title"],
                    source_ids={source: ids[source]}, source_order=(source,), allow_search=False,
                )
                if artwork and str(artwork.provider_entity_id) == ids[source]:
                    missing["image_url"] = artwork.url
            if not missing:
                continue
            write_provider_enrichment(conn, entity_type="album", entity_id=album_id, service=source, columns=missing)
            return source
        except Exception as exc:  # noqa: BLE001 - providers fail independently
            conn.rollback()
            logger.debug("gap fill %s album %s failed: %s", source, album_id, exc)
            # A failed provider must not starve a later configured provider.
            continue
    return None


__all__ = ["album_source", "apply_refresh", "refresh_preview", "fill_album_metadata_gaps"]
