"""3.6.1 locks the upgrade import did not carry, applied once afterwards.

3.6.1's genre cleanup set ``genres_locked`` on the albums and artists it
cleaned, so the next media-server scan or enrichment pass kept the cleaned
genres. The Library v2 import carried ``art_locked`` but not this one, and
every lib2 writer honours ``genres_locked`` only when it is 1: the first scan
after the upgrade put the off-whitelist genres back. This copies the lock
from the legacy rows (``artists``/``albums`` before the upgrade retires them,
``legacy_artists``/``legacy_albums`` after) onto the rows that mirror them,
once per database.
"""

from __future__ import annotations

from typing import Any, Dict

from utils.logging_config import get_logger

logger = get_logger("library2.legacy_locks")

MARKER = "legacy_genre_locks_v1"


def _source(conn, table: str):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for name in (table, f"legacy_{table}"):
        if name in names:
            cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{name}")')}
            if "genres_locked" in cols:
                return name
    return None


def carry_legacy_genre_locks(conn: Any) -> Dict[str, int]:
    """Set genres_locked on lib2 artists/albums whose legacy row had it.
    Never clears a lock, never raises; runs once (marker)."""
    stats = {"artists": 0, "albums": 0}
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS lib2_upgrade_repairs "
                     "(name TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        if conn.execute("SELECT 1 FROM lib2_upgrade_repairs WHERE name=?", (MARKER,)).fetchone():
            return stats
        for kind, lib2_table, back_ref in (("artists", "lib2_artists", "legacy_artist_id"),
                                           ("albums", "lib2_albums", "legacy_album_id")):
            source = _source(conn, kind)
            if source is None:
                continue
            cur = conn.execute(
                f"""UPDATE {lib2_table} SET genres_locked=1, updated_at=CURRENT_TIMESTAMP
                     WHERE COALESCE(genres_locked,0)=0 AND {back_ref} IS NOT NULL
                       AND CAST({back_ref} AS TEXT) IN (
                           SELECT CAST(id AS TEXT) FROM "{source}" WHERE genres_locked=1)""")
            stats[kind] = max(int(cur.rowcount or 0), 0)
        # no legacy tables at all (a fresh install): nothing ever to carry
        conn.execute("INSERT OR IGNORE INTO lib2_upgrade_repairs(name) VALUES(?)", (MARKER,))
        conn.commit()
        if any(stats.values()):
            logger.info("Carried 3.6.1 genre locks: %s", stats)
    except Exception as exc:  # noqa: BLE001 - the next start retries
        logger.warning("3.6.1 genre locks not carried yet: %s", exc)
        try:
            conn.rollback()
        except Exception as rollback_exc:  # noqa: BLE001
            logger.debug("Upgrade repair rollback failed: %s", rollback_exc)
    return stats


__all__ = ["carry_legacy_genre_locks"]
