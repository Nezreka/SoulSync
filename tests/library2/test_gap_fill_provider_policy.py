"""Gap-fill route policy with real native SQLite and isolated provider clients."""

from types import SimpleNamespace

import pytest

from core.library2.metadata_overrides import get_field_overrides, set_field_override
from tests.library2.test_api_routes import api, _wait_for_job  # noqa: F401


@pytest.fixture(autouse=True)
def offline_writes(monkeypatch):
    monkeypatch.setattr("core.library2.retag.write_tags", lambda *a, **kw: {"written": 0})
    monkeypatch.setattr("core.metadata.registry.get_primary_source", lambda: "musicbrainz")
    monkeypatch.setattr("core.library2.match_status.configured_services", lambda: {"musicbrainz", "spotify"})


def _fill(client, ids):
    response = client.post(f"/api/library/v2/tracks/{ids['album_track']}/fill-tag-gaps")
    assert response.status_code == 200
    state = _wait_for_job(client, response.get_json()["job_id"])
    assert state["error"] is None
    return state["result"]["enriched_from"]


@pytest.mark.parametrize("primary", ["musicbrainz", "spotify"])
def test_gap_fill_uses_configured_primary_and_only_available_services(api, monkeypatch, primary):
    client, _, ids = api
    calls = []
    monkeypatch.setattr("core.metadata.registry.get_primary_source", lambda: primary)
    monkeypatch.setattr("core.library2.match_status.configured_services", lambda: {primary, "deezer"})

    def enrich(conn, kind, entity_id, service):
        calls.append(service)
        return {"success": True, "source": service}

    monkeypatch.setattr("core.library2.native_enrich.enrich_native_entity_for_service", enrich)
    assert _fill(client, ids) == primary
    assert calls == [primary]


def test_no_available_services_performs_no_provider_calls(api, monkeypatch):
    client, _, ids = api
    monkeypatch.setattr("core.library2.match_status.configured_services", lambda: set())
    monkeypatch.setattr("core.library2.native_enrich.enrich_native_entity_for_service", lambda *a, **kw: pytest.fail("no services configured"))
    assert _fill(client, ids) is None


def test_gap_fill_exact_id_honors_selected_edition_and_manual_values(api, monkeypatch):
    client, db, ids = api
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_albums SET musicbrainz_id='old-release' WHERE id=?", (ids["views"],))
        conn.execute("INSERT INTO lib2_release_editions(release_group_id,is_default,musicbrainz_id) VALUES(?,1,'selected-release')", (ids["views"],))
        set_field_override(conn, entity_type="release_group", entity_id=ids["views"], field_name="year", value=1999)
    calls = []

    def album(provider_id, **kw):
        calls.append(provider_id)
        return {"name": "Provider title", "year": 2024, "label": "New label"}

    monkeypatch.setattr("core.metadata.registry.get_client_for_source", lambda source: SimpleNamespace(get_album=album))
    monkeypatch.setattr("core.library2.native_enrich.enrich_native_entity_for_service", lambda *a, **kw: pytest.fail("selected editions must not be searched"))
    assert _fill(client, ids) == "musicbrainz"
    assert calls == ["selected-release"]
    with db._get_connection() as conn:
        row = conn.execute("SELECT title,year,label FROM lib2_albums WHERE id=?", (ids["views"],)).fetchone()
        assert tuple(row) == ("Views", None, "New label")
        assert get_field_overrides(conn, entity_type="release_group", entity_id=ids["views"])["year"].value == 1999


def test_pin_cannot_fall_back_when_provider_is_partial(api, monkeypatch):
    client, db, ids = api
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_albums SET image_url='known-cover',musicbrainz_id='old-mb',spotify_id='old-sp',canonical_source='musicbrainz',canonical_album_id='pin',canonical_locked=1 WHERE id=?", (ids["views"],))
    calls = []

    def album(provider_id, **kw):
        calls.append(provider_id)
        return {"name": "Views"}  # Supplies none of the missing fields.

    monkeypatch.setattr("core.metadata.registry.get_client_for_source", lambda source: SimpleNamespace(get_album=album))
    assert _fill(client, ids) is None
    assert calls == ["pin"]


def test_unusable_provider_facts_do_not_starve_later_provider(api, monkeypatch):
    client, db, ids = api
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_albums SET image_url='known-cover',musicbrainz_id='mb',spotify_id='sp' WHERE id=?", (ids["views"],))
    calls = []

    def client_for(source):
        def album(provider_id, **kw):
            calls.append((source, provider_id))
            if source == "musicbrainz":
                raise RuntimeError("offline provider failure")
            return {"label": "Spotify label"}
        return SimpleNamespace(get_album=album)

    monkeypatch.setattr("core.metadata.registry.get_client_for_source", client_for)
    assert _fill(client, ids) == "spotify"
    assert calls == [("musicbrainz", "mb"), ("spotify", "sp")]


def test_hand_tagged_track_never_requeries_its_album(api, monkeypatch):
    from database.music_database import MusicDatabase

    client, db, ids = api
    monkeypatch.setattr(db, "manual_path_keys", lambda: {MusicDatabase.manual_path_key("/m/one-dance.flac")}, raising=False)
    monkeypatch.setattr("core.library2.native_enrich.enrich_native_entity_for_service", lambda *a, **kw: pytest.fail("hand tags must not be provider-refreshed"))
    assert _fill(client, ids) is None


def test_mapped_hand_tagged_path_is_also_protected(api, monkeypatch):
    from database.music_database import MusicDatabase

    client, db, ids = api
    monkeypatch.setattr(db, "manual_path_keys", lambda: {MusicDatabase.manual_path_key("/local/song.flac")}, raising=False)
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda *a, **kw: "/local/song.flac")
    monkeypatch.setattr("core.library2.native_enrich.enrich_native_entity_for_service", lambda *a, **kw: pytest.fail("mapped hand tags must remain protected"))
    assert _fill(client, ids) is None


def test_musicbrainz_exact_cover_fallback_survives_empty_descriptive_metadata(api, monkeypatch):
    client, db, ids = api
    with db._get_connection() as conn:
        conn.execute("UPDATE lib2_albums SET musicbrainz_id='mb-release' WHERE id=?", (ids["views"],))
    monkeypatch.setattr("core.metadata.registry.get_client_for_source", lambda _: SimpleNamespace(get_album=lambda *a, **kw: {}))
    assert _fill(client, ids) == "musicbrainz"
    with db._get_connection() as conn:
        assert conn.execute("SELECT image_url FROM lib2_albums WHERE id=?", (ids["views"],)).fetchone()[0] == "https://coverartarchive.org/release/mb-release/front-1200"
