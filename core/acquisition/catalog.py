"""Resolve acquisition catalog/policy context from server-owned Library-v2 rows."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from core.acquisition.decision_engine import CatalogContext, EffectivePolicy
from core.acquisition.requests import AcquisitionRequest


def _row_dict(row: Any) -> Dict[str, Any]:
    return dict(row) if row is not None else {}


def resolve_entity_quality_profile(
    conn: Any, scope: str, entity_id: int, *, search_options: Dict[str, Any],
) -> int:
    """Resolve the assigned quality profile without trusting an API payload."""
    if scope == "release_group":
        row = conn.execute(
            "SELECT quality_profile_id FROM lib2_albums WHERE id=?", (entity_id,)
        ).fetchone()
    elif scope == "release_edition":
        row = conn.execute(
            """SELECT al.quality_profile_id
                 FROM lib2_release_editions ed
                 JOIN lib2_albums al ON al.id=ed.release_group_id
                WHERE ed.id=?""",
            (entity_id,),
        ).fetchone()
    elif scope == "recording":
        row = conn.execute(
            """SELECT t.quality_profile_id
                 FROM lib2_recordings rec
                 JOIN lib2_release_tracks rt ON rt.recording_id=rec.id
                 JOIN lib2_tracks t ON t.id=rt.track_id
                WHERE rec.id=? ORDER BY rt.id LIMIT 1""",
            (entity_id,),
        ).fetchone()
    elif scope == "artist_missing":
        row = conn.execute(
            "SELECT quality_profile_id FROM lib2_artists WHERE id=?", (entity_id,)
        ).fetchone()
    elif scope == "upgrade":
        entity_type = str(search_options.get("entity_type") or "").strip().lower()
        if entity_type == "recording":
            return resolve_entity_quality_profile(
                conn, "recording", entity_id, search_options=search_options)
        if entity_type == "release_edition":
            return resolve_entity_quality_profile(
                conn, "release_edition", entity_id, search_options=search_options)
        raise ValueError(
            "upgrade requests require entity_type recording|release_edition")
    else:
        raise ValueError(f"unsupported acquisition scope: {scope}")
    if row is None or row[0] is None:
        raise ValueError("acquisition entity does not exist or has no quality profile")
    return int(row[0])


def resolve_catalog_context(conn: Any, request: AcquisitionRequest) -> CatalogContext:
    """Build identity facts for one request from the current catalog projection."""
    if request.scope == "release_group":
        row = conn.execute(
            """SELECT ar.name AS artist, al.title AS release_title,
                      ed.disambiguation AS edition,
                      COALESCE(ed.track_count, al.expected_track_count,
                               al.track_count) AS track_count
                 FROM lib2_albums al
                 JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                 LEFT JOIN lib2_release_editions ed
                        ON ed.release_group_id=al.id AND ed.is_default=1
                WHERE al.id=?""",
            (request.entity_id,),
        ).fetchone()
    elif request.scope == "release_edition":
        row = conn.execute(
            """SELECT ar.name AS artist, al.title AS release_title,
                      COALESCE(ed.disambiguation, ed.title) AS edition,
                      COALESCE(ed.track_count, al.expected_track_count,
                               al.track_count) AS track_count
                 FROM lib2_release_editions ed
                 JOIN lib2_albums al ON al.id=ed.release_group_id
                 JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                WHERE ed.id=?""",
            (request.entity_id,),
        ).fetchone()
    elif request.scope == "recording":
        row = conn.execute(
            """SELECT ar.name AS artist, rec.title AS release_title,
                      NULL AS edition, 1 AS track_count
                 FROM lib2_recordings rec
                 JOIN lib2_release_tracks rt ON rt.recording_id=rec.id
                 JOIN lib2_tracks t ON t.id=rt.track_id
                 JOIN lib2_albums al ON al.id=t.album_id
                 JOIN lib2_artists ar ON ar.id=al.primary_artist_id
                WHERE rec.id=? ORDER BY rt.id LIMIT 1""",
            (request.entity_id,),
        ).fetchone()
    elif request.scope == "artist_missing":
        row = conn.execute(
            """SELECT name AS artist, NULL AS release_title,
                      NULL AS edition, NULL AS track_count
                 FROM lib2_artists WHERE id=?""",
            (request.entity_id,),
        ).fetchone()
    elif request.scope == "upgrade":
        entity_type = str(
            request.search_options.get("entity_type") or "").strip().lower()
        shadow = AcquisitionRequest(
            **{
                **request.__dict__,
                "scope": entity_type,
            }
        )
        return resolve_catalog_context(conn, shadow)
    else:  # pragma: no cover - request validation owns supported scopes
        row = None
    if row is None:
        raise ValueError("acquisition catalog entity no longer exists")
    data = _row_dict(row)
    from core.acquisition.blocklist import active_blocklisted_dedupe_keys
    return CatalogContext(
        artist=data.get("artist"),
        release_title=data.get("release_title"),
        edition=data.get("edition"),
        track_count=data.get("track_count"),
        any_release_ok=bool(request.search_options.get("any_release_ok", False)),
        blocklisted_dedupe_keys=active_blocklisted_dedupe_keys(conn),
    )


def load_effective_policy(conn: Any, quality_profile_id: int) -> EffectivePolicy:
    row = conn.execute(
        "SELECT * FROM quality_profiles WHERE id=?", (int(quality_profile_id),)
    ).fetchone()
    if row is None:
        raise ValueError("acquisition quality profile no longer exists")
    return EffectivePolicy.from_profile(_row_dict(row))


def resolve_request_context(
    conn: Any, request: AcquisitionRequest,
) -> Tuple[CatalogContext, EffectivePolicy]:
    return (
        resolve_catalog_context(conn, request),
        load_effective_policy(conn, request.quality_profile_id),
    )


__all__ = [
    "load_effective_policy",
    "resolve_catalog_context",
    "resolve_entity_quality_profile",
    "resolve_request_context",
]
