"""batch delete for mirrored playlists (#1219).

four tidal playlists shared a name, three got deleted upstream, and the only
way to drop the stale mirrors was the card menu, one card at a time. the
mirrored tab now has a select mode with one delete for the lot; this is the
endpoint behind it. it has to keep the single delete's ownership scope: a
non-admin can only remove their own profile's mirrors, and a foreign id is
reported as not deleted, not as an error that says it exists.
"""

import pytest
from flask import Flask, g

from database.music_database import MusicDatabase


@pytest.fixture()
def mdb(tmp_path):
    return MusicDatabase(database_path=str(tmp_path / "music.db"))


def _mirror(db, name, profile_id):
    return db.mirror_playlist(
        source="tidal", source_playlist_id=f"{name}-{profile_id}", name=name,
        tracks=[{"name": "t", "artists": ["a"]}], profile_id=profile_id,
    )


def _client(db, *, is_admin, profile_id=1):
    import api.mirrored_playlists as mod
    mod.configure(get_database=lambda: db)
    app = Flask(__name__)

    @app.before_request
    def _stamp():
        g.is_admin = is_admin

    app.register_blueprint(mod.create_blueprint())
    import core.profile_context as pc
    app.config["_pc"] = pc
    return app.test_client()


def _count(db):
    with db._get_connection() as conn:
        return conn.execute("SELECT COUNT(*) FROM mirrored_playlists").fetchone()[0]


def test_admin_deletes_every_listed_mirror(mdb):
    ids = [_mirror(mdb, f"#wonderbracket", p) for p in (1, 2, 3)]
    keep = _mirror(mdb, "keep me", 1)
    c = _client(mdb, is_admin=True)
    resp = c.post("/api/mirrored-playlists/batch-delete", json={"ids": ids})
    assert resp.status_code == 200, resp.data
    body = resp.get_json()
    assert body["success"] is True
    assert sorted(body["deleted"]) == sorted(ids)
    assert body["not_deleted"] == []
    assert _count(mdb) == 1
    assert mdb.get_mirrored_playlist(keep) is not None


def test_unknown_ids_are_reported_not_raised(mdb):
    mine = _mirror(mdb, "a", 1)
    c = _client(mdb, is_admin=True)
    resp = c.post("/api/mirrored-playlists/batch-delete", json={"ids": [mine, 99999, mine]})
    body = resp.get_json()
    assert body["deleted"] == [mine]
    assert body["not_deleted"] == [99999]


def test_non_admin_cannot_reach_another_profiles_mirror(mdb, monkeypatch):
    mine = _mirror(mdb, "mine", 2)
    theirs = _mirror(mdb, "theirs", 3)
    import api.mirrored_playlists as mod
    monkeypatch.setattr(mod, "get_current_profile_id", lambda: 2)
    c = _client(mdb, is_admin=False)
    resp = c.post("/api/mirrored-playlists/batch-delete", json={"ids": [mine, theirs]})
    body = resp.get_json()
    assert body["deleted"] == [mine]
    # same answer as a nonexistent id, so the endpoint can't be used to probe
    assert body["not_deleted"] == [theirs]
    assert mdb.get_mirrored_playlist(theirs) is not None


@pytest.mark.parametrize("payload", [
    {}, {"ids": []}, {"ids": "1,2"}, {"ids": [1, "x"]}, {"ids": [True]},
])
def test_bad_payloads_are_400(mdb, payload):
    c = _client(mdb, is_admin=True)
    resp = c.post("/api/mirrored-playlists/batch-delete", json=payload)
    assert resp.status_code == 400
    assert _count(mdb) == 0


def test_cap_on_ids(mdb):
    c = _client(mdb, is_admin=True)
    resp = c.post("/api/mirrored-playlists/batch-delete", json={"ids": list(range(501))})
    assert resp.status_code == 400
