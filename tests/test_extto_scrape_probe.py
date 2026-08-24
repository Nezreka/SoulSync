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


def test_extto_probe_builds_get_torrent_magnet_candidates():
    urls = extto.magnet_endpoint_urls("https://ext.to/interstellar-21403167/", "21403167")
    assert any("getTorrentMagnet" in url for _method, url, _data in urls)
    assert any(method == "POST" and data == {"id": "21403167"} for method, _url, data in urls)


def test_extto_probe_reads_magnet_from_common_json_shapes():
    magnet = "magnet:?xt=urn:btih:abcdef1234567890abcdef1234567890abcdef12"
    assert extto.magnet_from_payload({"url": magnet}) == magnet
    assert extto.magnet_from_payload({"data": {"magnet": magnet}}) == magnet


def test_extto_probe_can_fallback_to_visible_info_hash():
    html = "<button id='show-hash-btn'>show</button><code>ABCDEF1234567890ABCDEF1234567890ABCDEF12</code>"
    assert extto.extract_info_hash(html) == "abcdef1234567890abcdef1234567890abcdef12"