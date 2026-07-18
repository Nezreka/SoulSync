"""Per-show synchronize — a deep scan scoped to ONE show.

Fetches the show's full tree from the active video server and reconciles the
local rows through the scanner's own ingest (upsert_show_tree adds/updates
episodes + files and prunes the ones the payload no longer carries). A show
the server verifiably no longer has is removed entirely (cascades clean its
children).

Safety, in order of paranoia:
  • a server ERROR (down, timeout) aborts — it never reads as "show gone"
  • "gone" requires the source to positively distinguish not-found from a
    failed request (Plex: NotFound; Jellyfin: item missing while the server
    still answers)
  • an EMPTY tree (0 episodes) against local episodes is refused — Plex's
    tree builder swallows a mid-fetch episodes() failure into an empty
    seasons list, and blindly upserting that would prune the whole show
"""

from __future__ import annotations

from utils.logging_config import get_logger

logger = get_logger("video.show_sync")


class ShowSyncError(RuntimeError):
    """Sync could not run (server unreachable, wrong server, busy…)."""


def _counts(db, show_id: int) -> tuple:
    conn = db._get_connection()
    try:
        eps = conn.execute("SELECT COUNT(*) c FROM episodes WHERE show_id=?",
                           (show_id,)).fetchone()["c"]
        files = conn.execute(
            "SELECT COUNT(*) c FROM media_files f JOIN episodes e ON f.episode_id=e.id "
            "WHERE e.show_id=?", (show_id,)).fetchone()["c"]
        return eps, files
    finally:
        conn.close()


def sync_show(db, show_id: int) -> dict:
    """Reconcile ONE local show against the server. Returns
    {status, title, episodes_added, episodes_removed, files_added,
    files_removed, show_removed} or raises ShowSyncError."""
    conn = db._get_connection()
    try:
        row = conn.execute(
            "SELECT id, title, server_id, server_source, tmdb_id FROM shows WHERE id=?",
            (int(show_id),)).fetchone()
    finally:
        conn.close()
    if not row:
        raise ShowSyncError("Show not found in the library")

    from core.video.scanner import get_video_scanner
    if (get_video_scanner(db).get_status() or {}).get("state") == "running":
        raise ShowSyncError("A library scan is already running — try again when it finishes")

    from core.video.sources import get_active_video_source
    source = get_active_video_source()
    if source is None:
        raise ShowSyncError("No video server configured/reachable")
    if source.server_name != row["server_source"]:
        raise ShowSyncError(
            "This show belongs to %s but the active server is %s"
            % (row["server_source"], source.server_name))

    # title + tmdb_id let Plex verify a NotFound isn't just a re-keyed item
    # (metadata refresh/optimize changes ratingKeys) before we believe "gone".
    tree = source.show_tree(row["server_id"], title=row["title"],
                            tmdb_id=row["tmdb_id"])   # raises on server errors

    if tree is None:
        # Verified gone from the server — remove it here too (cascades).
        conn = db._get_connection()
        try:
            conn.execute("DELETE FROM shows WHERE id=?", (int(show_id),))
            conn.commit()
        finally:
            conn.close()
        logger.info("show sync: '%s' verified gone from %s — removed locally",
                    row["title"], row["server_source"])
        return {"status": "ok", "title": row["title"], "show_removed": True,
                "episodes_added": 0, "episodes_removed": 0,
                "files_added": 0, "files_removed": 0}

    eps_before, files_before = _counts(db, int(show_id))
    tree_eps = sum(len(s.get("episodes", [])) for s in tree.get("seasons", []))
    if tree_eps == 0 and eps_before > 0:
        # Plex's tree builder swallows a mid-fetch episodes() failure into an
        # empty seasons list — upserting that would prune the entire show.
        raise ShowSyncError(
            "The server returned no episodes for this show — refusing to remove "
            "local data on a possibly-failed read. Run a Deep Scan if the show "
            "is really empty now.")

    db.upsert_show_tree(row["server_source"], tree, preserve_enrichment=True)

    # Healed key: the source found the show under a NEW server id (Plex re-key).
    # The upsert landed on a fresh row — retire the stale one and count against
    # the row the data actually lives on now.
    target_id = int(show_id)
    if str(tree.get("server_id")) != str(row["server_id"]):
        conn = db._get_connection()
        try:
            new_row = conn.execute(
                "SELECT id FROM shows WHERE server_source=? AND server_id=?",
                (row["server_source"], str(tree.get("server_id")))).fetchone()
            if new_row and int(new_row["id"]) != int(show_id):
                target_id = int(new_row["id"])
                conn.execute("DELETE FROM shows WHERE id=?", (int(show_id),))
                conn.commit()
                logger.info("show sync: '%s' re-keyed on %s (%s → %s) — row healed",
                            row["title"], row["server_source"],
                            row["server_id"], tree.get("server_id"))
        finally:
            conn.close()

    eps_after, files_after = _counts(db, target_id)
    return {
        "status": "ok", "title": row["title"], "show_removed": False,
        "show_id": target_id, "rekeyed": target_id != int(show_id),
        "episodes_added": max(0, eps_after - eps_before),
        "episodes_removed": max(0, eps_before - eps_after),
        "files_added": max(0, files_after - files_before),
        "files_removed": max(0, files_before - files_after),
    }
