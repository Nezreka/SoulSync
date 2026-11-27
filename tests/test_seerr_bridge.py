"""seerr in front, soulsync behind it in place of radarr and sonarr (#2324).

seerr's webhook posts each request to soulsync. these tests replay those
webhooks against the real blueprint, the real service and bridge and a real
video db in a temp folder (tests/test_seerr_compat.py's harness). seerr
itself is a stand-in that records every call, so nothing reaches a network.
"""

from __future__ import annotations

import pytest

import api.seerr_compat as sc
from core.seerr_compat import bridge as B
from core.seerr_compat.remote import SeerrRemote, SeerrRemoteError, base_url
from tests.test_seerr_compat import (BASE, JF_ADMIN, KEY, NEW_MOVIE, NEW_SHOW, OWNED_SHOW,  # noqa: F401
                                     PROFILES, READ_RAW, Harness, client, h)

PLEX_KID = "plex-kid-77"


class FakeSeerr:
    """seerr's api: the requests it holds, and every call soulsync made."""

    configured = True

    def __init__(self):
        self.requests = {}
        self.calls = []
        self.fail = set()

    def add(self, rid, *, media_type="movie", tmdb=NEW_MOVIE, media_id=None, status=1, is4k=False,
            seasons=None, by=None):
        self.requests[rid] = {
            "id": rid, "status": status, "is4k": is4k, "type": media_type,
            "media": {"id": media_id or rid + 1000, "tmdbId": tmdb, "mediaType": media_type},
            "seasons": [{"id": n, "seasonNumber": n} for n in (seasons or [])],
            "requestedBy": by or {}}

    def _check(self, what):
        if what in self.fail:
            raise SeerrRemoteError("seerr down")

    def get_request(self, rid):
        self.calls.append(("get", rid))
        self._check("get")
        if rid not in self.requests:
            raise SeerrRemoteError("seerr answered HTTP 404")
        return self.requests[rid]

    def decide(self, rid, approve):
        self.calls.append(("approve" if approve else "decline", rid))
        self._check("decide")
        if rid in self.requests:
            self.requests[rid]["status"] = 2 if approve else 3
        return {}

    def media_status(self, media_id, status, is4k=False):
        self.calls.append(("media", media_id, status, is4k))
        self._check("media")
        return {}

    def pushes(self):
        return [c for c in self.calls if c[0] != "get"]


def _profiles():
    out = []
    for p in PROFILES.values():
        p = dict(p)
        if p["id"] == 2:
            p["plex_username"] = "thibs_on_plex"
        out.append(p)
    return out


@pytest.fixture
def seerr():
    return FakeSeerr()


@pytest.fixture
def wired(client, h, seerr, monkeypatch):
    """the blueprint with a bridge whose seerr is the fake (None: no url set)."""
    state = {"remote": seerr}
    br = B.SeerrBridge(service=h.svc, links=B.SeerrLinks(lambda: h.db._get_connection()),
                       remote=lambda: state["remote"], list_profiles=_profiles,
                       plex_account=lambda pid: PROFILES[3] if pid == PLEX_KID else None)
    monkeypatch.setattr(sc, "_bridge", br)
    client.state = state
    client.bridge = br
    return client


def hook(client, body, user=None, path="/webhook"):
    return client.post(BASE + path, json=body, headers=KEY(user))


def default_payload(rid, ntype="MEDIA_PENDING", media_type="movie", tmdb=NEW_MOVIE, username="",
                    seasons=None, status4k="UNKNOWN"):
    """seerr's default webhook template, every value a string."""
    body = {"notification_type": ntype, "event": "New Movie Request", "subject": "x", "message": "",
            "media": {"media_type": media_type, "tmdbId": str(tmdb), "tvdbId": "",
                      "status": "PENDING" if status4k == "UNKNOWN" else "AVAILABLE",
                      "status4k": status4k},
            "request": {"request_id": str(rid), "requestedBy_email": "", "requestedBy_username": username,
                        "requestedBy_avatar": ""},
            "extra": []}
    if seasons:
        body["extra"] = [{"name": "Requested Seasons", "value": seasons}]
    return body


def link(client, rid):
    return client.bridge.links.get(rid)


def add_movie_file(h, tmdb):
    h.sql("INSERT INTO movies (server_source, server_id, tmdb_id, title, added_at) "
          "VALUES ('jellyfin', ?, ?, 'Landed', '2026-06-01 10:00:00')", ("m%d" % tmdb, tmdb))


# ---- in: a request from seerr -------------------------------------------------

def test_webhook_files_the_request_as_the_plex_matched_profile_and_approves_it_once(wired, h, seerr):
    """the full request comes from seerr: the plex name finds Thibs, who can
    download, so the movie lands on Thibs' wishlist and seerr's pending
    request is approved. the approval's own webhook, and a resend, do nothing."""
    seerr.add(5, by={"id": 9, "plexUsername": "thibs_on_plex", "username": "someone"})
    r = hook(wired, default_payload(5))
    assert r.status_code == 201, r.data
    assert r.get_json()["seerrRequestId"] == 5
    assert [w["tmdb_id"] for w in h.wishlist(2) if w["kind"] == "movie"].count(NEW_MOVIE) == 1
    assert not [w for w in h.wishlist(1) if w["tmdb_id"] == NEW_MOVIE]
    assert seerr.pushes() == [("approve", 5)]
    row = link(wired, 5)
    assert row["outcome"] == B.ADDED and row["profile_id"] == 2 and row["pushed_decision"] == "approved"
    assert row["notification_type"] == "MEDIA_PENDING" and row["requester"] == "someone"

    # seerr says it was approved (soulsync did that), then the same webhook again
    for again in (default_payload(5, "MEDIA_APPROVED"), default_payload(5)):
        r = hook(wired, again)
        assert r.status_code == 200 and r.get_json()["duplicate"] is True
    assert seerr.pushes() == [("approve", 5)]
    assert len(h.db.list_video_requests(profile_id=2)) == 0


def test_the_plex_account_from_an_import_wins_and_ask_first_waits_in_soulsync(wired, h, seerr):
    """#2319: a profile imported from plex is linked by plex account id. Kid
    can't download, so a soulsync request waits; seerr's request stays
    pending until an admin decides in soulsync, then hears it once."""
    seerr.add(6, tmdb=NEW_MOVIE, by={"plexId": PLEX_KID, "plexUsername": "kiddo"})
    r = hook(wired, default_payload(6))
    assert r.status_code == 201, r.data
    rid = r.get_json()["id"]
    assert h.db.get_video_request(rid)["profile_id"] == 3
    assert seerr.pushes() == []
    assert link(wired, 6)["outcome"] == B.WAITING

    wired.bridge.sync()
    assert seerr.pushes() == []                       # still waiting
    h.db.claim_video_requests("movie", NEW_MOVIE, resolved_by=1)
    wired.bridge.sync()
    wired.bridge.sync()
    assert seerr.pushes() == [("approve", 6)]
    assert link(wired, 6)["outcome"] == B.ADDED


def test_a_decline_in_soulsync_declines_in_seerr_and_its_echo_is_ignored(wired, h, seerr):
    seerr.add(7, tmdb=NEW_MOVIE, by={"plexId": PLEX_KID})
    rid = hook(wired, default_payload(7)).get_json()["id"]
    h.db.claim_video_requests("movie", NEW_MOVIE, resolved_by=1, status="denied")
    wired.bridge.sync()
    assert seerr.pushes() == [("decline", 7)]
    r = hook(wired, default_payload(7, "MEDIA_DECLINED"))
    assert r.status_code == 200 and r.get_json()["ignored"] == "MEDIA_DECLINED"
    assert h.db.get_video_request(rid)["status"] == "denied"
    wired.bridge.sync()
    assert seerr.pushes() == [("decline", 7)]


def test_a_decline_made_in_seerr_withdraws_the_waiting_soulsync_request(wired, h, seerr):
    seerr.add(8, tmdb=NEW_MOVIE, by={"plexId": PLEX_KID})
    rid = hook(wired, default_payload(8)).get_json()["id"]
    r = hook(wired, default_payload(8, "MEDIA_DECLINED"))
    assert r.get_json()["outcome"] == B.GONE
    assert h.db.get_video_request(rid) is None
    assert seerr.pushes() == []


def test_soulsyncs_rules_refusing_declines_in_seerr(wired, h, seerr):
    """MusicOnly has no video side: soulsync says no and seerr hears it."""
    seerr.add(9, by={"username": "MusicOnly"})
    r = hook(wired, default_payload(9))
    assert r.status_code == 200 and r.get_json()["outcome"] == B.REFUSED
    assert seerr.pushes() == [("decline", 9)]
    assert not [w for w in h.wishlist(4) if w["tmdb_id"] == NEW_MOVIE]


def test_tv_seasons_and_4k_come_from_seerr(wired, h, seerr, monkeypatch):
    """the webhook says nothing about seasons or 4k; seerr's request does.
    season 2 of a show whose season 1 is here wishes season 2 only, with the
    4k quality profile, and the show is marked partly available in seerr's
    4k status once episodes land, then never again for the same."""
    h.svc._quality_profiles = lambda: [{"id": 0, "name": "Default", "media_type": None, "tags": []},
                                       {"id": 9, "name": "TV 4K", "media_type": "tv", "tags": ["4k"]}]
    asked = []
    real = h.svc.create_request
    monkeypatch.setattr(h.svc, "create_request", lambda pid, body: asked.append(body) or real(pid, body))
    seerr.add(10, media_type="tv", tmdb=OWNED_SHOW, media_id=555, is4k=True, seasons=[2],
              by={"jellyfinUserId": JF_ADMIN})
    r = hook(wired, default_payload(10, media_type="tv", tmdb=OWNED_SHOW))
    assert r.status_code == 201, r.data
    assert asked[-1]["seasons"] == [2] and asked[-1]["is4k"] is True
    assert r.get_json()["is4k"] is True and r.get_json()["profileId"] == 9
    eps = [(w["season_number"], w["episode_number"]) for w in h.wishlist(1) if w["kind"] == "episode"]
    assert eps == [(2, 1), (2, 2)]
    assert seerr.pushes() == [("approve", 10)]
    row = link(wired, 10)
    assert row["is4k"] == 1 and row["seerr_media_id"] == 555 and row["all_seasons"] == 0

    h.sql("UPDATE episodes SET has_file=1 WHERE season_number=2 AND episode_number=1")
    wired.bridge.sync()
    h.sql("UPDATE episodes SET has_file=1 WHERE season_number=2")
    wired.bridge.sync()
    wired.bridge.sync()
    # every asked season is here, but not the whole show: partial is all seerr hears
    assert seerr.pushes() == [("approve", 10), ("media", 555, "partial", True)]


def test_a_4k_request_from_someone_without_4k_is_declined(wired, h, seerr):
    seerr.add(17, is4k=True, by={"plexUsername": "thibs_on_plex"})
    r = hook(wired, default_payload(17))
    assert r.status_code == 200 and r.get_json()["outcome"] == B.REFUSED
    assert seerr.pushes() == [("decline", 17)]
    assert not [w for w in h.wishlist(2) if w["tmdb_id"] == NEW_MOVIE]


def test_a_whole_show_landing_is_available(wired, h, seerr):
    seerr.add(18, media_type="tv", tmdb=OWNED_SHOW, media_id=556, seasons=[1, 2, 3],
              by={"jellyfinUserId": JF_ADMIN})
    assert hook(wired, default_payload(18, media_type="tv", tmdb=OWNED_SHOW)).status_code == 201
    assert link(wired, 18)["all_seasons"] == 1
    h.sql("UPDATE episodes SET has_file=1 WHERE air_date <= '2026-06-01'")
    wired.bridge.sync()
    assert seerr.pushes()[-1] == ("media", 556, "available", False)


def test_a_movie_landing_marks_seerr_media_available_once(wired, h, seerr):
    seerr.add(11, media_id=777, by={"plexUsername": "thibs_on_plex"})
    hook(wired, default_payload(11))
    wired.bridge.sync()
    assert seerr.pushes() == [("approve", 11)]
    add_movie_file(h, NEW_MOVIE)
    wired.bridge.sync()
    wired.bridge.sync()
    assert seerr.pushes() == [("approve", 11), ("media", 777, "available", False)]
    # seerr then fires MEDIA_AVAILABLE: soulsync caused it, nothing happens
    r = hook(wired, default_payload(11, "MEDIA_AVAILABLE"))
    assert r.get_json()["ignored"] == "MEDIA_AVAILABLE"
    assert len(seerr.pushes()) == 2


def test_already_here_is_not_filed_again_and_seerr_hears_it_is_available(wired, h, seerr):
    add_movie_file(h, NEW_MOVIE)
    seerr.add(12, media_id=900, by={"plexUsername": "thibs_on_plex"})
    r = hook(wired, default_payload(12))
    assert r.status_code == 200 and r.get_json()["outcome"] == B.EXISTS
    assert seerr.pushes() == [("media", 900, "available", False)]


def test_a_push_that_fails_is_tried_again_and_only_once_after(wired, h, seerr):
    seerr.add(13, by={"plexUsername": "thibs_on_plex"})
    seerr.fail.add("decide")
    assert hook(wired, default_payload(13)).status_code == 201
    assert link(wired, 13)["pushed_decision"] is None
    tried = len(seerr.pushes())
    seerr.fail.clear()
    wired.bridge.sync()
    wired.bridge.sync()
    assert seerr.pushes()[tried:] == [("approve", 13)]
    assert link(wired, 13)["pushed_decision"] == "approved"


def test_an_already_approved_seerr_request_is_not_approved_again(wired, h, seerr):
    seerr.add(14, status=2, by={"plexUsername": "thibs_on_plex"})
    assert hook(wired, default_payload(14, "MEDIA_AUTO_APPROVED")).status_code == 201
    assert seerr.pushes() == []


def test_an_unmatched_requester_while_unknown_users_are_refused_files_nothing(wired, h, seerr):
    """nobody to act for: nothing filed, nothing told to seerr, and the link
    is not kept, so the request can come through once the person is linked."""
    h.svc._unknown_users = lambda: "refuse"
    seerr.add(15, by={"username": "stranger"})
    assert hook(wired, default_payload(15)).status_code == 403
    assert link(wired, 15) is None and seerr.pushes() == []
    seerr.requests[15]["requestedBy"] = {"plexUsername": "thibs_on_plex"}
    assert hook(wired, default_payload(15)).status_code == 201


def test_seerr_unreachable_falls_back_to_the_webhook(wired, h, seerr):
    seerr.fail.add("get")
    r = hook(wired, default_payload(16, username="Thibs"))
    assert r.status_code == 201, r.data
    assert link(wired, 16)["profile_id"] == 2


# ---- without a seerr url --------------------------------------------------------

def test_no_seerr_url_files_from_the_webhook_alone_and_still_dedupes(wired, h, seerr):
    """no url: who asked comes from the webhook's username, then X-Api-User,
    then the fallback profile; seasons from its extra; nothing is pushed."""
    wired.state["remote"] = None
    r = hook(wired, default_payload(20, media_type="tv", tmdb=OWNED_SHOW, username="thibs", seasons="2"))
    assert r.status_code == 201, r.data
    eps = [(w["season_number"], w["episode_number"]) for w in h.wishlist(2) if w["kind"] == "episode"]
    assert eps == [(2, 1), (2, 2)]
    assert hook(wired, default_payload(20, media_type="tv", tmdb=OWNED_SHOW)).get_json()["duplicate"]
    # nobody named: X-Api-User acts, like a plain request
    r = hook(wired, default_payload(21, tmdb=NEW_MOVIE), user=3)
    assert r.status_code == 201 and h.db.get_video_request(r.get_json()["id"])["profile_id"] == 3
    assert wired.bridge.sync() == 0
    assert seerr.calls == []


def test_the_request_route_takes_a_seerr_id_and_plain_requests_are_unchanged(wired, h, seerr):
    """the webhook pointed at POST /request (a custom template shaped like a
    request) gets the same handling; a body without seerr fields is the
    plain request it always was."""
    wired.state["remote"] = None
    body = {"mediaType": "movie", "mediaId": NEW_MOVIE, "seerrRequestId": 30,
            "notification_type": "MEDIA_PENDING", "requestedBy": {"username": "Thibs"}}
    assert hook(wired, body, path="/request").status_code == 201
    assert hook(wired, body, path="/request").get_json()["duplicate"] is True
    assert link(wired, 30)["profile_id"] == 2
    r = hook(wired, {"mediaType": "movie", "mediaId": NEW_MOVIE + 50}, user=2, path="/request")
    assert r.status_code == 201 and r.get_json()["id"] == 0
    assert link(wired, 0) is None


def test_test_notifications_and_other_events_are_answered_and_ignored(wired, seerr):
    assert hook(wired, {"notification_type": "TEST_NOTIFICATION"}).status_code == 200
    for ntype in ("MEDIA_FAILED", "ISSUE_CREATED", "MEDIA_AVAILABLE"):
        r = hook(wired, default_payload(40, ntype))
        assert r.status_code == 200 and r.get_json()["ignored"] == ntype
    assert link(wired, 40) is None and seerr.calls == []


# ---- route guards --------------------------------------------------------------

def test_the_webhook_is_behind_the_switch_and_an_admin_key(wired):
    body = default_payload(50)
    assert wired.post(BASE + "/webhook", json=body).status_code == 403
    assert wired.post(BASE + "/webhook", json=body, headers={"X-Api-Key": READ_RAW}).status_code == 403
    assert wired.post(BASE + "/webhook", json=body,
                      headers={"Authorization": "Bearer sk_seerr-admin"}).status_code == 201
    assert wired.post(BASE + "/webhook", data="nope", headers=KEY()).status_code == 400
    wired.cfg.d["seerr_compat.enabled"] = False
    assert wired.post(BASE + "/webhook", json=default_payload(51), headers=KEY()).status_code == 404


def test_the_webhook_declares_admin_access():
    from core.permissions import ACCESS_ATTR
    assert getattr(sc.seerr_webhook, ACCESS_ATTR) == ("admin", None)


# ---- pieces ----------------------------------------------------------------------

def test_match_profile_order():
    profiles = [{"id": 2, "name": "Thibs", "plex_username": "tp"},
                {"id": 3, "name": "Kid", "jellyfin_user_id": "ab-cd"},
                {"id": 6, "name": "twin"}, {"id": 7, "name": "Twin"}]
    plex = {"p1": {"id": 9}}.get
    assert B.match_profile({"plexId": "p1", "plexUsername": "tp"}, profiles, plex) == 9
    assert B.match_profile({"jellyfinUserId": "ABCD", "username": "Thibs"}, profiles, plex) == 3
    assert B.match_profile({"plexUsername": "TP"}, profiles, plex) == 2
    assert B.match_profile({"username": "thibs"}, profiles, plex) == 2
    assert B.match_profile({"username": "twin"}, profiles, plex) is None      # two fit, neither picked
    assert B.match_profile({}, profiles, plex) is None


def test_parse_webhook_reads_seasons_and_4k_from_the_default_template():
    w = B.parse_webhook(default_payload(3, media_type="tv", tmdb=NEW_SHOW, seasons="1, 3",
                                        status4k="PENDING"))
    assert w["request_id"] == 3 and w["tmdb_id"] == NEW_SHOW and w["is4k"] is True
    assert B._seasons_from(w["seasons"]) == [1, 3]
    assert B.parse_webhook(default_payload(3))["is4k"] is False
    assert B._seasons_from([{"seasonNumber": 2}, {"seasonNumber": 0}]) == [2]
    assert B._seasons_from("all") is None


class _Resp:
    def __init__(self, code, body=None):
        self.status_code = code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


def test_remote_uses_seerrs_paths_and_key():
    sent = []

    def http(method, url, headers, json, timeout):
        sent.append((method, url, headers["X-Api-Key"], json))
        return _Resp(200, {"version": "2.7.3"} if url.endswith("/status") else {})

    r = SeerrRemote("http://seerr:5055/api/v1/", "k", http=http)
    assert r.base == "http://seerr:5055"
    r.decide(4, True)
    r.decide(4, False)
    r.media_status(8, "partial", True)
    assert r.test() == "Connected to Seerr v2.7.3"
    assert sent[:3] == [("POST", "http://seerr:5055/api/v1/request/4/approve", "k", None),
                        ("POST", "http://seerr:5055/api/v1/request/4/decline", "k", None),
                        ("POST", "http://seerr:5055/api/v1/media/8/partial", "k", {"is4k": True})]
    with pytest.raises(SeerrRemoteError):
        SeerrRemote("http://x", "k", http=lambda *a, **k: _Resp(401)).get_request(1)
    assert not SeerrRemote("", "k").configured and base_url("http://a/api") == "http://a"


def test_the_seerr_key_is_a_secret_and_test_needs_settings(monkeypatch):
    from core.settings import ConfigManager
    assert "seerr_compat.seerr_api_key" in ConfigManager._SENSITIVE_PATHS
    defaults = ConfigManager._get_default_config(ConfigManager.__new__(ConfigManager))
    assert defaults["seerr_compat"]["seerr_url"] == "" and defaults["seerr_compat"]["seerr_api_key"] == ""

    class Cfg:
        REDACTED_SENTINEL = "__redacted_unchanged__"

        def __init__(self, d):
            self.d = d

        def get(self, key, default=None):
            return self.d.get(key, default)

    monkeypatch.setattr(sc, "config_manager", Cfg({}))
    assert sc.seerr_remote() is None and sc.test_seerr()[0] is False
    monkeypatch.setattr(sc, "config_manager", Cfg({"seerr_compat.seerr_url": "http://s",
                                                   "seerr_compat.seerr_api_key": "k"}))
    assert sc.seerr_remote().base == "http://s"


def test_video_events_start_a_sync_only_when_seerr_is_set(monkeypatch):
    started = []
    monkeypatch.setattr(sc, "enabled", lambda: True)
    monkeypatch.setattr(sc.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: started.append(kw)})())
    monkeypatch.setattr(sc, "seerr_remote", lambda: None)
    sc.on_video_event("video_download_completed")
    assert started == []
    monkeypatch.setattr(sc, "seerr_remote", lambda: object())
    sc.on_video_event("video_wishlist_changed")
    assert started == []
    monkeypatch.setitem(sc._sync_state, "running", False)
    sc.on_video_event("video_download_completed")
    sc.on_video_event("video_request_approved")       # one already running: runs again after
    assert len(started) == 1 and sc._sync_state["again"] is True
    monkeypatch.setitem(sc._sync_state, "again", False)
