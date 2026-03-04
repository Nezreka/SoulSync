"""
Shared response helpers for the SoulSync public API.
"""

from typing import Optional, Set
from flask import jsonify


def api_success(data, pagination=None, status=200):
    """Wrap a successful response in the standard envelope."""
    return jsonify({
        "success": True,
        "data": data,
        "error": None,
        "pagination": pagination,
    }), status


def api_error(code, message, status=400):
    """Wrap an error response in the standard envelope."""
    return jsonify({
        "success": False,
        "data": None,
        "error": {"code": code, "message": message},
        "pagination": None,
    }), status


def build_pagination(page, limit, total):
    """Build a pagination dict from page/limit/total."""
    total_pages = max(1, (total + limit - 1) // limit)
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": total_pages,
        "has_next": page < total_pages,
        "has_prev": page > 1,
    }


def parse_pagination(request, default_limit=50, max_limit=200):
    """Extract and validate page/limit from a Flask request."""
    try:
        page = max(1, int(request.args.get("page", 1)))
    except (ValueError, TypeError):
        page = 1
    try:
        limit = min(max_limit, max(1, int(request.args.get("limit", default_limit))))
    except (ValueError, TypeError):
        limit = default_limit
    return page, limit


def parse_fields(request) -> Optional[Set[str]]:
    """Parse ?fields=id,name,thumb_url into a set. Returns None if not specified."""
    raw = request.args.get("fields", "").strip()
    if not raw:
        return None
    return {f.strip() for f in raw.split(",") if f.strip()}


def parse_profile_id(request, default: int = 1) -> int:
    """Extract profile_id from X-Profile-Id header or ?profile_id query param."""
    try:
        header = request.headers.get("X-Profile-Id")
        if header:
            return max(1, int(header))
        param = request.args.get("profile_id")
        if param:
            return max(1, int(param))
    except (ValueError, TypeError):
        pass
    return default
