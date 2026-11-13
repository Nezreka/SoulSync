"""A brand-new install, walked end to end (#2062 and after).

Nearly every Library test starts from an upgraded database: legacy tables
present, the importer already run. A new install has neither, and that shape
broke once without a single test noticing (the Import button raised on the
missing tables and the page said the upgrade had failed).

This uses the real MusicDatabase on an empty file, the real startup step and
the real routes: nothing is paused, every Library request answers, and music
that arrives by a server scan or a download shows up on the page.
"""

from __future__ import annotations

import re

import pytest

from database.music_database import MusicDatabase
from tests.repair_jobs.test_album_tag_consistency_reporting import _make_flac

flask = pytest.importorskip("flask")


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    monkeypatch.setattr("core.library2.unmapped_trigger.schedule_unmapped_artist_reconcile",
                        lambda *a, **k: True)
    monkeypatch.setattr("core.library2.autolink._warm_new_artwork", lambda *a, **k: None)
    db = MusicDatabase(str(tmp_path / "music_library.db"))
    monkeypatch.setattr("database.music_database.get_database", lambda *a, **k: db)
    from api.library_v2 import register_library_v2_routes
    app = flask.Flask(__name__)
    config = {"features.library_v2": True, "soulseek.transfer_path": str(tmp_path / "Transfer")}
    register_library_v2_routes(
        app, get_database=lambda: db,
        config_get=lambda key, default=None: config.get(key, default),
        config_manager=None, profile_id_getter=lambda: 1,
        profile_page_allowed_getter=lambda _page: True,
        acquisition_search_adapters_getter=lambda _criteria: [],
        acquisition_async_runner=None,
        acquisition_submission_adapter_getter=lambda _source: None,
        configured_match_services_getter=lambda: None)
    return db, app, tmp_path


def test_a_new_database_has_no_older_library_and_nothing_is_held_back(fresh):
    db, _app, _tmp = fresh
    from core.library2 import bootstrap
    from core.library2.migration_gate import migration_required
    with db._get_connection() as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not {"artists", "albums", "tracks"} & tables
    assert bootstrap.legacy_source_present(db) is False
    # downloads, sync and automations are never paused waiting for an upgrade that cannot happen
    assert migration_required(db) is False
    assert bootstrap.run_bootstrap_if_needed(db, lambda key, default=None: default) == {
        "skipped": "empty_source"}
    assert bootstrap.get_state(db)["status"] == "waiting_for_source"
    assert migration_required(db) is False
    # every later start is the same quiet no-op
    assert bootstrap.run_bootstrap_if_needed(db, lambda key, default=None: default) == {
        "skipped": "empty_source"}


def test_every_library_request_answers_on_an_empty_install(fresh):
    db, app, _tmp = fresh
    from core.library2 import bootstrap
    bootstrap.run_bootstrap_if_needed(db, lambda key, default=None: default)
    client = app.test_client()
    # these two need the app's config manager, which this harness does not build
    needs_config = {"/api/library/v2/maintenance/integrity-report",
                    "/api/library/v2/maintenance/reconcile-acquisition"}
    broken = []
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.rule.startswith("/static") or rule.rule in needs_config:
            continue
        url = re.sub(r"<int:[^>]+>", "1", rule.rule)
        url = re.sub(r"<(?:string:)?(entity|kind)>", "albums", url)
        url = re.sub(r"<[^>]+>", "x", url)
        response = client.get(url)
        if response.status_code >= 500:
            broken.append((response.status_code, url, response.get_data(as_text=True)[:120]))
    assert broken == []
    assert client.get("/api/library/v2/artists").get_json()["artists"] == []
    assert client.get("/api/library/v2/enabled").get_json()["enabled"] is True


def test_pressing_import_says_there_is_nothing_to_import_and_breaks_nothing(fresh):
    db, app, _tmp = fresh
    from core.library2 import bootstrap
    bootstrap.run_bootstrap_if_needed(db, lambda key, default=None: default)
    response = app.test_client().post("/api/library/v2/import", json={})
    assert response.status_code == 409
    assert response.get_json()["code"] == "nothing_to_import"
    state = bootstrap.get_state(db)
    assert (state["status"], state["last_error"]) == ("waiting_for_source", None)


def _scan(db, path, artist, album, title, number=1):
    artist_obj = _Obj(ratingKey=f"ar-{artist}", title=artist, thumb=None, genres=[], summary="")
    album_obj = _Obj(ratingKey=f"al-{album}", title=album, year=2015, thumb=None, genres=[],
                     leafCount=10, duration=1000, parentTitle=artist)
    track_obj = _Obj(ratingKey=f"t-{title}", title=title, trackNumber=number, discNumber=1,
                     duration=200000, path=str(path), grandparentTitle=artist, parentTitle=album,
                     originalTitle=artist)
    db.insert_or_update_media_artist(artist_obj, server_source="navidrome")
    db.insert_or_update_media_album(album_obj, artist_obj.ratingKey, server_source="navidrome")
    return db.insert_or_update_media_track(track_obj, album_obj.ratingKey, artist_obj.ratingKey,
                                           server_source="navidrome")


def test_music_arrives_by_a_scan_and_by_a_download_and_shows_on_the_page(fresh):
    db, app, tmp = fresh
    from core.library2 import bootstrap
    from core.library2.autolink import link_download_into_library_v2
    bootstrap.run_bootstrap_if_needed(db, lambda key, default=None: default)

    # a file SoulSync can open: named from its own tags
    here = tmp / "music" / "Tame Impala" / "Currents" / "01 Let It Happen.flac"
    here.parent.mkdir(parents=True)
    _make_flac(here, {"title": "Let It Happen", "artist": "Tame Impala", "albumartist": "Tame Impala",
                      "album": "Currents", "tracknumber": "1"})
    assert _scan(db, here, "Tame Impala", "Currents", "Let It Happen") == "inserted"
    # a media server on another machine: its listing is all there is, and it is enough
    assert _scan(db, "/srv/navidrome/music/Kraftwerk/Computer World/01 Computer World.flac",
                 "Kraftwerk", "Computer World", "Computer World") == "inserted"
    # a file the server still lists but that is gone from a folder SoulSync can see: left out
    assert _scan(db, here.parent / "02 Nangs.flac", "Tame Impala", "Currents", "Nangs", 2) is False

    finished = tmp / "Transfer" / "Caribou" / "Swim" / "01 Odessa.flac"
    finished.parent.mkdir(parents=True)
    _make_flac(finished, {"title": "Odessa", "artist": "Caribou", "albumartist": "Caribou",
                          "album": "Swim", "tracknumber": "1"})
    assert link_download_into_library_v2(
        {"_final_processed_path": str(finished), "username": "soulseek",
         "track_info": {"name": "Odessa", "artist": "Caribou", "album": "Swim",
                        "track_number": 1, "disc_number": 1}}, raise_on_error=True)

    client = app.test_client()
    artists = client.get("/api/library/v2/artists").get_json()["artists"]
    assert sorted(a["name"] for a in artists) == ["Caribou", "Kraftwerk", "Tame Impala"]
    albums = client.get("/api/library/v2/albums").get_json()["albums"]
    assert sorted(a["title"] for a in albums) == ["Computer World", "Currents", "Swim"]
    assert client.get("/api/library/v2/songs").get_json()["pagination"]["total_count"] == 3
    assert client.get(f"/api/library/v2/artists/{artists[0]['id']}").status_code == 200
