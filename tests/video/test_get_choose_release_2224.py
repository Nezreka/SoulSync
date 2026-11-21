"""Get → Choose release and Manual search ask the indexers the same way (#2224).

Thibs: for a french film, Get → Choose release only found the english-named
releases, Manual search found the ones under the original title too.

both send /downloads/search/start, but for a title that isn't in the library
the Get modal names it as media_id=<tmdb id>, media_source=tmdb and Manual
search as tmdb_id=<tmdb id>. _with_title_identity only filled the ids from the
library row, so the Get modal's search had no tmdb_id: no original title or
aliases to search under, no tmdb id query, and every release named
'Intouchables' was judged against 'The Intouchables' alone.
"""

from __future__ import annotations

import pytest

INTOUCHABLES = 77338
AMELIE = 194
AMELIE_FR = "Le.Fabuleux.Destin.d.Amelie.Poulain.2001.FRENCH.1080p.BluRay.x264-GRP"
EN_TITLE = "The Intouchables"
ORIGINAL = "Intouchables"
FR_REL = "Intouchables.2011.FRENCH.1080p.BluRay.x264-GRP"
FR_BARE = "Intouchables.2011.1080p.BluRay.x264-GRP"
EN_REL = "The.Intouchables.2011.1080p.BluRay.x264-GRP"

_IDENT = {INTOUCHABLES: {"name": EN_TITLE, "original_name": ORIGINAL, "year": 2011,
                         "imdb_id": "tt1675434", "aliases": ["Untouchable"]},
          AMELIE: {"name": "Amélie", "original_name": "Le Fabuleux Destin d'Amélie Poulain",
                   "year": 2001, "aliases": []}}


class _Engine:
    def title_identity(self, kind, tmdb_id):
        return dict(_IDENT.get(int(tmdb_id), {}))

    def alt_titles_for(self, kind, tmdb_id):
        i = _IDENT.get(int(tmdb_id), {})
        return [t for t in [i.get("name"), i.get("original_name"), *(i.get("aliases") or [])] if t]


@pytest.fixture()
def api(tmp_path, monkeypatch):
    from flask import Flask

    import api.video as videoapi
    import core.video.enrichment.engine as eng_mod
    import core.video.extto_search as es
    import core.video.prowlarr_search as ps
    import core.video.slskd_search as ss
    from database.video_database import VideoDatabase

    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: _Engine())
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    client = app.test_client()
    state = {"hits": [], "calls": []}

    def fake_prowlarr(scope, title, **kw):
        state["calls"].append({"scope": scope, "title": title, **kw})
        return {"configured": True, "hits": state["hits"]}

    monkeypatch.setattr(ps, "prowlarr_search", fake_prowlarr)
    monkeypatch.setattr(es, "extto_search", lambda *a, **k: {"configured": False, "hits": []})
    monkeypatch.setattr(ss, "poll_search", lambda sid: {"hits": state["hits"], "total_files": 1})
    client.state = state
    try:
        yield client
    finally:
        videoapi._video_db = None


def _hit(title):
    return {"title": title, "filename": title, "size_bytes": 8 * 1024 ** 3, "seeders": 50,
            "username": "indexer", "download_url": "http://x/" + title, "protocol": "usenet"}


# what each entry point sends for a french film you don't own yet
GET_MODAL = {"scope": "movie", "title": EN_TITLE, "year": 2011, "source": "usenet",
             "media_id": INTOUCHABLES, "media_source": "tmdb"}
MANUAL = {"scope": "movie", "title": EN_TITLE, "year": 2011, "source": "usenet",
          "media_id": INTOUCHABLES, "media_source": "tmdb", "tmdb_id": INTOUCHABLES}


def _search(api, body):
    api.state["calls"].clear()
    resp = api.post("/api/video/downloads/search/start", json=body)
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json(), api.state["calls"][-1]


def test_choose_release_searches_the_original_title_and_the_ids(api):
    _out, call = _search(api, {**GET_MODAL, "title": "Amélie", "year": 2001, "media_id": AMELIE})
    assert call["alt_titles"] == ["Le Fabuleux Destin d'Amélie Poulain"]
    assert call["tmdb_id"] == AMELIE
    # 'Intouchables' folds to the same search as 'The Intouchables', so that
    # film adds no text query, but its ids go to the indexers
    _out, call = _search(api, GET_MODAL)
    assert call["tmdb_id"] == INTOUCHABLES
    assert call["imdb_id"] == "tt1675434"


@pytest.mark.parametrize("film", [
    {},
    {"title": "Amélie", "year": 2001, "media_id": AMELIE, "tmdb_id": AMELIE},
])
def test_choose_release_and_manual_search_ask_the_same_thing(api, film):
    manual = {**MANUAL, **film}
    get = {k: v for k, v in manual.items() if k != "tmdb_id"}
    o1, get_call = _search(api, get)
    o2, manual_call = _search(api, manual)
    assert get_call == manual_call
    assert o1["queries"] == o2["queries"]
    assert any("tmdbid=" in q for q in o1["queries"])


def test_a_release_named_by_the_original_title_is_accepted(api):
    api.state["hits"] = [_hit(FR_REL), _hit(EN_REL)]
    out, _call = _search(api, GET_MODAL)
    rows = {r["title"]: r for r in out["results"]}
    assert rows[FR_REL]["accepted"] is True, rows[FR_REL].get("rejected")
    assert rows[EN_REL]["accepted"] is True


def test_a_release_named_only_by_a_different_original_title_is_accepted(api):
    # 'The Intouchables' folds to 'Intouchables' anyway. Amélie's original
    # title shares nothing with it, so only the identity's names let it in
    api.state["hits"] = [_hit(AMELIE_FR)]
    out, call = _search(api, {**GET_MODAL, "title": "Amélie", "year": 2001, "media_id": AMELIE})
    assert call["alt_titles"] == ["Le Fabuleux Destin d'Amélie Poulain"]
    row = out["results"][0]
    assert row["accepted"] is True, row.get("rejected")


def test_the_soulseek_poll_judges_by_the_same_names(api):
    api.state["hits"] = [_hit(FR_BARE)]
    resp = api.get("/api/video/downloads/search/poll", query_string={
        "id": "x", "scope": "movie", "title": EN_TITLE, "year": 2011,
        "media_id": INTOUCHABLES, "media_source": "tmdb"})
    rows = resp.get_json()["results"]
    assert rows and rows[0]["accepted"] is True, rows[0].get("rejected")


def test_a_library_id_is_never_read_as_a_tmdb_id(api):
    # media_source=library names a library row; an id that isn't there must
    # not turn into a tmdb id query
    _o, call = _search(api, {**GET_MODAL, "media_source": "library", "media_id": 5})
    assert "tmdb_id" not in call and call["alt_titles"] == []
    # nor an id with no source named: only an explicit tmdb source is one
    bare = {k: v for k, v in GET_MODAL.items() if k != "media_source"}
    _o, call = _search(api, {**bare, "media_id": 5})
    assert "tmdb_id" not in call


def test_french_audio_ranks_first_for_a_french_profile_and_stays_for_others():
    from api.video.downloads import _evaluate_hits
    from core.video import quality_profile
    hits = [_hit(EN_REL), _hit(FR_REL)]
    names = [EN_TITLE, ORIGINAL]

    def rank(**kw):
        return _evaluate_hits(hits, quality_profile.normalize(kw), "movie", None, None,
                              blocked=frozenset(), blocked_users=frozenset(),
                              want_year=2011, want_title=names)

    fr = rank(audio_language_mode="prefer", audio_languages=["fr"])
    assert fr[0]["title"] == FR_REL and fr[0]["accepted"]
    plain = rank()
    assert all(r["accepted"] for r in plain)
    en = rank(audio_language_mode="prefer", audio_languages=["en"])
    assert {r["title"] for r in en if r["accepted"]} == {FR_REL, EN_REL}
    assert en[0]["title"] == EN_REL


def test_tmdb_identity_carries_the_imdb_id():
    from core.video.enrichment.clients import parse_title_identity
    mv = {"id": 1, "title": EN_TITLE, "original_title": ORIGINAL, "release_date": "2011-11-02",
          "imdb_id": "tt1675434", "alternative_titles": {"titles": []}}
    assert parse_title_identity("movie", mv)["imdb_id"] == "tt1675434"
    tv = {"id": 2, "name": "Lupin", "original_name": "Lupin", "first_air_date": "2021-01-08",
          "external_ids": {"imdb_id": "tt2531336", "tvdb_id": 366314},
          "alternative_titles": {"results": []}}
    ident = parse_title_identity("show", tv)
    assert (ident["imdb_id"], ident["tvdb_id"]) == ("tt2531336", 366314)
    assert "imdb_id" not in parse_title_identity("movie", {**mv, "imdb_id": None})


def test_the_wishlist_drain_searches_by_the_same_ids(monkeypatch):
    import core.video.enrichment.engine as eng_mod
    from core.automation.handlers import video_process_wishlist as wl
    monkeypatch.setattr(eng_mod, "get_video_enrichment_engine", lambda: _Engine())
    ctx = wl.search_context({"tmdb_id": INTOUCHABLES, "title": EN_TITLE, "year": 2011}, "movie")
    assert (ctx["tmdb_id"], ctx["imdb_id"]) == (INTOUCHABLES, "tt1675434")
    # a row's own id wins over TMDB's
    own = wl.search_context({"tmdb_id": INTOUCHABLES, "title": EN_TITLE, "imdb_id": "tt0000001"},
                            "movie")
    assert own["imdb_id"] == "tt0000001"
