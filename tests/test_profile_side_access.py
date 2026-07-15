"""Per-profile SIDE access (music | video | both) — DB layer + video API gate.

Contract: never nothing. Non-admin profiles default to MUSIC ONLY (NULL column
reads as 'music' — most installs predate the video side); admins always resolve
to 'both' no matter what the row stores. A music-only profile gets NOTHING from
the /api/video blueprint (its whole UI is hidden — any request is a deep link
or a probe).
"""

from __future__ import annotations

import pytest
from flask import Flask

from database.music_database import MusicDatabase


@pytest.fixture
def db(tmp_path):
    return MusicDatabase(str(tmp_path / "music.db"))


# ── DB layer ─────────────────────────────────────────────────────────────────

def test_migration_adds_allowed_sides_column(db):
    with db._get_connection() as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(profiles)").fetchall()]
    assert 'allowed_sides' in cols


def test_nonadmin_defaults_to_music_only(db):
    pid = db.create_profile(name='Kid')                    # nothing stored → NULL
    assert db.get_profile(pid)['allowed_sides'] == 'music'
    assert all(p['allowed_sides'] == 'music' for p in db.get_all_profiles()
               if p['id'] == pid)


def test_admin_always_resolves_to_both(db):
    # Profile 1 (root admin) — even a bogus stored value is inert on the read side.
    with db._get_connection() as conn:
        conn.execute("UPDATE profiles SET allowed_sides='music' WHERE id=1")
        conn.commit()
    assert db.get_profile(1)['allowed_sides'] == 'both'


def test_sides_round_trip_and_validation(db):
    pid = db.create_profile(name='Kid', allowed_sides='video')
    assert db.get_profile(pid)['allowed_sides'] == 'video'

    assert db.update_profile(pid, allowed_sides='both') is True
    assert db.get_profile(pid)['allowed_sides'] == 'both'

    # Invalid values reset to NULL → the shipped music-only default. Never
    # empty, never garbage.
    db.update_profile(pid, allowed_sides='none')
    assert db.get_profile(pid)['allowed_sides'] == 'music'
    pid2 = db.create_profile(name='Kid2', allowed_sides='everything')
    assert db.get_profile(pid2)['allowed_sides'] == 'music'


# ── video API gate ───────────────────────────────────────────────────────────

def _client_as(tmp_path, *, is_admin, allowed_sides):
    import api.video as videoapi
    from database.video_database import VideoDatabase
    videoapi._video_db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    app = Flask(__name__)

    @app.before_request
    def _stamp_g():
        from flask import g
        g.is_admin = is_admin
        g.can_download = True
        g.allowed_sides = allowed_sides

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def test_music_only_profile_is_blocked_from_the_whole_video_api(tmp_path):
    c = _client_as(tmp_path, is_admin=False, allowed_sides='music')
    for method, url in [("get", "/api/video/dashboard"),
                        ("get", "/api/video/detail/movie/5"),
                        ("get", "/api/video/library?kind=movies"),
                        ("post", "/api/video/wishlist/add")]:
        r = getattr(c, method)(url, json={})
        assert r.status_code == 403, "%s %s must be side-gated" % (method.upper(), url)
        assert "Video access" in (r.get_json() or {}).get("error", "")


def test_video_and_both_profiles_pass_the_side_gate(tmp_path):
    for sides in ('video', 'both'):
        c = _client_as(tmp_path, is_admin=False, allowed_sides=sides)
        assert c.get("/api/video/dashboard").status_code != 403


def test_admin_ignores_a_stored_music_value(tmp_path):
    # Defensive: even if a bogus 'music' ever reaches g for an admin, admins
    # keep video access (the read side resolves them to 'both' anyway).
    c = _client_as(tmp_path, is_admin=True, allowed_sides='music')
    assert c.get("/api/video/dashboard").status_code != 403
