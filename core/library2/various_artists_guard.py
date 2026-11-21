"""No library artist holds a "Various Artists" id (#2229).

Spotify's compilation id (0LyfQWJT6nXafLPZqxe9Of) came in with a wishlist
download, was paired with the track's singer and stored on them. Every later
compilation was found by it: its tracks were credited to that artist and its
files named and tagged after them.

The writers are fixed where they paired the id (the import's artist guard,
autolink, the importer's resolver). This is the floor under all of them,
because lib2_artists has a couple of dozen writers: a trigger takes the id
back off whichever one stores it. And the once-only heal for libraries it
already happened to:

  * the ids come off every artist row (the provider credit triggers drop the
    featured credits they brought),
  * a track credited to that artist whose own artist is someone else (the
    compilation tracks the id pulled in) goes back to that someone,
  * their files are handed to Library Re-tag and Library Reorganize as a
    findings-only run, so the wrong name in the file and its file name show
    up as findings to apply. nothing is renamed on its own.
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

from core.various_artists_ids import VARIOUS_ARTISTS_IDS
from utils.logging_config import get_logger

logger = get_logger("library2.various_artists_guard")

_TRIGGER = "trg_lib2_artist_no_various_artists_id"
# names a compilation's own artist row goes by. its ids come off too, but its
# tracks are nobody else's to give back
_VARIOUS_NAMES = frozenset({"various artists", "various", "va", "v.a."})
# the jobs that turn a renamed file into a finding
FOLLOWUP_JOBS = ("library_retag", "library_reorganize")


def _ext(column: str, provider: str) -> str:
    from core.library2.provider_ids import external_id_sql
    return external_id_sql(column, provider)


def _external_providers() -> List[str]:
    return [p for p in VARIOUS_ARTISTS_IDS if p.isalnum()]


def _holds_sql(prefix: str) -> str:
    """true when the row ``prefix`` (NEW / a table alias) holds one of them"""
    spotify = VARIOUS_ARTISTS_IDS["spotify"]
    mbid = VARIOUS_ARTISTS_IDS["musicbrainz"]
    parts = [f"{prefix}.spotify_id = '{spotify}'",
             f"LOWER({prefix}.musicbrainz_id) = '{mbid}'"]
    for provider in _external_providers():
        value = VARIOUS_ARTISTS_IDS[provider]
        parts.append(f"{_ext(f'{prefix}.external_ids', provider)} = '{value}'")
    return "(" + " OR ".join(parts) + ")"


def _strip_statements(where: str) -> List[str]:
    """the UPDATEs that take every Various Artists id off the rows ``where`` picks"""
    spotify = VARIOUS_ARTISTS_IDS["spotify"]
    mbid = VARIOUS_ARTISTS_IDS["musicbrainz"]
    out = [
        f"UPDATE lib2_artists SET spotify_id = NULL WHERE {where} AND spotify_id = '{spotify}'",
        f"UPDATE lib2_artists SET musicbrainz_id = NULL "
        f"WHERE {where} AND LOWER(musicbrainz_id) = '{mbid}'",
    ]
    for provider in _external_providers():
        value = VARIOUS_ARTISTS_IDS[provider]
        out.append(
            f"UPDATE lib2_artists SET external_ids = json_remove(external_ids, '$.{provider}') "
            f"WHERE {where} AND {_ext('external_ids', provider)} = '{value}'")
    return out


def ensure_various_artists_guard(cursor: Any) -> None:
    """The trigger pair that takes a Various Artists id off any artist row it
    is written to. Recursive triggers are off, so its own UPDATEs don't fire
    it again; the credit-unlink triggers still see the change."""
    body = ";\n".join(_strip_statements("id = NEW.id")) + ";"
    holds = _holds_sql("NEW")
    cursor.execute(f"""CREATE TRIGGER IF NOT EXISTS {_TRIGGER}_insert
        AFTER INSERT ON lib2_artists WHEN {holds}
        BEGIN {body} END""")
    cursor.execute(f"""CREATE TRIGGER IF NOT EXISTS {_TRIGGER}_update
        AFTER UPDATE OF spotify_id, musicbrainz_id, external_ids ON lib2_artists
        WHEN {holds}
        BEGIN {body} END""")


def _fold(name: Any) -> str:
    return " ".join(str(name or "").split()).casefold()


def _names_this_artist(credit: str, names: Set[str]) -> bool:
    from core.imports.collab_artist import credit_segments
    if _fold(credit) in names:
        return True
    return any(_fold(segment) in names for segment in credit_segments(credit))


def _artist_for(conn: Any, name: str, avoid: Set[int]) -> int:
    """the library artist called ``name`` (not one of ``avoid``), made when
    there is none, as autolink would have made it without the borrowed id"""
    from core.library2.importer import normalize_name
    from core.library2.profile_lookup import default_quality_profile_id
    key = normalize_name(name)
    for row in conn.execute("SELECT id FROM lib2_artists WHERE name_key = ? ORDER BY id", (key,)):
        if int(row[0]) not in avoid:
            return int(row[0])
    cur = conn.execute(
        "INSERT INTO lib2_artists(name, name_key, sort_name, quality_profile_id, monitored) "
        "VALUES(?, ?, ?, ?, 0)", (name, key, name, default_quality_profile_id(conn)))
    return int(cur.lastrowid)


def _ensure_followup_table(conn: Any) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS lib2_findings_followups(
        job_id TEXT NOT NULL, path TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY(job_id, path))""")


def heal_various_artists_ids(conn: Any) -> Dict[str, int]:
    """Once per library: the ids off every artist, the compilation tracks they
    pulled in back with their own artist, their files queued for findings."""
    holders = [(int(r[0]), str(r[1] or "")) for r in conn.execute(
        f"SELECT a.id, a.name FROM lib2_artists a WHERE {_holds_sql('a')}")]
    stats = {"artists": len(holders), "tracks": 0, "files": 0}
    if not holders:
        return stats
    bad = {artist_id for artist_id, _name in holders}
    relinked: List[int] = []
    for artist_id, name in holders:
        if _fold(name) in _VARIOUS_NAMES:
            continue
        own = {_fold(name)}
        try:
            from core.library2.metadata_overrides import effective_artist_names
            own.add(_fold(effective_artist_names(conn, [artist_id]).get(artist_id)))
        except Exception:  # noqa: BLE001,S110 - no override table, the scanned name is enough
            pass
        rows = conn.execute(
            "SELECT ta.track_id, t.track_artist FROM lib2_track_artists ta "
            "JOIN lib2_tracks t ON t.id = ta.track_id "
            "WHERE ta.artist_id = ? AND ta.role = 'primary' "
            "AND COALESCE(TRIM(t.track_artist), '') <> ''", (artist_id,)).fetchall()
        for track_id, credit in rows:
            credit = str(credit).strip()
            if _names_this_artist(credit, own):
                continue
            target = _artist_for(conn, credit, bad)
            if conn.execute("SELECT 1 FROM lib2_track_artists WHERE track_id=? AND artist_id=?",
                            (track_id, target)).fetchone():
                conn.execute("DELETE FROM lib2_track_artists WHERE track_id=? AND artist_id=?",
                             (track_id, artist_id))
                conn.execute("UPDATE lib2_track_artists SET role='primary' "
                             "WHERE track_id=? AND artist_id=?", (track_id, target))
            else:
                conn.execute("UPDATE lib2_track_artists SET artist_id=? "
                             "WHERE track_id=? AND artist_id=?", (target, track_id, artist_id))
            relinked.append(int(track_id))
            logger.info("[Various Artists] track %s was credited to %r by the compilation id; "
                        "back to %r", track_id, name, credit)
    marks = ",".join("?" for _ in bad)
    for statement in _strip_statements(f"id IN ({marks})"):
        conn.execute(statement, tuple(sorted(bad)))
    # the credit snapshots name the compilation, never an artist to link
    try:
        conn.execute("UPDATE lib2_provider_credits SET source_artist_id = NULL "
                     "WHERE (source = 'spotify' AND source_artist_id = ?) "
                     "OR (source = 'deezer' AND source_artist_id = ?)",
                     (VARIOUS_ARTISTS_IDS["spotify"], VARIOUS_ARTISTS_IDS["deezer"]))
    except Exception as exc:  # noqa: BLE001 - an install without the snapshot table
        logger.debug("credit snapshots not cleared: %s", exc)
    stats["tracks"] = len(relinked)
    stats["files"] = queue_findings_followup(conn, relinked, reason="various_artists_id")
    return stats


def queue_findings_followup(conn: Any, track_ids: List[int], *, reason: str) -> int:
    """the active files of ``track_ids``, for a findings-only Re-tag and
    Reorganize run (the repair worker picks them up when idle)"""
    ids = sorted({int(t) for t in track_ids})
    if not ids:
        return 0
    _ensure_followup_table(conn)
    marks = ",".join("?" for _ in ids)
    paths = [str(r[0]) for r in conn.execute(
        f"SELECT DISTINCT path FROM lib2_track_files WHERE track_id IN ({marks}) "  # noqa: S608 - placeholders only
        "AND path IS NOT NULL AND TRIM(path) <> '' "
        "AND COALESCE(file_state, 'active') = 'active'", ids)]
    for job_id in FOLLOWUP_JOBS:
        conn.executemany(
            "INSERT OR IGNORE INTO lib2_findings_followups(job_id, path, reason) VALUES(?, ?, ?)",
            [(job_id, path, reason) for path in paths])
    return len(paths)


def take_findings_followups(conn: Any) -> Dict[str, List[str]]:
    """``{job id: [file path]}`` waiting for a findings-only run, removed as
    they are handed over."""
    try:
        rows = conn.execute("SELECT job_id, path FROM lib2_findings_followups").fetchall()
    except Exception:  # noqa: BLE001 - nothing ever queued
        return {}
    out: Dict[str, List[str]] = {}
    for job_id, path in rows:
        out.setdefault(str(job_id), []).append(str(path))
    if out:
        conn.execute("DELETE FROM lib2_findings_followups")
    return out


__all__ = ["FOLLOWUP_JOBS", "ensure_various_artists_guard", "heal_various_artists_ids",
           "queue_findings_followup", "take_findings_followups"]
