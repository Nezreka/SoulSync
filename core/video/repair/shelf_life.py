"""Shelf Life job — score every owned title for how much it has earned its
space, and flag the ones below the line for a space cleanup.

The scoring itself is core/video/shelf_life.py (pure); this job gathers each
title's facts from the library, scores movies and shows, stores every result
(the library and detail pages read the store, they never score), and creates a
finding for each candidate. Watched Cleanup is left alone: a movie it already
has a pending finding for is scored but not flagged twice.

Watching counts people (#1941): the owner (the shared columns) and every
profile linked to its own Plex account, each by its own watch state. How many
of them watched anything in the last ``active_days`` only decides whether
watching can tell at all; with no linked profiles the owner is the only
person, and always counts.

Approving a finding deletes the WHOLE title through Delete Title
(core/video/delete_title.py): acquisition stopped for every profile, the folder
to the recycle bin, the title hidden from Discover. Like Delete Title it only
runs with the title typed exactly; the typed title arrives as the fix_action,
so a blanket "fix all" can never delete anything. Dismissed titles are never
flagged again.
"""

from __future__ import annotations

from datetime import datetime

from core.video import shelf_life as sl
from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob
from utils.logging_config import get_logger

logger = get_logger("video_shelf_life")

FINDING_TYPE = "shelf_life"


def _int_setting(settings, key, default) -> int:
    try:
        return int(settings.get(key, default))
    except (TypeError, ValueError):
        return default


def _gb(size) -> str:
    return "%.1f GB" % ((size or 0) / 1024 ** 3)


def _recent(stamp, now: datetime, days: int) -> bool:
    age = sl._days(stamp, now)
    return age is not None and age < days


def _activity(db, linked) -> dict:
    """{person: when they last watched anything}: 'owner' from the shared
    columns, each linked profile id from its own rows."""
    if not linked:
        return {"owner": None}
    activity = db.watch_last_activity(linked)
    out = {"owner": activity.get("owner")}
    out.update({pid: activity["profiles"].get(pid) for pid in linked})
    return out


def _active(activity: dict, now: datetime, active_days: int) -> int:
    """how many people watched anything lately. the owner alone always counts:
    with nobody else to compare, an idle owner still decides."""
    if set(activity) == {"owner"}:
        return 1
    return sum(1 for stamp in activity.values() if _recent(stamp, now, active_days))


def _movie_people(row, members) -> dict:
    """{person: {plays, last, frac, offset}} for one movie, owner first."""
    def one(st):
        plays = int(st.get("play_count") or 0)
        return {"plays": plays, "last": st.get("last_viewed_at"), "frac": 1.0 if plays > 0 else 0.0,
                "offset": int(st.get("view_offset_ms") or 0)}
    people = {"owner": one(row)}
    people.update({pid: one(st or {}) for pid, st in (members or {}).items()})
    return people


def _show_people(row, members) -> dict:
    """{person: {plays, last, frac, in_progress_at}} for one show: ``frac`` is
    the share of its owned episodes that person has watched."""
    owned = int(row.get("owned_episodes") or 0)

    def one(st):
        seen = int(st.get("watched_episodes") or 0)
        return {"plays": int(st.get("plays") or 0), "last": st.get("last_viewed_at"),
                "frac": min(1.0, seen / owned) if owned else 0.0, "in_progress_at": st.get("in_progress_at")}
    people = {"owner": one(row)}
    people.update({pid: one(st or {}) for pid, st in (members or {}).items()})
    return people


def _watch(people: dict, active: int, history_since=None) -> dict:
    """what the watch signal reads, from everyone who watched the title."""
    watched = [p for p in people.values() if p["frac"] > 0]
    stamps = [p["last"] for p in watched if p.get("last")]
    return {"active": active, "watched": len(watched), "people": sum(p["frac"] for p in watched),
            "plays": sum(p["plays"] for p in watched),
            "last_viewed_at": max(stamps) if stamps else None, "history_since": history_since}


def _requester_watched(kind, row, req, linked, admins, members):
    """True/False for the requester's own watching, None when it isn't known
    (a member with no linked account keeps no watch state)."""
    pid = req.get("profile_id")
    if pid is not None and int(pid) in linked:
        st = (members or {}).get(int(pid)) or {}
        key = "watched_episodes" if kind == "show" else "play_count"
        return (st.get(key) or 0) > 0
    if pid is not None and (int(pid) in admins or (not admins and int(pid) == 1)):
        key = "watched_episodes" if kind == "show" else "play_count"
        return (row.get(key) or 0) > 0
    return None


def _failed_grabs(kind, row, failed, history):
    keys = [(kind, "library", str(row["id"]))]
    if row.get("tmdb_id"):
        keys.append((kind, "tmdb", str(int(row["tmdb_id"]))))
    if not any(k in history for k in keys):
        return None                 # never grabbed here: no grab data, not "easy"
    return sum(failed.get(k, 0) for k in keys)


def gather(db, settings: dict, now: datetime | None = None) -> dict:
    """{'movie': [(row, facts)], 'show': [(row, facts)]} for every owned title."""
    from core import media_accounts
    now = now or datetime.now()
    active_days = _int_setting(settings, "active_days", 90)
    trust_after = _int_setting(settings, "trust_add_date_after_days", 90)
    linked = [pid for pid, _token in media_accounts.plex_linked_profiles()]
    active = _active(_activity(db, linked), now, active_days)
    admins = media_accounts.admin_profile_ids()
    profiles = sorted(media_accounts.profile_names()[0]) or [1]
    keeps = db.shelf_life_keeps()
    asked = db.shelf_life_requests()
    failed = db.shelf_life_failed_grabs()
    history = db.shelf_life_grab_history_ids()
    following = db.shelf_life_followed_airing(profiles)
    movie_members = db.repair_movie_watch_by_profile()
    show_members = db.shelf_life_show_watch_by_profile()
    out = {"movie": [], "show": []}
    for kind, rows, members_by in (("movie", db.shelf_life_movies(trust_after) or [], movie_members),
                                   ("show", db.shelf_life_shows(trust_after) or [], show_members)):
        median = sl.median_gb_per_hour(rows)
        for row in rows:
            members = members_by.get(row["id"]) or {}
            if kind == "movie":
                people = _movie_people(row, members)
                # anyone part-way through holds it, counted lately or not
                in_progress = any(p["offset"] > 0 for p in people.values())
                watching = False
            else:
                people = _show_people(row, members)
                # an episode left part-way long ago doesn't hold a whole show forever
                in_progress = any(_recent(p["in_progress_at"], now, active_days) for p in people.values())
                # someone watching a show finishes an episode every so often
                watching = any(_recent(p["last"], now, active_days) for p in people.values())
            watch = _watch(people, active)
            tmdb = int(row["tmdb_id"]) if row.get("tmdb_id") else None
            req = asked.get((kind, tmdb)) if tmdb else None
            request = None
            if req:
                request = {"approved": True,
                           "arrived_at": req.get("available_at") or row.get("added_at"),
                           "requester_watched": _requester_watched(kind, row, req, linked, admins, members)}
            facts = {
                "kind": kind, "title": row.get("title"), "year": row.get("year"),
                "added_at": row.get("added_at"),
                "added_from": row.get("added_from"),
                "in_progress": in_progress,
                "watching": watching,
                "keep": bool(tmdb and (kind, tmdb) in keeps),
                "request": request,
                "followed": bool(kind == "show" and tmdb and tmdb in following),
                "airing": bool(kind == "show" and tmdb and tmdb in following),
                "watch": watch,
                "streaming_status": row.get("streaming_status"),
                "streaming_count": row.get("streaming_count"),
                "failed_grabs": _failed_grabs(kind, row, failed, history),
                "ratings": {"tmdb": row.get("rating"), "imdb": row.get("imdb_rating"),
                            "rt": row.get("rt_rating"), "metacritic": row.get("metacritic"),
                            "anilist": row.get("anilist_score"), "trakt_votes": row.get("trakt_votes")},
                "awards": row.get("awards"),
                "size_bytes": row.get("size_bytes"),
                "runtime_minutes": row.get("runtime_minutes"),
                "median_gb_per_hour": median,
            }
            out[kind].append((row, facts))
    return out


def batch_phrase(count: int) -> str:
    """what an admin types to delete a batch of titles at once."""
    return "delete %d title%s" % (count, "" if count == 1 else "s")


def _pending_findings(db) -> list:
    return [f for f in (db.repair_get_findings(job_id="shelf_life", status="pending", finding_type=FINDING_TYPE,
                                               limit=100000).get("items") or [])
            if (f.get("details") or {}).get("item_id")]


def plan_cleanup(db, target_bytes=None, ids=None) -> dict:
    """the titles a cleanup would delete, lowest Shelf Life first (the bigger
    one first on a tie). with ``target_bytes`` it stops once the total reaches
    it; only titles already below the line are ever picked, so a target the
    candidates can't reach says how short it falls instead of reaching higher.
    with ``ids`` it plans exactly those findings. sizes go to the recycle bin
    and come back as free space once the bin's retention clears them."""
    from core.video import organization
    pending = _pending_findings(db)
    if ids is not None:
        wanted = {int(i) for i in ids}
        pending = [f for f in pending if f["id"] in wanted]
    pending.sort(key=lambda f: ((f["details"].get("score") if f["details"].get("score") is not None else 100),
                                -(f["details"].get("size_bytes") or 0), f["id"]))
    target = int(target_bytes) if target_bytes else None
    picked, total = [], 0
    for f in pending:
        if target is not None and total >= target:
            break
        d = f["details"]
        picked.append({"id": f["id"], "kind": d.get("kind"), "item_id": d.get("item_id"),
                       "title": d.get("title"), "year": d.get("year"), "score": d.get("score"),
                       "size_bytes": int(d.get("size_bytes") or 0)})
        total += int(d.get("size_bytes") or 0)
    settings = organization.load(db)
    return {"items": picked, "total_bytes": total, "target_bytes": target,
            "short_bytes": max(0, target - total) if target is not None else 0,
            "candidates": len(pending),
            "recycling": bool(settings.get("recycle_deletes", True)),
            "keep_days": int(settings.get("recycle_keep_days") or 7),
            "phrase": batch_phrase(len(picked))}


def batch_delete_actions(db, ids, phrase: str):
    """check a batch delete before it starts. returns ({finding id: typed
    title}, None) for the run, or (None, error). every id must be a pending
    Shelf Life finding, and the phrase must name exactly how many there are."""
    pending = {f["id"]: f for f in _pending_findings(db)}
    try:
        wanted = sorted({int(i) for i in (ids or [])})
    except (TypeError, ValueError):
        return None, "bad finding ids"
    if not wanted:
        return None, "Nothing selected."
    missing = [i for i in wanted if i not in pending]
    if missing:
        return None, "%d of these aren't open Shelf Life findings any more. Refresh and try again." % len(missing)
    if (phrase or "").strip() != batch_phrase(len(wanted)):
        return None, "Type '%s' to confirm. Nothing was deleted." % batch_phrase(len(wanted))
    return {i: pending[i]["details"].get("title") or "" for i in wanted}, None


def title_view(db, kind: str, item_id) -> dict | None:
    """what a title's detail page shows an admin: its stored score or gate,
    the reasons, when it was scored, the line it is judged against, and
    whether it is marked Keep. None when there is no such title."""
    title = db.shelf_life_title(kind, item_id)
    if not title:
        return None
    from core.video.repair.worker import get_video_repair_worker
    cfg = get_video_repair_worker().job_config("shelf_life") or {}
    threshold = _int_setting(cfg.get("settings") or {}, "threshold", sl.DEFAULT_THRESHOLD)
    stored = db.shelf_life_get(kind, [title["id"]]).get(title["id"])
    tmdb = title.get("tmdb_id")
    out = {"kind": kind, "item_id": title["id"], "scored": bool(stored), "threshold": threshold,
           "enabled": bool(cfg.get("enabled")),
           "keep": bool(tmdb and (kind, int(tmdb)) in db.shelf_life_keeps()), "can_keep": bool(tmdb)}
    if stored:
        out.update({"score": stored["score"], "gate": stored["gate"],
                    "gate_text": sl.GATE_TEXT.get(stored["gate"]) if stored["gate"] else None,
                    "reasons": stored["reasons"], "scored_at": stored["scored_at"]})
    return out


def set_title_keep(db, kind: str, item_id, keep: bool, set_by=None) -> dict | None:
    """mark a title Keep, or take it off. Keep is held by TMDB id, so it
    survives a rescan or a re-add. a Kept title's open finding is retired at
    once, the way the next run would retire it, so no cleanup started before
    then can take it; taking Keep off lets the next run flag it again."""
    title = db.shelf_life_title(kind, item_id)
    if not title:
        return None
    if not title.get("tmdb_id") or not db.shelf_life_set_keep(kind, title["tmdb_id"], keep, set_by=set_by):
        return {"success": False, "error": "This title has no TMDB match yet, so it can't be kept."}
    if keep:
        mine = ("m:%d" if kind == "movie" else "s:%d") % title["id"]
        others = [f["entity_id"] for f in _pending_findings(db) if f.get("entity_id") != mine]
        db.repair_dismiss_absent("shelf_life", FINDING_TYPE, others)
    return {"success": True, **title_view(db, kind, item_id)}


def _resolver_id() -> int:
    """the admin approving, when there's a request to read it from."""
    try:
        from flask import g, has_request_context
        if has_request_context():
            return int(getattr(g, "profile_id", 1) or 1)
    except Exception:   # noqa: BLE001
        pass
    from core import media_accounts
    admins = media_accounts.admin_profile_ids()
    return admins[0] if admins else 1


def _notify(profile_id, message, kind="info"):
    try:
        from core.profile_notify import notify_profile
        notify_profile(profile_id, message, kind, link="video-requests")
    except Exception:  # noqa: BLE001 - a note never fails the action
        logger.debug("request note failed", exc_info=True)


@register_job
class ShelfLifeJob(VideoRepairJob):
    job_id = "shelf_life"
    display_name = "Shelf Life"
    description = "Scores every title for how much it has earned its space; flags the lowest for a cleanup."
    help_text = (
        "Scores every movie and show from 0 to 100 for how much it has earned its space on disk. Higher means "
        "keep. A title scoring under the Threshold is flagged here for you to look at. Nothing is deleted until "
        "you approve it.\n\n"
        "The score mixes five signals, each also 0 to 100 and higher meaning keep. On a finding they show green "
        "when the signal argues for keeping the title, red when it argues for cutting it, amber in between.\n"
        "- Watching: how many people have watched it, how recently and how often. One person who watched it "
        "gives 50 for this part, two 75, three 88: each adds half of what's left, so a title a couple of "
        "people love isn't sunk by everyone else on the server. A show counts each person by how much of it "
        "they watched. A title nobody has watched starts at 50 and halves for every year it sits.\n"
        "- Hard To Replace: how few streaming services carry it in your region (on none at all scores 100), and "
        "how many grabs failed before it landed.\n"
        "- Cultural Value: critic and audience ratings, trusted more the more votes stand behind them, plus "
        "awards, and a lift for a well rated title 25 or more years old.\n"
        "- Size For Its Runtime: gigabytes per hour of runtime against your library's median. At the median it "
        "scores 50; a copy twice as heavy scores 33, half as heavy 67.\n"
        "- Requests: asked for and watched by whoever asked scores 80. Asked for and never watched by them "
        "scores low once the request grace runs out. Never requested stays at 50.\n"
        "A signal with no data counts as 50, so it never drags a title down on its own.\n\n"
        "Never flagged, whatever the score: anything someone is part-way through, a show someone watched an "
        "episode of within Active Days, anything marked Keep, a "
        "request its requester hasn't watched yet (within Request Grace Days), a followed show still airing, "
        "and anything newer than New Arrival Days. A movie Watched Cleanup has already flagged is scored but "
        "not flagged twice. Dismissed titles are never flagged again.\n\n"
        "Approving deletes the whole title the way Delete Title does: acquisition stopped for every profile, "
        "the folder to the recycle bin, and the title hidden from Discover (undo that in Discover's ignore "
        "list). You type the title to confirm, so Fix All never deletes anything.\n\n"
        "Settings:\n"
        "- Preset: how many of the 100 points each signal carries. Balanced: Watching 40, Hard To Replace 20, "
        "Cultural Value 20, Size 10, Requests 10. Space First: Watching 35, Hard To Replace 15, Cultural Value "
        "15, Size 25, Requests 10, so oversized copies sink faster. Collector: Watching 30, Hard To Replace 30, "
        "Cultural Value 30, Size 5, Requests 5, so rare and well regarded titles hold on even when nobody "
        "watches them.\n"
        "- Threshold: titles scoring under this are flagged. Raise it to see more candidates, lower it to see "
        "only the weakest.\n"
        "- Active Days: if nobody has watched anything in this many days, Watching can't tell and counts as "
        "50. A show with an episode watched, or left part-way, in this many days is never flagged. With no "
        "profiles linked to their own Plex account, the owner is the only person counted.\n"
        "- New Arrival Days: anything newer than this is never flagged, so a title gets a fair chance to be "
        "watched.\n"
        "- Request Grace Days: how long a requester has to watch what they asked for. Inside it the title is "
        "never flagged; after it, an unwatched request counts against the title.\n"
        "- Trust Add Date After Days: a server often takes its add date from the file, and an *arr that stamps "
        "files with the release date makes it the release date. So the server's date is only believed when it "
        "falls at least this many days after the release or air date; otherwise time on the shelf counts from "
        "the day SoulSync first saw the title. 180 is the stricter choice, for libraries where a file is often "
        "stamped with a digital release months after the first one."
    )
    icon = "🗃️"
    default_enabled = False
    default_interval_hours = 24
    default_settings = {"preset": sl.DEFAULT_PRESET, "threshold": sl.DEFAULT_THRESHOLD,
                        "request_grace_days": 90, "new_arrival_days": 30, "active_days": 90,
                        # a server add date this close to the release is likely the file's date
                        "trust_add_date_after_days": 90}
    setting_options = {"preset": list(sl.PRESETS),
                       "threshold": [20, 25, 30, 35, 40, 50],
                       "request_grace_days": [30, 60, 90, 180, 365],
                       "new_arrival_days": [7, 14, 30, 60, 90],
                       "active_days": [30, 60, 90, 180, 365],
                       "trust_add_date_after_days": [90, 180]}
    auto_fix = False
    finding_types = (FINDING_TYPE,)

    def estimate_scope(self, context: JobContext) -> int:
        return len(context.db.shelf_life_movies() or []) + len(context.db.shelf_life_shows() or [])

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        settings = {**self.default_settings, **(context.settings or {})}
        now = datetime.now()
        titles = gather(context.db, settings, now)
        held = set(context.db.shelf_life_pending_entities("watched_cleanup"))
        total = sum(len(v) for v in titles.values())
        context.report(total=total, phase="scoring")
        valid, done = [], 0
        for kind in ("movie", "show"):
            stored = []
            for row, facts in titles[kind]:
                context.check_stop()
                done += 1
                result.scanned += 1
                context.report(processed=done, current_item=row.get("title"))
                r = sl.score(facts, settings, now)
                stored.append({"item_id": row["id"], "score": r["score"], "gate": r["gate"],
                               "signals": r["signals"], "reasons": r["reasons"],
                               "size_bytes": row.get("size_bytes")})
                if not r["candidate"]:
                    continue
                entity_id = ("m:%d" if kind == "movie" else "s:%d") % row["id"]
                if kind == "movie" and entity_id in held:
                    result.skipped += 1          # Watched Cleanup already has it
                    continue
                valid.append(entity_id)
                name = row.get("title") or "?"
                if row.get("year"):
                    name = "%s (%s)" % (name, row["year"])
                said = [x["text"] for x in r["reasons"] if x["signal"] in sl.SIGNALS]
                context.create_finding(
                    finding_type=FINDING_TYPE, severity="info",
                    entity_type=kind, entity_id=entity_id,
                    title="%s: Shelf Life %d, %s" % (name, r["score"], _gb(row.get("size_bytes"))),
                    description="; ".join(said[:3]),
                    # library_id + kind is what the findings page reads for the poster
                    details={"kind": kind, "item_id": row["id"], "library_id": row["id"], "tmdb_id": row.get("tmdb_id"),
                             "title": row.get("title"), "year": row.get("year"),
                             "score": r["score"], "signals": r["signals"], "reasons": r["reasons"],
                             "size_bytes": row.get("size_bytes"), "preset": settings.get("preset")})
            context.db.shelf_life_save(kind, stored)
        logger.info("Shelf Life: scored %d title(s), %d candidate(s), %d left to Watched Cleanup",
                    result.scanned, len(valid), result.skipped)
        # a title re-watched, kept or gone since leaves the candidate set
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, FINDING_TYPE, valid)
        return result

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        from core.video import delete_title
        d = finding.get("details") or {}
        kind, item_id = d.get("kind"), d.get("item_id")
        if kind not in ("movie", "show") or not item_id:
            return {"success": False, "error": "finding has no title to delete"}
        tmdb = d.get("tmdb_id")
        if tmdb and (kind, int(tmdb)) in context.db.shelf_life_keeps():
            return {"success": False, "error": "This title is marked Keep. Nothing was deleted."}
        typed = fix_action if isinstance(fix_action, str) else ""
        if not typed.strip():
            return {"success": False, "error": "Type the title to confirm. Nothing was deleted."}
        res = delete_title.run(context.db, kind, item_id, typed_title=typed,
                               resolved_by=_resolver_id(), notify=_notify)
        if not res.get("success"):
            return {"success": False, "error": res.get("error") or "delete failed"}
        return {"success": True, "action": "deleted",
                "message": "Deleted %s: %s to the recycle bin" % (d.get("title") or "the title",
                                                                  _gb(d.get("size_bytes")))}
