"""Enhance Quality: the files in one artist/album its profile would still upgrade.

Nezreka's review of #1062, point 2: keep the button people know on top of the
one upgrade path. The quality profile decides what an upgrade is, exactly as
for the Wishlist (``upgrade_candidate_track_ids``); this only lists the
candidates whatever their monitoring, so a pick can opt those tracks in.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List


def enhance_candidates(conn: Any, entity: str, entity_id: int, *,
                       profile_id: int) -> List[Dict[str, Any]]:
    """One row per upgradable track of the scope, by album and position."""
    from core.library2.quality_eval import (
        audio_quality_from_file, effective_track_profile, profile_targets,
    )
    from core.library2.track_files import primary_file_rows
    from core.library2.wanted import entity_track_ids
    from core.library2.wishlist_mirror import upgrade_candidate_track_ids

    def target_label(profile):
        # what an upgrade has to reach: the cutoff, or any target at all
        found, _policy, cutoff = profile_targets(profile)
        if not found:
            return None
        target = found[len(found) - 1 if cutoff is None else min(cutoff, len(found) - 1)]
        return target.label or (target.format or "").upper() or None

    ids = upgrade_candidate_track_ids(
        conn, profile_id=profile_id, track_ids=entity_track_ids(conn, entity, entity_id))
    if not ids:
        return []
    files = primary_file_rows(conn, ids)
    # display only: a stale projection row still says what the user last chose
    wanted = dict(conn.execute(
        "SELECT track_id, wanted FROM lib2_wanted_tracks"
        " WHERE profile_id=? AND track_id IN (SELECT value FROM json_each(?))",
        (int(profile_id), json.dumps(ids))).fetchall())
    targets: Dict[int, Any] = {}
    out = []
    for row in conn.execute(
            "SELECT t.id, t.title, t.disc_number, t.track_number, al.id AS album_id,"
            "       al.title AS album_title, al.year AS album_year,"
            "       al.album_type AS album_type, al.image_url AS album_image"
            "  FROM lib2_tracks t JOIN lib2_albums al ON al.id=t.album_id"
            " WHERE t.id IN (SELECT value FROM json_each(?))"
            " ORDER BY al.title COLLATE NOCASE, t.disc_number, t.track_number",
            (json.dumps(ids),)):
        profile = effective_track_profile(conn, row["id"])
        if profile["id"] not in targets:
            targets[profile["id"]] = target_label(profile)
        quality = audio_quality_from_file(files.get(row["id"]))
        # the cover through soulsync's artwork endpoint; the provider's own
        # stands in while a cold one builds
        stored = str(row["album_image"] or "")
        out.append({
            "track_id": row["id"], "title": row["title"],
            "album_id": row["album_id"], "album_title": row["album_title"],
            "album_year": row["album_year"], "album_type": row["album_type"],
            "album_image_url": f"/api/library/v2/artwork/album/{int(row['album_id'])}",
            "album_remote_image_url": stored if stored.startswith(("http://", "https://")) else None,
            "disc_number": row["disc_number"], "track_number": row["track_number"],
            "current": quality.label() if quality else None,
            "profile": profile["name"], "target": targets[profile["id"]],
            "monitored": bool(wanted.get(row["id"])),
        })
    return out
