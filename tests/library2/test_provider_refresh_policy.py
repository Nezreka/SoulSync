"""Exact provider refresh against disposable native catalogues; no provider I/O."""

from types import SimpleNamespace

import pytest

from core.library2 import catalogue_refresh, provider_adapters
from core.library2.metadata_overrides import get_field_overrides, set_field_override
from tests.lib2_seed import row_conn


@pytest.fixture
def native(tmp_path, monkeypatch):
    from core.library2.schema import ensure_library_v2_schema

    conn = row_conn(str(tmp_path / "native.db"))
    ensure_library_v2_schema(conn)
    artist = conn.execute("INSERT INTO lib2_artists(name) VALUES('Artist')").lastrowid
    album = conn.execute(
        "INSERT INTO lib2_albums(primary_artist_id,title,spotify_id,musicbrainz_id) "
        "VALUES(?,'Album','spotify-release','mb-release')", (artist,),
    ).lastrowid
    track = conn.execute(
        "INSERT INTO lib2_tracks(album_id,title,track_number,disc_number,spotify_id) "
        "VALUES(?,'Song',1,1,'spotify-song')", (album,),
    ).lastrowid
    conn.commit()
    monkeypatch.setattr("core.library2.match_status.configured_services", lambda: {"spotify", "musicbrainz", "deezer"})
    monkeypatch.setattr("core.metadata.registry.get_primary_source", lambda: "spotify")
    calls = []

    def client(source):
        def album_data(provider_id, **kwargs):
            calls.append((source, "album", provider_id))
            return {"name": "Album corrected", "release_date": "2024-05-02"}

        def tracks(provider_id, **kwargs):
            calls.append((source, "tracks", provider_id))
            return [{"id": "spotify-song", "name": "Song (feat. Guest)", "track_number": 1, "disc_number": 1}]

        return SimpleNamespace(get_album=album_data, get_album_tracks=tracks)

    monkeypatch.setattr("core.metadata.registry.get_client_for_source", client)
    yield conn, album, track, calls
    conn.close()


def test_explicit_refresh_uses_id_from_requested_namespace(native):
    conn, album, _, calls = native
    plan = catalogue_refresh.refresh_preview(conn, album, source="musicbrainz")
    assert plan["success"]
    assert {provider_id for _, _, provider_id in calls} == {"mb-release"}
    assert {source for source, _, _ in calls} == {"musicbrainz"}


def test_explicit_refresh_without_that_sources_id_never_borrows_an_id(native):
    conn, album, _, calls = native
    plan = catalogue_refresh.refresh_preview(conn, album, source="deezer")
    assert plan["status"] == "no_source"
    assert calls == []


def test_refresh_uses_selected_edition_instead_of_album_id(native):
    conn, album, _, calls = native
    conn.execute(
        "INSERT INTO lib2_release_editions(release_group_id,is_default,spotify_id) VALUES(?,1,'selected-edition')",
        (album,),
    )
    catalogue_refresh.refresh_preview(conn, album, source="spotify")
    assert {provider_id for _, _, provider_id in calls} == {"selected-edition"}


def test_locked_pin_wins_and_cannot_fall_back_to_other_edition(native):
    conn, album, _, calls = native
    conn.execute(
        "UPDATE lib2_albums SET canonical_source='spotify',canonical_album_id='pinned-release',canonical_locked=1 WHERE id=?",
        (album,),
    )
    plan = catalogue_refresh.refresh_preview(conn, album)
    assert plan["success"]
    assert {provider_id for _, _, provider_id in calls} == {"pinned-release"}
    calls.clear()
    assert catalogue_refresh.refresh_preview(conn, album, source="musicbrainz")["status"] == "no_source"
    assert calls == []


def test_configured_order_and_availability_are_shared(native, monkeypatch):
    conn, album, _, calls = native
    monkeypatch.setattr("core.metadata.registry.get_primary_source", lambda: "musicbrainz")
    monkeypatch.setattr("core.library2.match_status.configured_services", lambda: {"musicbrainz"})
    assert catalogue_refresh.album_source(conn, album) == ("musicbrainz", "mb-release")
    catalogue_refresh.refresh_preview(conn, album)
    assert {source for source, _, _ in calls} == {"musicbrainz"}


def test_preview_never_commits_or_mutates_callers_pending_work(native):
    conn, album, track, _ = native
    conn.execute("UPDATE lib2_tracks SET title='pending' WHERE id=?", (track,))
    before = list(conn.iterdump())
    assert catalogue_refresh.refresh_preview(conn, album)["success"]
    assert conn.in_transaction
    assert list(conn.iterdump()) == before
    conn.rollback()
    assert conn.execute("SELECT title FROM lib2_tracks WHERE id=?", (track,)).fetchone()[0] == "Song"


def test_native_identity_match_repairs_number_even_when_title_changes(native):
    conn, album, track, _ = native
    conn.execute("UPDATE lib2_tracks SET title='Old title',track_number=9 WHERE id=?", (track,))
    plan = catalogue_refresh.refresh_preview(conn, album)
    changes = {c["field"]: c["proposed"] for c in plan["tracks"][0]["changes"]}
    assert changes == {"title": "Song (feat. Guest)", "track_number": 1}


def test_native_match_does_not_retitle_an_unrelated_song_at_same_position(native):
    conn, album, track, _ = native
    conn.execute("UPDATE lib2_tracks SET title='Different recording',spotify_id=NULL WHERE id=?", (track,))
    plan = catalogue_refresh.refresh_preview(conn, album)
    assert plan["tracks"][0]["matched"] is False
    assert plan["tracks"][0]["changes"] == []


def test_refresh_preserves_manual_override_and_operational_ownership(native):
    conn, album, track, _ = native
    set_field_override(conn, entity_type="track", entity_id=track, field_name="title", value="My title")
    from core.library2.profile_lookup import default_quality_profile_id
    quality_id = default_quality_profile_id(conn)
    conn.execute("UPDATE lib2_tracks SET monitored=1,quality_profile_id=? WHERE id=?", (quality_id, track))
    catalogue_refresh.apply_refresh(conn, album)
    assert get_field_overrides(conn, entity_type="track", entity_id=track)["title"].value == "My title"
    row = conn.execute("SELECT monitored,quality_profile_id FROM lib2_tracks WHERE id=?", (track,)).fetchone()
    assert tuple(row) == (1, quality_id)


def test_exact_public_fetch_never_searches_or_allows_facade_fallback(native, monkeypatch):
    _, _, _, calls = native
    fallback_flags = []

    def tracks(provider_id, *, allow_fallback):
        fallback_flags.append(allow_fallback)
        return [{"id": "mb-track", "name": "Song", "track_number": 1}]

    monkeypatch.setattr("core.metadata.registry.get_client_for_source", lambda _: SimpleNamespace(get_album_tracks=tracks))
    result = provider_adapters.fetch_album_release_tracklist("musicbrainz", "mb-release")
    assert result.provider == "musicbrainz"
    assert result.provider_entity_id == "mb-release"
    assert result.tracks[0].musicbrainz_id == "mb-track"
    assert fallback_flags == [False]


@pytest.mark.parametrize("payload", [
    {"provider": "deezer", "id": "spotify-release", "name": "Wrong provider"},
    {"provider": "spotify", "id": "other-edition", "name": "Wrong edition"},
])
def test_exact_descriptive_fetch_rejects_declared_identity_mismatch(native, monkeypatch, payload):
    monkeypatch.setattr("core.metadata.registry.get_client_for_source", lambda _: SimpleNamespace(get_album=lambda *a, **kw: payload))
    assert provider_adapters.fetch_descriptive_metadata("album", {"spotify": "spotify-release"}) is None


def test_exact_artwork_cannot_fall_back_to_a_name_search(native, monkeypatch):
    monkeypatch.setattr("core.metadata.registry.get_client_for_source", lambda _: SimpleNamespace(get_album=lambda *a, **kw: {}))
    monkeypatch.setattr("core.metadata.art_lookup.select_preferred_art", lambda *a, **kw: pytest.fail("exact refresh cannot search for another edition"))
    assert provider_adapters.fetch_artwork_url("album", artist_name="Artist", album_title="Album", source_ids={"spotify": "spotify-release"}, source_order=("spotify",), allow_search=False) is None


def test_json_only_musicbrainz_release_id_is_still_an_exact_identity(native):
    conn, album, _, calls = native
    conn.execute("UPDATE lib2_albums SET musicbrainz_id=NULL,external_ids=? WHERE id=?", ('{"musicbrainz":"json-release"}', album))
    assert catalogue_refresh.album_source(conn, album, source="musicbrainz") == ("musicbrainz", "json-release")


def test_release_group_mbid_cannot_be_queried_as_an_exact_release(native):
    conn, album, _, calls = native
    conn.execute("UPDATE lib2_albums SET musicbrainz_id=NULL,musicbrainz_release_group_id='group-only' WHERE id=?", (album,))
    assert catalogue_refresh.refresh_preview(conn, album, source="musicbrainz")["status"] == "no_source"
    assert calls == []


def test_exact_tracklist_keeps_declared_partial_status(native, monkeypatch):
    monkeypatch.setattr("core.metadata.registry.get_client_for_source", lambda _: SimpleNamespace(get_album_tracks=lambda *a, **kw: {
        "id": "mb-release", "is_complete": False,
        "tracks": [{"id": "mb-track", "name": "Song", "track_number": 1}],
    }))
    assert provider_adapters.fetch_album_release_tracklist("musicbrainz", "mb-release").is_complete is False


def test_apply_is_rollbackable_and_leaves_no_parallel_sql_authority(native):
    conn, album, track, _ = native
    before = conn.execute("SELECT title FROM lib2_tracks WHERE id=?", (track,)).fetchone()[0]
    result = catalogue_refresh.apply_refresh(conn, album)
    assert result["tracks_updated"] == 1
    assert conn.in_transaction
    conn.rollback()
    assert conn.execute("SELECT title FROM lib2_tracks WHERE id=?", (track,)).fetchone()[0] == before


def test_selected_edition_manual_date_is_reported_and_explicitly_releasable(native):
    conn, album, _, _ = native
    edition = conn.execute("INSERT INTO lib2_release_editions(release_group_id,is_default,spotify_id) VALUES(?,1,'selected')", (album,)).lastrowid
    set_field_override(conn, entity_type="release_edition", entity_id=edition, field_name="release_date", value="1999-01-01")
    conn.commit()
    plan = catalogue_refresh.refresh_preview(conn, album)
    date_change = next(change for change in plan["album"]["changes"] if change["field"] == "release_date")
    assert date_change["current"] == "1999-01-01"
    assert date_change["manual"]
    catalogue_refresh.apply_refresh(conn, album)
    assert "release_date" in get_field_overrides(conn, entity_type="release_edition", entity_id=edition)
    catalogue_refresh.apply_refresh(conn, album, overwrite_manual=True)
    assert "release_date" not in get_field_overrides(conn, entity_type="release_edition", entity_id=edition)


def test_catalogue_refresh_never_changes_file_ownership_or_monitoring(native):
    conn, album, track, _ = native
    conn.execute("INSERT INTO lib2_track_files(track_id,path,owner_profile_id) VALUES(?,'/synthetic/song.flac',1)", (track,))
    conn.execute("UPDATE lib2_tracks SET monitored=1 WHERE id=?", (track,))
    conn.commit()
    before = [tuple(row) for row in conn.execute("SELECT * FROM lib2_track_files")]
    assert catalogue_refresh.apply_refresh(conn, album)["tracks_updated"] == 1
    assert [tuple(row) for row in conn.execute("SELECT * FROM lib2_track_files")] == before
    assert conn.execute("SELECT monitored FROM lib2_tracks WHERE id=?", (track,)).fetchone()[0] == 1
