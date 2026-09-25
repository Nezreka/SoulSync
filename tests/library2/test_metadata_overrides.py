"""ADR-06 field-level override storage and projection."""

from __future__ import annotations

import pytest

from core.library2.metadata_overrides import (
    MetadataOverrideError,
    clear_field_override,
    get_field_overrides,
    project_metadata,
    set_field_override,
)


def _album_id(conn):
    return conn.execute(
        "SELECT id FROM lib2_albums WHERE title='Views'"
    ).fetchone()[0]


def _track_id(conn):
    return conn.execute(
        "SELECT id FROM lib2_tracks WHERE title='One Dance'"
    ).fetchone()[0]


def _artist_id(conn):
    return conn.execute(
        "SELECT id FROM lib2_artists WHERE name='Drake'"
    ).fetchone()[0]


def test_override_is_separate_and_wins_read_projection(imported_conn):
    album_id = _album_id(imported_conn)
    override = set_field_override(
        imported_conn,
        entity_type="release_group",
        entity_id=album_id,
        field_name="title",
        value="Views (User Corrected)",
        reason="manual metadata edit",
    )
    assert override.value == "Views (User Corrected)"
    assert imported_conn.execute(
        "SELECT title FROM lib2_albums WHERE id=?", (album_id,)
    ).fetchone()[0] == "Views"

    effective, values = project_metadata(
        imported_conn,
        entity_type="release_group",
        entity_id=album_id,
        provider_fields={"title": "Provider Refresh Title", "year": 2016},
    )
    assert effective == {"title": "Views (User Corrected)", "year": 2016}
    assert values == {"title": "Views (User Corrected)"}


def test_provider_refresh_and_clear_reveal_latest_baseline(imported_conn):
    album_id = _album_id(imported_conn)
    set_field_override(
        imported_conn,
        entity_type="release_group",
        entity_id=album_id,
        field_name="year",
        value=2017,
    )
    imported_conn.execute(
        "UPDATE lib2_albums SET year=2020 WHERE id=?", (album_id,)
    )
    effective, _ = project_metadata(
        imported_conn,
        entity_type="release_group",
        entity_id=album_id,
        provider_fields={"year": 2020},
    )
    assert effective["year"] == 2017
    assert clear_field_override(
        imported_conn,
        entity_type="release_group",
        entity_id=album_id,
        field_name="year",
    )
    effective, values = project_metadata(
        imported_conn,
        entity_type="release_group",
        entity_id=album_id,
        provider_fields={"year": 2020},
    )
    assert effective["year"] == 2020 and values == {}


def test_typed_values_and_upsert(imported_conn):
    album_id = _album_id(imported_conn)
    set_field_override(
        imported_conn,
        entity_type="release_group",
        entity_id=album_id,
        field_name="genres",
        value=["Rap", " Pop "],
    )
    set_field_override(
        imported_conn,
        entity_type="release_group",
        entity_id=album_id,
        field_name="genres",
        value=["Hip-Hop"],
    )
    overrides = get_field_overrides(
        imported_conn, entity_type="release_group", entity_id=album_id
    )
    assert overrides["genres"].value == ["Hip-Hop"]
    assert imported_conn.execute(
        """SELECT COUNT(*) FROM lib2_metadata_overrides
            WHERE entity_type='release_group' AND entity_id=?""",
        (album_id,),
    ).fetchone()[0] == 1


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("unknown", "x", "cannot be overridden"),
        ("year", -1, "between 0 and 9999"),
        ("genres", "not-a-list", "list of strings"),
        ("album_type", "mixtape", "album_type must be one of"),
        ("explicit", "yes", "must be true/false"),
    ],
)
def test_invalid_override_values_are_rejected(imported_conn, field, value, message):
    with pytest.raises(MetadataOverrideError, match=message):
        set_field_override(
            imported_conn,
            entity_type="release_group",
            entity_id=_album_id(imported_conn),
            field_name=field,
            value=value,
        )


def test_rich_metadata_edit_fields_artist_album_track(imported_conn):
    """§48: style/mood/label (artist), +explicit (album), +bpm/explicit
    (track) are now overridable — previously only artist had base columns,
    and none of the three had these fields in the override whitelist."""
    artist_id = _artist_id(imported_conn)
    set_field_override(
        imported_conn, entity_type="artist", entity_id=artist_id,
        field_name="style", value="Hip Hop",
    )
    set_field_override(
        imported_conn, entity_type="artist", entity_id=artist_id,
        field_name="mood", value="Energetic",
    )
    set_field_override(
        imported_conn, entity_type="artist", entity_id=artist_id,
        field_name="label", value="OVO Sound",
    )
    artist_overrides = get_field_overrides(
        imported_conn, entity_type="artist", entity_id=artist_id
    )
    assert artist_overrides["style"].value == "Hip Hop"
    assert artist_overrides["mood"].value == "Energetic"
    assert artist_overrides["label"].value == "OVO Sound"

    album_id = _album_id(imported_conn)
    set_field_override(
        imported_conn, entity_type="release_group", entity_id=album_id,
        field_name="explicit", value=True,
    )
    set_field_override(
        imported_conn, entity_type="release_group", entity_id=album_id,
        field_name="style", value="Rap",
    )
    album_overrides = get_field_overrides(
        imported_conn, entity_type="release_group", entity_id=album_id
    )
    assert album_overrides["explicit"].value == 1
    assert album_overrides["style"].value == "Rap"

    track_id = _track_id(imported_conn)
    set_field_override(
        imported_conn, entity_type="track", entity_id=track_id,
        field_name="bpm", value=104.5,
    )
    set_field_override(
        imported_conn, entity_type="track", entity_id=track_id,
        field_name="explicit", value=False,
    )
    track_overrides = get_field_overrides(
        imported_conn, entity_type="track", entity_id=track_id
    )
    assert track_overrides["bpm"].value == 104.5
    assert track_overrides["explicit"].value == 0


def test_bpm_override_rejects_non_numeric(imported_conn):
    with pytest.raises(MetadataOverrideError, match="must be a number"):
        set_field_override(
            imported_conn,
            entity_type="track",
            entity_id=_track_id(imported_conn),
            field_name="bpm",
            value="fast",
        )


def test_non_admin_and_missing_entity_are_rejected(imported_conn):
    with pytest.raises(MetadataOverrideError, match="admin-only") as exc:
        set_field_override(
            imported_conn,
            entity_type="artist",
            entity_id=1,
            field_name="name",
            value="Nope",
            profile_id=7,
        )
    assert exc.value.status == 403
    with pytest.raises(MetadataOverrideError, match="not found") as exc:
        set_field_override(
            imported_conn,
            entity_type="track",
            entity_id=999999,
            field_name="title",
            value="Missing",
        )
    assert exc.value.status == 404


def test_entity_delete_cleans_current_overrides(imported_conn):
    album_id = _album_id(imported_conn)
    set_field_override(
        imported_conn,
        entity_type="release_group",
        entity_id=album_id,
        field_name="title",
        value="Temporary",
    )
    imported_conn.execute("DELETE FROM lib2_albums WHERE id=?", (album_id,))
    assert imported_conn.execute(
        """SELECT COUNT(*) FROM lib2_metadata_overrides
            WHERE entity_type='release_group' AND entity_id=?""",
        (album_id,),
    ).fetchone()[0] == 0
