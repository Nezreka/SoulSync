"""One request field carries the Quality Profile through every manual path.

Audit finding P1-01: the shared "Tracks to Add to Wishlist" and "Download
Missing" dialogs — the ones Search, Discover, Library, album, single, top
tracks, discography and playlist explorer all funnel into — had no Quality
Profile selector and sent no id, so every manual acquisition silently used the
global default. Worse, the two buttons in the SAME dialog disagreed: for a
mirror, "Begin Analysis" picked up the persisted assignment server-side while
the adjacent "Add to Wishlist" did not.

The contract these tests pin down:

* absent/None      -> server decides (mirror assignment, then global default)
* valid id         -> wins for this action, over the mirror assignment
* unknown/junk id  -> 400, never a silent downgrade
"""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

import web_server  # noqa: E402
from database.music_database import MusicDatabase  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db = MusicDatabase(str(tmp_path / "m.db"))
    import database.music_database as music_database
    import core.wishlist.service as wishlist_service
    monkeypatch.setattr(music_database, "get_database", lambda *a, **k: db)
    monkeypatch.setattr(web_server, "get_database", lambda *a, **k: db)
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    # The wishlist service is a singleton holding its own DB handle for the real
    # database path; point it at the temp one too.
    monkeypatch.setattr(wishlist_service, "get_database", lambda *a, **k: db)
    from core.wishlist_service import get_wishlist_service
    get_wishlist_service()._database = db
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as client:
        yield client, db


def _track_payload(quality_profile_id=None):
    payload = {
        "track": {"id": "sp-1", "name": "Song", "artists": [{"id": "ar1", "name": "Artist"}]},
        "artist": {"id": "ar1", "name": "Artist"},
        "album": {"id": "al1", "name": "Album"},
    }
    if quality_profile_id is not None:
        payload["quality_profile_id"] = quality_profile_id
    return payload


def _stored_profile(db, track_id="sp-1::al1"):
    # Composite key canonical from the first insert (P1-09) — the payload's
    # track+album keys the row as "<track>::<album>", not the bare track id.
    row = db.get_wishlist_track(track_id, profile_id=1)
    return row["quality_profile_id"] if row else None


# ── Add to Wishlist ──────────────────────────────────────────────────────────

def test_explicit_profile_is_stored_on_the_wishlist_row(env):
    client, db = env
    quality_id = db.create_quality_profile("Hi-Res", {})

    response = client.post("/api/add-album-to-wishlist", json=_track_payload(quality_id))

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["success"] is True
    assert _stored_profile(db) == quality_id


def test_omitted_profile_still_uses_the_default(env):
    client, db = env

    response = client.post("/api/add-album-to-wishlist", json=_track_payload())

    assert response.status_code == 200, response.get_json()
    assert _stored_profile(db) is not None


@pytest.mark.parametrize("bad", [999999, "abc", [], {}, True, 0, -1])
def test_bad_profile_is_rejected_and_nothing_is_written(env, bad):
    client, db = env

    response = client.post("/api/add-album-to-wishlist", json=_track_payload(bad))

    assert response.status_code == 400, response.get_json()
    assert _stored_profile(db) is None


# ── Download Missing / Begin Analysis ────────────────────────────────────────

def _start_missing(client, quality_profile_id=None, playlist_id="album_1"):
    # NOT an "enhanced_search_"/"gsearch_" id: that prefix is a confirmed-search
    # process (core.library2.confirmed_intent) that materializes lib2 rows and
    # requires full artist/album/track metadata — unrelated to what these
    # tests pin down (requested_quality_profile_id propagation onto the batch).
    body = {
        "tracks": [{"id": "sp-1", "name": "Song", "artists": [{"name": "Artist"}]}],
        "playlist_name": "Album",
    }
    if quality_profile_id is not None:
        body["quality_profile_id"] = quality_profile_id
    return client.post(f"/api/playlists/{playlist_id}/start-missing-process", json=body)


def test_begin_analysis_records_the_explicit_profile_on_the_batch(env, monkeypatch):
    client, db = env
    quality_id = db.create_quality_profile("Hi-Res", {})
    monkeypatch.setattr(web_server, "_run_full_missing_tracks_process", lambda *a, **k: None)

    response = _start_missing(client, quality_id)

    assert response.status_code == 200, response.get_json()
    batch_id = response.get_json()["batch_id"]
    assert web_server.download_batches[batch_id]["requested_quality_profile_id"] == quality_id


@pytest.mark.parametrize("bad", [999999, "abc", [], True])
def test_begin_analysis_rejects_a_bad_profile(env, monkeypatch, bad):
    client, _ = env
    monkeypatch.setattr(web_server, "_run_full_missing_tracks_process", lambda *a, **k: None)

    response = _start_missing(client, bad)

    assert response.status_code == 400, response.get_json()


def test_begin_analysis_without_a_profile_leaves_the_server_in_charge(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(web_server, "_run_full_missing_tracks_process", lambda *a, **k: None)

    response = _start_missing(client)

    assert response.status_code == 200, response.get_json()
    batch_id = response.get_json()["batch_id"]
    assert web_server.download_batches[batch_id]["requested_quality_profile_id"] is None


# ── precedence: explicit choice beats the persisted mirror assignment ────────

def test_explicit_choice_outranks_the_mirror_assignment(env):
    client, db = env
    mirror_profile = db.create_quality_profile("Mirror Standard", {})
    chosen_profile = db.create_quality_profile("Chosen Hi-Res", {})
    pk = db.mirror_playlist(source="spotify", source_playlist_id="pl-1", name="Mix",
                            tracks=[{"track_name": "Song", "artist_name": "Artist"}],
                            profile_id=1)
    assert db.set_mirrored_playlist_quality_profile(pk, mirror_profile, profile_id=1)

    tracks = [{"id": "sp-1", "name": "Song"}]

    stamped, effective = web_server._tracks_with_mirrored_quality_profile(
        "pl-1", "Mix", tracks, profile_id=1, explicit_quality_profile_id=chosen_profile,
    )
    assert effective == chosen_profile
    assert stamped[0]["quality_profile_id"] == chosen_profile


def test_without_an_explicit_choice_the_mirror_assignment_still_applies(env):
    client, db = env
    mirror_profile = db.create_quality_profile("Mirror Standard", {})
    pk = db.mirror_playlist(source="spotify", source_playlist_id="pl-1", name="Mix",
                            tracks=[{"track_name": "Song", "artist_name": "Artist"}],
                            profile_id=1)
    assert db.set_mirrored_playlist_quality_profile(pk, mirror_profile, profile_id=1)

    stamped, effective = web_server._tracks_with_mirrored_quality_profile(
        "pl-1", "Mix", [{"id": "sp-1", "name": "Song"}], profile_id=1,
    )
    assert effective == mirror_profile
    assert stamped[0]["quality_profile_id"] == mirror_profile
