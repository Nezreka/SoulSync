"""video request rules that don't need flask.

``monitor_for_new_request``: a show request with no season choice used to
default to 'future', which on an ended show wishes nothing at all, so an
approved request sat on "Acquiring…" forever. asking for a show means asking
for the show: 'all' unless the requester picked something.

``sweep_arrivals``: approved requests whose title reached the library get
stamped and their requesters told, once.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from core.video.monitor_policy import POLICIES

_TRUSTED_POSTER_PREFIXES = ("https://image.tmdb.org/", "/api/video/")


def monitor_for_new_request(kind: str, requested: Optional[str]) -> str:
    if kind != "show":
        return "future"
    value = str(requested or "").lower()
    return value if value in POLICIES else "all"


def trusted_poster(url: Optional[str]) -> Optional[str]:
    """only tmdb's image host or our own proxy. a request's poster is drawn in
    the admin's browser; a member-typed url could point anywhere."""
    url = str(url or "").strip()
    return url if url.startswith(_TRUSTED_POSTER_PREFIXES) else None


def parse_tmdb_id(value: Any) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def request_metadata(kind: str, tmdb_id: int, body: Dict[str, Any],
                     lookup: Callable[[str, int], Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    """title/year/poster from tmdb when it answers, the body only as a
    fallback (tmdb off or down). ``owned`` when tmdb says the library already
    has it (it answers with a redirect to the library item)."""
    detail = None
    try:
        detail = lookup(kind, tmdb_id)
    except Exception:  # noqa: BLE001 - a lookup failure falls back to the body
        detail = None
    if isinstance(detail, dict) and detail.get("redirect"):
        return {"owned": True, "title": str(body.get("title") or "").strip()[:300],
                "year": body.get("year"), "poster_url": trusted_poster(body.get("poster_url"))}
    if isinstance(detail, dict) and detail.get("title"):
        return {"owned": False, "title": str(detail["title"])[:300], "year": detail.get("year"),
                "poster_url": trusted_poster(detail.get("poster_url"))}
    return {"owned": False, "title": str(body.get("title") or "").strip()[:300],
            "year": body.get("year"), "poster_url": trusted_poster(body.get("poster_url"))}


def sweep_arrivals(db, notify: Callable[[int, str, str], Any]) -> int:
    rows = db.approved_requests_awaiting_arrival()
    if not rows:
        return 0
    db.annotate_requests_in_library(rows)
    arrived = [r for r in rows if r.get("in_library")]
    if not arrived:
        return 0
    marked = db.mark_video_requests_available([r["id"] for r in arrived])
    for r in arrived:
        what = f"{r.get('title')}" + (f" ({r['year']})" if r.get("year") else "")
        try:
            notify(int(r["profile_id"]), f"{what} is in your library now", "success")
        except Exception:  # noqa: BLE001, S110 - a note never undoes the stamp
            pass
    return marked


__all__ = ["monitor_for_new_request", "trusted_poster", "parse_tmdb_id", "request_metadata",
           "sweep_arrivals"]
