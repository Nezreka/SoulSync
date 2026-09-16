import pytest
from flask import Flask, redirect, request, session, url_for
from core.url_base import configure_url_base, normalize_url_base


@pytest.mark.parametrize("value, expected", [("", ""), ("/", ""), ("/soulsync/", "/soulsync"), ("/media/soulsync", "/media/soulsync")])
def test_normalization(value, expected):
    assert normalize_url_base(value) == expected


@pytest.mark.parametrize("value", ["https://host/path", "//host", "/../x", "/a?b", "/a#b", "/a b", "/a%2fb"])
def test_rejects_invalid_mounts(value):
    with pytest.raises(ValueError): normalize_url_base(value)


@pytest.mark.parametrize("base", ["", "/soulsync", "/media/soulsync"])
def test_mount_preserved_and_stripped_requests_assets_redirects_and_cookie(base):
    app = Flask(__name__)
    app.secret_key = "test-only"
    @app.route("/api/example", methods=["POST"])
    def example():
        session["profile"] = 1
        return {"path": request.path, "root": request.script_root,
                "body": request.get_json(), "asset": url_for("static", filename="app.js")}
    @app.route("/go")
    def go(): return redirect("/discover?from=login")
    @app.route("/")
    def home(): return '<a href="/discover">Music</a><img src="/api/image"><a href="https://other/app">External</a>'
    configure_url_base(app, base)
    client = app.test_client()
    for path in ["/api/example", base + "/api/example"]:
        response = client.post(path, json={"query": "music"})
        assert response.status_code == 200
        assert response.json == {"path": "/api/example", "root": base,
                                 "body": {"query": "music"}, "asset": base + "/static/app.js"}
        assert "Path=" + (base or "/") in response.headers["Set-Cookie"]
    assert client.get(base + "/go").headers["Location"] == base + "/discover?from=login"
    html = client.get(base + "/").text
    assert 'href="' + base + '/discover"' in html
    assert 'src="' + base + '/api/image"' in html
    assert 'href="https://other/app"' in html


def test_mount_wraps_other_wsgi_endpoints_and_preserves_range():
    app = Flask(__name__)
    captured = {}
    def socket_or_stream(environ, start_response):
        captured.update(path=environ["PATH_INFO"], root=environ["SCRIPT_NAME"], range=environ.get("HTTP_RANGE"))
        start_response("206 Partial Content", [("Content-Type", "audio/mpeg"), ("Content-Range", "bytes 0-2/3")])
        return [b"abc"]
    app.wsgi_app = socket_or_stream
    configure_url_base(app, "/soulsync")
    response = app.test_client().get("/soulsync/socket.io/", headers={"Range": "bytes=0-2"})
    assert captured == {"path": "/socket.io/", "root": "/soulsync", "range": "bytes=0-2"}
    assert response.status_code == 206 and response.data == b"abc"


def test_api_url_fields_are_prefixed_but_file_paths_and_external_urls_are_not():
    app = Flask(__name__)
    @app.route("/api/example")
    def example():
        return {"rows": [{"image_url": "/api/image-proxy/1", "file_path": "/api/local.flac",
                          "url": "https://provider.test/art"}],
                "downloadUrl": "/api/export/1", "stream_url": "/soulsync/stream/1"}
    configure_url_base(app, "/soulsync")
    data = app.test_client().get("/soulsync/api/example").json
    assert data["rows"][0] == {"image_url": "/soulsync/api/image-proxy/1", "file_path": "/api/local.flac", "url": "https://provider.test/art"}
    assert data["downloadUrl"] == "/soulsync/api/export/1"
    assert data["stream_url"] == "/soulsync/stream/1"
