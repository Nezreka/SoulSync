"""Shadow adapter from Library-v2 wanted projection to acquisition requests.

ADR-02 keeps the legacy Wishlist operational during the measured cutover. This
adapter creates durable Phase-4 requests from ``lib2_wanted_tracks`` without
dispatching them, so parity can be observed before source-of-truth cutover.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence, Tuple

from core.acquisition.requests import (
    AcquisitionRequest,
    create_request,
    transition_request,
)


@dataclass(frozen=True)
class WantedRequest:
    track_id: int
    recording_id: int
    request: AcquisitionRequest
    created: bool

    def to_dict(self) -> dict:
        return {
            "track_id": self.track_id,
            "recording_id": self.recording_id,
            "created": self.created,
            "request": self.request.to_dict(),
        }


def _request_key(row: Any) -> str:
    identity = "\x1f".join((
        "wanted-missing",
        str(row["profile_id"]),
        str(row["track_id"]),
        str(row["recording_id"]),
        str(row["projection_version"]),
        str(row["wanted_updated_at"] or ""),
        str(row["track_updated_at"] or ""),
        str(row["file_updated_at"] or ""),
    ))
    return "wanted:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _retry_due(request: AcquisitionRequest, now: datetime) -> bool:
    if request.next_retry_at is None:
        return True
    try:
        parsed = datetime.fromisoformat(str(request.next_retry_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= now


def materialize_wanted_requests(
    conn: Any,
    *,
    profile_id: int = 1,
    track_ids: Optional[Sequence[int]] = None,
    trigger: str = "scheduled",
    now: Optional[datetime] = None,
) -> Tuple[WantedRequest, ...]:
    """Create/reuse search requests for wanted tracks with no active file.

    Does not commit and never dispatches a search/download. Existing searching,
    ready or grabbing requests remain untouched. ``no_candidate``/``failed`` rows
    retry only when their persisted retry time is due.
    """
    profile_id = int(profile_id)
    if profile_id != 1:
        raise ValueError("Library v2 wanted acquisition is admin-profile only")
    scope_sql = ""
    args: list[Any] = [profile_id]
    if track_ids is not None:
        normalized_ids = sorted({int(track_id) for track_id in track_ids})
        if not normalized_ids:
            return tuple()
        marks = ",".join("?" for _ in normalized_ids)
        scope_sql = f" AND t.id IN ({marks})"
        args.extend(normalized_ids)
    rows = conn.execute(
        f"""SELECT wt.profile_id, wt.track_id, wt.projection_version,
                   wt.updated_at AS wanted_updated_at,
                   t.updated_at AS track_updated_at,
                   (SELECT MAX(fu.updated_at) FROM lib2_track_files fu
                     WHERE fu.track_id=t.id) AS file_updated_at,
                   COALESCE(wt.effective_profile_id, t.quality_profile_id)
                       AS quality_profile_id,
                   t.album_id AS release_group_id,
                   MIN(rt.recording_id) AS recording_id,
                   MIN(rt.release_edition_id) AS release_edition_id
              FROM lib2_wanted_tracks wt
              JOIN lib2_tracks t ON t.id=wt.track_id
              JOIN lib2_release_tracks rt ON rt.track_id=t.id
             WHERE wt.profile_id=? AND wt.wanted=1
               AND NOT EXISTS (
                   SELECT 1 FROM lib2_track_files f
                    WHERE f.track_id=t.id
                      AND f.file_state='active'
                      AND f.path IS NOT NULL AND f.path<>''
               ){scope_sql}
             GROUP BY wt.profile_id, wt.track_id, wt.projection_version,
                      wt.updated_at, t.updated_at, file_updated_at,
                      COALESCE(wt.effective_profile_id, t.quality_profile_id),
                      t.album_id
             ORDER BY wt.track_id""",
        args,
    ).fetchall()

    current_time = now or datetime.now(timezone.utc)
    results = []
    for row in rows:
        if row["recording_id"] is None or row["quality_profile_id"] is None:
            continue
        request, created = create_request(
            conn,
            profile_id=profile_id,
            scope="recording",
            entity_id=int(row["recording_id"]),
            quality_profile_id=int(row["quality_profile_id"]),
            trigger=trigger,
            idempotency_key=_request_key(row),
            search_options={
                "content_scope": "recording",
                "lib2_track_id": int(row["track_id"]),
                "release_group_id": int(row["release_group_id"]),
                "release_edition_id": (
                    int(row["release_edition_id"])
                    if row["release_edition_id"] is not None else None),
                "projection_version": int(row["projection_version"]),
                "shadow_source": "lib2_wanted_tracks",
            },
        )
        if request.status == "pending":
            request = transition_request(
                conn, request.id, "searching", expected_status="pending",
                increment_attempts=True)
        elif request.status in {"no_candidate", "failed"} and _retry_due(
            request, current_time
        ):
            request = transition_request(
                conn, request.id, "searching",
                expected_status=request.status, increment_attempts=True)
        results.append(WantedRequest(
            int(row["track_id"]), int(row["recording_id"]), request, created))
    return tuple(results)


__all__ = ["WantedRequest", "materialize_wanted_requests"]
