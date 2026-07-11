"""Complete the Collection job — franchise gaps in the movie library.

Scan: owned movies grouped by their TMDB collection (belongs_to_collection is
already stored by enrichment); the full member list comes from the same
``engine.collection`` fetch the Collection Studio uses. One finding per
franchise — "The Matrix Collection — you have 2 of 4". Unreleased members
(year in the future, or unknown) don't count as missing.

Fix (approve): the missing films go to the movie wishlist (poster + year so
they render properly); the existing wishlist drain downloads them.
"""

from __future__ import annotations

import datetime
import hashlib
import json

from core.video.repair import register_job
from core.video.repair.base import JobContext, JobResult, VideoRepairJob
from utils.logging_config import get_logger

logger = get_logger("video.repair.movie_collections")


def _sig(ids) -> str:
    return hashlib.sha1(json.dumps(sorted(ids)).encode("utf-8")).hexdigest()[:12]


def _members(collection_id):
    """Franchise members with FULL release dates + full poster URLs (the TMDB
    client provides both; the generic list normalizer drops the date, and the
    date is what makes the unreleased filter precise)."""
    from core.video.enrichment.engine import get_video_enrichment_engine
    out, seen = [], set()
    for it in (get_video_enrichment_engine().collection(int(collection_id)) or []):
        tid = it.get("tmdb_id") if it.get("tmdb_id") is not None else it.get("id")
        if tid is None or tid in seen:
            continue
        seen.add(tid)
        try:
            year = int(it.get("year")) if it.get("year") is not None else None
        except (TypeError, ValueError):
            year = None
        out.append({"tmdb_id": int(tid), "title": it.get("title"), "year": year,
                    "date": it.get("date") or "",
                    "poster_url": it.get("poster") or it.get("poster_url")})
    return out


@register_job
class MovieCollectionsJob(VideoRepairJob):
    job_id = "movie_collections"
    display_name = "Complete the Collection"
    description = "Finds franchises you've started but not finished."
    help_text = ("Groups your owned movies by their TMDB collection (The Matrix, "
                 "John Wick…) and checks the full franchise member list — one "
                 "finding per collection with gaps. Unreleased films don't count. "
                 "Approving sends the missing films to the wishlist for download.")
    icon = "🎬"
    default_enabled = False
    default_interval_hours = 168      # weekly — franchises don't change often
    default_settings = {}
    setting_options = {}
    auto_fix = False
    finding_types = ("incomplete_collection",)

    def scan(self, context: JobContext) -> JobResult:
        result = JobResult()
        groups = context.db.repair_movie_franchises()
        context.report(total=len(groups), phase="checking franchises")
        today = datetime.date.today().isoformat()
        valid = []
        for i, (cid, g) in enumerate(groups.items(), 1):
            context.check_stop()
            result.scanned += 1
            context.report(processed=i, current_item=g.get("name") or f"collection {cid}")
            try:
                members = _members(cid)
            except Exception:   # noqa: BLE001 - one bad fetch never kills the scan
                logger.debug("collection fetch failed for %s", cid, exc_info=True)
                result.errors += 1
                continue
            if not members:
                continue
            owned_ids = {m["tmdb_id"] for m in g["movies"]}
            # Released only: a full release date makes this precise — a sequel
            # out this December is upcoming, not missing. No date = not out.
            missing = [m for m in members
                       if m["tmdb_id"] not in owned_ids
                       and m.get("date") and m["date"] <= today]
            if not missing:
                continue
            name = g.get("name") or (members[0].get("title") or "Collection")
            entity_id = f"{cid}:{_sig([m['tmdb_id'] for m in missing])}"
            valid.append(entity_id)
            n = len(missing)
            context.create_finding(
                finding_type="incomplete_collection", severity="info",
                entity_type="collection", entity_id=entity_id,
                title=f"{name} — {len(owned_ids)} of {len(members)} owned",
                description=", ".join(m["title"] or "?" for m in missing[:6]) +
                            ("…" if n > 6 else ""),
                details={"collection_id": cid, "name": name,
                         "owned": g["movies"], "missing": missing,
                         "total": len(members), "count": n})
        # Retire pending findings a COMPLETE scan no longer produced — but a
        # fetch error means partial knowledge, so leave everything standing.
        if result.errors == 0:
            context.db.repair_dismiss_absent(self.job_id, "incomplete_collection", valid)
        return result

    def fix(self, context: JobContext, finding: dict, fix_action=None) -> dict:
        d = finding.get("details") or {}
        missing = d.get("missing") or []
        if not missing:
            return {"success": False, "error": "finding has no missing list"}
        n = 0
        for m in missing:
            if context.db.add_movie_to_wishlist(m.get("tmdb_id"), m.get("title"),
                                                year=m.get("year"),
                                                poster_url=m.get("poster_url")):
                n += 1
        if not n:
            return {"success": False, "error": "nothing could be wishlisted"}
        return {"success": True, "action": "wishlisted",
                "message": f"Sent {n} film{'s' if n != 1 else ''} from {d.get('name')} to the wishlist"}
