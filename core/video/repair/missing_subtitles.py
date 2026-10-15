"""Missing Subtitles job — bazarr's "wanted" sweep as a maintenance job.

Scan: every library movie and episode with a file, checked the same way the
import hook and the subtitle worker check ("have it" rules: any sidecar
naming, unlabeled sidecars by detected language, embedded tracks), for the
languages it should have (per-title overrides, the forced toggle).

Two modes, the music standard's ``dry_run`` switch:

- dry run OFF (the default): queue what's missing straight away. queuing only
  adds wanted rows; the subtitle worker fetches them within the daily quota,
  so nothing touches a file until a fetch succeeds. counts as auto-fixed.
- dry run ON: report only. one finding per movie, one per show (like Missing
  Episodes), listing what's missing; approving queues it.

Either way, subtitles already there get recorded as 'have'. In dry run
nothing is written at all.

Findings key on the title + a hash of its missing set, so a changed set
replaces the stale pending finding and a handled one never comes back; a
complete scan retires pending findings whose gaps got filled elsewhere.
"""

from __future__ import annotations

import hashlib
import json

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob
from utils.logging_config import get_logger

logger = get_logger("video.repair.missing_subtitles")

FINDING_TYPE = "missing_subtitles"


def _sig(items) -> str:
    key = json.dumps(sorted((it["kind"], it["id"], sorted(map(tuple, it["missing"])))
                            for it in items))
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]


def _variant_label(lang, hi, forced) -> str:
    return lang + (" (hi)" if hi else "") + (" (forced)" if forced else "")


def _summary(items) -> str:
    """'en, es (forced)' for a movie; '12 episodes · en, es (forced)' for a show."""
    seen: list = []
    for it in items:
        for v in it["missing"]:
            label = _variant_label(*v)
            if label not in seen:
                seen.append(label)
    langs = ", ".join(seen)
    if items and items[0]["kind"] == "episode":
        n = len(items)
        return "%d episode%s · %s" % (n, "s" if n != 1 else "", langs)
    return langs


def _episode_label(v) -> str:
    try:
        return "S%02dE%02d" % (int(v.get("season") or 0), int(v.get("episode") or 0))
    except (TypeError, ValueError):
        return "S??E??"


@register_job
class MissingSubtitlesJob(VideoRepairJob):
    job_id = "missing_subtitles"
    display_name = "Missing Subtitles"
    description = "Finds library videos missing subtitles in your languages and queues them."
    help_text = ("Checks every movie and episode in your library against your subtitle "
                 "languages (and per-title overrides) the same way new imports are "
                 "checked: any subtitle file next to the video counts, whatever it's "
                 "named, and so do subtitles embedded in the file. What's missing gets "
                 "queued for the subtitle worker, which fetches it within your daily "
                 "quota. Turn on Dry Run to review it first: you get one finding per "
                 "movie or show, and approving one queues it. Needs Download subtitles "
                 "on (Settings → Library → Subtitles).")
    icon = "💬"
    default_enabled = False
    default_interval_hours = 168
    default_settings = {"dry_run": False}
    auto_fix = True
    finding_types = (FINDING_TYPE,)

    @staticmethod
    def _subtitle_settings(db) -> dict:
        from core.video.subtitles.worker import _load_settings
        return _load_settings(db) or {}

    def estimate_scope(self, context: JobContext) -> int:
        try:
            return len(context.db.subtitle_library_videos())
        except Exception:  # noqa: BLE001 - scope is a nicety
            return 0

    def scan(self, context: JobContext) -> JobResult:
        from core.video.subtitles.worker import queue_gaps, subtitle_gaps
        result = JobResult()
        settings = self._subtitle_settings(context.db)
        if not settings.get("download_subtitles"):
            # nothing to want: subtitles are off. not an error, and no
            # findings get retired either (the scan saw nothing).
            context.report(total=0, phase="subtitle downloads are off")
            return result
        dry_run = bool(context.settings.get("dry_run", False))
        videos = context.db.subtitle_library_videos()
        context.report(total=len(videos), phase="checking subtitles")

        groups: dict = {}   # (entity_type, id) -> {"title", "items": [...]}
        for i, v in enumerate(videos, 1):
            context.check_stop()
            result.scanned += 1
            label = (v.get("title") if v["kind"] == "movie"
                     else "%s %s" % (v.get("show_title") or "?", _episode_label(v)))
            gaps = subtitle_gaps(context.db, settings, v["kind"], v["id"])
            if gaps is None:
                result.skipped += 1          # file not reachable from here
            elif dry_run:
                if gaps["missing"]:
                    gk = (("movie", v["id"]) if v["kind"] == "movie"
                          else ("show", v["show_id"]))
                    g = groups.setdefault(gk, {
                        "title": v.get("title") if v["kind"] == "movie" else v.get("show_title"),
                        "year": v.get("year"), "items": []})
                    g["items"].append({
                        "kind": v["kind"], "id": v["id"],
                        "label": None if v["kind"] == "movie" else _episode_label(v),
                        "missing": [list(m) for m in gaps["missing"]]})
            else:
                queued, _marked = queue_gaps(context.db, v["kind"], v["id"], gaps)
                result.auto_fixed += queued
            context.report(processed=i, current_item=label)

        valid = []
        for (etype, eid), g in groups.items():
            items = g["items"]
            entity_id = "%s:%s:%s" % (etype, eid, _sig(items))
            valid.append(entity_id)
            n_items = len(items)
            title = g["title"] or "?"
            context.create_finding(
                finding_type=FINDING_TYPE,
                severity="info",
                entity_type=etype, entity_id=entity_id,
                title=(f"{title} — missing subtitles" if etype == "movie"
                       else f"{title} — {n_items} episode{'s' if n_items != 1 else ''} "
                            f"missing subtitles"),
                description=_summary(items),
                details={"kind": etype, "library_id": eid, "title": title,
                         "year": g.get("year"), "items": items})
        # A COMPLETE scan retires pending findings it no longer produced: the
        # gaps were filled elsewhere, the set changed, or dry run went off and
        # this scan queued them itself. Never on a cancelled/errored scan.
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, FINDING_TYPE, valid)
        return result

    # ── approve == fix: queue what's STILL missing ───────────────────────────
    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        from core.video.subtitles.worker import queue_gaps, subtitle_gaps
        d = finding.get("details") or {}
        items = d.get("items") or []
        if not items:
            return {"success": False, "error": "finding has no videos"}
        settings = self._subtitle_settings(context.db)
        if not settings.get("download_subtitles"):
            return {"success": False,
                    "error": "subtitle downloads are off (Settings → Library → Subtitles)"}
        queued = reachable = 0
        for it in items:
            # re-check at approve time: a subtitle may have landed since the scan
            gaps = subtitle_gaps(context.db, settings, it.get("kind"), it.get("id"))
            if gaps is None:
                continue
            reachable += 1
            q, _m = queue_gaps(context.db, it.get("kind"), it.get("id"), gaps)
            queued += q
        title = d.get("title") or "this title"
        if not reachable:
            return {"success": False,
                    "error": "the video files can't be reached from SoulSync right now"}
        if queued:
            return {"success": True, "action": "queued",
                    "message": f"Queued {queued} subtitle{'s' if queued != 1 else ''} "
                               f"for {title}"}
        return {"success": True, "action": "resolved",
                "message": f"Nothing left to queue for {title}: it's already there "
                           f"or already queued"}
