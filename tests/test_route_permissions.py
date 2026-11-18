"""route inventory (#2166, plan 4.5): every Flask rule declares who may call
it: @requires(perm), @admin_only / @admin_or_api_key, @self_only,
@own_or_admin, @signed_in, @api_key_only or @public (core.permissions).

everything outside the video side and the seerr shim declares already (piece
A), and fails here the moment it doesn't. the video and seerr routes that
don't declare yet are listed in route_permissions_allowlist.txt (pieces B and
C), and that list may only shrink: declaring a route is the fix, adding it to
the list is not, and the list can't hold a route outside those two. at the
end of P1 the list is empty and this test fails on any undeclared rule.

regenerate the list (after declaring routes, never to add one):
    SOULSYNC_WRITE_ROUTE_ALLOWLIST=1 python -m pytest tests/test_route_permissions.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from flask import Flask

from core.permissions import declared_access, public, requires, self_only

ALLOWLIST = Path(__file__).with_name("route_permissions_allowlist.txt")
IGNORED_METHODS = {"HEAD", "OPTIONS"}


def inventory(app) -> dict:
    """{"METHODS /rule": ("perm"|"admin"|"self"|"public", detail) or None}."""
    out = {}
    for rule in app.url_map.iter_rules():
        methods = ",".join(sorted(m for m in (rule.methods or ()) if m not in IGNORED_METHODS))
        key = "%s %s" % (methods, rule.rule)
        if rule.endpoint == "static":
            access = ("public", None)
        else:
            access = declared_access(app.view_functions.get(rule.endpoint))
        if out.get(key) is None:
            out[key] = access
    return out


def undeclared(app) -> list:
    return sorted(k for k, v in inventory(app).items() if v is None)


def new_undeclared(app, allowed) -> list:
    """undeclared routes the allowlist doesn't excuse."""
    return [r for r in undeclared(app) if r not in allowed]


def read_allowlist() -> set:
    if not ALLOWLIST.exists():
        return set()
    return {line.strip() for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")}


def write_allowlist(rows) -> None:
    head = ("# routes that don't declare their access yet (tests/test_route_permissions.py).\n"
            "# this list may only shrink: declare the route, then regenerate. never add to it.\n")
    ALLOWLIST.write_bytes((head + "".join(r + "\n" for r in rows)).encode("utf-8"))


# ── the checker itself (a fixture app, so the guard is proven to bite) ───

def _fixture_app():
    app = Flask(__name__)

    @app.route("/declared")
    @requires("music.stats")
    def declared():
        return "x"

    @app.route("/open")
    @public
    def opened():
        return "x"

    @app.route("/mine", methods=["GET", "POST"])
    @self_only
    def mine():
        return "x"

    @app.route("/forgot")
    def forgot():
        return "x"
    return app


def test_the_checker_finds_an_undeclared_route():
    app = _fixture_app()
    assert undeclared(app) == ["GET /forgot"]
    inv = inventory(app)
    assert inv["GET /declared"] == ("perm", "music.stats")
    assert inv["GET /open"] == ("public", None)
    assert inv["GET,POST /mine"] == ("self", None)
    assert inv["GET /static/<path:filename>"] == ("public", None)


def test_a_route_missing_from_the_allowlist_fails():
    app = _fixture_app()
    assert new_undeclared(app, set()) == ["GET /forgot"]
    assert new_undeclared(app, {"GET /forgot"}) == []
    assert new_undeclared(app, {"GET /something-else"}) == ["GET /forgot"]


# ── the real app ─────────────────────────────────────────────────────────

web_server = pytest.importorskip("web_server")


def test_no_new_undeclared_routes():
    app = web_server.app
    inv = inventory(app)
    missing = undeclared(app)
    if os.environ.get("SOULSYNC_WRITE_ROUTE_ALLOWLIST") == "1":
        write_allowlist(missing)
    allowed = read_allowlist()
    new = new_undeclared(app, allowed)
    stale = sorted(allowed - set(missing))
    counts = {}
    for v in inv.values():
        kind = v[0] if v else "undeclared"
        counts[kind] = counts.get(kind, 0) + 1
    print("\nroute inventory: %d rules, %s; allowlist %d (%d no longer needed)"
          % (len(inv), ", ".join("%s %d" % kv for kv in sorted(counts.items())), len(allowed), len(stale)))
    assert not new, ("these routes don't declare who may call them; add @requires(perm), "
                     "@admin_only, @self_only or @public (core.permissions):\n  " + "\n  ".join(new))


# the only routes the allowlist may still excuse (pieces B and C)
STILL_LISTED_PREFIXES = ("/api/video/", "/seerr/")


def outside_the_listed_sides(rows) -> list:
    return sorted(r for r in rows if not r.split(" ", 1)[1].startswith(STILL_LISTED_PREFIXES))


def test_everything_outside_video_and_seerr_declares():
    """no allowlist for these: an undeclared music, shell, profile or api
    route fails right here."""
    missing = outside_the_listed_sides(undeclared(web_server.app))
    assert not missing, ("these routes don't declare who may call them; add @requires(perm), "
                         "@admin_only, @self_only, @signed_in or @public (core.permissions):\n  "
                         + "\n  ".join(missing))


def test_the_allowlist_only_excuses_video_and_seerr():
    assert outside_the_listed_sides(read_allowlist()) == []


def test_a_listed_route_outside_video_and_seerr_is_caught():
    assert outside_the_listed_sides({"GET /api/video/x", "GET /seerr/api/v1/y"}) == []
    assert outside_the_listed_sides({"GET /api/video/x", "POST /api/discover/z"}) == ["POST /api/discover/z"]


def test_the_allowlist_is_sorted_and_unique():
    rows = [line.strip() for line in ALLOWLIST.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")]
    assert rows == sorted(set(rows))
