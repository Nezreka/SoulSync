"""#2193: a reassign is a move. With "replace the originals" unticked it staged
a copy of every file and left the original album where it was, so the user
ended up with the album twice. The route always asks for the replace now."""

from __future__ import annotations

import pytest

flask = pytest.importorskip("flask")


@pytest.fixture
def client(monkeypatch):
    from api import import_routes
    monkeypatch.setattr("core.profile_context.is_admin_request", lambda: True)
    monkeypatch.setattr(import_routes, "get_database", lambda: object(), raising=False)
    monkeypatch.setattr("core.imports.staging.get_staging_path", lambda: "/staging")
    seen = {}

    def fake_apply(database, **kwargs):
        seen.update(kwargs)
        return {"success": True, "staged": []}

    monkeypatch.setattr("core.imports.reassign_service.apply_reassign", fake_apply)
    app = flask.Flask(__name__)
    app.register_blueprint(import_routes.bp)
    return app.test_client(), seen


@pytest.mark.parametrize("asked", [False, True, None])
def test_reassign_always_replaces_the_originals(client, asked):
    test_client, seen = client
    body = {"source": "spotify", "local_album_id": "lib2:5", "album_id": "sp-ost",
            "album_name": "The Crabjoys", "artist_name": "OST"}
    if asked is not None:
        body["replace"] = asked
    resp = test_client.post("/api/reassign/apply", json=body)
    assert resp.status_code == 200, resp.get_json()
    assert seen["replace"] is True
