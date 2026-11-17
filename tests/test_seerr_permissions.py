"""seerr compat follows the acting profile's permissions (#2166, piece C).

the real blueprint and service over a temp video db (tests/test_seerr_compat.py's
harness), with the acting profile's can() context stood in from
tests/permissions_fixtures.py: members on built-in templates with personal
changes, a moderator, an admin, and a profile whose map couldn't be built.
the quality resolver is P0's stub unless a test stands a decision in.
"""

from __future__ import annotations

import pytest

import api.seerr_compat as sc
from core import permissions as P
from core.seerr_compat import shapes
from core.seerr_compat.service import NOT_LINKED, legacy_context
from tests.permissions_fixtures import admin_ctx, broken_ctx, member_ctx
from tests.test_seerr_compat import (ASKED_MOVIE, BASE, KEY, NEW_MOVIE, NEW_SHOW, PROFILES,  # noqa: F401
                                     client, h, resolve_like_the_plugin)

QUALITIES = [{"id": 0, "name": "Default", "media_type": None, "tags": []},
             {"id": 3, "name": "Movie 1080p", "media_type": "movie", "tags": []},
             {"id": 7, "name": "Movie 4K", "media_type": "movie", "tags": ["4k"]},
             {"id": 9, "name": "TV 4K", "media_type": "tv", "tags": ["4k"]},
             {"id": 11, "name": "TV 720p", "media_type": "tv", "tags": []}]


@pytest.fixture
def ctxs(h, monkeypatch):
    """{profile id: can() context} the service reads; anyone else reads its
    old columns like before."""
    table = {}
    h.svc._perms_for = lambda p: table.get(p.get("id")) or legacy_context(p)
    h.svc._quality_profiles = lambda: QUALITIES

    def put(pid, ctx, name=None, **row):
        monkeypatch.setitem(PROFILES, pid, dict({"id": pid, "name": name or "p%d" % pid, "is_admin": False,
                                                 "allowed_sides": "both", "can_download": True}, **row))
        table[pid] = ctx
        h.svc._ctx_cache.clear()
        return pid
    return put


def _post(client, user, body):
    return client.post(BASE + "/request", json=body, headers=KEY(user))


def _perms(client, user):
    return client.get(BASE + "/auth/me", headers=KEY(user)).get_json()["permissions"]


_ASK_BITS = (shapes.PERM_REQUEST | shapes.PERM_REQUEST_MOVIE | shapes.PERM_REQUEST_TV | shapes.PERM_AUTO_APPROVE
             | shapes.PERM_AUTO_APPROVE_MOVIE | shapes.PERM_AUTO_APPROVE_TV)


def _ask_bits(client, user):
    """the request and auto-approve bits only (4K and advanced follow the
    quality profiles, tested on their own)."""
    return _perms(client, user) & _ASK_BITS


# ---- who a call acts for (S7, decision 12) -------------------------------------

def test_unknown_users_are_refused_when_settings_say_so(client, h):
    h.svc._unknown_users = lambda: "refuse"
    before = len(h.db.list_video_requests())
    for hdr in (KEY(), KEY(99), KEY("garbage")):
        r = client.get(BASE + "/discover/trending", headers=hdr)
        assert r.status_code == 403 and r.get_json()["message"] == NOT_LINKED
        assert _post_hdr(client, hdr).status_code == 403
    assert len(h.db.list_video_requests()) == before and h.wishlist(1) == []
    # /status stays open, a known user still works
    assert client.get(BASE + "/status").status_code == 200
    assert client.get(BASE + "/discover/trending", headers=KEY(2)).status_code == 200


def _post_hdr(client, hdr):
    return client.post(BASE + "/request", json={"mediaType": "movie", "mediaId": NEW_MOVIE}, headers=hdr)


def test_the_bare_key_still_passes_a_connection_test_but_is_nobody(client, h):
    """a client's Test Connection reads /auth/me with only the key: a user
    with no rights and id 0, which itself acts as nobody."""
    h.svc._unknown_users = lambda: "refuse"
    me = client.get(BASE + "/auth/me", headers=KEY())
    assert me.status_code == 200 and me.get_json()["id"] == 0 and me.get_json()["permissions"] == 0
    assert client.get(BASE + "/auth/me", headers=KEY(0)).status_code == 200
    assert client.get(BASE + "/discover/trending", headers=KEY(0)).status_code == 403
    assert client.get(BASE + "/auth/me", headers=KEY(99)).status_code == 403


def test_refusing_drops_unlinked_jellyfin_users_from_the_user_list(client, h):
    users = client.get(BASE + "/user?take=100", headers=KEY(1)).get_json()["results"]
    assert resolve_like_the_plugin(users, "d" * 32, "guest") == 1          # today: the fallback
    h.svc._unknown_users = lambda: "refuse"
    users = client.get(BASE + "/user?take=100", headers=KEY(1)).get_json()["results"]
    assert resolve_like_the_plugin(users, "d" * 32, "guest") is None
    assert resolve_like_the_plugin(users, "c" * 32, "thibs") == 2           # same name still maps


def test_an_existing_install_keeps_acting_as_the_fallback_profile(client, h):
    h.fallback = 5
    r = _post(client, None, {"mediaType": "movie", "mediaId": NEW_MOVIE})
    assert r.status_code == 201 and r.get_json()["requestedBy"]["id"] == 5
    assert [x["tmdb_id"] for x in h.db.list_video_requests(profile_id=5)] == [NEW_MOVIE]


def test_a_turned_off_profile_is_refused(client, ctxs):
    pid = ctxs(20, member_ctx(profile_id=20), disabled=True)
    assert client.get(BASE + "/discover/trending", headers=KEY(pid)).status_code == 403
    assert client.get(BASE + "/user/%d" % pid, headers=KEY(1)).status_code == 404


@pytest.mark.parametrize("stored,want", [(None, "profile"), (False, "profile"), (True, "refuse"),
                                         ("false", "profile"), ("true", "refuse")])
def test_the_setting_new_installs_refuse_old_ones_keep_the_fallback(monkeypatch, stored, want):
    class Cfg:
        def get(self, key, default=None):
            if key == "seerr_compat.refuse_unknown":
                return default if stored is None else stored
            return default
    monkeypatch.setattr(sc, "config_manager", Cfg())
    assert sc.unknown_users() == want


def test_new_installs_ship_refusing():
    from core.settings import ConfigManager
    defaults = ConfigManager._get_default_config(ConfigManager.__new__(ConfigManager))
    assert defaults["seerr_compat"]["refuse_unknown"] is True


# ---- movies and tv are separate (decision 8) -------------------------------------

def test_a_tv_only_profile_never_sees_or_asks_for_movies(client, h, ctxs):
    pid = ctxs(21, member_ctx(profile_id=21, **{"movies.access": False}))
    trending = client.get(BASE + "/discover/trending", headers=KEY(pid)).get_json()
    assert {r["mediaType"] for r in trending["results"]} == {"tv"}
    assert client.get(BASE + "/movie/%d" % NEW_MOVIE, headers=KEY(pid)).status_code == 404
    r = _post(client, pid, {"mediaType": "movie", "mediaId": NEW_MOVIE})
    assert r.status_code == 403 and "request movies" in r.get_json()["message"]
    assert _post(client, pid, {"mediaType": "tv", "mediaId": NEW_SHOW, "seasons": [3]}).status_code == 201
    assert _ask_bits(client, pid) == shapes.PERM_REQUEST_TV | shapes.PERM_AUTO_APPROVE_TV
    assert _perms(client, pid) & shapes.PERM_REQUEST_4K_TV
    assert not _perms(client, pid) & (shapes.PERM_REQUEST_4K | shapes.PERM_REQUEST_4K_MOVIE)


def test_get_modes_per_type(client, h, ctxs):
    """movies right away, shows ask first: the movie lands on the wishlist,
    the show waits as a request. seerr sees request + auto approve for movies."""
    pid = ctxs(22, member_ctx(profile_id=22, **{"tv.get": "ask"}))
    assert _ask_bits(client, pid) == shapes.PERM_REQUEST | shapes.PERM_AUTO_APPROVE_MOVIE
    assert _post(client, pid, {"mediaType": "movie", "mediaId": NEW_MOVIE}).get_json()["status"] \
        == shapes.REQUEST_APPROVED
    assert [w["tmdb_id"] for w in h.wishlist(pid)] == [NEW_MOVIE]
    r = _post(client, pid, {"mediaType": "tv", "mediaId": NEW_SHOW, "seasons": [1]})
    assert r.get_json()["status"] == shapes.REQUEST_PENDING
    assert [x["kind"] for x in h.db.list_video_requests(profile_id=pid)] == ["show"]


def test_cant_request_is_seerrs_403(client, h, ctxs):
    pid = ctxs(23, member_ctx(profile_id=23, **{"movies.get": "none"}))
    r = _post(client, pid, {"mediaType": "movie", "mediaId": NEW_MOVIE})
    assert r.status_code == 403
    assert h.wishlist(pid) == [] and h.db.list_video_requests(profile_id=pid) == []
    # browsing still works, only asking is off
    assert client.get(BASE + "/movie/%d" % NEW_MOVIE, headers=KEY(pid)).status_code == 200
    assert not _perms(client, pid) & (shapes.PERM_REQUEST | shapes.PERM_REQUEST_MOVIE)


def test_a_map_that_cant_be_built_gets_nothing(client, h, ctxs):
    pid = ctxs(24, broken_ctx(24))
    assert _perms(client, pid) == 0
    assert _post(client, pid, {"mediaType": "movie", "mediaId": NEW_MOVIE}).status_code == 403
    assert client.get(BASE + "/discover/trending", headers=KEY(pid)).get_json()["results"] == []
    users = client.get(BASE + "/user?take=100", headers=KEY(1)).get_json()["results"]
    assert pid not in {u["id"] for u in users}


# ---- ratings per type -------------------------------------------------------------

def test_movie_and_tv_ratings_are_capped_separately(client, h, ctxs):
    seen = []
    h.svc._title_allowed = lambda kind, tid, cap: seen.append((kind, cap)) or cap is None
    pid = ctxs(25, member_ctx(profile_id=25, **{"movies.max_rating": "PG", "tv.max_rating": "any"}))
    caps = h.svc.caps(PROFILES[pid])
    assert caps["movie"] is not None and caps["show"] is None
    assert _post(client, pid, {"mediaType": "movie", "mediaId": NEW_MOVIE}).status_code == 403
    assert _post(client, pid, {"mediaType": "tv", "mediaId": NEW_SHOW, "seasons": [3]}).status_code == 201
    assert seen[0][0] == "movie" and seen[0][1] is not None and seen[-1] == ("show", None)
    # tv following movies takes the movie cap
    pid2 = ctxs(26, member_ctx(profile_id=26, **{"movies.max_rating": "PG"}))
    caps = h.svc.caps(PROFILES[pid2])
    assert caps["show"] == caps["movie"] is not None


# ---- request limit from the effective map ----------------------------------------

def test_the_request_limit_comes_from_permissions_not_the_old_column(client, h, ctxs):
    pid = ctxs(27, member_ctx(profile_id=27, **{"movies.get": "ask", "requests.limit": {"n": 1, "days": 7}}),
               request_limit=0)
    assert _post(client, pid, {"mediaType": "movie", "mediaId": NEW_MOVIE}).status_code == 201
    over = _post(client, pid, {"mediaType": "movie", "mediaId": 555})
    assert over.status_code == 403 and "used your 1 request" in over.get_json()["message"]
    # downloads right away are never capped
    pid2 = ctxs(28, member_ctx(profile_id=28, **{"requests.limit": {"n": 1, "days": 7}}))
    assert _post(client, pid2, {"mediaType": "movie", "mediaId": NEW_MOVIE}).status_code == 201
    assert _post(client, pid2, {"mediaType": "movie", "mediaId": 555}).status_code == 201


# ---- quality (#2168) ---------------------------------------------------------------

def test_is4k_with_no_allowed_4k_profile_is_refused(client, h, ctxs):
    pid = ctxs(30, member_ctx(profile_id=30, **{"movies.quality": [0, 3]}))
    r = _post(client, pid, {"mediaType": "movie", "mediaId": NEW_MOVIE, "is4k": True})
    assert r.status_code == 403 and "4K" in r.get_json()["message"]
    assert h.wishlist(pid) == []
    assert not _perms(client, pid) & (shapes.PERM_REQUEST_4K | shapes.PERM_REQUEST_4K_MOVIE)


def test_is4k_picks_the_allowed_4k_profile(client, h, ctxs):
    pid = ctxs(31, member_ctx(profile_id=31, **{"movies.quality": [0, 3, 7], "tv.quality": [0, 11]}))
    assert _perms(client, pid) & shapes.PERM_REQUEST_4K_MOVIE
    assert not _perms(client, pid) & (shapes.PERM_REQUEST_4K | shapes.PERM_REQUEST_4K_TV)
    r = _post(client, pid, {"mediaType": "movie", "mediaId": NEW_MOVIE, "is4k": True})
    assert r.status_code == 201, r.data
    assert r.get_json()["is4k"] is True and r.get_json()["profileId"] == 7
    conn = h.db._get_connection()
    try:
        qp = conn.execute("SELECT quality_profile_id FROM video_wishlist WHERE profile_id=? AND tmdb_id=?",
                          (pid, NEW_MOVIE)).fetchone()[0]
    finally:
        conn.close()
    assert qp == 7


def test_a_4k_profile_that_always_asks_turns_a_download_into_a_request(client, h, ctxs):
    pid = ctxs(32, member_ctx(profile_id=32, **{"movies.quality": [0, 7], "movies.quality_ask": ["4k"]}))
    r = _post(client, pid, {"mediaType": "movie", "mediaId": NEW_MOVIE, "is4k": True})
    assert r.status_code == 201 and r.get_json()["status"] == shapes.REQUEST_PENDING
    req = h.db.list_video_requests(profile_id=pid)[0]
    assert req["quality_profile_id"] == 7 and h.wishlist(pid) == []
    # the normal profile still downloads right away
    r = _post(client, pid, {"mediaType": "movie", "mediaId": 556})
    assert r.get_json()["status"] == shapes.REQUEST_APPROVED


def test_profile_id_goes_through_the_resolver(client, h, ctxs):
    calls = []

    def resolver(profile, media, wanted, perms=None):
        calls.append((profile["id"], media, wanted))
        if wanted == 3:
            return P.QualityDecision(3, False)
        return P.QualityDecision(None, False, refused=True, error=P.QUALITY_NOT_ALLOWED)
    h.svc._resolver = resolver
    pid = ctxs(33, member_ctx(profile_id=33))
    ok = _post(client, pid, {"mediaType": "movie", "mediaId": NEW_MOVIE, "profileId": 3})
    assert ok.status_code == 201 and ok.get_json()["profileId"] == 3
    refused = _post(client, pid, {"mediaType": "movie", "mediaId": 556, "profileId": 5})
    assert refused.status_code == 403 and "quality" in refused.get_json()["message"]
    assert calls == [(33, "movie", 3), (33, "movie", 5)]
    assert [w["tmdb_id"] for w in h.wishlist(pid)] == [NEW_MOVIE]


def test_a_resolver_that_asks_files_a_request_with_the_stored_id(client, h, ctxs):
    h.svc._resolver = lambda profile, media, wanted, perms=None: P.QualityDecision(11, True)
    pid = ctxs(34, member_ctx(profile_id=34))
    r = _post(client, pid, {"mediaType": "tv", "mediaId": NEW_SHOW, "seasons": [1]})
    assert r.get_json()["status"] == shapes.REQUEST_PENDING
    assert h.db.list_video_requests(profile_id=pid)[0]["quality_profile_id"] == 11


def test_the_stub_resolver_drops_a_members_video_pick_like_today(client, h, ctxs):
    """until piece B fills resolve_request_quality in, a member's profileId
    lands without a stored quality (the admin picks at approve)."""
    pid = ctxs(35, member_ctx(profile_id=35, **{"movies.get": "ask"}))
    _post(client, pid, {"mediaType": "movie", "mediaId": NEW_MOVIE, "profileId": 3})
    assert h.db.list_video_requests(profile_id=pid)[0]["quality_profile_id"] is None


def test_service_lists_only_the_profiles_a_person_may_pick(client, h, ctxs):
    pid = ctxs(36, member_ctx(profile_id=36, **{"movies.quality": [3, 7], "movies.quality_default": 3,
                                                "tv.access": False}))
    radarr = client.get(BASE + "/service/radarr", headers=KEY(pid)).get_json()
    assert [(s["id"], s["is4k"], s["activeProfileId"]) for s in radarr] == [(0, False, 3), (1, True, 7)]
    one = client.get(BASE + "/service/radarr/0", headers=KEY(pid)).get_json()
    assert [p["id"] for p in one["profiles"]] == [3]
    four = client.get(BASE + "/service/radarr/1", headers=KEY(pid)).get_json()
    assert [p["id"] for p in four["profiles"]] == [7]
    assert client.get(BASE + "/service/sonarr", headers=KEY(pid)).get_json() == []
    assert client.get(BASE + "/service/sonarr/0", headers=KEY(pid)).status_code == 404
    admin = client.get(BASE + "/service/sonarr/0", headers=KEY(1)).get_json()
    assert [p["id"] for p in admin["profiles"]] == [0, 11] and admin["languageProfiles"] == []
    # two to pick from: seerr's advanced request options
    assert _perms(client, pid) & shapes.PERM_REQUEST_ADVANCED


# ---- request list and cancel: see_all and moderators --------------------------------

def test_moderators_and_see_all_see_everyones_requests(client, h, ctxs):
    mod = ctxs(40, member_ctx(profile_id=40, **{"requests.moderate": True}))
    seer = ctxs(41, member_ctx(profile_id=41, **{"requests.see_all": True}))
    member = ctxs(42, member_ctx(profile_id=42))
    for pid in (mod, seer):
        rows = client.get(BASE + "/request?filter=all", headers=KEY(pid)).get_json()["results"]
        assert {r["requestedBy"]["id"] for r in rows} == {3}
        narrowed = client.get(BASE + "/request?filter=all&requestedBy=42", headers=KEY(pid)).get_json()
        assert narrowed["results"] == []
        d = client.get(BASE + "/movie/%d" % ASKED_MOVIE, headers=KEY(pid)).get_json()
        assert [r["requestedBy"]["id"] for r in d["mediaInfo"]["requests"]] == [3]
    assert client.get(BASE + "/request?filter=all", headers=KEY(member)).get_json()["results"] == []
    assert _perms(client, mod) & shapes.PERM_MANAGE_REQUESTS and _perms(client, mod) & shapes.PERM_REQUEST_VIEW
    assert not _perms(client, seer) & shapes.PERM_MANAGE_REQUESTS
    assert not _perms(client, member) & (shapes.PERM_MANAGE_REQUESTS | shapes.PERM_REQUEST_VIEW)


def test_a_moderator_may_cancel_someone_elses_request_a_member_may_not(client, h, ctxs):
    mod = ctxs(43, member_ctx(profile_id=43, **{"requests.moderate": True}))
    member = ctxs(44, member_ctx(profile_id=44))
    rid = h.db.list_video_requests(profile_id=3)[0]["id"]
    assert client.delete(BASE + "/request/%d" % rid, headers=KEY(member)).status_code == 404
    assert client.delete(BASE + "/request/%d" % rid, headers=KEY(mod)).status_code == 204
    assert h.db.list_video_requests(profile_id=3) == []


def test_a_moderator_still_cancels_their_own(client, h, ctxs):
    mod = ctxs(45, member_ctx(profile_id=45, **{"requests.moderate": True, "movies.get": "ask"}))
    rid = _post(client, mod, {"mediaType": "movie", "mediaId": NEW_MOVIE}).get_json()["id"]
    assert client.delete(BASE + "/request/%d" % rid, headers=KEY(mod)).status_code == 204


# ---- users list ---------------------------------------------------------------------

def test_users_list_only_profiles_allowed_video(client, ctxs):
    music = ctxs(50, member_ctx(profile_id=50, **{"movies.access": False, "tv.access": False}))
    tv = ctxs(51, member_ctx(profile_id=51, **{"movies.access": False}))
    ids = {u["id"] for u in client.get(BASE + "/user?take=100", headers=KEY(1)).get_json()["results"]}
    assert tv in ids and music not in ids and 4 not in ids
    assert client.get(BASE + "/user/%d" % music, headers=KEY(1)).status_code == 404
    assert client.get(BASE + "/user/%d" % tv, headers=KEY(1)).status_code == 200


def test_admin_context_gets_everything(client, h, ctxs):
    pid = ctxs(52, admin_ctx(52), is_admin=True)
    bits = _perms(client, pid)
    assert bits & shapes.PERM_REQUEST and bits & shapes.PERM_AUTO_APPROVE and bits & shapes.PERM_REQUEST_4K
    assert not bits & 2
    r = _post(client, pid, {"mediaType": "movie", "mediaId": NEW_MOVIE, "is4k": True})
    assert r.status_code == 201 and r.get_json()["profileId"] == 7


# ---- route inventory -------------------------------------------------------------------

def test_every_seerr_route_declares_its_access(client):
    """#2166 4.5: the key is admin-only, so every route declares admin (the
    acting profile is checked inside); /status is public like seerr's."""
    app = client.application
    seen = {}
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/seerr/"):
            seen[rule.rule] = P.declared_access(app.view_functions[rule.endpoint])
    assert seen and all(v is not None for v in seen.values()), seen
    assert seen.pop("/seerr/api/v1/status") == ("public", None)
    assert set(seen.values()) == {("admin", None)}
