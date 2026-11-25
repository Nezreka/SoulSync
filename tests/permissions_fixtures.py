"""shared fixtures for people and permissions (#2166). pieces A-G reuse these.

contexts for can() without the app, and music databases shaped like real
installs (made in a temp dir, never the real one), with the profile rows
written straight into the old columns the way older builds left them.

usage:
    from tests.permissions_fixtures import member_ctx, admin_ctx, build_install, SHAPES

    ctx = member_ctx(**{"movies.get": "ask"})          # adult + changes
    ctx = member_ctx(template="kids")                  # a built-in
    db, ids = build_install(tmp_path, "household")     # ids: {name: profile id}
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core import permissions as P

BUILTIN = {t["slug"]: t["perms"] for t in P.BUILTIN_TEMPLATES}


def member_perms(template: str = "adult", policy: Optional[Dict[str, Any]] = None, **changes) -> Dict[str, Any]:
    """an effective map: a built-in template, personal changes, a policy."""
    return P.effective(policy, BUILTIN[template], changes, {"id": 50})


def member_ctx(template: str = "adult", profile_id: int = 50, policy=None, **changes) -> Dict[str, Any]:
    return P.make_context(profile_id, perms=member_perms(template, policy, **changes))


def admin_ctx(profile_id: int = 1) -> Dict[str, Any]:
    return P.make_context(profile_id, is_admin=True, perms=P.admin_map(), source="admin")


def broken_ctx(profile_id: int = 50) -> Dict[str, Any]:
    """a member whose map couldn't be built: can() answers no."""
    return P.make_context(profile_id, perms=None, source="none")


def legacy_row(pid: int = 50, **cols) -> Dict[str, Any]:
    """a profile dict as get_profile returns it, from old-column values."""
    row = {"id": pid, "name": "p%d" % pid, "is_admin": False, "allowed_pages": None, "allowed_sides": "music",
           "can_download": True, "hide_explicit": False, "max_rating": None, "request_limit": 0,
           "request_limit_days": 7, "disabled": False}
    row.update(cols)
    return row


# ── installs ─────────────────────────────────────────────────────────────
# each profile: raw column values as they sit in sqlite (allowed_pages is
# the stored TEXT, None = NULL). "flag" = the mapping must keep it on its
# own settings and list it for a look.

SIGNUP_BOTH_PAGES = json.dumps(["discover", "search", "library", "artist-detail", "wishlist", "stats", "help",
                                "issues", "video-discover", "video-search", "video-library"])

SHAPES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "single_admin": {},
    "household": {
        "Dad": {"allowed_sides": "both", "can_download": 1},
        "Mum": {"is_admin": 1, "allowed_sides": None},
        "Teen": {"allowed_sides": "both", "can_download": 0, "max_rating": "PG-13"},
        "Kid": {"allowed_sides": "both", "can_download": 0, "hide_explicit": 1, "max_rating": "PG"},
        "Kid2": {"allowed_sides": "both", "can_download": 0, "hide_explicit": 1, "max_rating": "PG",
                 "allowed_pages": json.dumps(["discover", "search", "library", "video-search", "video-library"]),
                 "request_limit": 3, "request_limit_days": 7, "home_page": "discover"},
        "Grandma": {"allowed_sides": None, "can_download": 0},
    },
    "login_plex": {
        "Co-admin": {"is_admin": 1, "allowed_sides": "both"},
        "plex_maya": {"allowed_sides": "both", "can_download": 0, "allowed_pages": SIGNUP_BOTH_PAGES,
                      "request_limit": 5, "request_limit_days": 7, "home_page": "discover"},
        "plex_liam": {"allowed_sides": None, "can_download": 0,
                      "allowed_pages": json.dumps(["discover", "search", "library", "artist-detail", "wishlist",
                                                   "stats", "help", "issues"]),
                      "request_limit": 2, "request_limit_days": 1},
        "plex_priya": {"allowed_sides": "video", "can_download": 1,
                       "allowed_pages": json.dumps(["help", "issues", "video-discover", "video-search",
                                                    "video-library", "video-wishlist", "video-downloads"])},
    },
    "odd_legacy": {
        "null_pages": {"allowed_pages": None},
        "empty_string_pages": {"allowed_pages": ""},
        "no_pages": {"allowed_pages": "[]"},
        "alias_pages": {"allowed_pages": json.dumps(["downloads", "library-v2", "dashboard"])},
        "every_page_listed": {"allowed_pages": json.dumps(sorted(
            {"dashboard", "sync", "search", "discover", "playlist-explorer", "watchlist", "wishlist",
             "automations", "active-downloads", "library", "tools", "artist-detail", "stats", "import",
             "help", "issues", "podcasts", "audiobooks", "sample-studio", "video-dashboard", "video-search",
             "video-discover", "video-library", "video-watchlist", "video-wishlist", "video-downloads",
             "video-calendar", "video-tools"}))},
        "lowercase_rating": {"allowed_sides": "both", "max_rating": "pg-13"},
        "empty_rating": {"max_rating": ""},
        "rating_and_clean_music_only": {"allowed_sides": "music", "can_download": 1, "hide_explicit": 1,
                                        "max_rating": "R"},
        "null_download": {"can_download": None},
        "negative_limit": {"request_limit": -2},
        "limit_null_days": {"request_limit": 3, "request_limit_days": None},
        "limit_zero_days": {"request_limit": 4, "request_limit_days": 0},
        "garbage_sides": {"allowed_sides": "everything", "can_download": 1},
        "disabled_teen": {"allowed_sides": "both", "can_download": 0, "max_rating": "PG-13", "disabled": 1},
        # these can't be said in the catalogue: kept as they are, flagged
        "flag_unknown_rating": {"allowed_sides": "both", "max_rating": "NC-17"},
        "flag_hydrabase_page": {"allowed_pages": json.dumps(["search", "hydrabase"])},
        "flag_pages_object": {"allowed_pages": json.dumps({"search": True})},
        "flag_huge_limit": {"request_limit": 5000, "request_limit_days": 7},
    },
}

EXPECTED_FLAGGED = {"flag_unknown_rating", "flag_hydrabase_page", "flag_pages_object", "flag_huge_limit"}

LEGACY_COLS = ("allowed_sides", "allowed_pages", "can_download", "hide_explicit", "max_rating", "request_limit",
               "request_limit_days", "home_page", "disabled", "is_admin")


def _conn(db) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db.database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def make_db(tmp_path: Path, name: str = "perm.db"):
    from database.music_database import MusicDatabase
    return MusicDatabase(str(Path(tmp_path) / name))


def unmigrate(db) -> None:
    """put a database back to how a build before P0 left it: no templates
    table, no permission metadata, every profile template-less."""
    conn = _conn(db)
    try:
        conn.execute("DROP TABLE IF EXISTS permission_templates")
        conn.execute("DELETE FROM metadata WHERE key LIKE 'perm_%'")
        conn.execute("UPDATE profiles SET template_id = NULL, perm_overrides = NULL")
        conn.commit()
    finally:
        conn.close()


def insert_profiles(db, rows: Dict[str, Dict[str, Any]]) -> Dict[str, int]:
    """write profiles straight into the old columns. returns {name: id}."""
    conn = _conn(db)
    ids = {}
    try:
        for name, cols in rows.items():
            data = {"name": name, "avatar_color": "#6366f1", "is_admin": 0}
            data.update(cols)
            keys = list(data)
            cur = conn.execute("INSERT INTO profiles (%s) VALUES (%s)" % (", ".join(keys), ", ".join("?" * len(keys))),
                               [data[k] for k in keys])
            ids[name] = cur.lastrowid
        conn.commit()
    finally:
        conn.close()
    return ids


def raw_columns(db) -> Dict[int, Tuple]:
    """every profile's old columns, exactly as stored."""
    conn = _conn(db)
    try:
        return {r["id"]: tuple(r[c] for c in LEGACY_COLS)
                for r in conn.execute("SELECT id, %s FROM profiles" % ", ".join(LEGACY_COLS))}
    finally:
        conn.close()


def layers(db) -> Dict[int, Tuple]:
    conn = _conn(db)
    try:
        return {r["id"]: (r["template_id"], r["perm_overrides"])
                for r in conn.execute("SELECT id, template_id, perm_overrides FROM profiles")}
    finally:
        conn.close()


def run_migration(db) -> None:
    """the boot step, on a database that may have been unmigrated."""
    conn = sqlite3.connect(str(db.database_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        db._add_permission_layers(conn.cursor())
        conn.commit()
    finally:
        conn.close()


def build_install(tmp_path: Path, shape: str, *, migrate: bool = True):
    """(db, {name: id}) for one of SHAPES, as a pre-P0 install, then (by
    default) migrated the way a boot migrates it."""
    db = make_db(tmp_path, "%s.db" % shape)
    unmigrate(db)
    ids = insert_profiles(db, SHAPES[shape])
    if migrate:
        run_migration(db)
    return db, ids


__all__ = ["BUILTIN", "member_perms", "member_ctx", "admin_ctx", "broken_ctx", "legacy_row", "SHAPES",
           "EXPECTED_FLAGGED", "make_db", "unmigrate", "insert_profiles", "raw_columns", "layers",
           "run_migration", "build_install"]


# ── sign-ups (#2166 follow-up 7) ─────────────────────────────────────────

def signup_not_moved(db):
    """a generator for a fixture: the shared test database as an install
    whose old sign-up settings haven't moved to a template yet, the sign-up
    template Guest. anything the test's sign-ins create (a "New sign-ups"
    template, the moved flag) is undone after.

        @pytest.fixture(autouse=True)
        def _signup(): yield from signup_not_moved(web_server.get_database())
    """
    from core import permission_store as PS
    keys = (PS.SIGNUP_MOVED_KEY, "perm_signup_template")
    saved = {k: db.get_metadata(k) for k in keys}
    guest = next((t["id"] for t in db.list_permission_templates() if t.get("slug") == "guest"), None)
    # start clean: a sign-in in an earlier test (one without this fixture)
    # can leave its "New sign-ups" template behind, and a later move reuses
    # it by name instead of building one from this test's settings
    if guest is not None:
        for t in db.list_permission_templates():
            if (t.get("name") or "").lower() == PS.SIGNUP_TEMPLATE_NAME.lower():
                db.delete_permission_template(int(t["id"]), reassign_to=guest)
        if saved.get("perm_signup_template") and not db.get_permission_template(int(saved["perm_signup_template"])):
            saved["perm_signup_template"] = str(guest)
    before = {int(t["id"]) for t in db.list_permission_templates()}
    db.delete_metadata(PS.SIGNUP_MOVED_KEY)
    if guest is not None:
        db.set_metadata("perm_signup_template", str(guest))
    yield
    for t in db.list_permission_templates():
        if int(t["id"]) not in before:
            db.delete_permission_template(int(t["id"]), reassign_to=guest)
    for k, v in saved.items():
        if v is None:
            db.delete_metadata(k)
        else:
            db.set_metadata(k, v)
