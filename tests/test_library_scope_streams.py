"""A streamed response body runs after its request is gone (#1199).

Flask pops the request context before it iterates a plain generator, so a
stream that reads the library asked ``current_library_scope()`` with no
request and got the shared library. An own-library profile saw the shared
library's ownership badges on the artist page.
"""

from __future__ import annotations

import inspect

from flask import Flask, Response, g

from core import library_scope


def _streaming_app(monkeypatch, *, scoped: bool) -> Flask:
    monkeypatch.setattr(library_scope, "library_scope_for_profile",
                        lambda pid: pid if pid and int(pid) != 1 else "shared")
    app = Flask(__name__)

    @app.route("/stream")
    def stream():
        g.profile_id = 7

        def body():
            yield str(library_scope.current_library_scope())

        return Response(library_scope.scoped_stream(body()) if scoped else body())

    return app


def test_the_stream_reads_the_callers_library(monkeypatch):
    client = _streaming_app(monkeypatch, scoped=True).test_client()
    assert client.get("/stream").get_data(as_text=True) == "7"


def test_an_unscoped_stream_falls_back_to_the_shared_library(monkeypatch):
    client = _streaming_app(monkeypatch, scoped=False).test_client()
    assert client.get("/stream").get_data(as_text=True) == "shared"


def test_the_scope_ends_with_the_stream(monkeypatch):
    client = _streaming_app(monkeypatch, scoped=True).test_client()
    client.get("/stream").get_data()
    assert library_scope._explicit_scope.get() is library_scope._UNSET


def test_the_library_streams_keep_the_callers_library():
    import web_server
    from api import artist_detail

    for view in (web_server.library_completion_stream,
                 artist_detail.check_artist_discography_completion_stream,
                 artist_detail.download_discography):
        assert "scoped_stream(" in inspect.getsource(view), view.__name__
