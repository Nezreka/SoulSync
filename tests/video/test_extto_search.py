from __future__ import annotations

from core.video import extto_search as extto


MAGNET_BUTTON = '''
<a href="javascript:void(0);" class="btn btn-primary detail-magnet-link download-btn-magnet"
   data-id="21403167" role="button" title="Get magnet link">
    <i class="fa fa-magnet" aria-hidden="true"></i>
</a>
'''


def test_extto_extracts_js_magnet_button_id():
    assert extto.extract_magnet_ids(MAGNET_BUTTON) == ["21403167"]


def test_extto_extracts_page_tokens_and_hmac():
    html = """
    <script>
      window.pageToken = 'page-token';
      window.csrfToken = "csrf-token";
    </script>
    """
    assert extto.extract_page_tokens(html) == ("page-token", "csrf-token")
    assert extto.compute_hmac("21403167", 1234, "page-token") == (
        "19098d3ce84ac87a4f392ff413eed3509f3f6e48d50bb58bfad338e0d7bbbf40"
    )


def test_extto_parse_results_finds_detail_links():
    html = """
    <div class="torrent">
      <a href="/interstellar-2014-1080p-yify-1014669/">Interstellar (2014) 1080p BrRip x264 - YIFY</a>
      <span>1.95 GB</span><span>Seeders 42</span><span>Leechers 7</span>
    </div>
    """
    hits = extto.parse_results(html, "https://ext.to/browse/?q=interstellar", limit=5)
    assert len(hits) == 1
    assert hits[0].title == "Interstellar (2014) 1080p BrRip x264 - YIFY"
    assert hits[0].url == "https://ext.to/interstellar-2014-1080p-yify-1014669/"
    assert hits[0].size_text == "1.95 GB"
    assert hits[0].seeders == 42
    assert hits[0].leechers == 7


class _FakeClient:
    def __init__(self, base_url, timeout=30):
        self.base_url = base_url
        self.timeout = timeout
        self.posts = []

    def request(self, method, url, data=None):
        if method == "GET" and "/browse/" in url:
            return 200, '''
            <div><a href="/interstellar-2014-1080p-yify-1014669/">Interstellar (2014) 1080p BrRip x264 - YIFY</a>
            <span>1.95 GB</span><span>Seeders 42</span><span>Leechers 7</span></div>
            ''', url
        if method == "GET":
            return 200, MAGNET_BUTTON + """
            <script>window.pageToken = 'page-token'; window.csrfToken = 'csrf-token';</script>
            """, url
        self.posts.append((url, data or {}))
        return 200, '{"url":"magnet:?xt=urn:btih:abcdef1234567890abcdef1234567890abcdef12"}', url

    def close(self):
        pass


def test_extto_search_projects_hits(monkeypatch):
    monkeypatch.setattr(extto, "FlareSolverrClient", _FakeClient)
    monkeypatch.setattr(extto.time, "time", lambda: 1234)

    out = extto.extto_search("interstellar", limit=1, timeout=5, flaresolverr="http://solver:8191")

    assert out["configured"] is True
    assert out["live"] is True
    assert len(out["hits"]) == 1
    hit = out["hits"][0]
    assert hit["title"] == "Interstellar (2014) 1080p BrRip x264 - YIFY"
    assert hit["protocol"] == "torrent"
    assert hit["indexer_id"] == "extto"
    assert hit["username"] == "EXT.to"
    assert hit["download_url"].startswith("magnet:?xt=urn:btih:")
    assert hit["magnet_uri"] == hit["download_url"]
    assert hit["seeders"] == 42
    assert hit["peers"] == 7
    assert hit["size_bytes"] > 1_000_000_000



def test_extto_is_wired_as_native_video_basic_search_source():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    api = (root / "api" / "video" / "downloads.py").read_text(encoding="utf-8")
    ui = (root / "webui" / "static" / "video" / "video-search.js").read_text(encoding="utf-8")

    assert 'source == "extto"' in api
    assert "resolve_magnets=False, max_candidates=1" in api
    # an EXT.to grab must be STORED as a torrent: the row's source is what the monitor,
    # the importer's seed guard and the seeding sweep all dispatch on
    assert 'if source == "extto":\n            source = "torrent"' in api
    assert '"extto": ["ext.to", "ext torrents", "ext"]' not in api
    assert "kind: 'FlareSolverr torrent scraper', source: 'extto'" in ui


def test_extto_search_can_skip_magnet_resolution_for_fast_listing(monkeypatch):
    class _ListingOnlyClient(_FakeClient):
        def request(self, method, url, data=None):
            if method == "GET" and "/browse/" in url:
                return super().request(method, url, data)
            raise AssertionError("detail pages should not be fetched in fast listing mode")

    monkeypatch.setattr(extto, "FlareSolverrClient", _ListingOnlyClient)

    out = extto.extto_search(
        "interstellar", limit=1, timeout=5, flaresolverr="http://solver:8191", resolve_magnets=False
    )

    assert len(out["hits"]) == 1
    hit = out["hits"][0]
    assert hit["title"] == "Interstellar (2014) 1080p BrRip x264 - YIFY"
    assert hit["protocol"] == "torrent"
    assert hit["info_url"] == "https://ext.to/interstellar-2014-1080p-yify-1014669/"
    assert hit["download_url"] == ""
    assert hit["magnet_uri"] == ""


# ── the real search-results table ────────────────────────────────────────────
# ext.to renders search hits with the SAME markup as its homepage: a <tr> whose
# stat cells are labelled `<span class="add-block">Size</span><span>2.26 GB</span>`
# pairs. parse_results used to read a.find_parent(), which is the TITLE div
# ('float-left has-movie' — title, IMDb badge, uploader) and holds no stats at all,
# so every hit came back sizeless and swarmless: rendered as "Size unknown /
# Health unknown", ranked at availability 0, and refused by any profile with a
# seeder floor.
SEARCH_ROW = '''
<table><tbody><tr>
  <td class="text-left">
    <div class="float-left has-movie"><a href="/interstellar-2014-1080p-bluray-yts-yify-1014669/">Interstellar (2014) 1080p BrRip x264 - YIFY</a>
      <span class="imdb-badge">IMDb</span> Posted by <a href="?user_nick=YIFY">YIFY</a> in Movies</div>
    <a class="dwn-btn search-magnet-btn" data-id="1014669" href="javascript:void(0);"></a>
  </td>
  <td><div class="add-block-wrapper"><span class="add-block">Size</span><span>2.26 GB</span></div></td>
  <td><div class="add-block-wrapper"><span class="add-block">Files</span><span>2</span></div></td>
  <td><div class="add-block-wrapper"><span class="add-block">Age</span><span>10 years ago</span></div></td>
  <td><div class="add-block-wrapper"><span class="add-block">Seeds</span><span class="text-success">726</span></div></td>
  <td><div class="add-block-wrapper"><span class="add-block">Leechs</span><span class="text-danger">339</span></div></td>
</tr></tbody></table>
'''


def test_search_hits_take_their_stats_from_the_row_not_the_title_div():
    hits = extto.parse_results(SEARCH_ROW, "https://ext.to/browse/?q=interstellar", limit=5)
    assert len(hits) == 1
    hit = hits[0]
    assert hit.title == "Interstellar (2014) 1080p BrRip x264 - YIFY"
    assert hit.size_text == "2.26 GB", "the size cell was not read"
    assert hit.seeders == 726 and hit.leechers == 339, "the swarm cells were not read"
    assert hit.files == 2 and hit.age == "10 years ago"
    # the magnet button's torrent id rides along so a grab can resolve without
    # re-finding the row
    assert hit.magnet_id == "1014669"
    projected = extto._project(hit)
    assert projected["size_bytes"] > 2_000_000_000
    assert projected["availability"] == 726, "ranking would treat a 726-seeder torrent as dead"
    assert projected["info_url"].endswith("-1014669/")


def test_a_results_page_with_no_table_still_scrapes_its_stats():
    """The card/div layout a mirror can serve — the original scrape, kept as the
    fallback so fixing ext.to did not break everywhere else."""
    html = ('<div class="torrent"><a href="/dune-part-two-2024-1080p-99/">Dune Part Two 2024 1080p</a>'
            '<span>7.4 GB</span><span>Seeders 51</span><span>Leechers 3</span></div>')
    hit = extto.parse_results(html, "https://ext.to", limit=5)[0]
    assert hit.size_text == "7.4 GB" and hit.seeders == 51 and hit.leechers == 3


# ── FlareSolverr's answers ───────────────────────────────────────────────────
def test_the_magnet_survives_flaresolverrs_json_viewer_intact(monkeypatch):
    """getTorrentMagnet.php returns plain JSON, but FlareSolverr hands back the
    RENDERED DOM — Chrome's JSON viewer wrapping it in <html>..<pre>{...}</pre>.
    json.loads fails on that, and the old fallback regex-scraped the magnet out of
    the markup: it stopped at the first space (the `dn=` display name is full of
    them) and left \\u0026 undecoded, so the magnet arrived with its name mangled
    and EVERY TRACKER DROPPED — a torrent left to find its swarm on DHT alone."""
    wrapped = ('<html><head><meta charset="utf-8"></head><body><pre>'
               '{"success":true,"url":"magnet:?xt=urn:btih:89599bf4dc369a3a8eca26411c5ccf922d78b486'
               '\\u0026dn=Interstellar (2014) 1080p BluRay YTS YIFY'
               '\\u0026tr=udp:\\/\\/tracker.opentrackr.org:1337\\/announce","type":"magnet"}'
               '</pre><div class="json-formatter-container"></div></body></html>')
    # through the REAL fetch_magnet, so this covers the wiring and not just the helper
    detail = ('<a class="download-btn-magnet" data-id="1014669"></a>'
              "<script>window.pageToken = 'pt'; window.csrfToken = 'cs';</script>")
    monkeypatch.setattr(extto, "_fetch",
                        lambda client, url, method="GET", data=None: wrapped if method == "POST" else detail)
    magnet = extto.fetch_magnet(object(), "https://ext.to/x-1014669/", detail, "1014669")
    assert magnet.startswith("magnet:?xt=urn:btih:89599bf4dc369a3a8eca26411c5ccf922d78b486&dn=")
    assert "\\u0026" not in magnet, "the JSON escape was never decoded"
    assert "tr=udp://tracker.opentrackr.org:1337/announce" in magnet, "the trackers were dropped"
    assert "Interstellar (2014) 1080p BluRay YTS YIFY" in magnet, "the name was truncated at a space"


def test_a_failed_solve_reports_what_flaresolverr_said(monkeypatch):
    """FlareSolverr answers a timed-out challenge with HTTP 500 AND a body naming the
    cause. raise_for_status() threw the body away, leaving '500 Server Error:
    Internal Server Error for url: http://localhost:8191/v1' — which names the proxy,
    not the problem, and reads like FlareSolverr is down when it just ran out of time."""
    class _Resp:
        status_code = 500

        @staticmethod
        def json():
            return {"status": "error", "message": "Error solving the challenge. Timeout after 20.0 seconds."}

    monkeypatch.setattr(extto.requests, "post", lambda *a, **k: _Resp())
    client = extto.FlareSolverrClient("http://localhost:8191", timeout=20)
    try:
        client.request("GET", "https://ext.to/browse/?q=x")
    except RuntimeError as exc:
        assert "Timeout after 20.0 seconds" in str(exc)
    else:
        raise AssertionError("a failed solve must raise")


def test_a_magnet_resolve_retries_once_on_a_poisoned_session(monkeypatch):
    """Every EXT.to call shares one FlareSolverr session id, and the magnet endpoint
    answers {"success":false,"error":"Invalid session"} once its cookies go stale —
    after which every resolve fails until something re-establishes it. A grab is one
    deliberate click and must not lose to that, so the second pass starts clean."""
    calls = {"detail": 0, "closed": 0}
    DETAIL = '<a class="download-btn-magnet" data-id="1014669"></a><script>' \
             "window.pageToken = 'pt'; window.csrfToken = 'cs';</script>"

    def fake_fetch(client, url, method="GET", data=None):
        if method == "GET":
            calls["detail"] += 1
            return DETAIL
        if calls["detail"] == 1:      # first pass: the stale session is refused
            return '<html><body><pre>{"success":false,"error":"Invalid session"}</pre></body></html>'
        return '<html><body><pre>{"success":true,"url":"magnet:?xt=urn:btih:abc123"}</pre></body></html>'

    monkeypatch.setattr(extto, "_fetch", fake_fetch)
    monkeypatch.setattr(extto.FlareSolverrClient, "close",
                        lambda self: calls.__setitem__("closed", calls["closed"] + 1))
    out = extto.resolve_magnet("https://ext.to/x-1014669/", magnet_id="1014669",
                               flaresolverr="http://localhost:8191")
    assert out["ok"] and out["magnet"] == "magnet:?xt=urn:btih:abc123"
    assert calls["closed"] == 1, "the poisoned session was never dropped"


def test_a_resolve_that_never_works_fails_honestly(monkeypatch):
    monkeypatch.setattr(extto, "_fetch", lambda *a, **k: "<html></html>")
    out = extto.resolve_magnet("https://ext.to/x-1/", magnet_id="1", flaresolverr="http://localhost:8191")
    assert out["ok"] is False and "magnet" in out["error"].lower()
    assert extto.resolve_magnet("", flaresolverr="http://localhost:8191")["ok"] is False
