"""A profile-only Watchlist update must not reset the monitoring strategy.

Audit finding P2-03: ``POST /api/watchlist/artist/<id>/config`` filled every
absent field with a hardcoded default and wrote the whole configuration back. A
client that sent only ``quality_profile_id`` — exactly what a thin native client
or the Quality Profile selector does — therefore silently reset release types,
lookback, metadata source and auto-download.
"""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

import web_server  # noqa: E402
from database.music_database import MusicDatabase  # noqa: E402


MONITORING_COLUMNS = (
    "include_albums", "include_eps", "include_singles", "include_live",
    "include_remixes", "include_acoustic", "include_compilations",
    "include_instrumentals", "lookback_days", "preferred_metadata_source",
    "auto_download",
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    db.add_artist_to_watchlist("sp-1", "Artist", source="spotify")

    # A deliberately NON-default monitoring strategy — every field differs from
    # the hardcoded defaults the endpoint used to fall back to.
    with db._get_connection() as conn:
        conn.execute(
            """UPDATE watchlist_artists
                  SET include_albums = 1, include_eps = 0, include_singles = 1,
                      include_live = 1, include_remixes = 1, include_acoustic = 1,
                      include_compilations = 1, include_instrumentals = 1,
                      lookback_days = 90, preferred_metadata_source = 'deezer',
                      auto_download = 0
                WHERE spotify_artist_id = 'sp-1'"""
        )
        conn.commit()

    # The route imports get_database() from the database module at call time,
    # so patch it at the source rather than on web_server.
    import database.music_database as music_database
    monkeypatch.setattr(music_database, "get_database", lambda *a, **k: db)
    monkeypatch.setattr(web_server, "get_database", lambda *a, **k: db)
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as test_client:
        yield test_client, db


def _config(db):
    with db._get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM watchlist_artists WHERE spotify_artist_id = 'sp-1'"
        ).fetchone()
    return {column: row[column] for column in MONITORING_COLUMNS}


def test_quality_profile_only_update_preserves_every_monitoring_field(client):
    test_client, db = client
    before = _config(db)
    quality_id = db.create_quality_profile("Hi-Res", {})

    response = test_client.post(
        "/api/watchlist/artist/sp-1/config",
        json={"quality_profile_id": quality_id},
    )

    assert response.status_code == 200, response.get_json()
    assert _config(db) == before
    with db._get_connection() as conn:
        stored = conn.execute(
            "SELECT quality_profile_id FROM watchlist_artists WHERE spotify_artist_id = 'sp-1'"
        ).fetchone()[0]
    assert stored == quality_id


def test_single_monitoring_field_update_preserves_the_quality_profile(client):
    test_client, db = client
    quality_id = db.create_quality_profile("Hi-Res", {})
    test_client.post("/api/watchlist/artist/sp-1/config", json={"quality_profile_id": quality_id})

    response = test_client.post(
        "/api/watchlist/artist/sp-1/config", json={"include_eps": True}
    )

    assert response.status_code == 200, response.get_json()
    after = _config(db)
    assert after["include_eps"] == 1
    assert after["lookback_days"] == 90
    assert after["preferred_metadata_source"] == "deezer"
    with db._get_connection() as conn:
        stored = conn.execute(
            "SELECT quality_profile_id FROM watchlist_artists WHERE spotify_artist_id = 'sp-1'"
        ).fetchone()[0]
    assert stored == quality_id


def test_full_form_request_still_overwrites_everything(client):
    """The legacy settings modal posts the complete form — that must still work."""
    test_client, db = client

    response = test_client.post("/api/watchlist/artist/sp-1/config", json={
        "include_albums": True, "include_eps": True, "include_singles": False,
        "include_live": False, "include_remixes": False, "include_acoustic": False,
        "include_compilations": False, "include_instrumentals": False,
        "lookback_days": 30, "preferred_metadata_source": "spotify",
        "auto_download": True,
    })

    assert response.status_code == 200, response.get_json()
    after = _config(db)
    assert after["include_singles"] == 0
    assert after["include_live"] == 0
    assert after["lookback_days"] == 30
    assert after["preferred_metadata_source"] == "spotify"
    assert after["auto_download"] == 1


def test_unknown_quality_profile_changes_nothing(client):
    test_client, db = client
    before = _config(db)

    response = test_client.post(
        "/api/watchlist/artist/sp-1/config", json={"quality_profile_id": 999999}
    )

    assert response.status_code == 400
    assert _config(db) == before


def test_string_boolean_is_not_inverted(client):
    """`bool("false")` is True — the endpoint must not fall for that (P3-01)."""
    test_client, db = client

    response = test_client.post(
        "/api/watchlist/artist/sp-1/config",
        json={"include_albums": True, "include_live": "false"},
    )

    assert response.status_code == 200, response.get_json()
    assert _config(db)["include_live"] == 0


def test_unknown_artist_is_404(client):
    test_client, _ = client
    response = test_client.post(
        "/api/watchlist/artist/nope/config", json={"include_albums": True}
    )
    assert response.status_code == 404


def test_unparseable_boolean_is_rejected_instead_of_silently_reset(client):
    """R2-08: a JSON number is not a boolean.

    ``parse_strict_bool`` returns None for it, and the endpoint used to
    substitute the hardcoded default — silently flipping a stored ``True`` off
    on a 200 response. The modular API answers 400 for the same input, so the
    two contracts also disagreed.
    """
    test_client, db = client
    before = _config(db)
    assert before["include_live"] == 1

    response = test_client.post(
        "/api/watchlist/artist/sp-1/config", json={"include_live": 1}
    )

    assert response.status_code == 400
    assert "include_live" in (response.get_json() or {}).get("error", "")
    assert _config(db) == before, "a rejected request must not write anything"


def test_unparseable_auto_download_is_rejected(client):
    test_client, db = client
    before = _config(db)

    response = test_client.post(
        "/api/watchlist/artist/sp-1/config", json={"auto_download": "maybe"}
    )

    assert response.status_code == 400
    assert _config(db) == before
