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
    assert 'client_source = "torrent" if source == "extto" else source' in api
    assert '"extto": ["ext.to", "ext torrents", "ext"]' not in api
    assert "kind: 'FlareSolverr torrent scraper', source: 'extto'" in ui
