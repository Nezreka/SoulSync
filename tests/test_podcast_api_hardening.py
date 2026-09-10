"""The podcast endpoints that take a url or a file from the caller.

audio-proxy is the one that matters most: it fetches whatever url it is handed,
from the server, and streams the result back. SoulSync's login gate is off by
default, so before it was checked anyone who could reach the web ui could read
internal http services through it. The audiobook sample proxy says the same
thing about itself in its docstring and solves it with an allowlist; podcast
audio comes from thousands of cdns so the check is on the address instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from flask import Flask

from api.podcasts import create_podcasts_blueprint
from core.podcast_ingest_guard import MAX_OPML_BYTES


@pytest.fixture
def client():
    app = Flask(__name__)
    app.register_blueprint(create_podcasts_blueprint())
    app.config["TESTING"] = True
    return app.test_client()


# ---------------------------------------------------------------------------
# audio-proxy
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8080/admin",
    "http://169.254.169.254/latest/meta-data/",
    "http://192.168.1.1/",
    "http://[::1]/",
    "file:///etc/passwd",
])
def test_the_proxy_refuses_to_fetch_off_the_internet(client, url):
    with patch("api.podcasts.requests.get",
               side_effect=AssertionError("a refused url must never be fetched")):
        resp = client.get("/api/podcasts/audio-proxy", query_string={"url": url})
    assert resp.status_code == 400


def test_the_proxy_still_streams_a_real_enclosure(client):
    upstream = MagicMock()
    upstream.status_code = 200
    upstream.headers = {"Content-Type": "audio/mpeg", "Content-Length": "3"}
    upstream.iter_content = lambda chunk_size=None: iter([b"abc"])

    with patch("api.podcasts.check_url", return_value=(True, "")), \
         patch("api.podcasts.requests.get", return_value=upstream):
        resp = client.get("/api/podcasts/audio-proxy",
                          query_string={"url": "https://cdn.example/ep1.mp3"})

    assert resp.status_code == 200
    assert resp.data == b"abc"


def test_the_proxy_does_not_follow_a_redirect_blindly(client):
    """A url that passes the check could still 302 straight to 127.0.0.1."""
    redirect = MagicMock()
    redirect.status_code = 302
    redirect.headers = {"Location": "http://127.0.0.1:8080/admin"}

    calls = []

    def _get(url, **kw):
        calls.append(url)
        assert kw.get("allow_redirects") is False, "requests must not follow it for us"
        return redirect

    # the first hop passes the check; the loopback it redirects to must not
    with patch("api.podcasts.check_url",
               side_effect=lambda u: (not u.startswith("http://127."), "refused")), \
         patch("api.podcasts.requests.get", side_effect=_get):
        resp = client.get("/api/podcasts/audio-proxy",
                          query_string={"url": "https://cdn.example/ep1.mp3"})

    assert resp.status_code == 502
    # the first hop was fetched, the loopback hop never was
    assert calls == ["https://cdn.example/ep1.mp3"]


def test_a_missing_url_is_still_a_400(client):
    assert client.get("/api/podcasts/audio-proxy").status_code == 400


# ---------------------------------------------------------------------------
# opml
# ---------------------------------------------------------------------------

def test_an_oversized_opml_upload_is_refused(client):
    import io as _io
    big = b"<opml>" + b"x" * (MAX_OPML_BYTES + 100) + b"</opml>"
    resp = client.post(
        "/api/podcasts/opml/import",
        data={"file": (_io.BytesIO(big), "subs.opml")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 413


def test_an_oversized_opml_body_is_refused(client):
    resp = client.post("/api/podcasts/opml/import",
                       json={"opml_text": "x" * (MAX_OPML_BYTES + 100)})
    assert resp.status_code == 413


def test_an_opml_declaring_entities_imports_nothing(client):
    bomb = """<?xml version="1.0"?>
<!DOCTYPE o [ <!ENTITY a "AAAAAAAAAA"> <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"> ]>
<opml version="2.0"><body><outline text="&b;" xmlUrl="https://feeds.example.com/x"/></body></opml>"""
    resp = client.post("/api/podcasts/opml/import", json={"opml_text": bomb})
    assert resp.status_code == 200
    assert resp.get_json()["count"] == 0


def test_a_normal_opml_still_previews(client):
    opml = ('<opml version="2.0"><body>'
            '<outline text="Show" xmlUrl="https://feeds.example.com/show.xml"/>'
            '</body></opml>')
    resp = client.post("/api/podcasts/opml/import", json={"opml_text": opml})
    body = resp.get_json()
    assert resp.status_code == 200
    assert body["count"] == 1
    assert body["feeds"][0]["feed_url"] == "https://feeds.example.com/show.xml"


def test_opml_drops_a_scheme_we_would_never_fetch(client):
    opml = ('<opml version="2.0"><body>'
            '<outline text="Good" xmlUrl="https://feeds.example.com/a.xml"/>'
            '<outline text="Bad" xmlUrl="file:///etc/passwd"/>'
            '</body></opml>')
    resp = client.post("/api/podcasts/opml/import", json={"opml_text": opml})
    feeds = resp.get_json()["feeds"]
    assert [f["feed_url"] for f in feeds] == ["https://feeds.example.com/a.xml"]
