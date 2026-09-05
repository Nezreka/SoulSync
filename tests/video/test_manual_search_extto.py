"""EXT.to is half the torrent lane in a manual search too, not a separate source.

Boulder: "how come when doing a manual search for tv shows or movies, why doesnt
ext.to results appear in the torrent list?"

Because the two search paths disagreed about what "torrent" meant. The wishlist
drain runs Prowlarr AND EXT.to and merges them - its own comment calls EXT.to
"the OTHER HALF of the torrent lane". The manual endpoint ran Prowlarr alone, so
the same search that found an EXT.to release in the background returned nothing
by hand, and EXT.to only showed up if you knew to pick it as its own source.

The rules that carry weight here:

* The lane has only failed when NEITHER half could run. Prowlarr being down or
  unconfigured must not hide EXT.to results behind a message about a service the
  user may not even run.
* A half-answered lane says so. Results plus a note beats a silently short list.
* Usenet is Prowlarr-only. EXT.to is torrents.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def search(tmp_path, monkeypatch):
    """POST /api/video/downloads/search with both halves stubbed.

    Builds its own app around the video blueprint, the way the rest of the
    video API tests do - the shared flask_client does not mount it.
    """
    from flask import Flask

    import api.video as videoapi
    from database.video_database import VideoDatabase

    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    flask_client = app.test_client()

    state = {"prowlarr": {"configured": True, "hits": []},
             "extto": {"configured": True, "hits": []},
             "extto_calls": []}

    import core.video.prowlarr_search as ps
    import core.video.extto_search as es

    monkeypatch.setattr(ps, "prowlarr_search", lambda *a, **kw: state["prowlarr"])

    def fake_extto(query, **kw):
        state["extto_calls"].append(query)
        if isinstance(state["extto"], Exception):
            raise state["extto"]
        return state["extto"]

    monkeypatch.setattr(es, "extto_search", fake_extto)

    def run(source="torrent", endpoint="/api/video/downloads/search", **body):
        payload = {"scope": "movie", "title": "Arrival", "source": source}
        payload.update(body)
        return flask_client.post(endpoint, json=payload)

    run.state = state
    try:
        yield run
    finally:
        videoapi._video_db = None


def _hit(title, seeders=10, indexer="nyaa"):
    return {"title": title, "size_bytes": 5 * 1024 ** 3, "seeders": seeders,
            "download_url": "magnet:?xt=urn:btih:" + title.replace(" ", ""),
            "indexer": indexer, "indexer_id": indexer}


def _titles(resp):
    return [r.get("title") for r in resp.get_json().get("results", [])]


# ── the actual complaint ─────────────────────────────────────────────────────
def test_a_torrent_search_returns_extto_results_alongside_prowlarr(search):
    search.state["prowlarr"] = {"configured": True,
                                "hits": [_hit("Arrival 2016 1080p BluRay x264-GRP")]}
    search.state["extto"] = {"configured": True,
                             "hits": [_hit("Arrival.2016.1080p.WEB.H264-EXT", indexer="extto")]}

    resp = search()
    assert resp.status_code == 200
    titles = _titles(resp)
    assert any("GRP" in t for t in titles), "lost the Prowlarr half"
    assert any("EXT" in t for t in titles), "EXT.to results still missing from the torrent list"


def test_extto_is_searched_at_all_for_a_plain_torrent_search(search):
    search.state["extto"] = {"configured": True, "hits": []}
    search(source="torrent")
    assert search.state["extto_calls"] == ["Arrival"]


# ── neither-half-ran is the only real failure ────────────────────────────────
def test_prowlarr_being_unconfigured_no_longer_hides_extto(search):
    """A user running FlareSolverr without Prowlarr got an error and no results,
    while EXT.to sat there able to answer."""
    search.state["prowlarr"] = {"configured": False, "hits": []}
    search.state["extto"] = {"configured": True,
                             "hits": [_hit("Arrival.2016.1080p.WEB.H264-EXT", indexer="extto")]}

    resp = search()
    data = resp.json
    assert "error" not in data, data.get("error")
    assert any("EXT" in t for t in _titles(resp))
    # ...but it still says the other half did not run
    assert "Prowlarr" in (data.get("note") or "")


def test_both_halves_down_is_still_an_error(search):
    search.state["prowlarr"] = {"configured": False, "hits": []}
    search.state["extto"] = {"configured": False, "hits": []}

    data = search().get_json()
    assert data["results"] == []
    assert "Prowlarr" in data["error"]
    assert "FlareSolverr" in data["error"]


def test_a_broken_extto_never_sinks_the_prowlarr_results(search):
    """FlareSolverr throwing must not take the whole search down with it."""
    search.state["prowlarr"] = {"configured": True,
                                "hits": [_hit("Arrival 2016 1080p BluRay x264-GRP")]}
    search.state["extto"] = RuntimeError("flaresolverr timed out")

    resp = search()
    data = resp.get_json()
    assert "error" not in data
    assert any("GRP" in t for t in _titles(resp))
    assert "EXT.to" in (data.get("note") or "")


def test_a_fully_working_lane_adds_no_note(search):
    search.state["prowlarr"] = {"configured": True, "hits": [_hit("Arrival 2016 1080p")]}
    search.state["extto"] = {"configured": True, "hits": []}
    assert "note" not in search().get_json()


# ── scope ────────────────────────────────────────────────────────────────────
def test_usenet_does_not_reach_for_extto(search):
    """EXT.to is a torrent board. Searching it for a usenet request would spend
    a FlareSolverr round trip to produce results the lane cannot use."""
    search.state["prowlarr"] = {"configured": True, "hits": [_hit("Arrival 2016")]}
    search(source="usenet")
    assert search.state["extto_calls"] == []


def test_picking_extto_alone_still_works(search):
    """The explicit source is not removed - someone who wants only EXT.to can
    still ask for exactly that."""
    search.state["extto"] = {"configured": True,
                             "hits": [_hit("Arrival.2016.WEB-EXT", indexer="extto")]}
    resp = search(source="extto")
    assert any("EXT" in t for t in _titles(resp))


# ── the endpoint the UI ACTUALLY calls ───────────────────────────────────────
#
# There are two manual-search endpoints and only ONE is reachable from any UI:
# /downloads/search/start. /downloads/search has no caller outside these tests.
# Wiring EXT.to into the second one shipped a feature nobody could see, so both
# share _torrent_lane_hits now and every test below runs against BOTH.

START = "/api/video/downloads/search/start"
PLAIN = "/api/video/downloads/search"


@pytest.mark.parametrize("endpoint", [PLAIN, START])
def test_both_search_endpoints_return_extto_in_the_torrent_lane(search, endpoint):
    search.state["prowlarr"] = {"configured": True,
                                "hits": [_hit("Arrival 2016 1080p BluRay x264-GRP")]}
    search.state["extto"] = {"configured": True,
                             "hits": [_hit("Arrival.2016.1080p.WEB.H264-EXT", indexer="extto")]}

    titles = _titles(search(endpoint=endpoint))
    assert any("GRP" in t for t in titles), "lost the Prowlarr half"
    assert any("EXT" in t for t in titles), f"EXT.to missing from {endpoint}"


@pytest.mark.parametrize("endpoint", [PLAIN, START])
def test_both_endpoints_survive_prowlarr_being_unconfigured(search, endpoint):
    search.state["prowlarr"] = {"configured": False, "hits": []}
    search.state["extto"] = {"configured": True,
                             "hits": [_hit("Arrival.2016.WEB-EXT", indexer="extto")]}

    data = search(endpoint=endpoint).get_json()
    assert "error" not in data, data.get("error")
    assert any("EXT" in t for t in _titles(search(endpoint=endpoint)))
    assert "Prowlarr" in (data.get("note") or "")


@pytest.mark.parametrize("endpoint", [PLAIN, START])
def test_both_endpoints_error_only_when_neither_half_ran(search, endpoint):
    search.state["prowlarr"] = {"configured": False, "hits": []}
    search.state["extto"] = {"configured": False, "hits": []}
    data = search(endpoint=endpoint).get_json()
    assert "Prowlarr" in data["error"] and "FlareSolverr" in data["error"]


@pytest.mark.parametrize("endpoint", [PLAIN, START])
def test_neither_endpoint_asks_extto_for_a_usenet_search(search, endpoint):
    search.state["prowlarr"] = {"configured": True, "hits": [_hit("Arrival 2016")]}
    search(source="usenet", endpoint=endpoint)
    assert search.state["extto_calls"] == []


# ── what the grab reads off a row ────────────────────────────────────────────
@pytest.mark.parametrize("endpoint", [PLAIN, START])
def test_an_extto_row_carries_its_indexer_id_and_info_url(search, endpoint):
    """These two are how a grab tells an EXT.to row apart and resolves its magnet.

    NOT the row's `source` field: that is the RELEASE source parsed out of the
    filename ("BluRay", "WEB-DL"). Reading it as the download source sends
    source="BluRay" to the grab endpoint, which rejects it as unsupported, and
    then EVERY grab fails - which is exactly what happened when this was first
    "fixed".
    """
    ext = _hit("Arrival.2016.1080p.WEB.H264-EXT", indexer="extto")
    ext["download_url"] = ""            # unresolved by design
    ext["info_url"] = "https://ext.to/torrent/123"
    ext["magnet_id"] = "123"
    search.state["prowlarr"] = {"configured": True, "hits": []}
    search.state["extto"] = {"configured": True, "hits": [ext]}

    rows = search(endpoint=endpoint).get_json()["results"]
    assert rows, "no rows came back"
    row = rows[0]
    assert row["indexer_id"] == "extto"
    assert row["info_url"] == "https://ext.to/torrent/123"
    assert row["magnet_id"] == "123"


@pytest.mark.parametrize("endpoint", [PLAIN, START])
def test_a_rows_source_field_is_the_release_source_not_the_download_source(search, endpoint):
    """Pinning the trap directly. A BluRay release parses to source "BluRay",
    and nothing may mistake that for how it gets downloaded."""
    search.state["prowlarr"] = {"configured": True,
                                "hits": [_hit("Arrival 2016 1080p BluRay x264-GRP")]}
    search.state["extto"] = {"configured": True, "hits": []}

    row = search(endpoint=endpoint).get_json()["results"][0]
    assert str(row.get("source") or "").lower() not in ("torrent", "usenet", "soulseek", "extto")
