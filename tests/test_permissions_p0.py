"""people and permissions P0 (#2166, #2167, #2168): the catalogue, the
layers, can(), requires(), the resolver stub, the mapping to and from the
old columns, the migration on install-shaped databases, the store and the
request context. every test says what it pins down.
"""

from __future__ import annotations

import json

import pytest
from flask import Flask, g

from core import permission_store as S
from core import permissions as P
from tests.permissions_fixtures import (
    BUILTIN, EXPECTED_FLAGGED, SHAPES, admin_ctx, broken_ctx, build_install, insert_profiles, layers,
    legacy_row, make_db, member_ctx, member_perms, raw_columns, run_migration, unmigrate,
)

# ── the catalogue ─────────────────────────────────────────────────────────


def test_catalogue_ids_kinds_and_categories_are_sound():
    ids = [r.id for r in P.CATALOGUE_ROWS]
    assert len(ids) == len(set(ids))
    for r in P.CATALOGUE_ROWS:
        assert r.kind in P.KINDS, r.id
        assert r.category in P.CATEGORY_IDS, r.id
        assert r.label and r.help, r.id
        for other in r.also_in:
            assert other in P.CATEGORY_IDS
        for need in r.needs:
            assert need in P.CATALOGUE, (r.id, need)
        if r.kind == P.CHOICE:
            assert P.CATALOGUE[r.set_row].kind == P.SET
    # every category the rail shows has rows
    assert {r.category for r in P.CATALOGUE_ROWS} == set(P.CATEGORY_IDS)


def test_every_stored_row_has_valid_open_floor_and_default_values():
    for pid in P.STORED_IDS:
        for layer in (P.OPEN_POLICY, P.FLOOR, P.TEMPLATE_DEFAULTS):
            assert P.coerce(pid, layer[pid]) == layer[pid], pid
    for t in P.BUILTIN_TEMPLATES:
        assert P.clean_layer(t["perms"]) == t["perms"]
        assert set(t["perms"]) == set(P.STORED_IDS)


def test_presentation_rows_are_listed_but_never_stored():
    assert set(P.PRESENTATION_IDS) == {"ui.sidebar", "ui.locked", "ui.home_page"}
    for pid in P.PRESENTATION_IDS:
        assert pid not in P.STORED_IDS
        assert pid not in P.OPEN_POLICY and pid not in P.admin_map()
        assert P.CATALOGUE[pid].category == "look"


def test_v1_changes_movies_and_tv_access_split_and_a_moderator():
    assert "video.access" not in P.CATALOGUE
    assert P.CATALOGUE["movies.access"].category == "movies"
    assert P.CATALOGUE["tv.access"].category == "tv"
    assert P.CATALOGUE["requests.moderate"].kind == P.BOOL
    assert P.CATALOGUE["requests.moderate"].category == "requests"
    for t in BUILTIN.values():
        assert t["requests.moderate"] is False and t["requests.see_all"] is False


def test_each_page_has_exactly_one_owner_and_the_page_gate_is_unchanged():
    owners = {}
    for r in P.PAGE_ROWS:
        for pg in r.pages:
            assert pg not in owners, pg
            owners[pg] = r.id
    old = {("/api/import", "import"), ("/api/auto-import", "import"),
           ("/api/playlist-explorer", "playlist-explorer"), ("/api/listening-stats", "stats"),
           ("/api/audiobooks", "audiobooks"), ("/api/sample", "sample-studio")}
    assert set(P.PAGE_EXCLUSIVE_APIS) == old
    assert P.page_for_path("/api/import/inbox") == "import"
    assert P.page_denied("/api/sample/x", ["dashboard"], False)
    assert not P.page_denied("/api/sample/x", None, False)


def test_every_page_a_profile_can_be_given_today_is_in_the_catalogue():
    # the editor's checklist (init.js PROFILE_PAGE_GROUPS) and the sidebar parts
    editor_pages = ['dashboard', 'sync', 'podcasts', 'audiobooks', 'search', 'discover', 'watchlist', 'wishlist',
                    'automations', 'active-downloads', 'library', 'stats', 'playlist-explorer', 'import',
                    'video-dashboard', 'video-search', 'video-discover', 'video-library', 'video-watchlist',
                    'video-wishlist', 'video-downloads', 'video-calendar', 'video-tools', 'tools',
                    'sample-studio']
    for pg in editor_pages:
        assert pg in P.PAGE_OWNER, pg
    from core.ui_features import FEATURES
    for f in FEATURES:
        for pg in f["pages"]:
            assert pg in P.PAGE_OWNER or pg in P.UNGATED_PAGES, (f["id"], pg)


def test_the_overhaul_findings_have_rows_to_enforce():
    # S4 automations, S15 sides and pages, S17 hide explicit, S23 quality, S7 see-all
    assert P.CATALOGUE["automations.personal"].pages == ("automations",)
    assert P.PERSONAL_AUTOMATION_ACTIONS and "run_script" not in P.PERSONAL_AUTOMATION_ACTIONS
    assert "search_and_download" not in P.PERSONAL_AUTOMATION_ACTIONS
    assert P.CATALOGUE["music.access"].legacy == "allowed_sides"
    assert P.CATALOGUE["music.hide_explicit"].kind == P.RESTRICT
    for media in ("music", "movies", "tv"):
        assert P.CATALOGUE["%s.quality" % media].kind == P.SET
        assert P.CATALOGUE["%s.quality_default" % media].kind == P.CHOICE
    assert P.CATALOGUE["requests.see_all"].kind == P.BOOL


def test_catalogue_payload_is_plain_json():
    body = json.loads(json.dumps(P.catalogue_payload()))
    assert [c["id"] for c in body["categories"]] == list(P.CATEGORY_IDS)
    rows = {r["id"]: r for r in body["rows"]}
    assert rows["ui.sidebar"]["presentation"] is True and rows["ui.sidebar"]["stored"] is False
    assert rows["movies.get"]["values"] == ["none", "ask", "download"]
    assert rows["movies.get"]["value_labels"]["ask"] == "Ask first"
    assert rows["tv.max_rating"]["value_labels"]["PG-13"] == "TV-14"


# ── coerce and clamp, per kind ───────────────────────────────────────────


@pytest.mark.parametrize("pid,bad", [
    ("music.access", "yes"), ("music.get", "maybe"), ("books.get", "ask"), ("movies.max_rating", "NC-17"),
    ("movies.max_rating", "follow"), ("requests.limit", {"n": -1}), ("requests.limit", {"n": 2, "days": 0}),
    ("requests.limit", {"n": 2000}), ("requests.limit", 5), ("movies.quality", "everything"),
    ("movies.quality", ["x"]), ("movies.quality_default", "x"), ("movies.quality_ask", ["8k"]),
    ("ui.sidebar", {"off": []}), ("requests.cancel_own", True), ("account.admin", True), ("nope.nope", True),
])
def test_coerce_refuses_what_a_row_cant_hold(pid, bad):
    with pytest.raises(P.PermissionDataError):
        P.coerce(pid, bad)


def test_coerce_cleans_what_it_can():
    assert P.coerce("movies.quality", [3, "2", 3]) == [2, 3]
    assert P.coerce("movies.quality_ask", ["4k", 5]) == [5, "4k"]
    assert P.coerce("requests.limit", {"n": "3"}) == {"n": 3, "days": 7}
    assert P.coerce("tv.max_rating", "follow") == "follow"
    assert P.coerce("music.access", 1) is True


def test_clamp_per_kind():
    assert P.clamp("music.access", True, False) is False
    assert P.clamp("music.access", False, True) is False
    assert P.clamp("music.hide_explicit", False, True) is True      # a policy can force clean music
    assert P.clamp("music.hide_explicit", True, False) is True
    assert P.clamp("movies.get", "download", "ask") == "ask"
    assert P.clamp("movies.get", "none", "download") == "none"
    assert P.clamp("movies.max_rating", "any", "PG-13") == "PG-13"
    assert P.clamp("movies.max_rating", "PG", "R") == "PG"
    assert P.clamp("requests.limit", {"n": 0, "days": 7}, {"n": 20, "days": 7}) == {"n": 20, "days": 7}
    assert P.clamp("requests.limit", {"n": 5, "days": 1}, {"n": 20, "days": 7}) == {"n": 20, "days": 7}
    assert P.clamp("requests.limit", {"n": 2, "days": 7}, {"n": 20, "days": 7}) == {"n": 2, "days": 7}
    assert P.clamp("requests.limit", {"n": 9, "days": 1}, {"n": 0, "days": 7}) == {"n": 9, "days": 1}
    assert P.clamp("movies.quality", P.ALL, [1, 2]) == [1, 2]
    assert P.clamp("movies.quality", [2, 3], [1, 2]) == [2]
    assert P.clamp("movies.quality", [2, 3], P.ALL) == [2, 3]
    assert P.clamp("movies.quality_ask", [3], ["4k"]) == [3, "4k"]


# ── effective ────────────────────────────────────────────────────────────


def test_admins_skip_every_layer():
    tight = {"music.get": "none", "movies.access": False, "requests.limit": {"n": 1, "days": 7}}
    eff = P.effective(tight, BUILTIN["kids"], {"music.access": False}, {"id": 7, "is_admin": True})
    assert eff == P.admin_map()
    assert P.effective(tight, None, None, {"id": 1}) == P.admin_map()
    assert eff["music.hide_explicit"] is False and eff["requests.moderate"] is True


def test_no_template_fails_closed_to_the_floor():
    eff = P.effective(None, None, {}, {"id": 9})
    assert eff == P.floor_map()
    assert eff["music.get"] == "none" and eff["music.hide_explicit"] is True and eff["music.access"] is False


def test_unreadable_layers_raise_instead_of_guessing():
    with pytest.raises(P.PermissionDataError):
        P.effective("{not json", BUILTIN["adult"], {}, {"id": 9})
    with pytest.raises(P.PermissionDataError):
        P.effective(None, dict(BUILTIN["adult"], **{"music.get": "lots"}), {}, {"id": 9})
    with pytest.raises(P.PermissionDataError):
        P.effective(None, BUILTIN["adult"], {"music.get": True}, {"id": 9})


def test_an_override_for_a_row_retired_later_is_ignored_not_fatal():
    eff = P.effective(None, BUILTIN["adult"], {"music.retired_thing": True}, {"id": 9})
    assert "music.retired_thing" not in eff


def test_a_template_missing_a_row_added_later_gets_the_row_default():
    tpl = dict(BUILTIN["teen"])
    del tpl["requests.moderate"]
    del tpl["video.youtube"]
    eff = P.effective(None, tpl, {}, {"id": 9})
    assert eff["requests.moderate"] is False and eff["video.youtube"] is True


def test_an_override_above_the_ceiling_is_kept_but_inert_and_comes_back():
    policy = {"movies.get": "ask"}
    ov = {"movies.get": "download"}
    eff = P.effective(policy, BUILTIN["teen"], ov, {"id": 9})
    assert eff["movies.get"] == "ask"
    rows = {r["id"]: r for r in P.explain(policy, BUILTIN["teen"], ov, {"id": 9})}
    assert rows["movies.get"]["capped"] and rows["movies.get"]["inert"]
    assert rows["movies.get"]["override_value"] == "download"
    assert rows["movies.get"]["source"] == "override" and rows["movies.get"]["template_value"] == "ask"
    # lifting the ceiling brings the personal change back, nothing was lost
    assert P.effective(None, BUILTIN["teen"], ov, {"id": 9})["movies.get"] == "download"


def test_explain_marks_where_each_value_comes_from():
    rows = {r["id"]: r for r in P.explain(None, BUILTIN["teen"], {"music.get": "download"}, {"id": 9})}
    assert rows["music.get"]["source"] == "override" and not rows["music.get"]["capped"]
    assert rows["movies.get"]["source"] == "template" and not rows["movies.get"]["override"]
    assert all(r["source"] == "admin" for r in P.explain(None, None, {}, {"id": 1}))
    assert all(r["source"] == "floor" for r in P.explain(None, None, {}, {"id": 9}))
    assert {r["source"] for r in P.explain(None, BUILTIN["adult"], {}, {"id": 9}, base_source="legacy")} == {"legacy"}


def test_tv_rating_follows_movies_unless_set():
    assert member_perms("teen")["tv.max_rating"] == "PG-13"
    assert member_perms("teen", **{"movies.max_rating": "PG"})["tv.max_rating"] == "PG"
    assert member_perms("teen", **{"tv.max_rating": "R"})["tv.max_rating"] == "R"
    # a movie ceiling reaches tv too while the tv ceiling follows it
    assert member_perms("adult", policy={"movies.max_rating": "PG"})["tv.max_rating"] == "PG"
    # with a tv ceiling of its own, a tv value of its own can sit above the movie one
    assert member_perms("adult", policy={"movies.max_rating": "PG", "tv.max_rating": "any"})["tv.max_rating"] == "PG"
    assert member_perms("adult", policy={"movies.max_rating": "PG", "tv.max_rating": "any"},
                        **{"tv.max_rating": "R"})["tv.max_rating"] == "R"


def test_quality_default_only_counts_inside_the_allowed_set():
    eff = member_perms("adult", **{"movies.quality": [3, 4], "movies.quality_default": 4})
    assert eff["movies.quality_default"] == 4
    eff = member_perms("adult", **{"movies.quality": [3], "movies.quality_default": 4})
    assert eff["movies.quality_default"] is None
    # the policy's "members start on" is the fallback
    eff = member_perms("adult", policy={"movies.quality_default": 3}, **{"movies.quality": [3, 4]})
    assert eff["movies.quality_default"] == 3
    # 4k always asks: a policy flag token unions in
    eff = member_perms("adult", policy={"movies.quality_ask": ["4k"]}, **{"movies.quality_ask": [7]})
    assert eff["movies.quality_ask"] == [7, "4k"]


def test_diff_overrides_and_impact():
    ov = P.diff_overrides(BUILTIN["teen"], dict(BUILTIN["teen"], **{"music.get": "download"}))
    assert ov == {"music.get": "download"}
    moved = P.impact({5: member_perms("teen")}, {5: member_perms("teen", **{"music.get": "download"})})
    assert moved == {5: [{"id": "music.get", "before": "ask", "after": "download"}]}


# ── can() ────────────────────────────────────────────────────────────────


def test_can_answers_per_kind():
    teen = member_ctx("teen")
    assert P.can(teen, "music.access")
    assert P.can(teen, "movies.get") and not P.can(teen, "movies.get", "download")
    assert not P.can(member_ctx("teen", **{"movies.get": "none"}), "movies.get")
    assert P.can(teen, "movies.max_rating", "PG") and not P.can(teen, "movies.max_rating", "R")
    assert P.can(teen, "tv.max_rating", "TV-14") and not P.can(teen, "tv.max_rating", "TV-MA")
    assert not P.can(teen, "movies.max_rating", "NR")            # unrated stays out under a cap
    assert P.can(member_ctx(), "movies.max_rating", "NR")        # no cap: everything
    limited = member_ctx(**{"requests.limit": {"n": 2, "days": 7}})
    assert P.can(limited, "requests.limit", 1) and not P.can(limited, "requests.limit", 2)
    assert P.can(member_ctx(), "requests.limit", 999)
    q = member_ctx(**{"movies.quality": [3], "movies.quality_ask": [3]})
    assert P.can(q, "movies.quality", 3) and not P.can(q, "movies.quality", 4)
    assert P.can(q, "movies.quality_ask", 3) and not P.can(q, "movies.quality_ask", 4)
    assert P.can(member_ctx("kids"), "music.hide_explicit") and not P.can(member_ctx(), "music.hide_explicit")
    assert P.can(teen, "requests.cancel_own") and P.can(teen, "issues.report")
    assert not P.can(teen, "admin.everything") and not P.can(teen, "account.admin")


def test_can_for_admins_and_unbuildable_maps():
    admin = admin_ctx()
    assert P.can(admin, "movies.get", "download") and P.can(admin, "admin.everything")
    assert P.can(admin, "account.admin")
    assert not P.can(admin, "music.hide_explicit") and not P.can(admin, "movies.quality_ask", 3)
    broken = broken_ctx()
    assert not P.can(broken, "music.access") and not P.can(broken, "movies.get")
    assert P.can(broken, "music.hide_explicit")                   # a restriction fails closed: on
    assert not P.can(member_ctx(), "made.up")


def test_presentation_never_grants():
    with pytest.raises(ValueError):
        P.can(member_ctx(), "ui.sidebar")
    with pytest.raises(ValueError):
        P.requires("ui.locked")


def test_can_reads_the_request_context():
    app = Flask(__name__)
    with app.test_request_context("/"):
        g.profile_id, g.is_admin, g.perms = 9, False, member_perms("teen")
        assert P.can(None, "music.access") and not P.can(None, "music.get", "download")
        g.perms = None
        assert not P.can(None, "music.access")
        g.is_admin = True
        assert P.can(None, "music.get", "download")
    assert not P.can(None, "music.access")       # no request, no context: no


def test_moderator_answers_other_peoples_requests_never_their_own():
    mod = member_ctx(profile_id=12, **{"requests.moderate": True})
    assert P.may_moderate(mod, 30) and P.may_moderate(mod, 1)
    assert not P.may_moderate(mod, 12)
    assert not P.may_moderate(member_ctx(profile_id=12), 30)
    assert P.may_moderate(admin_ctx(), 1)
    assert not P.may_moderate(broken_ctx(12), 30)
    assert P.may_see_request(member_ctx(profile_id=12), 12)
    assert not P.may_see_request(member_ctx(profile_id=12), 13)
    assert P.may_see_request(member_ctx(profile_id=12, **{"requests.see_all": True}), 13)
    assert P.may_see_request(mod, 13)


# ── requires(), refusal and the declarations ─────────────────────────────


def _app_with(view_ctx):
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.profile_id, g.is_admin, g.perms = view_ctx["profile_id"], view_ctx["is_admin"], view_ctx["perms"]

    @app.route("/books")
    @P.requires("books.access")
    def books():
        return "ok"

    @app.route("/grab")
    @P.requires("movies.get", "download")
    def grab():
        return "ok"
    return app


def test_requires_refuses_calmly_and_lets_the_allowed_through():
    c = _app_with(member_ctx("teen", **{"books.access": False})).test_client()
    r = c.get("/books")
    assert r.status_code == 403
    body = r.get_json()
    assert body == P.refusal_body("books.access")
    assert body["error"] == "not_allowed" and body["permission"] == "books.access"
    assert "Audiobooks isn't part of your profile" in body["message"]
    assert c.get("/grab").status_code == 403
    c = _app_with(member_ctx("adult")).test_client()
    assert c.get("/books").status_code == 200 and c.get("/grab").status_code == 200
    c = _app_with(admin_ctx()).test_client()
    assert c.get("/books").status_code == 200
    c = _app_with(broken_ctx()).test_client()
    assert c.get("/books").status_code == 403


def test_requires_catches_typos_at_import():
    with pytest.raises(ValueError):
        P.requires("books.acess")


def test_declarations_are_found_through_wrappers():
    import functools

    from core.profile_context import admin_only

    def view():
        return "x"

    def outer(fn):
        @functools.wraps(fn)
        def w(*a, **k):
            return fn(*a, **k)
        return w
    assert P.declared_access(outer(P.requires("music.stats")(view))) == ("perm", "music.stats")
    assert P.declared_access(outer(admin_only(view))) == ("admin", None)
    assert P.declared_access(P.public(lambda: 1)) == ("public", None)
    assert P.declared_access(P.self_only(lambda: 1)) == ("self", None)
    assert P.declared_access(lambda: 1) is None


# ── request quality (P0 stub = today's behaviour) ────────────────────────


def test_resolve_request_quality_keeps_todays_behaviour():
    admin = {"id": 1, "is_admin": True}
    assert P.resolve_request_quality(admin, "movie", 4).as_dict() == {
        "profile_id": 4, "needs_approval": False, "refused": False, "error": None, "note": ""}
    asker = legacy_row(can_download=False)
    d = P.resolve_request_quality(asker, "movie", 4)
    assert d.profile_id is None and d.needs_approval and not d.refused    # member picks dropped today
    d = P.resolve_request_quality(asker, "music", "4")
    assert d.profile_id == 4 and d.needs_approval                          # music takes the pick
    d = P.resolve_request_quality(legacy_row(), "tv", None, perms=member_perms("adult"))
    assert d.profile_id is None and not d.needs_approval
    d = P.resolve_request_quality(legacy_row(), "tv", 2, perms=member_perms("teen"))
    assert d.needs_approval
    with pytest.raises(ValueError):
        P.resolve_request_quality(asker, "book", 1)


# ── old columns: the mapping and the cache ───────────────────────────────


@pytest.mark.parametrize("slug", ["adult", "teen", "kids", "guest"])
def test_builtins_round_trip_through_the_old_columns(slug):
    cols = P.legacy_from_perms(BUILTIN[slug])
    perms, reasons = P.perms_from_legacy(legacy_row(**cols))
    assert reasons == [] and perms == BUILTIN[slug]
    assert cols["allowed_pages"] is None


def test_builtins_are_the_init_js_presets():
    want = {"adult": ("both", True, False, None), "teen": ("both", False, False, "PG-13"),
            "kids": ("both", False, True, "PG"), "guest": ("music", False, False, None)}
    for slug, (sides, dl, clean, rating) in want.items():
        cols = P.legacy_from_perms(BUILTIN[slug])
        assert (cols["allowed_sides"], cols["can_download"], cols["hide_explicit"], cols["max_rating"]) == \
            (sides, dl, clean, rating), slug
        assert cols["request_limit"] == 0


def test_a_page_list_always_hides_chat_today_and_the_mapping_says_so():
    # a non-null page list never holds 'chat' (the server strips it), and the
    # shell hides chat for any page not in the list
    perms, reasons = P.perms_from_legacy(legacy_row(allowed_pages=["search"]))
    assert reasons == [] and perms["chat.use"] is False and perms["music.search"] is True
    assert P.perms_from_legacy(legacy_row())[0]["chat.use"] is True


def test_cache_matches_reports_real_behaviour_changes_only():
    prof = legacy_row(allowed_pages=None, max_rating="pg-13", request_limit=-1)
    eff = member_perms("adult", **{"movies.access": False, "tv.access": False, "movies.max_rating": "PG-13"})
    assert P.cache_matches(prof, eff) == []
    assert P.cache_matches(prof, member_perms("adult")) == ["allowed_sides", "cap"]
    split = member_perms("adult", **{"movies.access": False, "tv.access": False, "movies.max_rating": "PG-13",
                                     "movies.get": "ask"})
    assert P.cache_matches(prof, split) == []                       # the cache can't see a split
    assert P.cache_matches(prof, split, strict=True) == ["movies.get"]  # the migration's verify can
    assert P.cache_matches(prof, member_perms("adult"), strict=True) == ["allowed_sides", "cap", "tv.max_rating"]


def test_signup_overrides_reproduce_what_signup_gives_today():
    from core.security.signup_defaults import SignupAccess, signup_access
    for cfg in ({}, {"security.signup_sides": "both", "security.signup_request_limit": 4},
                {"security.signup_sides": "video"}):
        access = signup_access(lambda k, d, cfg=cfg: cfg.get(k, d)) if cfg else SignupAccess()
        ov = P.signup_overrides(access, BUILTIN["guest"])
        eff = P.effective(None, BUILTIN["guest"], ov, {"id": 9})
        prof = legacy_row(allowed_sides=access.allowed_sides or "music", can_download=False,
                          allowed_pages=list(access.allowed_pages), request_limit=access.request_limit,
                          request_limit_days=access.request_limit_days)
        assert P.cache_matches(prof, eff) == [], cfg


def test_summary_is_built_from_the_map():
    s = P.summarize(member_perms("teen", **{"requests.limit": {"n": 3, "days": 7}}), name="Maya")
    texts = " ".join(line["text"] for line in s["lines"])
    assert "Movies: asks first, up to PG-13." in texts
    assert "TV shows: asks first, up to TV-14." in texts
    assert "Up to 3 asks a week." in texts
    assert s["sentence"] == "Maya uses music, movies and TV shows."
    assert P.summarize({}, is_admin=True)["lines"] == []
    assert "Audiobooks" in P.summarize(member_perms("guest", **{"books.access": False}))["never"]


# ── the migration on install-shaped databases ────────────────────────────

EXPECTED_TEMPLATE = {"Dad": "adult", "Teen": "teen", "Kid": "kids", "Kid2": "kids", "Grandma": "guest",
                     "null_download": "guest", "disabled_teen": "teen", "lowercase_rating": "adult"}


@pytest.mark.parametrize("shape", list(SHAPES))
def test_migration_changes_nothing_anyone_can_see(tmp_path, shape):
    db, ids = build_install(tmp_path, shape, migrate=False)
    before = raw_columns(db)
    behaviour = {pid: P.legacy_behaviour(db.get_profile(pid)) for pid in before}
    run_migration(db)
    # not one old column moved, for anyone
    assert raw_columns(db) == before
    slugs = {t["id"]: t["slug"] for t in db.list_permission_templates()}
    flagged = S.needs_look(db)
    lay = layers(db)
    for name, pid in ids.items():
        prof = db.get_profile(pid)
        eff, source = S.effective_for(db, prof)
        if P.is_admin_row(prof):
            assert source == "admin" and lay[pid][0] is None
            continue
        if name in EXPECTED_FLAGGED:
            assert source == "legacy" and lay[pid][0] is None, name
            assert flagged.get(str(pid)), name
            continue
        assert source == "template", (name, flagged.get(str(pid)))
        assert str(pid) not in flagged
        # effective, through the cache, behaves exactly like the old columns
        assert P.cache_matches(prof, eff) == [], name
        assert P.legacy_behaviour(dict(prof, **P.legacy_from_perms(eff))) == behaviour[pid], name
        if name in EXPECTED_TEMPLATE:
            assert slugs[lay[pid][0]] == EXPECTED_TEMPLATE[name], name
    # the four presets on a preset match carry no personal changes
    for name in ("Dad", "Teen", "Kid", "Grandma"):
        if name in ids:
            assert json.loads(lay[ids[name]][1]) == {}, name
    # admin profile 1 never gets a template
    assert lay[1][0] is None


@pytest.mark.parametrize("shape", list(SHAPES))
def test_migration_rerun_is_a_no_op(tmp_path, shape):
    db, _ids = build_install(tmp_path, shape)
    first = (layers(db), raw_columns(db), db.list_permission_templates(), S.needs_look(db))
    run_migration(db)
    run_migration(db)
    again = (layers(db), raw_columns(db), db.list_permission_templates(), S.needs_look(db))
    assert again == first


def test_migration_seeds_templates_policy_and_signup_from_today(tmp_path):
    db = make_db(tmp_path)
    unmigrate(db)
    db.set_metadata("ui_features_default", json.dumps({"off": ["tools", "stats", "bogus"], "locked": True}))
    run_migration(db)
    tpls = db.list_permission_templates()
    assert [t["slug"] for t in tpls] == ["adult", "teen", "kids", "guest"]
    for t in tpls:
        assert json.loads(t["perms"]) == BUILTIN[t["slug"]]
        assert json.loads(t["sidebar"]) == {"off": ["stats", "tools"], "locked": True}
        assert t["builtin"] == 1
    assert db.get_metadata("perm_policy") == "{}"
    assert S.load_policy(db) == P.OPEN_POLICY
    guest = next(t["id"] for t in tpls if t["slug"] == "guest")
    assert S.signup_template_id(db) == guest
    assert db.get_metadata("perm_migrated_v1") == "1"


def test_a_fresh_install_has_the_templates(tmp_path):
    db = make_db(tmp_path, "fresh.db")
    assert len(db.list_permission_templates()) == 4
    assert S.effective_for(db, db.get_profile(1)) == (P.admin_map(), "admin")


def test_a_profile_made_the_old_way_later_is_mapped_next_boot(tmp_path):
    db, _ids = build_install(tmp_path, "single_admin")
    pid = db.create_profile("Newbie", can_download=False, allowed_sides="both")
    assert layers(db)[pid][0] is None
    assert S.effective_for(db, db.get_profile(pid))[1] == "legacy"   # runs on its columns meanwhile
    db.remap_legacy_profiles()
    tid = layers(db)[pid][0]
    # closest built-in: teen, with "any rating" as the one personal change
    assert db.get_permission_template(tid)["slug"] == "teen"
    assert json.loads(layers(db)[pid][1]) == {"movies.max_rating": "any"}
    eff, source = S.effective_for(db, db.get_profile(pid))
    assert source == "template" and P.cache_matches(db.get_profile(pid), eff) == []


def test_mapping_is_skipped_whole_when_the_policy_is_unreadable(tmp_path):
    db, _ = build_install(tmp_path, "single_admin")
    db.set_metadata("perm_policy", "{broken")
    pid = db.create_profile("Late")
    db.remap_legacy_profiles()
    assert layers(db)[pid][0] is None


# ── the store ────────────────────────────────────────────────────────────


def test_context_for_fails_closed(tmp_path):
    db, ids = build_install(tmp_path, "household")
    assert S.context_for(db, 1)["is_admin"]
    assert S.context_for(db, ids["Teen"])["perms"]["movies.get"] == "ask"
    assert S.context_for(db, 99999)["perms"] is None
    assert S.context_for(db, "x")["perms"] is None
    db.set_metadata("perm_policy", "{broken")
    ctx = S.context_for(db, ids["Teen"])
    assert ctx["perms"] is None and not P.can(ctx, "music.access")
    with pytest.raises(S.PermissionsUnavailable):
        S.effective_for(db, db.get_profile(ids["Teen"]))
    with pytest.raises(S.PermissionsUnavailable):
        S.load_policy(db)


def test_a_template_that_is_gone_fails_closed(tmp_path):
    db, ids = build_install(tmp_path, "household")
    db.set_profile_permission_layers(ids["Dad"], 999, "{}")
    with pytest.raises(S.PermissionsUnavailable):
        S.effective_for(db, db.get_profile(ids["Dad"]))


def test_old_editor_edit_becomes_personal_changes(tmp_path):
    db, ids = build_install(tmp_path, "household")
    dad = ids["Dad"]
    assert db.update_profile(dad, can_download=0)
    tid, ov = layers(db)[dad]
    assert db.get_permission_template(tid)["slug"] == "adult"
    ov = json.loads(ov)
    assert ov["music.get"] == "ask" and ov["movies.get"] == "ask" and ov["books.get"] == "none"
    prof = db.get_profile(dad)
    assert P.cache_matches(prof, S.effective_for(db, prof)[0]) == []


def test_old_editor_edit_keeps_a_split_it_cant_see(tmp_path):
    db, ids = build_install(tmp_path, "household")
    teen = ids["Teen"]
    S.change_profile(db, db.get_profile(teen), {"set": {"movies.get": "download", "music.get": "ask"}})
    # the old editor saves the clean-music switch; downloads didn't move
    db.update_profile(teen, hide_explicit=1, can_download=0)
    ov = json.loads(layers(db)[teen][1])
    assert ov.get("movies.get") == "download" and ov.get("music.hide_explicit") is True


def test_old_editor_edit_the_catalogue_cant_say_goes_back_to_its_own_settings(tmp_path):
    db, ids = build_install(tmp_path, "household")
    db.update_profile(ids["Teen"], max_rating="NC-17")
    assert layers(db)[ids["Teen"]][0] is None
    prof = db.get_profile(ids["Teen"])
    assert prof["max_rating"] == "NC-17"
    assert S.effective_for(db, prof)[1] == "legacy"


def test_template_change_reaches_followers_and_their_columns(tmp_path):
    db, ids = build_install(tmp_path, "household")
    teen_tpl = layers(db)[ids["Teen"]][0]
    dry = S.change_template(db, teen_tpl, {"perms": {"movies.max_rating": "PG"}}, dry_run=True)
    assert dry["impact"]["changes"][0]["profile_id"] == ids["Teen"]
    assert db.get_profile(ids["Teen"])["max_rating"] == "PG-13"           # dry run wrote nothing
    S.change_template(db, teen_tpl, {"perms": {"movies.max_rating": "PG"}})
    assert db.get_profile(ids["Teen"])["max_rating"] == "PG"
    assert db.get_profile(ids["Dad"])["max_rating"] is None


def test_policy_ceiling_writes_the_cache_and_lifts_cleanly(tmp_path):
    db, ids = build_install(tmp_path, "household")
    out = S.change_policy(db, {"set": {"music.get": "ask"}}, dry_run=True)
    assert out["impact"]["reaches"] == 5
    moved = {c["profile_id"] for c in out["impact"]["changes"]}
    assert moved == {ids["Dad"]}
    assert db.get_profile(ids["Dad"])["can_download"] is True
    S.change_policy(db, {"set": {"music.get": "ask"}})
    assert db.get_profile(ids["Dad"])["can_download"] is False
    assert json.loads(db.get_metadata("perm_policy")) == {"music.get": "ask"}
    S.change_policy(db, {"reset": ["music.get"]})
    assert db.get_profile(ids["Dad"])["can_download"] is True
    assert db.get_metadata("perm_policy") == "{}"
    # the admin is never limited
    assert db.get_profile(ids["Mum"])["can_download"] is True


def test_each_layer_only_takes_rows_in_its_scope(tmp_path):
    db, ids = build_install(tmp_path, "household")
    teen_tpl = layers(db)[ids["Teen"]][0]
    with pytest.raises(S.PermissionRequestError):
        S.change_template(db, teen_tpl, {"perms": {"account.own_library": False}})
    with pytest.raises(S.PermissionRequestError):
        S.create_template(db, {"name": "x", "perms": {"account.own_library": False}})
    S.change_policy(db, {"set": {"account.own_library": False}})
    S.change_profile(db, db.get_profile(ids["Teen"]), {"set": {"account.own_library": True}})


def test_policy_refuses_rows_it_cant_cap(tmp_path):
    db, _ = build_install(tmp_path, "single_admin")
    with pytest.raises(S.PermissionRequestError):
        S.change_policy(db, {"set": {"music.get": "lots"}})
    with pytest.raises(S.PermissionRequestError):
        S.change_policy(db, {"set": {"ui.sidebar": {"off": []}}})


def test_change_profile_resets_rows_categories_and_all(tmp_path):
    db, ids = build_install(tmp_path, "household")
    kid = ids["Kid2"]
    out = S.change_profile(db, db.get_profile(kid), {"set": {"music.get": "download"}})
    assert out["overrides"]["music.get"] == "download" and "music" in out["changed_categories"]
    assert db.get_profile(kid)["can_download"] is True
    assert {"id": "music.get", "before": "ask", "after": "download"} in out["changes"]
    out = S.change_profile(db, db.get_profile(kid), {"reset": ["music.get"]})
    assert "music.get" not in out["overrides"]
    out = S.change_profile(db, db.get_profile(kid), {"reset_categories": ["movies"]})
    assert not any(P.CATALOGUE[k].category == "movies" or "movies" in P.CATALOGUE[k].also_in
                   for k in out["overrides"])
    out = S.change_profile(db, db.get_profile(kid), {"reset": "all"})
    assert out["overrides"] == {} and out["mode"] == "template"
    assert db.get_profile(kid)["allowed_pages"] is None and db.get_profile(kid)["request_limit"] == 0


def test_change_profile_switches_template_and_keeps_personal_changes(tmp_path):
    db, ids = build_install(tmp_path, "household")
    out = S.change_profile(db, db.get_profile(ids["Teen"]), {"set": {"requests.limit": {"n": 2, "days": 7}}})
    adult = next(t["id"] for t in db.list_permission_templates() if t["slug"] == "adult")
    out = S.change_profile(db, db.get_profile(ids["Teen"]), {"template_id": adult})
    assert out["template"]["slug"] == "adult" and out["overrides"] == {"requests.limit": {"n": 2, "days": 7}}
    assert db.get_profile(ids["Teen"])["can_download"] is True
    with pytest.raises(S.PermissionRequestError):
        S.change_profile(db, db.get_profile(1), {"set": {"music.get": "ask"}})
    with pytest.raises(S.PermissionRequestError):
        S.change_profile(db, db.get_profile(ids["Teen"]), {"template_id": 999})
    with pytest.raises(S.PermissionRequestError):
        S.change_profile(db, db.get_profile(ids["Teen"]), {"set": {"music.get": "lots"}})


def test_change_profile_moves_a_flagged_profile_onto_a_template(tmp_path):
    db, ids = build_install(tmp_path, "odd_legacy")
    pid = ids["flag_hydrabase_page"]
    out = S.change_profile(db, db.get_profile(pid), {"set": {"chat.use": True}})
    assert out["mode"] == "template" and out["needs_look"] == []
    assert str(pid) not in S.needs_look(db)


def test_templates_create_rename_delete(tmp_path):
    db, ids = build_install(tmp_path, "household")
    made = S.create_template(db, {"name": "Movie buff", "from_template_id": layers(db)[ids["Teen"]][0],
                                  "perms": {"movies.get": "download"}})["template"]
    assert made["perms"]["movies.get"] == "download" and made["perms"]["music.get"] == "ask"
    with pytest.raises(S.PermissionRequestError):
        S.create_template(db, {"name": "movie BUFF"})
    with pytest.raises(S.PermissionRequestError):
        S.create_template(db, {"name": ""})
    teen_tpl = layers(db)[ids["Teen"]][0]
    with pytest.raises(S.PermissionRequestError) as e:
        S.delete_template(db, teen_tpl)
    assert e.value.code == "has_followers"
    out = S.delete_template(db, teen_tpl, reassign_to=made["id"])
    assert [f["id"] for f in out["moved"]] == [ids["Teen"]]
    assert layers(db)[ids["Teen"]][0] == made["id"]
    assert db.get_profile(ids["Teen"])["can_download"] is False        # music still asks
    with pytest.raises(S.PermissionRequestError) as e:
        S.delete_template(db, S.signup_template_id(db))
    assert e.value.code == "signup_template"
    S.change_template(db, made["id"], {"name": "Film fan", "sidebar": {"off": ["stats"], "locked": True}})
    t = db.get_permission_template(made["id"])
    assert t["name"] == "Film fan" and json.loads(t["sidebar"]) == {"off": ["stats"], "locked": True}


# ── the request context ──────────────────────────────────────────────────

web_server = pytest.importorskip("web_server")


def _ctx_for(pid, path="/api/nothing-here"):
    from flask import session
    app = web_server.app
    rc = app.test_request_context(path)
    rc.push()
    session.clear()
    session["profile_id"] = pid
    resp = web_server._set_profile_context()
    return rc, resp


def _member(**cols):
    db = web_server.get_database()
    from uuid import uuid4
    pid = db.create_profile(name="perm_%s" % uuid4().hex[:8], **cols)
    db.remap_legacy_profiles()
    return pid


def test_request_context_loads_the_effective_map():
    pid = _member(can_download=False, allowed_sides="both")
    rc, resp = _ctx_for(pid)
    try:
        assert resp is None
        assert g.perms_source == "template" and g.perms["movies.get"] == "ask"
        assert not P.can(None, "music.get", "download") and P.can(None, "music.get")
    finally:
        rc.pop()
    rc, resp = _ctx_for(1)
    try:
        assert g.perms_source == "admin" and g.perms == P.admin_map()
    finally:
        rc.pop()


def test_request_context_fails_closed_when_the_map_cant_be_built(monkeypatch):
    pid = _member(can_download=True, allowed_sides="both")

    def boom(db, profile):
        raise S.PermissionsUnavailable("test")
    monkeypatch.setattr(S, "effective_for", boom)
    rc, resp = _ctx_for(pid)
    try:
        assert resp is None
        assert g.perms is None and g.can_download is False and g.is_admin is False
        assert g.allowed_sides == "none"
        assert not P.can(None, "music.access")
    finally:
        rc.pop()
    rc, resp = _ctx_for(pid, "/api/import/inbox")
    try:
        assert resp is not None and resp[1] == 403
    finally:
        rc.pop()
