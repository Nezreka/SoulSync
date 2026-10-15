"""Read-only, profile-scoped verification of missing album tracks."""

from core.library_scope import library_scope_for_profile


def filter_still_missing(db, album_id, missing_tracks, profile_id):
    """Return unowned tracks, or raise if library verification is unsafe."""
    if album_id is None or not isinstance(missing_tracks, list):
        raise ValueError("Invalid album verification request")
    scope = library_scope_for_profile(profile_id)
    if scope is None:
        raise ValueError("Unrestricted library scope is unsafe")
    conn = db._get_connection()
    try:
        cursor = conn.cursor()
        columns = {row[1] for row in cursor.execute("PRAGMA table_info(tracks)")}
        required = {"album_id", "disc_number", "track_number", "owner_profile_id", "file_path"}
        if not required.issubset(columns):
            raise ValueError("Track verification columns unavailable")
        scope_sql, scope_params = db._owner_scope_sql(scope, "owner_profile_id")
        rows = cursor.execute(
            f"SELECT disc_number, track_number FROM tracks WHERE album_id = ? AND {scope_sql} AND file_path IS NOT NULL AND file_path != ?",
            [album_id, *scope_params, ""],
        ).fetchall()
        owned = {(int(row[0] or 1), int(row[1])) for row in rows if row[1] is not None}
        remaining = []
        for track in missing_tracks:
            if not isinstance(track, dict):
                raise ValueError("Invalid missing track")
            disc = int(track.get("disc_number") or 1)
            number = int(track.get("track_number") or 0)
            if disc < 1 or number < 1:
                raise ValueError("Missing disc or track number")
            if (disc, number) not in owned:
                remaining.append(track)
        return remaining
    finally:
        conn.close()
