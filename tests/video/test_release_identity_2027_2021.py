"""A release has to be the wanted show before it is grabbed (#2027), and a
show is searched under more than the one name the library stores (#2021).

#2027 (SeadogsBooty): Grab season on The Office (2005) sent
'The.Office.2024.S01E01..E06.1080p.HEVC.x265' to SABnzbd. the episode
searches the show page runs sent the title and nothing else, so the year
gate in evaluate_release never had a year to check, and The Office (2024)
was the best accepted release. the importer caught it after the download
("Release is from 2024, not The Office (2005)"), which is too late.

#2021 (Thibs): a library in french stores "L'amour sous couverture" for the
show TMDB calls "Love in Disguise", and that one name was the only one
searched. TMDB's alternative titles carry neither, so even a release named in
english was refused as a wrong title.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

OFFICE_2024 = "The.Office.2024.S01E06.1080p.HEVC.x265-GRP"
OFFICE_2005 = "The.Office.S01E06.Hot.Girl.1080p.WEB.h264-GRP"
LOVE_EN = "Love.in.Disguise.S01E06.1080p.WEB.h264-KBBQ"
LOVE_LOWER = "love in disguise s01e06 1080p web h264 kbbq"     # the #2021 screenshot

_IDENT = {
    2316: {"name": "The Office", "original_name": "The Office", "year": 2005,
           "aliases": ["The Office (U.S.)"]},
    303745: {"name": "Love in Disguise", "original_name": "나의 유죄인간", "year": 2026,
             "aliases": ["Amor disfrazado", "My Guilty Man"]},
    14: {"name": "Shuriken School", "original_name": "Shuriken School", "year": 2006,
         "aliases": []},
}


class _Engine:
    """the enrichment engine, as far as release identity reads it"""

    def __init__(self):
        self.calls = []

    def title_identity(self, kind, tmdb_id):
        self.calls.append((kind, tmdb_id))
        return dict(_IDENT.get(int(tmdb_id), {}))

    def alt_titles_for(self, kind, tmdb_id):
        i = _IDENT.get(int(tmdb_id), {})
        return [t for t in [i.get("name"), i.get("original_name"), *(i.get("aliases") or [])] if t]


@pytest.fixture()
def engine(monkeypatch):
    import core.video.enrichment.engine as eng_mod
    eng = _Engine()
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: eng)
    return eng


@pytest.fixture()
def api(tmp_path, monkeypatch, engine):
    """the video blueprint on its own db, Prowlarr and slskd polls stubbed"""
    from flask import Flask

    import api.video as videoapi
    from database.video_database import VideoDatabase

    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()

    state = {"hits": [], "calls": []}
    import core.video.extto_search as es
    import core.video.prowlarr_search as ps
    import core.video.slskd_search as ss

    def fake_prowlarr(scope, title, **kw):
        state["calls"].append({"scope": scope, "title": title, **kw})
        return {"configured": True, "hits": state["hits"]}

    monkeypatch.setattr(ps, "prowlarr_search", fake_prowlarr)
    monkeypatch.setattr(es, "extto_search", lambda *a, **k: {"configured": False, "hits": []})
    monkeypatch.setattr(ss, "poll_search", lambda sid: {"hits": state["hits"], "total_files": 1})
    client.state = state
    client.db = videoapi._video_db
    try:
        yield client
    finally:
        videoapi._video_db = None


def _hit(title):
    return {"title": title, "filename": title, "size_bytes": 2 * 1024 ** 3, "seeders": 50,
            "username": "indexer", "download_url": "http://x/" + title, "protocol": "usenet"}


def _by_title(resp):
    return {r["title"]: r for r in resp.get_json()["results"]}


# ── #2027: the year gate runs on every manual / Grab season search ──────────

def test_grab_season_search_refuses_the_2024_office_for_the_2005_one(api):
    # exactly what the show page's Grab season sends: title + tmdb id, no year
    api.state["hits"] = [_hit(OFFICE_2024), _hit(OFFICE_2005)]
    resp = api.post("/api/video/downloads/search/start", json={
        "scope": "episode", "title": "The Office", "season": 1, "episode": 6,
        "source": "usenet", "tmdb_id": 2316})
    rows = _by_title(resp)
    assert rows[OFFICE_2024]["accepted"] is False
    assert "2024" in rows[OFFICE_2024]["rejected"] and "2005" in rows[OFFICE_2024]["rejected"]
    assert rows[OFFICE_2005]["accepted"] is True
    # the auto-pick takes the first accepted row: never the 2024 show
    assert resp.get_json()["results"][0]["title"] == OFFICE_2005


def test_the_library_year_wins_and_needs_no_tmdb(api, engine):
    conn = api.db._get_connection()
    try:
        conn.execute("INSERT INTO shows (id, title, tmdb_id, first_air_date) "
                     "VALUES (77, 'The Office', 2316, '2005-03-24')")
        conn.commit()
    finally:
        conn.close()
    assert api.db.search_ids_for("show", library_id=77)["year"] == 2005
    api.state["hits"] = [_hit(OFFICE_2024)]
    resp = api.post("/api/video/downloads/search/start", json={
        "scope": "episode", "title": "The Office", "season": 1, "episode": 6,
        "source": "usenet", "media_id": 77, "media_source": "library"})
    assert _by_title(resp)[OFFICE_2024]["accepted"] is False


def test_the_soulseek_poll_judges_by_the_same_identity(api):
    # the poll url carries the ids, never a year: the server finds it
    api.state["hits"] = [_hit(OFFICE_2024), _hit(OFFICE_2005)]
    resp = api.get("/api/video/downloads/search/poll?id=abc&scope=episode&title=The%20Office"
                   "&season=1&episode=6&tmdb_id=2316")
    rows = _by_title(resp)
    assert rows[OFFICE_2024]["accepted"] is False
    assert rows[OFFICE_2005]["accepted"] is True


def test_a_different_show_is_refused_by_title(api):
    # the #2021 second screenshot: this release on Shuriken School
    api.state["hits"] = [_hit(LOVE_LOWER)]
    resp = api.post("/api/video/downloads/search/start", json={
        "scope": "episode", "title": "Shuriken School", "season": 1, "episode": 6,
        "source": "usenet", "tmdb_id": 14})
    row = _by_title(resp)[LOVE_LOWER]
    assert row["accepted"] is False and "Wrong title" in row["rejected"]


def test_basic_search_without_an_id_is_left_alone(api, engine):
    api.state["hits"] = [_hit(OFFICE_2024)]
    resp = api.post("/api/video/downloads/search/start", json={
        "scope": "episode", "title": "The Office", "season": 1, "episode": 6,
        "source": "usenet"})
    assert _by_title(resp)[OFFICE_2024]["accepted"] is True
    assert engine.calls == []


def test_the_drain_gets_the_year_for_a_show_outside_the_library(engine):
    from core.automation.handlers import video_process_wishlist as wl
    item = {"show_title": "The Office", "show_tmdb_id": 2316, "season_number": 1,
            "episode_number": 6}
    ctx = wl.search_context(item, "episode")
    assert ctx["year"] == "2005"
    # the library's year still wins over TMDB's
    assert wl.search_context({**item, "show_year": 2004}, "episode")["year"] == "2004"


# ── #2021: search under the english and original names too ──────────────────

def test_a_french_library_title_still_takes_the_english_release(api):
    api.state["hits"] = [_hit(LOVE_EN), _hit(LOVE_LOWER)]
    resp = api.post("/api/video/downloads/search/start", json={
        "scope": "episode", "title": "L'amour sous couverture", "season": 1, "episode": 6,
        "source": "usenet", "tmdb_id": 303745})
    rows = _by_title(resp)
    assert rows[LOVE_EN]["accepted"] is True
    assert rows[LOVE_LOWER]["accepted"] is True
    call = api.state["calls"][-1]
    assert call["title"] == "L'amour sous couverture"
    assert call["alt_titles"] == ["Love in Disguise", "나의 유죄인간"]
    queries = resp.get_json()["queries"]
    assert any("Love in Disguise S01E06" in q for q in queries)


def test_strategies_add_one_text_query_per_other_name_and_stay_bounded():
    from core.video.prowlarr_search import build_strategies
    base = build_strategies("episode", "L'amour sous couverture", season=1, episode=6, tvdb_id=9)
    more = build_strategies("episode", "L'amour sous couverture", season=1, episode=6, tvdb_id=9,
                            alt_titles=["Love in Disguise", "나의 유죄인간", "Amor disfrazado"])
    extra = more[len(base):]
    assert extra == [("search", "Love in Disguise S01E06", []),
                     ("search", "나의 유죄인간 S01E06", [])]
    # a name that is the primary again adds nothing
    assert build_strategies("season", "Dexter", season=2, alt_titles=["dexter"]) == \
        build_strategies("season", "Dexter", season=2)


def test_search_titles_are_deduped_and_capped(engine):
    from core.automation.handlers import video_process_wishlist as wl
    assert wl.search_titles("L'amour sous couverture", "show", 303745) == [
        "L'amour sous couverture", "Love in Disguise", "나의 유죄인간"]
    # The Office: library, english and original all fold to one name
    assert wl.search_titles("The Office", "show", 2316) == ["The Office"]
    assert wl.search_titles("Whatever", "show", None) == ["Whatever"]


def test_the_drain_searches_every_name_and_accepts_any(engine, monkeypatch):
    from core.automation.handlers import video_process_wishlist as wl
    import core.video.prowlarr_search as ps
    import core.video.extto_search as es
    seen = {}

    def fake(scope, title, **kw):
        seen.update(kw, title=title)
        return {"configured": True, "hits": [_hit(LOVE_EN)]}

    monkeypatch.setattr(ps, "prowlarr_search", fake)
    monkeypatch.setattr(es, "extto_search", lambda *a, **k: {"configured": False, "hits": []})
    item = {"show_title": "L'amour sous couverture", "show_tmdb_id": 303745,
            "season_number": 1, "episode_number": 6}
    cands, _err = wl._search_one_source("usenet", item, "episode")
    assert seen["alt_titles"] == ["Love in Disguise", "나의 유죄인간"]
    assert [c["title"] for c in cands if c["accepted"]] == [LOVE_EN]


def test_tmdb_identity_parses_names_year_and_aliases():
    from core.video.enrichment.clients import parse_title_identity
    tv = {"id": 303745, "name": "Love in Disguise", "original_name": "나의 유죄인간",
          "first_air_date": "2026-10-19",
          "alternative_titles": {"results": [{"title": "My Guilty Man"}, {"title": "my guilty man"}]}}
    assert parse_title_identity("show", tv) == {
        "name": "Love in Disguise", "original_name": "나의 유죄인간", "year": 2026,
        "aliases": ["My Guilty Man"]}
    mv = {"id": 1, "title": "Amelie", "original_title": "Le Fabuleux Destin d'Amélie Poulain",
          "release_date": "", "alternative_titles": {"titles": []}}
    assert parse_title_identity("movie", mv)["year"] is None
    assert parse_title_identity("show", {}) == {}


def test_engine_alias_set_leads_with_tmdb_names_and_caches():
    from core.video.enrichment.cache import TTLCache
    from core.video.enrichment.engine import VideoEnrichmentEngine
    calls = {"n": 0}

    class _Client:
        def title_identity(self, kind, tmdb_id):
            calls["n"] += 1
            return dict(_IDENT[303745])

    eng = VideoEnrichmentEngine.__new__(VideoEnrichmentEngine)
    eng._cache = TTLCache(maxsize=16, ttl=60)
    eng.workers = {"tmdb": type("W", (), {"enabled": True, "client": _Client()})()}
    want = ["Love in Disguise", "나의 유죄인간", "Amor disfrazado", "My Guilty Man"]
    assert eng.alt_titles_for("show", 303745) == want
    assert eng.alt_titles_for("show", 303745) == want
    assert eng.title_identity("show", 303745)["year"] == 2026
    assert calls["n"] == 1, "one TMDB call serves the aliases and the identity"


# ── the show page's headless grab (video-grab.js) names its show ─────────────

def _node():
    for candidate in ("node", "/mnt/c/Program Files/nodejs/node.exe"):
        if shutil.which(candidate) or Path(candidate).exists():
            return candidate
    return None


_GRAB_HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync('webui/static/video/video-grab.js', 'utf8');
const seen = { start: [], poll: [] };
global.window = {}; global.document = { dispatchEvent() {} };
global.CustomEvent = function () {};
global.setTimeout = (fn) => fn();
global.fetch = (url, opt) => {
  const body = opt && opt.body ? JSON.parse(opt.body) : null;
  const reply = (x) => Promise.resolve({ ok: true, json: () => Promise.resolve(x) });
  if (url.indexOf('/downloads/config') >= 0) return reply({ download_mode: 'soulseek' });
  if (url.indexOf('/search/start') >= 0) { seen.start.push(body); return reply({ id: 'abc', poll_ms: 1 }); }
  if (url.indexOf('/search/poll') >= 0) { seen.poll.push(url); return reply({ results: [] }); }
  return reply({});
};
eval(src);
(async () => {
  await global.window.VideoGrab.season(
    { title: 'The Office', season: 1, episodes: [6], mediaId: 2316, mediaSource: 'tmdb',
      tmdbId: 2316, year: 2005 }, () => {});
  console.log(JSON.stringify(seen));
})();
"""


@pytest.mark.skipif(_node() is None, reason="node not available")
def test_the_show_page_grab_sends_the_shows_ids_and_year():
    out = subprocess.run([_node(), "-e", _GRAB_HARNESS], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    seen = json.loads(out.stdout.strip())
    start = seen["start"][0]
    assert start["tmdb_id"] == 2316 and start["year"] == 2005
    assert "media_id" not in start, "a tmdb id is not a library id"
    assert "tmdb_id=2316" in seen["poll"][0] and "year=2005" in seen["poll"][0]


def test_the_get_modal_search_carries_the_year():
    js = Path("webui/static/video/video-download-view.js").read_text(encoding="utf-8")
    fn = js[js.index("function withIdentity("):js.index("function searchInto(")]
    assert "extra.year = o.year" in fn
    assert "'&year=' + encodeURIComponent(params.year)" in js
    detail = Path("webui/static/video/video-detail.js").read_text(encoding="utf-8")
    assert detail.count("tmdbId: data.tmdb_id || null, poster: _showPoster()") == 2
