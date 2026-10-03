"""#1411: "all mirrored playlists" runs select the automation owner's mirrors.

the automation editor never saves profile_id, so the pipeline and refresh
mirrored used to fall back to get_mirrored_playlists() = admin's mirrors. a
non-admin's scheduled pipeline processed admin's playlists (or none) and still
reported success. the engine already runs each handler AS the owner, so "all"
has to read that background profile.
"""

from unittest.mock import MagicMock

import pytest

from core.automation.handlers import refresh_mirrored as refresh_mod
from core.automation.handlers.refresh_mirrored import auto_refresh_mirrored
from core.automation_engine import AutomationEngine
from core.playlists.pipeline import _resolve_pipeline_playlists
from core.profile_context import reset_background_profile, set_background_profile
from database.music_database import MusicDatabase
from tests.automation.test_handlers_playlist import _build_deps


@pytest.fixture()
def db(tmp_path):
    db = MusicDatabase(str(tmp_path / "music.db"))
    db.mirror_playlist(source='spotify', source_playlist_id='admin-pl',
                       name='Admin Mix', tracks=[], profile_id=1)
    db.mirror_playlist(source='spotify', source_playlist_id='user-pl',
                       name='User Mix', tracks=[], profile_id=4)
    return db


def _engine_running(handler, owner_profile_id):
    auto_db = MagicMock()
    auto_db.get_automation.return_value = {
        'id': 1, 'name': 'Refresh', 'enabled': True,
        'action_type': 'refresh_mirrored', 'action_config': '{"all": true}',
        'trigger_type': 'interval_hours', 'trigger_config': '{"hours": 1}',
        'profile_id': owner_profile_id,
    }
    auto_db.update_automation_run = MagicMock(return_value=True)
    eng = AutomationEngine(auto_db)
    eng._running = True
    eng._action_handlers['refresh_mirrored'] = {'handler': handler, 'guard': None}
    return eng


def _refreshed_names(monkeypatch, db, owner_profile_id):
    """run refresh-all through the real engine; return the mirrors it touched."""
    seen = []

    def _fake_fetch(source, source_id, pl, deps, auto_id):
        seen.append(pl['name'])
        return None  # no network; counted as an error, which is fine here

    monkeypatch.setattr(refresh_mod, '_fetch_detail', _fake_fetch)
    deps = _build_deps(get_database=lambda: db)
    eng = _engine_running(lambda config: auto_refresh_mirrored(config, deps), owner_profile_id)
    eng.run_automation(1, skip_delay=True)
    return seen


def test_nonadmin_refresh_all_picks_owner_mirrors(monkeypatch, db):
    assert _refreshed_names(monkeypatch, db, owner_profile_id=4) == ['User Mix']


def test_admin_refresh_all_still_picks_admin_mirrors(monkeypatch, db):
    assert _refreshed_names(monkeypatch, db, owner_profile_id=1) == ['Admin Mix']


def test_refresh_all_explicit_profile_id_wins(monkeypatch, db):
    # the playlists-page pipeline passes profile_id on a thread with no
    # background profile; its phase-1 refresh must use it
    seen = []
    monkeypatch.setattr(refresh_mod, '_fetch_detail',
                        lambda source, sid, pl, deps, aid: seen.append(pl['name']))
    auto_refresh_mirrored({'all': True, 'profile_id': 4}, _build_deps(get_database=lambda: db))
    assert seen == ['User Mix']


def test_pipeline_all_uses_background_owner(db):
    token = set_background_profile(4)
    try:
        playlists = _resolve_pipeline_playlists(db, None, True)
    finally:
        reset_background_profile(token)
    assert [p['name'] for p in playlists] == ['User Mix']


def test_pipeline_all_explicit_profile_id_wins_over_background(db):
    token = set_background_profile(4)
    try:
        playlists = _resolve_pipeline_playlists(db, None, True, profile_id=1)
    finally:
        reset_background_profile(token)
    assert [p['name'] for p in playlists] == ['Admin Mix']


def test_pipeline_single_playlist_not_scoped_by_background(db):
    # picking one playlist is by id and worked already; an admin-owned
    # automation targeting a user's mirror must keep working
    user_pl = db.get_mirrored_playlists(4)[0]
    token = set_background_profile(1)
    try:
        playlists = _resolve_pipeline_playlists(db, user_pl['id'], False)
    finally:
        reset_background_profile(token)
    assert [p['name'] for p in playlists] == ['User Mix']
