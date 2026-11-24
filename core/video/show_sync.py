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


# what a retired row knew that the new one may not: the ids, the year, and
# the choices made by hand for the show (anime/standard, quality profile,
# release rules, aliases, subtitle languages). each only fills a gap: what
# the server sent wins
_CARRY_OVER = ("tmdb_id", "tvdb_id", "imdb_id", "year", "series_type", "quality_profile_id",
               "manual_aliases", "preferred_sources", "release_group_allow",
               "release_group_block", "pack_preference", "subtitle_langs")
# choices whose default is a value, not a gap: the old row's wins
_CARRY_CHOICE = ("hands_off", "monitored")


def _carry_over(conn, old: dict, target_id: int) -> None:
    """fill the healed row from the one it replaces. a field the user locked
    (Manage, or a fix-match's ids) keeps the old row's value and its lock,
    since a lock means a scan never changes it; everything else only fills
    what the new row lacks."""
    import json
    cols = {r[1] for r in conn.execute("PRAGMA table_info(shows)").fetchall()}
    try:
        locked = set(json.loads(old.get("locked_fields") or "[]") or [])
    except (TypeError, ValueError):
        locked = set()
    sets, params = [], []
    for c in _CARRY_OVER:
        if c in cols and c not in locked and old.get(c) not in (None, ""):
            sets.append("%s=COALESCE(NULLIF(%s, ''), ?)" % (c, c))
            params.append(old[c])
    for c in _CARRY_CHOICE:
        if c in cols and old.get(c) is not None:
            sets.append("%s=?" % c)
            params.append(old[c])
    for c in sorted(locked & cols):
        sets.append("%s=?" % c)
        params.append(old.get(c))
    if locked:
        new = conn.execute("SELECT locked_fields FROM shows WHERE id=?", (target_id,)).fetchone()
        try:
            mine = set(json.loads((new["locked_fields"] if new else None) or "[]") or [])
        except (TypeError, ValueError):
            mine = set()
        sets.append("locked_fields=?")
        params.append(json.dumps(sorted(mine | locked)))
    if sets:
        conn.execute("UPDATE shows SET " + ", ".join(sets) + " WHERE id=?", params + [target_id])
    if "genres" in locked and old.get("_genres") is not None:
        conn.execute("DELETE FROM show_genres WHERE show_id=?", (target_id,))
        conn.executemany("INSERT OR IGNORE INTO show_genres (show_id, genre_id) VALUES (?, ?)",
                         [(target_id, g) for g in old["_genres"]])


class _Skip(Exception):
    """the caller refreshes the schedule itself"""


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


def sync_show(db, show_id: int, *, remove_when_gone: bool = True,
              refresh_schedule: bool = True) -> dict:
    """Reconcile ONE local show against the server. Returns
    {status, title, episodes_added, episodes_removed, files_added,
    files_removed, show_removed} or raises ShowSyncError.

    ``remove_when_gone=False``: a show the server no longer has is reported
    (``gone``) and left in place. the page's own refresh reads this way: only
    a person pressing Sync removes a title. ``refresh_schedule=False`` leaves
    the episode schedule to whoever asked."""
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

    from core.video.movie_sync import _waiting_for_server
    if tree is None and _waiting_for_server(db, source, row, "show"):
        # soulsync placed it and the server hasn't scanned it in yet: it stays
        return {"status": "ok", "title": row["title"], "show_removed": False, "gone": False,
                "waiting": True, "episodes_added": 0, "episodes_removed": 0,
                "files_added": 0, "files_removed": 0}
    if tree is None and not remove_when_gone:
        return {"status": "ok", "title": row["title"], "show_removed": False, "gone": True,
                "episodes_added": 0, "episodes_removed": 0, "files_added": 0, "files_removed": 0}
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
                old = dict(conn.execute("SELECT * FROM shows WHERE id=?", (int(show_id),)).fetchone())
                old["_genres"] = [r[0] for r in conn.execute(
                    "SELECT genre_id FROM show_genres WHERE show_id=?", (int(show_id),))]
                conn.execute("DELETE FROM shows WHERE id=?", (int(show_id),))
                # the new row is the server's one-show read, which can come
                # back without ids or a year the old row had (a re-keyed show
                # lost its imdb id and year this way), and never with what the
                # user chose for the show: those come over from the row it
                # replaces, after the delete so a legacy unique id index can't
                # collide
                _carry_over(conn, old, target_id)
                conn.commit()
                logger.info("show sync: '%s' re-keyed on %s (%s → %s) — row healed",
                            row["title"], row["server_source"],
                            row["server_id"], tree.get("server_id"))
        finally:
            conn.close()

    # Episode-list refresh from TMDB: the server only knows about FILES — the
    # aired-episode schedule comes from enrichment, and a hole in it (an
    # episode row lost before the demote fix existed) would otherwise stay a
    # hole. recent_seasons_only keeps it to 1-2 API calls; a TMDB failure must
    # never fail the sync itself — but it must be VISIBLE (result surfaced in
    # the response + logged at info), not a silent fire-and-forget: an
    # unobservable heal is indistinguishable from no heal. Runs BEFORE the
    # counts so restored episodes show up in the toast.
    schedule_refresh = "skipped"
    try:
        if not refresh_schedule:
            raise _Skip
        from core.video.enrichment.engine import get_video_enrichment_engine
        res = get_video_enrichment_engine().refresh_show_art(
            target_id, with_ratings=False, recent_seasons_only=True)
        if res and res.get("ok"):
            schedule_refresh = "ok"
            logger.info("show sync: episode schedule refreshed for '%s'", row["title"])
        else:
            schedule_refresh = "failed:%s" % ((res or {}).get("reason") or "unknown")
            logger.warning("show sync: episode schedule refresh for '%s' did not run: %s",
                           row["title"], schedule_refresh)
    except _Skip:
        pass
    except Exception as e:   # noqa: BLE001 - schedule refresh is a bonus, not the sync
        schedule_refresh = "error:%s" % e
        logger.warning("show sync: episode-list refresh failed for '%s': %s", row["title"], e)

    eps_after, files_after = _counts(db, target_id)
    return {
        "status": "ok", "title": row["title"], "show_removed": False,
        "show_id": target_id, "rekeyed": target_id != int(show_id),
        "schedule_refresh": schedule_refresh,
        "episodes_added": max(0, eps_after - eps_before),
        "episodes_removed": max(0, eps_before - eps_after),
        "files_added": max(0, files_after - files_before),
        "files_removed": max(0, files_before - files_after),
    }
