"""Read-only verification of an album's current canonical pin."""


def get_current_album_pin(db, album_id):
    """Return the current pin, or None if unsafe to complete."""
    if album_id is None:
        return None

    conn = None
    try:
        conn = db._get_connection()
        cursor = conn.cursor()

        cursor.execute("PRAGMA table_info(albums)")
        columns = {row[1] for row in cursor.fetchall()}

        required = {'canonical_source', 'canonical_album_id', 'owner_profile_id'}
        if not required.issubset(columns):
            return None

        locked = (
            'COALESCE(metadata_locked, 0)'
            if 'metadata_locked' in columns else '0'
        )

        cursor.execute(
            f"""SELECT canonical_source, canonical_album_id,
                       {locked} AS locked, owner_profile_id
                FROM albums WHERE id = ?""",
            (album_id,),
        )
        row = cursor.fetchone()

        if not row or row[2]:
            return None

        source = str(row[0] or '').strip()
        release_id = str(row[1] or '').strip()

        if not source or not release_id:
            return None

        return {
            'canonical_source': source,
            'canonical_album_id': release_id,
            'owner_profile_id': row[3],
        }
    except Exception:
        return None
    finally:
        if conn is not None:
            conn.close()
