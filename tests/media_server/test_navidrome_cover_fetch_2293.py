"""#2293: SoulSync's own code can download a Navidrome cover.

image_url() answers for the browser: a getCoverArt reference becomes the
proxy path /api/navidrome/cover/<id>. the re-tagger and the cover.jpg writer
need bytes, took that path, saw it wasn't http, and wrote no cover.
"""

from unittest.mock import MagicMock, patch

from core.navidrome_client import NavidromeClient


def _client():
    c = NavidromeClient.__new__(NavidromeClient)
    c.base_url = "http://nav:4533"
    c.username = "boulder"
    c.password = "pw"
    return c


def test_a_cover_reference_gets_an_address_to_download_from():
    c = _client()
    for ref in ("/rest/getCoverArt?id=al-123", "/api/navidrome/cover/al-123", "getCoverArt?id=al-123"):
        url = c.image_fetch_url(ref)
        assert url and url.startswith("http://nav:4533/rest/getCoverArt"), ref
        assert "id=al-123" in url and "u=boulder" in url
    # the browser's answer is unchanged
    assert c.image_url("/rest/getCoverArt?id=al-123") == "/api/navidrome/cover/al-123"


def test_nothing_to_fetch_without_a_connection_or_a_reference():
    c = _client()
    assert c.image_fetch_url("") is None
    c.password = ""
    assert c.image_fetch_url("/rest/getCoverArt?id=al-123") is None


def _engine(client):
    engine = MagicMock()
    engine.active_client.return_value = client
    return engine


def test_the_cover_writer_fetches_it_from_the_server():
    from core.metadata import artwork
    response = MagicMock(status_code=200, content=b"\xff\xd8\xffJPEG", headers={"Content-Type": "image/jpeg"})
    with patch("core.media_server.engine.get_media_server_engine", return_value=_engine(_client())), \
         patch("requests.get", return_value=response) as get:
        data, mime = artwork._fetch_art_bytes("/rest/getCoverArt?id=al-123")
    assert (data, mime) == (b"\xff\xd8\xffJPEG", "image/jpeg")
    assert get.call_args[0][0].startswith("http://nav:4533/rest/getCoverArt")


def test_a_subsonic_error_body_is_not_a_cover():
    from core.metadata import artwork
    response = MagicMock(status_code=200, content=b'{"subsonic-response":{"status":"failed"}}',
                         headers={"Content-Type": "application/json"})
    with patch("core.media_server.engine.get_media_server_engine", return_value=_engine(_client())), \
         patch("requests.get", return_value=response):
        assert artwork._fetch_art_bytes("/rest/getCoverArt?id=al-123") == (None, None)


def test_a_server_with_no_such_references_is_asked_nothing():
    from core.metadata import artwork
    plex = object()
    with patch("core.media_server.engine.get_media_server_engine", return_value=_engine(plex)), \
         patch("requests.get") as get:
        assert artwork._fetch_art_bytes("/library/metadata/1/thumb/2") == (None, None)
    get.assert_not_called()
