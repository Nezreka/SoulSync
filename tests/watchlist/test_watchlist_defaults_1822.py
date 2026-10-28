"""#1822: watchlist defaults instead of a global override.

the old global settings did nothing until you flipped "global override",
then they silently beat every artist's own rules. now they are defaults an
artist follows (follow_defaults) or overrides. the migration, the scanner,
the artist-page filter explanation and the api all agree on one rule, and
the day it ships nothing a scan grabs changes.
"""

from __future__ import annotations

import pytest

pytest.importorskip("flask")

import web_server  # noqa: E402
import core.settings as settings  # noqa: E402
from core.watchlist_defaults import (  # noqa: E402
    INCLUDE_DEFAULTS, follows_defaults, retire_legacy_override, stamp_value)
from database.music_database import MusicDatabase, WatchlistArtist  # noqa: E402

CUSTOM_DEFAULTS = {**INCLUDE_DEFAULTS, 'include_singles': False, 'include_live': True}


@pytest.fixture()
def cfg(monkeypatch):
    store = {f'watchlist.global_{k}': v for k, v in CUSTOM_DEFAULTS.items()}
    store['watchlist.global_override_enabled'] = False
    cm = settings.config_manager
    monkeypatch.setattr(cm, 'get', lambda key, default=None: store.get(key, default))
    monkeypatch.setattr(cm, 'set', lambda key, value: store.__setitem__(key, value))
    return store


def test_stamp_keeps_what_each_artist_was_getting():
    assert stamp_value(dict(CUSTOM_DEFAULTS), CUSTOM_DEFAULTS, False) == 1
    assert stamp_value({**CUSTOM_DEFAULTS, 'include_eps': False}, CUSTOM_DEFAULTS, False) == 0
    # the override gave everyone the defaults already
    assert stamp_value({'include_albums': 0}, CUSTOM_DEFAULTS, True) == 1
    # a missing column reads as its fresh-install value
    assert stamp_value({}, INCLUDE_DEFAULTS, False) == 1


def test_a_missing_flag_means_its_own_rules():
    assert follows_defaults(WatchlistArtist(1, 'sp', 'A', None)) is False
    assert follows_defaults({'follow_defaults': None}) is False
    assert follows_defaults({'follow_defaults': 1}) is True
    assert follows_defaults({'follow_defaults': 0}, override_on=True) is True


def _db_without_the_column(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    with db._get_connection() as conn:
        conn.execute("ALTER TABLE watchlist_artists DROP COLUMN follow_defaults")
        for n, (name, flags) in enumerate([('Same', CUSTOM_DEFAULTS),
                                           ('Own', {**CUSTOM_DEFAULTS, 'include_eps': False})]):
            cols = ', '.join(flags)
            conn.execute(
                f"INSERT INTO watchlist_artists (spotify_artist_id, artist_name, date_added, {cols}) "
                f"VALUES (?, ?, CURRENT_TIMESTAMP, {', '.join('?' * len(flags))})",
                (f'sp-{n}', name, *[int(v) for v in flags.values()]))
        conn.commit()
    return db


def _migrate(db):
    with db._get_connection() as conn:
        db._add_watchlist_follow_defaults_column(conn.cursor())
        conn.commit()
        return {r[0]: r[1] for r in conn.execute(
            "SELECT artist_name, follow_defaults FROM watchlist_artists")}


def test_migration_stamps_followers_by_their_rules(tmp_path, cfg):
    assert _migrate(_db_without_the_column(tmp_path)) == {'Same': 1, 'Own': 0}


def test_migration_under_the_override_makes_everyone_follow(tmp_path, cfg):
    cfg['watchlist.global_override_enabled'] = True
    assert _migrate(_db_without_the_column(tmp_path)) == {'Same': 1, 'Own': 1}


def test_migration_that_cannot_read_defaults_keeps_everyone_on_their_own(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError('config unreadable')
    import core.watchlist_defaults as wd
    monkeypatch.setattr(wd, 'default_values', boom)
    assert _migrate(_db_without_the_column(tmp_path)) == {'Same': 0, 'Own': 0}


def test_a_new_artist_follows_the_defaults(tmp_path):
    db = MusicDatabase(str(tmp_path / "m.db"))
    db.add_artist_to_watchlist("sp-new", "New", source="spotify")
    assert db.get_watchlist_artists()[0].follow_defaults is True


def test_scanner_gives_defaults_to_followers_only(cfg):
    from core.watchlist_scanner import WatchlistScanner
    follower = WatchlistArtist(1, 'a', 'Follower', None, follow_defaults=True)
    own = WatchlistArtist(2, 'b', 'Own', None, include_singles=True, include_live=False)
    scanner = WatchlistScanner.__new__(WatchlistScanner)
    scanner._database = object()
    scanner._apply_global_watchlist_overrides([follower, own])
    assert (follower.include_singles, follower.include_live) == (False, True)
    assert (own.include_singles, own.include_live) == (True, False)


def test_a_still_on_override_is_retired_without_changing_any_rule(tmp_path, cfg):
    cfg['watchlist.global_override_enabled'] = True
    db = MusicDatabase(str(tmp_path / "m.db"))
    db.add_artist_to_watchlist("sp-1", "Own", source="spotify")
    with db._get_connection() as conn:
        conn.execute("UPDATE watchlist_artists SET follow_defaults = 0")
        conn.commit()
    assert retire_legacy_override(db, settings.config_manager) is True
    assert cfg['watchlist.global_override_enabled'] is False
    assert db.get_watchlist_artists()[0].follow_defaults is True
    assert retire_legacy_override(db, settings.config_manager) is False


def test_filter_explanation_uses_the_defaults_for_a_follower(tmp_path, cfg):
    from core.metadata.discography_filters import resolve_watchlist_content_settings
    db = MusicDatabase(str(tmp_path / "m.db"))
    db.add_artist_to_watchlist("sp-1", "Follower", source="spotify")
    prefs = resolve_watchlist_content_settings(db, "Follower", profile_id=1)
    assert prefs['include_singles'] is False


@pytest.fixture()
def client(tmp_path, monkeypatch, cfg):
    db = MusicDatabase(str(tmp_path / "m.db"))
    db.add_artist_to_watchlist("sp-1", "Artist", source="spotify")
    import database.music_database as music_database
    import api.artist_watchlist as aw
    monkeypatch.setattr(music_database, "get_database", lambda *a, **k: db)
    monkeypatch.setattr(web_server, "get_database", lambda *a, **k: db)
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(aw, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(aw, "_spotify_client", lambda: None)
    web_server.app.config["TESTING"] = True
    with web_server.app.test_client() as test_client:
        yield test_client, db


def _row(db):
    with db._get_connection() as conn:
        return dict(conn.execute("SELECT * FROM watchlist_artists WHERE spotify_artist_id='sp-1'").fetchone())


def test_config_get_shows_a_follower_the_rules_it_gets(client):
    test_client, _db = client
    config = test_client.get("/api/watchlist/artist/sp-1/config").get_json()['config']
    assert config['follow_defaults'] is True
    assert config['include_singles'] is False and config['include_live'] is True
    assert config['defaults'] == CUSTOM_DEFAULTS


def test_going_custom_starts_from_the_defaults(client):
    test_client, db = client
    resp = test_client.post("/api/watchlist/artist/sp-1/config",
                            json={'follow_defaults': False, 'include_eps': False})
    assert resp.status_code == 200, resp.get_json()
    row = _row(db)
    assert row['follow_defaults'] == 0
    # unsent rules are the defaults it was getting, the one sent rule applies
    assert row['include_live'] == 1 and row['include_singles'] == 0 and row['include_eps'] == 0


def test_sending_rules_without_the_flag_means_own_rules(client):
    test_client, db = client
    assert test_client.post("/api/watchlist/artist/sp-1/config",
                            json={'include_remixes': True}).status_code == 200
    assert _row(db)['follow_defaults'] == 0


def test_an_unrelated_save_keeps_a_follower_following(client):
    test_client, db = client
    quality_id = db.create_quality_profile("Hi-Res", {})
    assert test_client.post("/api/watchlist/artist/sp-1/config",
                            json={'quality_profile_id': quality_id}).status_code == 200
    assert _row(db)['follow_defaults'] == 1


def test_reset_puts_custom_artists_back_on_the_defaults(client):
    test_client, db = client
    test_client.post("/api/watchlist/artist/sp-1/config", json={'follow_defaults': False})
    assert test_client.get("/api/watchlist/global-config").get_json()['config']['custom_artist_count'] == 1
    assert test_client.post("/api/watchlist/reset-to-defaults").get_json()['reset'] == 1
    assert _row(db)['follow_defaults'] == 1


def test_defaults_always_need_a_release_type(client):
    test_client, _db = client
    resp = test_client.post("/api/watchlist/global-config", json={
        'include_albums': False, 'include_eps': False, 'include_singles': False})
    assert resp.status_code == 400


def test_v1_patch_on_a_follower_keeps_the_rules_it_was_getting(client, monkeypatch):
    _client, db = client
    from unittest.mock import patch

    from flask import Blueprint, Flask

    import api.watchlist as v1
    monkeypatch.setattr(v1, "get_database", lambda *a, **k: db)
    app = Flask(__name__)
    bp = Blueprint("v1", __name__, url_prefix="/api/v1")
    with patch.object(v1, "require_api_key", lambda f: f):
        v1.register_routes(bp)
    app.register_blueprint(bp)

    resp = app.test_client().patch("/api/v1/watchlist/sp-1", json={'include_eps': False})
    assert resp.status_code == 200, resp.get_json()
    row = _row(db)
    assert row['follow_defaults'] == 0
    assert row['include_live'] == 1 and row['include_singles'] == 0 and row['include_eps'] == 0
    # and the list reports what a follower gets
    _row_follow = app.test_client().patch("/api/v1/watchlist/sp-1", json={'follow_defaults': True})
    assert _row_follow.status_code == 200
    listed = app.test_client().get("/api/v1/watchlist").get_json()['data']['artists'][0]
    assert listed['follow_defaults'] is True and listed['include_eps'] is True
