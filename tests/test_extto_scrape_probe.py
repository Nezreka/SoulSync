from __future__ import annotations

from scripts import extto_scrape_probe as extto


MAGNET_BUTTON = '''
<a href="javascript:void(0);" class="btn btn-primary detail-magnet-link download-btn-magnet"
   data-id="21403167" role="button" title="Get magnet link">
    <i class="fa fa-magnet" aria-hidden="true"></i>
</a>
'''


def test_extto_probe_extracts_js_magnet_button_id():
    assert extto.extract_magnet_ids(MAGNET_BUTTON) == ["21403167"]


def test_extto_probe_builds_get_torrent_magnet_post_payload():
    urls = extto.magnet_endpoint_urls(
        "https://ext.to/interstellar-21403167/",
        "21403167",
        "page-token",
        "csrf-token",
        1234,
    )
    assert urls == [(
        "POST",
        "https://ext.to/ajax/getTorrentMagnet.php",
        {
            "torrent_id": "21403167",
            "download_type": "magnet",
            "timestamp": "1234",
            "hmac": extto.compute_hmac("21403167", 1234, "page-token"),
            "sessid": "csrf-token",
        },
    )]


def test_extto_probe_reads_magnet_from_common_json_shapes():
    magnet = "magnet:?xt=urn:btih:abcdef1234567890abcdef1234567890abcdef12"
    assert extto.magnet_from_payload({"url": magnet}) == magnet
    assert extto.magnet_from_payload({"data": {"magnet": magnet}}) == magnet
    assert extto.magnet_from_payload({"hash": "ABCDEF1234567890ABCDEF1234567890ABCDEF12"}) == magnet


def test_extto_probe_can_fallback_to_visible_info_hash():
    html = "<button id='show-hash-btn'>show</button><code>ABCDEF1234567890ABCDEF1234567890ABCDEF12</code>"
    assert extto.extract_info_hash(html) == "abcdef1234567890abcdef1234567890abcdef12"


def test_extto_probe_extracts_page_tokens():
    html = """
    <script>window.pageToken = 'page-token';</script>
    <script>window.csrfToken = "csrf-token";</script>
    """
    assert extto.extract_page_tokens(html) == ("page-token", "csrf-token")


def test_flaresolverr_client_uses_v1_request_get(monkeypatch):
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "ok", "solution": {"status": 200, "response": "<html></html>", "url": "https://ext.to/"}}

    def fake_post(url, json=None, timeout=None):
        seen.update(url=url, json=json, timeout=timeout)
        return Resp()

    monkeypatch.setattr(extto.requests, "post", fake_post)
    status, html, final_url = extto.FlareSolverrClient("http://localhost:8191", 25).request("GET", "https://ext.to/")
    assert seen["url"] == "http://localhost:8191/v1"
    assert seen["json"]["cmd"] == "request.get"
    assert seen["json"]["session"] == "soulsync-extto-probe"
    assert status == 200 and html == "<html></html>" and final_url == "https://ext.to/"


def test_flaresolverr_client_uses_v1_request_post(monkeypatch):
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"status": "ok", "solution": {"status": 200, "response": '{"url":"magnet:?xt=urn:btih:abc"}'}}

    def fake_post(url, json=None, timeout=None):
        seen.update(url=url, json=json, timeout=timeout)
        return Resp()

    monkeypatch.setattr(extto.requests, "post", fake_post)
    extto.FlareSolverrClient("http://localhost:8191", 25).request(
        "POST",
        "https://ext.to/ajax/getTorrentMagnet.php",
        data={"torrent_id": "21403167"},
    )
    assert seen["json"]["cmd"] == "request.post"
    assert seen["json"]["postData"] == "torrent_id=21403167"
