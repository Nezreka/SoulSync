"""Missing Episodes job — the first video maintenance job.

Scan: every LIBRARY show, one finding per show listing its aired, monitored,
un-owned episodes (specials opt-in; 'ended' shows included — ended matters for
watching-for-NEW, not for filling back-catalog gaps). The finding's entity_id
is show_id + a hash of the missing set, so when the set changes (new episode
airs, some got downloaded) a fresh finding appears and the end-of-scan
absent-dismissal retires the stale pending one — while a dismissed/resolved
finding for the SAME set never comes back (standard dedup).

Fix (approve): send the episodes to the video wishlist through the SAME
write-parity path as a manual add — show poster proxy URL + library_id +
per-episode stills/overviews/season posters from a cached TMDB season fetch
(the art-less-orb standard, mirroring the auto-airing automation). The
existing wishlist drain then downloads them; no new plumbing.
"""

from __future__ import annotations

import hashlib
import json

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob
from utils.logging_config import get_logger

logger = get_logger("video.repair.missing_episodes")


def _sig(eps) -> str:
    key = json.dumps(sorted((e["season_number"], e["episode_number"]) for e in eps))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _season_summary(eps) -> str:
    """'S01: 3 · S02: 1 · Specials: 2' — the finding's one-line description."""
    per: dict = {}
    for e in eps:
        per[e["season_number"]] = per.get(e["season_number"], 0) + 1
    parts = []
    for sn in sorted(per):
        label = "Specials" if sn == 0 else "S%02d" % sn
        parts.append(f"{label}: {per[sn]}")
    return " · ".join(parts)


@register_job
class MissingEpisodesJob(VideoRepairJob):
    job_id = "missing_episodes"
    display_name = "Missing Episodes"
    description = "Finds aired episodes your shows don't have files for."
    help_text = ("Scans every show in your library for aired, monitored episodes "
                 "without a file — one finding per show. Approving a finding sends "
                 "those episodes to the wishlist, where the auto-downloader picks "
                 "them up. Ended shows are included: a finished show can still "
                 "have gaps worth filling. Specials (season 0) are opt-in.")
    icon = "🧩"
    default_enabled = False
    default_interval_hours = 24
    default_settings = {"include_specials": False}
    setting_options = {"include_specials": [False, True]}
    auto_fix = False
    finding_types = ("missing_episodes",)

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        rows = context.db.missing_episode_rows(
            include_specials=bool(context.settings.get("include_specials")))
        shows: dict = {}
        for r in rows:
            shows.setdefault(r["show_id"], []).append(r)
        context.report(total=len(shows), phase="scanning shows")
        valid = []
        for i, (show_id, eps) in enumerate(shows.items(), 1):
            context.check_stop()
            result.scanned += 1
            head = eps[0]
            sig = _sig(eps)
            entity_id = f"{show_id}:{sig}"
            valid.append(entity_id)
            n = len(eps)
            context.create_finding(
                finding_type="missing_episodes",
                severity="warning" if n >= 10 else "info",
                entity_type="show", entity_id=entity_id,
                title=f"{head['show_title']} — {n} missing episode{'s' if n != 1 else ''}",
                description=_season_summary(eps),
                details={
                    "show_id": show_id,
                    "show_title": head["show_title"],
                    "tmdb_id": head["show_tmdb_id"],
                    "server_source": head["server_source"],
                    "count": n,
                    "episodes": [{"season_number": e["season_number"],
                                  "episode_number": e["episode_number"],
                                  "title": e["title"], "air_date": e["air_date"],
                                  "still_url": e["still_url"],
                                  "overview": e["overview"]} for e in eps],
                })
            context.report(processed=i, current_item=head["show_title"])
        # A COMPLETE scan retires pending findings it no longer produced (the
        # set changed or the gaps got filled elsewhere) — never on errors.
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "missing_episodes", valid)
        return result

    # ── approve == fix: wishlist the episodes (write-parity standard) ─────────
    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        d = finding.get("details") or {}
        show_id, tmdb_id = d.get("show_id"), d.get("tmdb_id")
        title, eps = d.get("show_title"), d.get("episodes") or []
        if not eps:
            return {"success": False, "error": "finding has no episode list"}
        if not tmdb_id or not title:
            return {"success": False,
                    "error": "show is not TMDB-matched yet — enrich it first"}
        season_cache: dict = {}

        def season_meta(sn):
            """One cached TMDB season fetch per season — the SAME art source a
            manual add uses, so these rows never render as art-less orbs. A
            TMDB hiccup degrades to the DB values."""
            if sn not in season_cache:
                sm = {}
                try:
                    from core.video.enrichment.engine import get_video_enrichment_engine
                    sm = get_video_enrichment_engine().tmdb_season(tmdb_id, sn) or {}
                except Exception:   # noqa: BLE001 - art is a nicety, the wish isn't
                    logger.debug("tmdb_season failed for %s S%s", tmdb_id, sn, exc_info=True)
                emap = {e.get("episode_number"): e for e in (sm.get("episodes") or [])
                        if isinstance(e, dict)}
                season_cache[sn] = (sm.get("poster_url"), emap)
            return season_cache[sn]

        rows = []
        for e in eps:
            sn, en = e.get("season_number"), e.get("episode_number")
            season_poster, emap = season_meta(sn)
            t = emap.get(en) or {}
            rows.append({
                "season_number": sn, "episode_number": en,
                "title": t.get("title") or e.get("title"),
                "air_date": t.get("air_date") or e.get("air_date"),
                "still_url": t.get("still_url") or e.get("still_url"),
                "overview": t.get("overview") or e.get("overview"),
                "season_poster_url": season_poster,
            })
        poster = f"/api/video/poster/show/{show_id}" if show_id is not None else None
        n = context.db.add_episodes_to_wishlist(
            tmdb_id, title, rows, poster_url=poster, library_id=show_id,
            server_source=d.get("server_source"))
        if not n:
            return {"success": False, "error": "nothing could be wishlisted"}
        return {"success": True, "action": "wishlisted",
                "message": f"Sent {n} episode{'s' if n != 1 else ''} of {title} to the wishlist"}
