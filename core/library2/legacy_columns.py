"""3.6.1 columns the Library v2 import left behind, carried once after it.

Found in the third #1062 review (importer pass):

* ``tracks.track_artist`` (P2A-2): the performer of a compilation or guest
  track. The import used it only to derive credits, so the player, stats,
  Songs search and the playlist-sync title fallback read the album artist
  ("Various Artists") and could call an owned song missing.
* ``tracks.server_path`` (P2A-4, #1573): the path the media server reports.
  Without it a playlist or library M3U export wrote SoulSync's own mount,
  which the server can't open, until a full server refresh.
* ``albums.musicbrainz_release_group_id`` (P2A-7): re-asked of MusicBrainz
  for every album otherwise, at about one request a second.
* ``albums.record_type = 'compile'`` (P2A-1): Deezer's word for a
  compilation. The import didn't know it and filed those as albums (or a
  1-track one as a single, then locked as known).

Reads the legacy tables where they live (``tracks`` before the rename,
``legacy_tracks`` after), so one pass serves a fresh upgrade and an install
that already upgraded. Fill-only, batched, never raises; runs once (marker).
"""

from __future__ import annotations

from typing import Any, Dict

from utils.logging_config import get_logger

logger = get_logger("library2.legacy_columns")

MARKER = "legacy_columns_v1"
# review 4 R4-B-2, its own marker: installs that already ran v1 need it too
KIND_MARKER = "legacy_album_kind_known_v1"
_BATCH = 2000


def carry_legacy_columns(conn: Any) -> Dict[str, int]:
    stats = {"track_artists": 0, "server_paths": 0, "compilations": 0, "release_groups": 0}
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS lib2_upgrade_repairs "
                     "(name TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
        _carry_known_album_kinds(conn)
        if conn.execute("SELECT 1 FROM lib2_upgrade_repairs WHERE name=?", (MARKER,)).fetchone():
            return stats
        from core.library2.source_snapshot import source_table
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        tracks = source_table(tables, "tracks")
        albums = source_table(tables, "albums")
        if tracks:
            cols = {r[1] for r in conn.execute(f'PRAGMA table_info("{tracks}")')}
            if "track_artist" in cols:
                stats["track_artists"] = _carry_track_artists(conn, tracks)
            if "server_path" in cols and "lib2_media_server_mappings" in tables:
                stats["server_paths"] = _carry_server_paths(conn, tracks)
        album_cols = ({r[1] for r in conn.execute(f'PRAGMA table_info("{albums}")')}
                      if albums else set())
        if "musicbrainz_release_group_id" in album_cols:
            # the MB worker re-asked every matched album for it, ~1/s
            cur = conn.execute(
                f"""UPDATE lib2_albums SET musicbrainz_release_group_id = (
                        SELECT la.musicbrainz_release_group_id FROM "{albums}" la
                         WHERE la.id = CAST(lib2_albums.legacy_album_id AS TEXT))
                     WHERE COALESCE(musicbrainz_release_group_id, '') = ''
                       AND legacy_album_id IN (
                           SELECT id FROM "{albums}"
                            WHERE COALESCE(musicbrainz_release_group_id, '') <> '')""")
            stats["release_groups"] = max(int(cur.rowcount or 0), 0)
        if "record_type" in album_cols:
            cur = conn.execute(
                f"""UPDATE lib2_albums SET album_type='compilation', updated_at=CURRENT_TIMESTAMP
                     WHERE album_type IN ('album','single') AND legacy_album_id IN (
                           SELECT id FROM "{albums}"
                            WHERE LOWER(TRIM(record_type)) IN ('compile','compilations'))""")
            stats["compilations"] = max(int(cur.rowcount or 0), 0)
        conn.execute("INSERT OR IGNORE INTO lib2_upgrade_repairs(name) VALUES(?)", (MARKER,))
        conn.commit()
        if any(stats.values()):
            logger.info("3.6.1 columns carried after the import: %s", stats)
    except Exception as exc:  # noqa: BLE001 - the next start retries
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        logger.warning("3.6.1 columns not carried yet: %s", exc)
    return stats


def _carry_known_album_kinds(conn) -> int:
    """review 4 R4-B-2: the import never set album_type_known, so a release
    type 3.6.1 stored read as the column default, and the #829 folder guard
    let a same-named single into the album's folder. a type the legacy row
    stored is a known kind. once (own marker); raises to the caller's retry"""
    if conn.execute("SELECT 1 FROM lib2_upgrade_repairs WHERE name=?", (KIND_MARKER,)).fetchone():
        return 0
    from core.library2.source_snapshot import source_table
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    albums = source_table(tables, "albums")
    lib2_cols = {r[1] for r in conn.execute("PRAGMA table_info(lib2_albums)")}
    album_cols = ({r[1] for r in conn.execute(f'PRAGMA table_info("{albums}")')}
                  if albums else set())
    changed = 0
    if "album_type_known" in lib2_cols and "record_type" in album_cols:
        from core.library2.importer import _EXPLICIT_ALBUM_TYPES
        kinds = sorted(_EXPLICIT_ALBUM_TYPES)
        marks = ",".join("?" for _ in kinds)
        cur = conn.execute(
            f"""UPDATE lib2_albums SET album_type_known=1
                 WHERE album_type_known=0 AND legacy_album_id IN (
                       SELECT id FROM "{albums}" WHERE LOWER(TRIM(record_type)) IN ({marks}))""",
            kinds)
        changed = max(int(cur.rowcount or 0), 0)
    conn.execute("INSERT OR IGNORE INTO lib2_upgrade_repairs(name) VALUES(?)", (KIND_MARKER,))
    conn.commit()
    if changed:
        logger.info("3.6.1 release kinds marked known: %s", changed)
    return changed


# the legacy ids are TEXT keys and lib2's legacy_*_id columns are INTEGER.
# compared bare, SQLite gives the TEXT side numeric affinity and can't use
# its key, so every lib2 row scanned the whole legacy table (316k x 310k on a
# real library; the upgrade held the write lock for minutes per batch and
# never finished). the lib2 side is cast instead, and the legacy key serves.


def _batches(conn, table: str):
    top = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0]
    for start in range(0, int(top) + 1, _BATCH):
        yield start, start + _BATCH


def _carry_track_artists(conn, legacy: str) -> int:
    """the performer, where it isn't the release's own artist (the same rule a
    media-server scan follows); never over a value already there"""
    carried = 0
    for low, high in _batches(conn, "lib2_tracks"):
        cur = conn.execute(
            f"""UPDATE lib2_tracks AS t
                   SET track_artist = (SELECT TRIM(lt.track_artist) FROM "{legacy}" lt
                                        WHERE lt.id = CAST(t.legacy_track_id AS TEXT))
                 WHERE t.id > ? AND t.id <= ?
                   AND t.legacy_track_id IS NOT NULL
                   AND COALESCE(t.track_artist, '') = ''
                   AND EXISTS (
                       SELECT 1 FROM "{legacy}" lt
                         JOIN lib2_albums al ON al.id = t.album_id
                         LEFT JOIN lib2_artists ar ON ar.id = al.primary_artist_id
                        WHERE lt.id = CAST(t.legacy_track_id AS TEXT)
                          AND COALESCE(TRIM(lt.track_artist), '') <> ''
                          AND LOWER(TRIM(lt.track_artist)) <> LOWER(COALESCE(ar.name, '')))""",
            (low, high))
        carried += max(int(cur.rowcount or 0), 0)
        conn.commit()
    return carried


def _carry_server_paths(conn, legacy: str) -> int:
    """the server's own path onto the track's mapping for that server"""
    carried = 0
    for low, high in _batches(conn, "lib2_media_server_mappings"):
        cur = conn.execute(
            f"""UPDATE lib2_media_server_mappings AS m
                   SET server_path = (
                       SELECT lt.server_path FROM lib2_tracks t
                         JOIN "{legacy}" lt ON lt.id = CAST(t.legacy_track_id AS TEXT)
                        WHERE t.id = m.entity_id AND lt.server_source = m.server_source
                          AND COALESCE(lt.server_path, '') <> '' LIMIT 1)
                 WHERE m.id > ? AND m.id <= ? AND m.entity_type = 'track'
                   AND COALESCE(m.server_path, '') = ''
                   AND EXISTS (
                       SELECT 1 FROM lib2_tracks t
                         JOIN "{legacy}" lt ON lt.id = CAST(t.legacy_track_id AS TEXT)
                        WHERE t.id = m.entity_id AND lt.server_source = m.server_source
                          AND COALESCE(lt.server_path, '') <> '')""",
            (low, high))
        carried += max(int(cur.rowcount or 0), 0)
        conn.commit()
    return carried
