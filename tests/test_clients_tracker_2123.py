"""#2123: the clients tab names the tracker a torrent is on.

private tracker users want to see which tracker each torrent came from. the
adapters dropped the client's tracker field, and the label soulsync puts on
its own grabs never carried the indexer the grab came from. now both reach
the tab: the indexer for soulsync's grabs, the tracker host for the rest. the
announce url is never passed on, it carries a private tracker's passkey.
"""

from __future__ import annotations

import pytest

from core.client_match import audiobook_known, video_known
from core.torrent_clients.base import tracker_host
from tests.test_torrent_client_adapters import (_deluge_with_config, _qbit_with_config,
                                                _trans_with_config)


@pytest.mark.parametrize("raw, host", [
    ("https://tracker.example.org:8443/0123456789abcdef/announce", "tracker.example.org"),
    ("udp://open.tracker.cl:1337/announce", "open.tracker.cl"),
    ("http://www.tracker.net/announce.php?passkey=SECRET", "tracker.net"),
    ("tracker.example.org", "tracker.example.org"),       # deluge's tracker_host
    ([{"announce": "https://p.example/abc/announce"}], "p.example"),   # transmission
    (["", "https://b.example/x"], "b.example"),
    ("", None),
    (None, None),
    ([], None),
])
def test_tracker_host_never_keeps_the_path(raw, host):
    assert tracker_host(raw) == host


def test_qbittorrent_reports_its_tracker():
    st = _qbit_with_config()._parse_status({
        "hash": "h", "name": "n", "state": "uploading",
        "tracker": "https://tracker.example.org/SECRETPASSKEY/announce"})
    assert st.tracker == "tracker.example.org"
    assert _qbit_with_config()._parse_status({"hash": "h", "tracker": ""}).tracker is None


def test_transmission_reports_its_tracker():
    adapter = _trans_with_config()
    assert "trackers" in adapter._STATUS_FIELDS
    st = adapter._parse_status({"hashString": "h", "name": "n", "status": 6,
                                "trackers": [{"announce": "https://t.example/KEY/announce"}]})
    assert st.tracker == "t.example"


def test_deluge_reports_its_tracker():
    adapter = _deluge_with_config()
    assert "tracker_host" in adapter._STATUS_FIELDS
    st = adapter._parse_status({"hash": "h", "name": "n", "state": "Seeding",
                                "tracker_host": "tracker.example.org"})
    assert st.tracker == "tracker.example.org"


def test_soulsync_grabs_carry_their_indexer():
    known = video_known([
        {"source": "torrent", "client_ref": "ABC", "kind": "movie", "title": "Dune",
         "username": "YGGTorrent", "status": "downloading"},
        {"source": "soulseek", "username": "peer", "filename": "f.mkv", "kind": "movie",
         "title": "Up", "status": "downloading"},
    ])
    assert known["torrent"]["abc"] == {"kind": "movie", "title": "Dune", "indexer": "YGGTorrent"}
    # a soulseek peer is not an indexer
    assert "indexer" not in known["slskd"][("peer", "f.mkv")]
    books = audiobook_known([{"source": "torrent", "client_id": "DEF", "title": "Book",
                              "indexer": "MyAnonamouse", "status": "downloading"}])
    assert books["torrent"]["def"]["indexer"] == "MyAnonamouse"
