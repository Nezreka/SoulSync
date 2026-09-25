"""request quotas: at most N asks per D days for a profile that has a limit.

an ask is one request as the person sees it: a movie, a show, an album or a
single track (music groups rows the way core.requests.music does). admins and
profiles without a limit (0, the default) are never counted. pure.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def quota_for(profile: Optional[Dict[str, Any]]) -> Optional[Dict[str, int]]:
    """{limit, days} when this profile is limited, else None."""
    if not profile:
        return None
    try:
        if int(profile.get("id") or 0) == 1 or profile.get("is_admin"):
            return None
        limit = int(profile.get("request_limit") or 0)
        days = int(profile.get("request_limit_days") or 7)
    except (TypeError, ValueError):
        return None
    if limit <= 0:
        return None
    return {"limit": limit, "days": max(1, days)}


def quota_state(quota: Optional[Dict[str, int]], used: int) -> Optional[Dict[str, int]]:
    if not quota:
        return None
    return {"limit": quota["limit"], "days": quota["days"], "used": int(used),
            "remaining": max(0, quota["limit"] - int(used))}


def over_quota(quota: Optional[Dict[str, int]], used: int) -> bool:
    return bool(quota) and int(used) >= quota["limit"]


def quota_message(quota: Dict[str, int]) -> str:
    span = "day" if quota["days"] == 1 else "week" if quota["days"] == 7 else f"{quota['days']} days"
    noun = "request" if quota["limit"] == 1 else "requests"
    return f"You've used your {quota['limit']} {noun} for this {span}" if span in ("day", "week") \
        else f"You've used your {quota['limit']} {noun} for the last {span}"


__all__ = ["quota_for", "quota_state", "over_quota", "quota_message"]
