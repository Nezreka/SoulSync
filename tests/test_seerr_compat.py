"""seerr compatible api for the video side (#2151, Thibs).

litefin's jellyfin plugin talks to a seerr server with X-Api-Key and picks
the seerr user with X-Api-User. these tests replay the calls that plugin
makes (read from its SeerrController.cs) against the real blueprint, the real
service and a real video db in a temp folder. the tmdb engine, profiles and
jellyfin users are stand-ins, so nothing reaches the network or a live db.
"""

from __future__ import annotations

import hashlib

import pytest
from flask import Flask

import api.seerr_compat as sc
from core.seerr_compat import shapes
from core.seerr_compat.service import SeerrCompat, SeerrError, monitor_for_seasons, parse_seasons
from core.seerr_compat.store import SeerrStore
from database.video_database import VideoDatabase

TODAY = "2026-06-01"
IMG = "https://image.tmdb.org/t/p/w300"

OWNED_MOVIE, WISHED_MOVIE, ASKED_MOVIE, NEW_MOVIE = 101, 102, 103, 104
OWNED_SHOW, NEW_SHOW = 201, 202
JF_ADMIN, JF_KID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "11111111222233334444555555555555"


def _movie(tid, title, **kw):
    return {"kind": "movie", "tmdb_id": tid, "title": title, "year": "2020", "date": "2020-05-01",
            "overview": "o", "rating": 7.5, "poster": IMG + "/p%d.jpg" % tid,
            "backdrop": "https://image.tmdb.org/t/p/w780/b%d.jpg" % tid, **kw}


def _show(tid, title, **kw):
    return {"kind": "show", "tmdb_id": tid, "title": title, "year": "2019", "date": "2019-09-09",
            "overview": "o", "rating": 8.0, "poster": IMG + "/p%d.jpg" % tid, **kw}


SEASONS = {
    # season 1 aired, season 2 aired, season 3 has one aired and one future
    1: [("2019-01-01", 1), ("2019-01-08", 2)],
    2: [("2020-01-01", 1), ("2020-01-08", 2)],
    3: [("2026-05-01", 1), ("2026-09-01", 2)],
}


class FakeEngine:
    def __init__(self, db):
        self.db = db
        self.calls = []

    def _stamp(self, items):
        out = []
        for it in items:
            it = dict(it)
            if it["kind"] in ("movie", "show"):
                it["library_id"] = self.db.library_id_for_tmdb(it["kind"], it["tmdb_id"], "jellyfin")
            out.append(it)
        return out

    def trending(self, window="week", kind=None):
        return self._stamp([_movie(OWNED_MOVIE, "Owned"), _movie(WISHED_MOVIE, "Wished"),
                            _movie(ASKED_MOVIE, "Asked"), _movie(NEW_MOVIE, "New"),
                            _show(OWNED_SHOW, "Owned Show")])

    def discover_filter(self, kind, **kw):
        self.calls.append(("discover_filter", kind, kw))
        if kind == "movie":
            return self._stamp([_movie(1000 + i, "M%d" % i) for i in range(20)])
        return self._stamp([_show(2000 + i, "S%d" % i) for i in range(5)])

    def discover_curated(self, key, page=1):
        self.calls.append(("discover_curated", key, page))
        return self._stamp([_movie(NEW_MOVIE, "New")])

    def genre_list(self, kind):
        return [{"id": 28, "name": "Action"}, {"id": 18, "name": "Drama"}]

    def search(self, q):
        return self._stamp([_show(NEW_SHOW, "New Show"), _movie(OWNED_MOVIE, "Owned"),
                            {"kind": "person", "tmdb_id": 9, "title": "Someone",
                             "poster": "https://image.tmdb.org/t/p/w185/face.jpg"}])

    def _show_detail(self, tid):
        return {"kind": "show", "tmdb_id": tid, "title": "Show %d" % tid, "year": "2019",
                "first_air_date": "2019-01-01", "poster_url": IMG + "/s.jpg", "status": "Returning Series",
                "genres": ["Drama"], "cast": [{"name": "A", "character": "B", "tmdb_id": 5,
                                               "photo": "https://image.tmdb.org/t/p/w185/a.jpg"}],
                "crew": [{"name": "C", "job": "Creator", "tmdb_id": 6}], "runtime_minutes": 45,
                "networks": ["HBO"]}

    def tmdb_detail(self, kind, tid):
        if self.db.library_id_for_tmdb(kind, tid, "jellyfin"):
            return {"redirect": {"source": "library", "kind": kind, "id": 1}}
        if kind == "movie":
            return {"kind": "movie", "tmdb_id": tid, "title": "Movie %d" % tid, "year": "2021",
                    "release_date": "2021-02-03", "poster_url": IMG + "/m.jpg", "runtime_minutes": 99,
                    "genres": ["Action"], "facts": {"budget": 5, "original_language": "en"},
                    "videos": [{"key": "yt1", "name": "Trailer", "type": "Trailer"}],
                    "similar": [{"title": "Sim", "tmdb_id": OWNED_MOVIE, "kind": "movie",
                                 "poster": IMG + "/sim.jpg"}],
                    "collection": {"id": 77, "name": "Saga", "poster": IMG + "/c.jpg"}}
        d = self._show_detail(tid)
        d["seasons"] = [{"season_number": 0, "title": "Specials", "episode_total": 1}] + [
            {"season_number": n, "title": "Season %d" % n, "episode_total": len(eps)}
            for n, eps in SEASONS.items()]
        return d

    def tmdb_full_detail(self, kind, tid):
        self.calls.append(("full_detail", kind, tid))
        if kind == "movie":
            return {"kind": "movie", "tmdb_id": tid, "title": "Movie %d" % tid, "year": "2020",
                    "_extras": {"similar": []}}
        d = self._show_detail(tid)
        d["_extras"] = {"keywords": ["anime"]}
        d["_seasons"] = [{"season_number": n, "title": "Season %d" % n, "episode_count": len(eps)}
                         for n, eps in SEASONS.items()]
        return d

    def tmdb_season(self, tid, sn):
        return {"episodes": [{"episode_number": en, "air_date": ad, "title": "E%d" % en}
                             for ad, en in SEASONS.get(sn, [])]}

    def recommendations(self, kind, tid, page=1):
        return self._stamp([_movie(NEW_MOVIE, "New")])

    def person_detail(self, pid):
        if pid != 9:
            return None
        return {"tmdb_id": 9, "name": "Someone", "photo": "https://image.tmdb.org/t/p/w185/face.jpg",
                "credits": [{"kind": "movie", "tmdb_id": OWNED_MOVIE, "title": "Owned",
                             "department": "Acting", "role": "Hero", "date": "2020-01-01"},
                            {"kind": "show", "tmdb_id": NEW_SHOW, "title": "New Show",
                             "department": "Directing", "role": "Director"}]}

    def collection(self, cid):
        return self._stamp([_movie(OWNED_MOVIE, "Owned")]) if cid == 77 else []


PROFILES = {
    1: {"id": 1, "name": "Admin", "is_admin": True, "can_download": True, "allowed_sides": "both",
        "jellyfin_user_id": JF_ADMIN.replace("-", "")},
    2: {"id": 2, "name": "Thibs", "is_admin": False, "can_download": True, "allowed_sides": "both"},
    3: {"id": 3, "name": "Kid", "is_admin": False, "can_download": False, "allowed_sides": "video",
        "jellyfin_user_id": JF_KID, "request_limit": 0},
    4: {"id": 4, "name": "MusicOnly", "is_admin": False, "can_download": True, "allowed_sides": "music"},
    5: {"id": 5, "name": "Limited", "is_admin": False, "can_download": False, "allowed_sides": "both",
        "request_limit": 1, "request_limit_days": 7},
}
JF_USERS = [{"id": JF_ADMIN, "name": "boss"}, {"id": JF_KID, "name": "kiddo"},
            {"id": "cccccccccccccccccccccccccccccccc", "name": "thibs"},
            {"id": "dddddddddddddddddddddddddddddddd", "name": "guest"}]


class Harness:
    def __init__(self, tmp_path):
        self.db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
        self.engine = FakeEngine(self.db)
        self.fallback = 1
        self.restricted = set()
        self.events = []
        self.wanted = []
        self.svc = SeerrCompat(
            engine=lambda: self.engine, video_db=lambda: self.db,
            store=SeerrStore(lambda: self.db._get_connection()),
            get_profile=lambda pid: PROFILES.get(pid), list_profiles=lambda: list(PROFILES.values()),
            jellyfin_users=lambda: JF_USERS, fallback_profile_id=lambda: self.fallback,
            server=lambda: "jellyfin",
            title_allowed=lambda kind, tid, p: (kind, tid) not in self.restricted,
            publish=lambda e, d: self.events.append((e, d)), show_wanted=self.wanted.append,
            today=lambda: TODAY)
        self._seed()

    def sql(self, query, args=()):
        conn = self.db._get_connection()
        try:
            cur = conn.execute(query, args)
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    def _seed(self):
        self.sql("INSERT INTO movies (server_source, server_id, tmdb_id, title, added_at) "
                 "VALUES ('jellyfin', 'm1', ?, 'Owned', '2026-05-20 10:00:00')", (OWNED_MOVIE,))
        self.db.add_movie_to_wishlist(WISHED_MOVIE, "Wished", profile_id=2)
        self.db.add_video_request(profile_id=3, requester_name="Kid", kind="movie",
                                  tmdb_id=ASKED_MOVIE, title="Asked")
        show = self.sql("INSERT INTO shows (server_source, server_id, tmdb_id, title, added_at) "
                        "VALUES ('jellyfin', 's1', ?, 'Owned Show', '2026-05-21 10:00:00')", (OWNED_SHOW,))
        for sn, eps in SEASONS.items():
            season = self.sql("INSERT INTO seasons (show_id, season_number) VALUES (?, ?)", (show, sn))
            for ad, en in eps:
                # the library has all of season 1 and nothing after
                self.sql("INSERT INTO episodes (show_id, season_id, server_source, season_number, "
                         "episode_number, air_date, has_file) VALUES (?, ?, 'jellyfin', ?, ?, ?, ?)",
                         (show, season, sn, en, ad, 1 if sn == 1 else 0))

    def wishlist(self, profile_id):
        conn = self.db._get_connection()
        try:
            return [dict(r) for r in conn.execute(
                "SELECT kind, tmdb_id, season_number, episode_number FROM video_wishlist "
                "WHERE profile_id=? ORDER BY kind, tmdb_id, season_number, episode_number", (profile_id,))]
        finally:
            conn.close()

    def watchlist(self, profile_id):
        conn = self.db._get_connection()
        try:
            return [r[0] for r in conn.execute(
                "SELECT tmdb_id FROM video_watchlist WHERE profile_id=? AND kind='show' AND state='follow'",
                (profile_id,))]
        finally:
            conn.close()


ADMIN_RAW, READ_RAW = "sk_seerr-admin", "sk_seerr-read"


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


class FakeConfig:
    def __init__(self):
        self.d = {"seerr_compat.enabled": True, "seerr_compat.profile_id": 1,
                  "api_keys": [{"id": "a", "key_hash": _hash(ADMIN_RAW), "label": "litefin", "scope": "admin"},
                               {"id": "r", "key_hash": _hash(READ_RAW), "label": "dash", "scope": "read"}]}

    def get(self, key, default=None):
        return self.d.get(key, default)

    def set(self, key, value):
        self.d[key] = value


@pytest.fixture
def h(tmp_path):
    return Harness(tmp_path)


@pytest.fixture
def client(h, monkeypatch):
    cfg = FakeConfig()
    monkeypatch.setattr(sc, "config_manager", cfg)
    monkeypatch.setattr(sc, "_service", h.svc)
    from api import auth
    monkeypatch.setattr(auth, "_should_persist_usage", lambda *a, **k: False)
    app = Flask(__name__)
    app.soulsync = {"config_manager": cfg}
    app.register_blueprint(sc.create_blueprint())
    c = app.test_client()
    c.cfg = cfg
    return c


BASE = "/seerr/api/v1"


def KEY(user=None):
    hdr = {"X-Api-Key": ADMIN_RAW, "Accept": "application/json"}
    if user is not None:
        hdr["X-Api-User"] = str(user)
    return hdr


def resolve_like_the_plugin(users, jellyfin_id, jellyfin_name):
    """the litefin plugin's ResolveSeerrUserIdAsync: jellyfinUserId first
    (dashes stripped, any case), then jellyfinUsername, then username."""
    want = jellyfin_id.replace("-", "").lower()
    for u in users:
        cand = (u.get("jellyfinUserId") or "").replace("-", "").lower()
        if cand and cand == want:
            return u["id"]
        for field in ("jellyfinUsername", "username"):
            if (u.get(field) or "").lower() == jellyfin_name.lower():
                return u["id"]
    return None


# ---- access ------------------------------------------------------------------

def test_off_answers_like_the_routes_are_not_there(client):
    """#2151: switched off, every seerr route is a 404, key or not."""
    client.cfg.d["seerr_compat.enabled"] = False
    assert client.get(BASE + "/status").status_code == 404
    assert client.get(BASE + "/auth/me", headers=KEY()).status_code == 404


def test_status_is_open_and_everything_else_wants_an_admin_key(client):
    """#2151: the plugin's ping hits /status without a key, like seerr allows.
    /auth/me (its Test Connection) needs the admin key; a read key is refused."""
    st = client.get(BASE + "/status")
    assert st.status_code == 200 and st.get_json()["version"]
    assert client.get(BASE + "/auth/me").status_code == 403
    assert client.get(BASE + "/auth/me", headers={"X-Api-Key": READ_RAW}).status_code == 403
    me = client.get(BASE + "/auth/me", headers=KEY())
    assert me.status_code == 200
    assert me.get_json()["permissions"] & shapes.PERM_REQUEST


def test_permissions_follow_the_profile_x_api_user_names(client):
    """#2151: the plugin reads permissions to decide on request options. a
    request-only profile asks, a download profile auto approves, a profile
    without the video side gets nothing. none of them look like seerr admins."""
    def perms(user):
        return client.get(BASE + "/auth/me", headers=KEY(user)).get_json()["permissions"]
    assert perms(2) == shapes.PERM_REQUEST | shapes.PERM_AUTO_APPROVE
    assert perms(3) == shapes.PERM_REQUEST
    assert perms(4) == 0
    for user in (1, 2, 3):
        assert not perms(user) & (2 | 8192)


# ---- users ---------------------------------------------------------------------

def test_every_jellyfin_user_resolves_to_a_profile_the_way_the_plugin_matches(client):
    """#2151: the plugin pages /user and matches the jellyfin account by id,
    then by name. linked profiles win, then a same-named profile, then the
    fallback profile from settings."""
    body = client.get(BASE + "/user?take=100&skip=0", headers=KEY()).get_json()
    users = body["results"]
    assert body["pageInfo"]["results"] == len(users)
    assert resolve_like_the_plugin(users, JF_ADMIN, "boss") == 1           # linked, dashed id
    assert resolve_like_the_plugin(users, JF_KID, "kiddo") == 3            # linked, no dashes
    assert resolve_like_the_plugin(users, "c" * 32, "thibs") == 2          # same name as a profile
    assert resolve_like_the_plugin(users, "d" * 32, "guest") == 1          # fallback


def test_the_fallback_profile_setting_catches_unlinked_jellyfin_users(client, h):
    """#2151: requests from jellyfin users with no profile go where settings say."""
    h.fallback = 5
    users = client.get(BASE + "/user?take=100", headers=KEY()).get_json()["results"]
    assert resolve_like_the_plugin(users, "d" * 32, "guest") == 5


def test_user_list_pages(client):
    first = client.get(BASE + "/user?take=2&skip=0", headers=KEY()).get_json()
    second = client.get(BASE + "/user?take=2&skip=2", headers=KEY()).get_json()
    everyone = client.get(BASE + "/user?take=100", headers=KEY()).get_json()["results"]
    assert len(first["results"]) == 2 and second["pageInfo"]["page"] == 2
    assert first["results"] + second["results"] == everyone[:4]


# ---- discover and search -----------------------------------------------------------

def test_trending_cards_carry_tmdb_paths_and_soulsync_status(client):
    """#2151: litefin builds posters from posterPath and badges from
    mediaInfo.status: available in the library, processing on a wishlist,
    pending while a request waits, nothing when nobody asked."""
    body = client.get(BASE + "/discover/trending", headers=KEY()).get_json()
    by_id = {r["id"]: r for r in body["results"]}
    assert by_id[OWNED_MOVIE]["posterPath"] == "/p%d.jpg" % OWNED_MOVIE
    assert by_id[OWNED_MOVIE]["backdropPath"] == "/b%d.jpg" % OWNED_MOVIE
    assert by_id[OWNED_MOVIE]["mediaType"] == "movie"
    assert by_id[OWNED_MOVIE]["releaseDate"] == "2020-05-01"
    assert by_id[OWNED_MOVIE]["mediaInfo"]["status"] == shapes.STATUS_AVAILABLE
    assert by_id[WISHED_MOVIE]["mediaInfo"]["status"] == shapes.STATUS_PROCESSING
    assert by_id[ASKED_MOVIE]["mediaInfo"]["status"] == shapes.STATUS_PENDING
    assert by_id[NEW_MOVIE]["mediaInfo"] is None
    # season 1 of 3 aired seasons on disk: partly there
    assert by_id[OWNED_SHOW]["mediaType"] == "tv"
    assert by_id[OWNED_SHOW]["name"] == "Owned Show"
    assert by_id[OWNED_SHOW]["mediaInfo"]["status"] == shapes.STATUS_PARTIAL
    assert body["page"] == 1 and body["totalPages"] == 1


def test_discover_movies_passes_the_plugin_filters_to_the_engine(client, h):
    """#2151: genre, language, studio and sort reach tmdb's discover; a full
    page says there's more so the plugin keeps paging."""
    body = client.get(BASE + "/discover/movies?page=3&genre=28&language=en&studio=420"
                      "&sortBy=vote_average.desc", headers=KEY()).get_json()
    _name, kind, kw = h.engine.calls[-1]
    assert kind == "movie" and kw["page"] == 3 and kw["genre"] == "28"
    assert kw["language"] == "en" and kw["companies"] == "420" and kw["sort_by"] == "vote_average.desc"
    assert body["page"] == 3 and body["totalPages"] > 3 and len(body["results"]) == 20


def test_discover_tv_maps_network_and_sort_and_ends_on_a_short_page(client, h):
    body = client.get(BASE + "/discover/tv/network/49?page=2", headers=KEY()).get_json()
    _name, kind, kw = h.engine.calls[-1]
    assert kind == "show" and kw["networks"] == "49" and kw["companies"] is None
    assert body["totalPages"] == 2
    client.get(BASE + "/discover/tv?sortBy=primary_release_date.desc", headers=KEY())
    assert h.engine.calls[-1][2]["sort_by"] == "first_air_date.desc"
    client.get(BASE + "/discover/tv?sortBy=drop table", headers=KEY())
    assert h.engine.calls[-1][2]["sort_by"] == "popularity.desc"


def test_upcoming_uses_tmdb_upcoming_and_a_forward_window_without_vote_floor(client, h):
    client.get(BASE + "/discover/movies/upcoming?page=2", headers=KEY())
    assert h.engine.calls[-1] == ("discover_curated", "upcoming_movies", 2)
    client.get(BASE + "/discover/tv/upcoming", headers=KEY())
    _name, kind, kw = h.engine.calls[-1]
    assert kind == "show" and kw["release_window"] == "next_90" and kw["vote_count_min"] == 0


def test_genre_slider_is_tmdb_genres(client):
    body = client.get(BASE + "/discover/genreslider/movie", headers=KEY()).get_json()
    assert body == [{"id": 28, "name": "Action", "backdrops": []}, {"id": 18, "name": "Drama", "backdrops": []}]


def test_search_returns_people_too(client):
    body = client.get(BASE + "/search?query=some&page=1", headers=KEY()).get_json()
    kinds = {r["mediaType"]: r for r in body["results"]}
    assert kinds["person"]["profilePath"] == "/face.jpg" and kinds["person"]["name"] == "Someone"
    assert kinds["tv"]["firstAirDate"] == "2019-09-09"
    assert kinds["movie"]["mediaInfo"]["status"] == shapes.STATUS_AVAILABLE
    assert client.get(BASE + "/search?query=some&page=2", headers=KEY()).get_json()["results"] == []


# ---- details -------------------------------------------------------------------------

def test_movie_detail_has_what_the_details_page_reads(client):
    """#2151: title, date, runtime, trailer and collection come through in seerr's names."""
    d = client.get(BASE + "/movie/%d" % NEW_MOVIE, headers=KEY()).get_json()
    assert d["title"] == "Movie %d" % NEW_MOVIE and d["releaseDate"] == "2021-02-03"
    assert d["runtime"] == 99 and d["budget"] == 5 and d["posterPath"] == "/m.jpg"
    assert d["relatedVideos"][0]["key"] == "yt1" and d["relatedVideos"][0]["site"] == "YouTube"
    assert d["collection"] == {"id": 77, "name": "Saga", "posterPath": "/c.jpg"}
    assert d["mediaInfo"] is None


def test_pending_request_shows_on_the_detail_so_it_can_be_cancelled(client):
    d = client.get(BASE + "/movie/%d" % ASKED_MOVIE, headers=KEY()).get_json()
    assert d["mediaInfo"]["status"] == shapes.STATUS_PENDING
    assert d["mediaInfo"]["requests"][0]["status"] == shapes.REQUEST_PENDING


def test_owned_show_detail_reads_tmdb_and_the_library_per_season(client, h):
    """#2151: an owned show's tmdb detail is a library redirect in soulsync,
    so its data comes from the full detail. seasons say what's on disk."""
    d = client.get(BASE + "/tv/%d" % OWNED_SHOW, headers=KEY()).get_json()
    assert ("full_detail", "show", OWNED_SHOW) in h.engine.calls
    assert d["name"] == "Show %d" % OWNED_SHOW
    assert [s["seasonNumber"] for s in d["seasons"]] == [1, 2, 3]
    assert d["numberOfSeasons"] == 3 and d["createdBy"][0]["name"] == "C"
    assert d["mediaInfo"]["status"] == shapes.STATUS_PARTIAL
    assert d["mediaInfo"]["seasons"] == [{"id": 1, "seasonNumber": 1, "status": shapes.STATUS_AVAILABLE,
                                          "status4k": shapes.STATUS_UNKNOWN}]


def test_unknown_title_and_unknown_media_type_are_404(client):
    assert client.get(BASE + "/person/1", headers=KEY()).status_code == 404
    assert client.get(BASE + "/book/1", headers=KEY()).status_code == 404


def test_similar_recommendations_person_and_collection(client):
    sim = client.get(BASE + "/movie/%d/similar" % NEW_MOVIE, headers=KEY()).get_json()
    assert sim["results"][0]["id"] == OWNED_MOVIE
    assert sim["results"][0]["mediaInfo"]["status"] == shapes.STATUS_AVAILABLE
    rec = client.get(BASE + "/movie/%d/recommendations" % NEW_MOVIE, headers=KEY()).get_json()
    assert rec["results"][0]["id"] == NEW_MOVIE
    p = client.get(BASE + "/person/9", headers=KEY()).get_json()
    assert p["name"] == "Someone" and p["profilePath"] == "/face.jpg"
    cc = client.get(BASE + "/person/9/combined_credits", headers=KEY()).get_json()
    assert cc["cast"][0]["character"] == "Hero" and cc["crew"][0]["job"] == "Director"
    assert cc["cast"][0]["mediaInfo"]["status"] == shapes.STATUS_AVAILABLE
    coll = client.get(BASE + "/collection/77", headers=KEY()).get_json()
    assert coll["parts"][0]["id"] == OWNED_MOVIE


# ---- requests ----------------------------------------------------------------------

def _post(client, user, body):
    return client.post(BASE + "/request", json=body, headers=KEY(user))


def test_download_profile_movie_request_goes_on_its_wishlist(client, h):
    """#2151: the plugin posts {mediaType, mediaId, is4k} as the matched user.
    a profile that can download gets it on its own video wishlist right away."""
    r = _post(client, 2, {"mediaType": "movie", "mediaId": NEW_MOVIE, "is4k": False})
    assert r.status_code == 201, r.data
    body = r.get_json()
    assert body["media"]["tmdbId"] == NEW_MOVIE and body["status"] == shapes.REQUEST_APPROVED
    assert {"kind": "movie", "tmdb_id": NEW_MOVIE, "season_number": None,
            "episode_number": None} in h.wishlist(2)
    assert h.wishlist(1) == []
    assert _post(client, 2, {"mediaType": "movie", "mediaId": NEW_MOVIE}).status_code == 409


def test_owned_movie_request_is_a_conflict(client, h):
    assert _post(client, 2, {"mediaType": "movie", "mediaId": OWNED_MOVIE}).status_code == 409
    assert h.wishlist(2) == [{"kind": "movie", "tmdb_id": WISHED_MOVIE, "season_number": None,
                              "episode_number": None}]


def test_tv_request_wishes_only_the_aired_missing_episodes_of_the_asked_seasons(client, h):
    """#2151: seasons [1, 2] of a show whose season 1 is on disk wishes
    season 2's episodes only, and no follow since season 3 wasn't asked."""
    r = _post(client, 2, {"mediaType": "tv", "mediaId": OWNED_SHOW, "seasons": [1, 2]})
    assert r.status_code == 201, r.data
    eps = [(w["season_number"], w["episode_number"]) for w in h.wishlist(2) if w["kind"] == "episode"]
    assert eps == [(2, 1), (2, 2)]
    assert h.watchlist(2) == []
    assert h.wanted == [OWNED_SHOW]


def test_tv_request_for_the_newest_season_also_follows_the_show(client, h):
    """#2151: asking for the season that's airing means its next episodes too."""
    r = _post(client, 2, {"mediaType": "tv", "mediaId": NEW_SHOW, "seasons": [3]})
    assert r.status_code == 201, r.data
    eps = [(w["season_number"], w["episode_number"]) for w in h.wishlist(2) if w["kind"] == "episode"]
    assert eps == [(3, 1)]                    # the unaired one is left to the follow
    assert h.watchlist(2) == [NEW_SHOW]


def test_tv_request_for_seasons_already_on_disk_is_a_conflict(client, h):
    assert _post(client, 2, {"mediaType": "tv", "mediaId": OWNED_SHOW, "seasons": [1]}).status_code == 409
    assert h.wishlist(2) == [{"kind": "movie", "tmdb_id": WISHED_MOVIE, "season_number": None,
                              "episode_number": None}]


def test_tv_request_needs_real_seasons(client):
    assert _post(client, 2, {"mediaType": "tv", "mediaId": NEW_SHOW, "seasons": []}).status_code == 400
    assert _post(client, 2, {"mediaType": "tv", "mediaId": NEW_SHOW, "seasons": [9]}).status_code == 400
    assert _post(client, 2, {"mediaType": "music", "mediaId": 1}).status_code == 400


def test_request_only_profile_files_a_request_with_the_nearest_policy(client, h):
    """#2151: a profile that can't download files a soulsync video request
    that waits on the Requests page. its seasons pick the monitor policy."""
    r = _post(client, 3, {"mediaType": "tv", "mediaId": NEW_SHOW, "seasons": [1]})
    assert r.status_code == 201, r.data
    assert r.get_json()["status"] == shapes.REQUEST_PENDING and r.get_json()["id"] > 0
    rows = h.db.list_video_requests(profile_id=3, status="pending")
    show = next(x for x in rows if x["kind"] == "show")
    assert show["tmdb_id"] == NEW_SHOW and show["monitor"] == "first_season"
    assert show["title"] == "Show %d" % NEW_SHOW
    assert h.wishlist(3) == []
    assert h.events[-1][0] == "video_request_created"
    assert _post(client, 3, {"mediaType": "tv", "mediaId": NEW_SHOW, "seasons": [1]}).status_code == 409


def test_seasons_no_policy_can_say_go_in_the_note(client, h):
    r = _post(client, 3, {"mediaType": "tv", "mediaId": NEW_SHOW, "seasons": [1, 2]})
    assert r.status_code == 201
    show = next(x for x in h.db.list_video_requests(profile_id=3) if x["kind"] == "show")
    assert show["monitor"] == "all" and show["note"] == "asked for seasons 1, 2"


def test_request_quota_and_side_and_kids_checks(client, h):
    """#2151: a profile over its request limit, one without the video side and
    a title over a kids cap are all refused with seerr's 403."""
    assert _post(client, 5, {"mediaType": "movie", "mediaId": NEW_MOVIE}).status_code == 201
    over = _post(client, 5, {"mediaType": "movie", "mediaId": 555})
    assert over.status_code == 403 and "used your 1 request" in over.get_json()["message"]
    assert _post(client, 4, {"mediaType": "movie", "mediaId": NEW_MOVIE}).status_code == 403
    h.restricted.add(("movie", 556))
    assert _post(client, 2, {"mediaType": "movie", "mediaId": 556}).status_code == 403
    assert _post(client, 2, {"mediaType": "movie", "mediaId": 557, "is4k": True}).status_code == 403


def test_request_list_and_cancel(client, h):
    """#2151: litefin's requests row reads results[].media.tmdbId and status;
    its cancel deletes a pending request of the user's own."""
    _post(client, 3, {"mediaType": "movie", "mediaId": NEW_MOVIE})
    body = client.get(BASE + "/request?take=20&skip=0&filter=pending&requestedBy=3",
                      headers=KEY(3)).get_json()
    tids = {r["media"]["tmdbId"] for r in body["results"]}
    assert tids == {ASKED_MOVIE, NEW_MOVIE}
    mine = next(r for r in body["results"] if r["media"]["tmdbId"] == NEW_MOVIE)
    assert mine["media"]["status"] == shapes.STATUS_PENDING and mine["requestedBy"]["id"] == 3
    # somebody else's request can't be cancelled by a member
    assert client.delete(BASE + "/request/%d" % mine["id"], headers=KEY(5)).status_code == 404
    assert client.delete(BASE + "/request/%d" % mine["id"], headers=KEY(3)).status_code == 204
    assert client.get(BASE + "/request?filter=pending&requestedBy=3",
                      headers=KEY(3)).get_json()["pageInfo"]["results"] == 1


def test_recently_added_carries_tmdb_ids(client):
    body = client.get(BASE + "/media?filter=allavailable&sort=mediaAdded&take=10&skip=0",
                      headers=KEY()).get_json()
    assert [(r["tmdbId"], r["mediaType"]) for r in body["results"]] == [
        (OWNED_SHOW, "tv"), (OWNED_MOVIE, "movie")]
    assert all(r["status"] == shapes.STATUS_AVAILABLE for r in body["results"])


def test_what_soulsync_does_not_have_answers_neutral(client):
    """#2151: no watch-later list, no radarr/sonarr, no outside ratings."""
    assert client.get(BASE + "/user/1/watchlist?page=1", headers=KEY()).get_json()["results"] == []
    assert client.post(BASE + "/watchlist", json={"tmdbId": 1}, headers=KEY(1)).status_code == 501
    assert client.get(BASE + "/service/radarr", headers=KEY(1)).get_json() == []
    assert client.get(BASE + "/movie/1/ratingscombined", headers=KEY(1)).get_json() == {}


# ---- pure helpers --------------------------------------------------------------------

def test_monitor_for_seasons():
    real = [1, 2, 3]
    assert monitor_for_seasons(real, None) == ("all", None)
    assert monitor_for_seasons(real, [1, 2, 3]) == ("all", None)
    assert monitor_for_seasons(real, [1]) == ("first_season", None)
    assert monitor_for_seasons(real, [3]) == ("latest_season", None)
    assert monitor_for_seasons(real, [2]) == ("all", "asked for season 2")


def test_parse_seasons():
    assert parse_seasons("all") is None and parse_seasons(None) is None
    assert parse_seasons([2, "1", 0, 2]) == [1, 2]
    with pytest.raises(SeerrError):
        parse_seasons([0])


@pytest.mark.parametrize("url,path", [
    ("https://image.tmdb.org/t/p/w300/abc.jpg", "/abc.jpg"),
    ("https://image.tmdb.org/t/p/original/x/y.png", "/x/y.png"),
    ("/api/video/poster/1", None), (None, None)])
def test_tmdb_path(url, path):
    assert shapes.tmdb_path(url) == path


def test_media_status_order():
    assert shapes.media_status("show", owned=True, coverage={1: (2, 2)}) == shapes.STATUS_AVAILABLE
    assert shapes.media_status("show", owned=True, coverage={1: (2, 1)}) == shapes.STATUS_PARTIAL
    assert shapes.media_status("show", owned=True, coverage={1: (2, 0)},
                               state={"pending": True}) == shapes.STATUS_PENDING
    assert shapes.media_status("movie", owned=False, state={"approved": True}) == shapes.STATUS_PROCESSING
    assert shapes.media_status("movie", owned=False) is None


def test_store_reads_states_across_profiles(h):
    st = h.svc.store.title_states("movie", [WISHED_MOVIE, ASKED_MOVIE, NEW_MOVIE])
    assert st == {WISHED_MOVIE: {"wished": True}, ASKED_MOVIE: {"pending": True}}
    cov = h.svc.store.show_coverage([OWNED_SHOW], TODAY, "jellyfin")
    assert cov == {OWNED_SHOW: {1: (2, 2), 2: (2, 0), 3: (1, 0)}}
