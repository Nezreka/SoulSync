"""Fail-closed library ownership check for automatic album completion."""


def verify_shared_album_scope(db, album_id, profile_id):
    """Allow only albums and profiles belonging to the shared library."""
    if album_id is None or profile_id is None:
        return False

    try:
        profile_id = int(profile_id)
        if profile_id < 1:
            return False

        conn = db._get_connection()
        try:
            album_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(albums)")
            }
            if "owner_profile_id" not in album_columns:
                return False

            if profile_id != 1:
                profile_columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(profiles)")
                }
                if not {"id", "library_mode", "library_root"}.issubset(
                    profile_columns
                ):
                    return False

                profile = conn.execute(
                    "SELECT library_mode, library_root "
                    "FROM profiles WHERE id = ?",
                    (profile_id,),
                ).fetchone()

                if profile is None:
                    return False

                mode, root = profile
                if mode not in (None, "shared", "own"):
                    return False
                if mode == "own" and root:
                    return False

            row = conn.execute(
                "SELECT owner_profile_id FROM albums WHERE id = ?",
                (str(album_id),),
            ).fetchone()

            return row is not None and row[0] is None
        finally:
            conn.close()
    except Exception:
        return False
