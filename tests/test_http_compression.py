"""compressed responses: text and JSON go out brotli or gzip, and nothing that
streams, seeks or is already packed is touched. static files come from a
precompressed sibling when the image built one, else are compressed once."""

from __future__ import annotations

import gzip
import io
import os
import re
from pathlib import Path

import pytest
from flask import Flask, Response, jsonify, send_file, stream_with_context

from core import http_compression as hc

brotli = pytest.importorskip("brotli")

BIG = {"rows": [{"title": f"track {i}", "artist": "someone"} for i in range(200)]}


def _unpack(resp):
    enc = resp.headers.get("Content-Encoding")
    data = resp.get_data()
    if enc == "br":
        return brotli.decompress(data)
    if enc == "gzip":
        return gzip.decompress(data)
    return data


@pytest.fixture()
def app(tmp_path):
    static = tmp_path / "static"
    (static / "dist" / "assets").mkdir(parents=True)
    a = Flask(__name__, static_folder=str(static))
    hc.install(a)

    @a.route("/json")
    def big_json():
        return jsonify(BIG)

    @a.route("/small")
    def small_json():
        return jsonify({"ok": True})

    @a.route("/page")
    def page():
        return "<html>" + "x" * 5000 + "</html>"

    @a.route("/etag")
    def etagged():
        r = jsonify(BIG)
        r.set_etag("abc")
        return r

    @a.route("/stream")
    def stream():
        def gen():
            for _ in range(50):
                yield "data: " + "y" * 100 + "\n\n"
        return Response(stream_with_context(gen()), mimetype="text/event-stream")

    @a.route("/textstream")
    def textstream():
        return Response((("z" * 2000) for _ in range(3)), mimetype="text/plain")

    @a.route("/audio")
    def audio():
        return send_file(io.BytesIO(b"\x00" * 50000), mimetype="audio/flac")

    @a.route("/download")
    def download():
        r = Response("w" * 5000, mimetype="text/plain")
        r.headers["Content-Disposition"] = "attachment; filename=log.txt"
        return r

    @a.route("/packed")
    def packed():
        r = Response(gzip.compress(b"q" * 5000), mimetype="application/json")
        r.headers["Content-Encoding"] = "gzip"
        return r

    @a.route("/image")
    def image():
        return Response(b"\x89PNG" + b"\x00" * 5000, mimetype="image/png")

    @a.route("/socket.io/")
    def sio():
        return Response("p" * 5000, mimetype="text/plain")

    a.config["STATIC"] = static
    return a


@pytest.fixture()
def client(app):
    hc._static_cache.clear()
    return app.test_client()


# ---------------------------------------------------------------- choosing
@pytest.mark.parametrize("header,want", [
    ("gzip, deflate, br", "br"),
    ("br;q=0.5, gzip", "gzip"),
    ("gzip", "gzip"),
    ("br", "br"),
    ("identity", None),
    ("", None),
    (None, None),
    ("*", "br"),
    ("br;q=0, *;q=0.1", "gzip"),
    ("gzip;q=0", None),
])
def test_choose_encoding(header, want):
    assert hc.choose_encoding(header) == want


def test_gzip_when_brotli_is_missing():
    assert hc.choose_encoding("br, gzip", brotli_ok=False) == "gzip"


# ---------------------------------------------------------------- dynamic
@pytest.mark.parametrize("accept,enc", [("br, gzip", "br"), ("gzip", "gzip")])
def test_json_is_compressed_and_round_trips(client, accept, enc):
    r = client.get("/json", headers={"Accept-Encoding": accept})
    assert r.headers["Content-Encoding"] == enc
    assert "Accept-Encoding" in r.headers["Vary"]
    assert int(r.headers["Content-Length"]) == len(r.get_data())
    assert _unpack(r) == client.get("/json").get_data()
    assert len(r.get_data()) < len(_unpack(r)) / 4


def test_html_is_compressed(client):
    r = client.get("/page", headers={"Accept-Encoding": "br"})
    assert r.headers["Content-Encoding"] == "br"


def test_nothing_asked_nothing_packed_but_still_varies(client):
    r = client.get("/json")
    assert "Content-Encoding" not in r.headers
    assert "Accept-Encoding" in r.headers["Vary"]


def test_a_small_body_goes_out_plain(client):
    r = client.get("/small", headers={"Accept-Encoding": "br, gzip"})
    assert "Content-Encoding" not in r.headers


def test_a_compressed_body_gets_a_weak_etag_that_still_revalidates(app, client):
    @app.route("/cond")
    def cond():
        from flask import request
        r = jsonify(BIG)
        r.set_etag("abc")
        return r.make_conditional(request)

    plain = client.get("/etag").headers["ETag"]
    packed = client.get("/etag", headers={"Accept-Encoding": "gzip"}).headers["ETag"]
    assert plain == '"abc"' and packed == 'W/"abc"'
    again = client.get("/cond", headers={"Accept-Encoding": "gzip", "If-None-Match": 'W/"abc"'})
    assert again.status_code == 304


@pytest.mark.parametrize("path", ["/stream", "/textstream", "/audio", "/download", "/image", "/socket.io/"])
def test_streams_files_and_media_are_left_alone(client, path):
    r = client.get(path, headers={"Accept-Encoding": "br, gzip"})
    assert "Content-Encoding" not in r.headers, path


def test_an_already_encoded_body_is_not_packed_twice(client):
    r = client.get("/packed", headers={"Accept-Encoding": "br, gzip"})
    assert r.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(r.get_data()) == b"q" * 5000


def test_a_range_request_is_left_alone(client):
    r = client.get("/json", headers={"Accept-Encoding": "br", "Range": "bytes=0-10"})
    assert "Content-Encoding" not in r.headers


def test_head_is_left_alone(client):
    r = client.head("/json", headers={"Accept-Encoding": "br"})
    assert "Content-Encoding" not in r.headers


# ---------------------------------------------------------------- static
def _write(path: Path, text: str, mtime=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    if mtime is not None:
        os.utime(path, ns=(mtime, mtime))


CSS = ".a { color: var(--accent); }\n" * 400


def test_static_uses_the_precompressed_file(app, client):
    static = app.config["STATIC"]
    _write(static / "style.css", CSS, mtime=1_000_000_000_000_000_000)
    marker = brotli.compress(CSS.encode())
    (static / "style.css.br").write_bytes(marker)
    os.utime(static / "style.css.br", ns=(1_000_000_000_000_000_001,) * 2)
    r = client.get("/static/style.css", headers={"Accept-Encoding": "br, gzip"})
    assert r.status_code == 200
    assert r.headers["Content-Encoding"] == "br"
    assert r.headers["Content-Type"].startswith("text/css")
    assert "Accept-Encoding" in r.headers["Vary"]
    assert r.get_data() == marker
    assert brotli.decompress(r.get_data()).decode() == CSS
    # and the browser's revalidation is a 304
    again = client.get("/static/style.css", headers={"Accept-Encoding": "br",
                                                     "If-None-Match": r.headers["ETag"]})
    assert again.status_code == 304


def test_a_stale_precompressed_file_is_ignored(app, client):
    static = app.config["STATIC"]
    _write(static / "app.js", "let x = 1;\n" * 500, mtime=2_000_000_000_000_000_000)
    (static / "app.js.gz").write_bytes(gzip.compress(b"old build"))
    os.utime(static / "app.js.gz", ns=(1_000_000_000_000_000_000,) * 2)
    r = client.get("/static/app.js", headers={"Accept-Encoding": "gzip"})
    assert r.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(r.get_data()).decode() == "let x = 1;\n" * 500


def test_without_a_sibling_it_is_compressed_once_and_kept(app, client, monkeypatch):
    static = app.config["STATIC"]
    _write(static / "app.js", "let y = 2;\n" * 500)
    calls = []
    real = hc.compress_bytes
    monkeypatch.setattr(hc, "compress_bytes", lambda d, e, **k: calls.append(e) or real(d, e, **k))
    for _ in range(3):
        r = client.get("/static/app.js", headers={"Accept-Encoding": "br"})
        assert brotli.decompress(r.get_data()).decode() == "let y = 2;\n" * 500
    assert calls == ["br"]
    # the file changes: the next request compresses the new content
    _write(static / "app.js", "let z = 3;\n" * 500)
    os.utime(static / "app.js", ns=(3_000_000_000_000_000_000,) * 2)
    r = client.get("/static/app.js", headers={"Accept-Encoding": "br"})
    assert brotli.decompress(r.get_data()).decode() == "let z = 3;\n" * 500


def test_static_plain_for_ranges_small_files_and_images(app, client):
    static = app.config["STATIC"]
    _write(static / "big.js", "a" * 5000)
    _write(static / "tiny.js", "a")
    (static / "pic.png").write_bytes(b"\x89PNG" + b"\x00" * 5000)
    r = client.get("/static/big.js", headers={"Accept-Encoding": "br", "Range": "bytes=0-9"})
    assert r.status_code == 206 and "Content-Encoding" not in r.headers
    assert r.get_data() == b"a" * 10
    assert "Content-Encoding" not in client.get("/static/tiny.js", headers={"Accept-Encoding": "br"}).headers
    assert "Content-Encoding" not in client.get("/static/pic.png", headers={"Accept-Encoding": "br"}).headers
    plain = client.get("/static/big.js")
    assert plain.status_code == 200 and "Content-Encoding" not in plain.headers
    assert "Accept-Encoding" in plain.headers["Vary"]


def test_hashed_dist_assets_are_immutable(app, client):
    static = app.config["STATIC"]
    _write(static / "dist" / "assets" / "main-DVMIhOlL.js", "b" * 5000)
    _write(static / "dist" / "shell.js", "b" * 5000)
    r = client.get("/static/dist/assets/main-DVMIhOlL.js", headers={"Accept-Encoding": "br"})
    assert "immutable" in r.headers["Cache-Control"]
    r = client.get("/static/dist/shell.js", headers={"Accept-Encoding": "br"})
    assert "immutable" not in (r.headers.get("Cache-Control") or "")
    assert hc.is_hashed_dist_asset("dist/assets/chunk-a1B2c3_-.css")
    assert not hc.is_hashed_dist_asset("style.css")
    assert not hc.is_hashed_dist_asset("dist/assets/settings-premier.css")


def test_path_escape_is_refused(client):
    r = client.get("/static/../secret.js", headers={"Accept-Encoding": "br"})
    assert r.status_code == 404


# ---------------------------------------------------------------- first load
ROOT = Path(__file__).resolve().parents[1]


def shell_assets():
    """every local js/css the shell page loads, from webui/index.html"""
    html = (ROOT / "webui" / "index.html").read_text(encoding="utf-8")
    names = re.findall(r"url_for\('static', filename='([^']+\.(?:js|css))'", html)
    return [ROOT / "webui" / "static" / n for n in dict.fromkeys(names)]


def first_load_bytes():
    raw = gz = br = 0
    files = [p for p in shell_assets() if p.is_file()]
    for p in files:
        data = p.read_bytes()
        raw += len(data)
        gz += len(gzip.compress(data, compresslevel=9))
        br += len(brotli.compress(data, quality=11))
    return len(files), raw, gz, br


def test_the_first_load_shrinks():
    count, raw, gz, br = first_load_bytes()
    assert count > 50
    print(f"\nfirst load: {count} files, {raw:,} B plain, {gz:,} B gzip-9, {br:,} B brotli-11")
    assert gz < raw * 0.35
    assert br < gz


def test_the_switch_turns_it_all_off(tmp_path, monkeypatch):
    monkeypatch.setenv("SOULSYNC_COMPRESSION", "0")
    static = tmp_path / "s"
    _write(static / "app.js", "q" * 5000)
    a = Flask(__name__, static_folder=str(static))
    hc.install(a)

    @a.route("/json")
    def big_json():
        return jsonify(BIG)

    c = a.test_client()
    assert "Content-Encoding" not in c.get("/json", headers={"Accept-Encoding": "br"}).headers
    assert "Content-Encoding" not in c.get("/static/app.js", headers={"Accept-Encoding": "br"}).headers


def test_precompress_script_writes_fresh_copies(tmp_path):
    import subprocess
    import sys
    _write(tmp_path / "a.js", "let a = 1;\n" * 400)
    _write(tmp_path / "tiny.css", "a{}")
    (tmp_path / "pic.png").write_bytes(b"\x00" * 5000)
    script = ROOT / "core" / "webui" / "precompress.py"
    out = subprocess.run([sys.executable, str(script), str(tmp_path)], capture_output=True, text=True,
                         check=True)
    assert "precompressed 1 files" in out.stdout
    assert gzip.decompress((tmp_path / "a.js.gz").read_bytes()) == (tmp_path / "a.js").read_bytes()
    assert brotli.decompress((tmp_path / "a.js.br").read_bytes()) == (tmp_path / "a.js").read_bytes()
    assert not (tmp_path / "tiny.css.gz").exists() and not (tmp_path / "pic.png.gz").exists()
    # newer than the file, so the server takes them
    assert (tmp_path / "a.js.br").stat().st_mtime_ns >= (tmp_path / "a.js").stat().st_mtime_ns
    assert hc.static_variant(str(tmp_path / "a.js"), "br")[1] is None
