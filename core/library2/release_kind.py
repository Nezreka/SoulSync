"""Fill a genuinely unknown release kind without reclassifying a release."""

KINDS = {'album', 'single', 'ep', 'compilation', 'live', 'remix', 'soundtrack', 'other'}


def confirm_release_kind(conn, album_id: int, value, *, known: bool = True) -> bool:
    kind = str(value or '').strip().lower()
    if not known or kind not in KINDS:
        return False
    changed = conn.execute(
        "UPDATE lib2_albums SET album_type=?, album_type_known=1, "
        "updated_at=CURRENT_TIMESTAMP WHERE id=? AND album_type_known=0",
        (kind, album_id),
    )
    return bool(changed.rowcount)
