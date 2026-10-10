"""Display/write corrections stay separate from provider lookup identity."""

import pytest

from core.library2.metadata_overrides import set_field_override
from core.library2.maintenance_subjects import active_album_subjects, active_file_subjects
from core.library2.lyrics import _track_lyrics_context
from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import RowDb, row_conn, track


@pytest.fixture
def catalogue(tmp_path):
    path = str(tmp_path / "contexts.db")
    conn = row_conn(path)
    ensure_library_v2_schema(conn)
    tid = track(conn, "Provider Artist", "Provider Album", "Provider Song", duration=210000,
                spotify_id="provider-track")
    album_id = conn.execute("SELECT album_id FROM lib2_tracks WHERE id=?", (tid,)).fetchone()[0]
    aid = conn.execute("SELECT primary_artist_id FROM lib2_albums WHERE id=?", (album_id,)).fetchone()[0]
    for entity, eid, field, value in [("track", tid, "title", "My Song"),
                                     ("release_group", album_id, "title", "My Album"),
                                     ("artist", aid, "name", "My Artist")]:
        set_field_override(conn, entity_type=entity, entity_id=eid, field_name=field, value=value)
    conn.commit()
    yield conn, RowDb(path), tid, album_id, aid
    conn.close()


def test_display_and_write_use_manual_values_while_lookup_uses_provider_identity(catalogue):
    from core.library2.metadata_context import track_metadata_context

    conn, _, tid, _, _ = catalogue
    display = track_metadata_context(conn, tid, purpose="display")
    write = track_metadata_context(conn, tid, purpose="write")
    lookup = track_metadata_context(conn, tid, purpose="provider_lookup")
    assert (display["title"], display["album_title"], display["artist_name"]) == ("My Song", "My Album", "My Artist")
    assert (write["title"], write["album_title"], write["artist_name"]) == ("My Song", "My Album", "My Artist")
    assert write["_manual_fields"]["title"] == "Provider Song"
    assert (lookup["title"], lookup["album_title"], lookup["artist_name"]) == ("Provider Song", "Provider Album", "Provider Artist")
    assert lookup["track_source_ids"]["spotify"] == "provider-track"
    assert conn.execute("SELECT title FROM lib2_tracks WHERE id=?", (tid,)).fetchone()[0] == "Provider Song"


def test_lyrics_lookup_keeps_provider_titles_and_exposes_manual_display(catalogue):
    conn, _, tid, _, _ = catalogue
    lookup = _track_lyrics_context(conn, tid)
    display = _track_lyrics_context(conn, tid, purpose="display")
    assert (lookup["title"], lookup["album_title"], lookup["artist"]) == ("Provider Song", "Provider Album", "Provider Artist")
    assert display["title"] == "My Song"
    assert display["artist"] == "My Artist"
    assert display["duration_seconds"] == 210


def test_maintenance_subjects_offer_explicit_display_lookup_and_write_contexts(catalogue):
    _, db, tid, album_id, _ = catalogue
    subjects = active_file_subjects(db, None, purpose="display")
    assert len(subjects) == 1
    subject = subjects[0]
    assert (subject["title"], subject["album_title"], subject["artist_name"]) == ("My Song", "My Album", "My Artist")
    assert subject["provider_lookup_metadata"]["title"] == "Provider Song"
    write_subject = active_file_subjects(db, None, purpose="write")[0]
    assert write_subject["write_metadata"]["title"] == "My Song"
    assert write_subject["_manual_fields"]["title"] == "Provider Song"
    assert subject["track_id"] == tid and subject["album_id"] == album_id
    albums = active_album_subjects(db, None, purpose="display")
    assert albums[0]["title"] == "My Album"
    assert albums[0]["artist_name"] == "My Artist"
    assert albums[0]["provider_lookup_metadata"]["title"] == "Provider Album"


def test_context_rejects_unknown_purpose(catalogue):
    from core.library2.metadata_context import track_metadata_context

    conn, _, tid, _, _ = catalogue
    with pytest.raises(ValueError, match="purpose"):
        track_metadata_context(conn, tid, purpose="implicit")


def test_lookup_preserves_native_positions_despite_stale_or_missing_edition_slots(catalogue):
    from core.library2.metadata_context import track_metadata_context

    conn, _, tid, album_id, _ = catalogue
    conn.execute("UPDATE lib2_tracks SET track_number=9,disc_number=2 WHERE id=?", (tid,))
    edition = conn.execute("INSERT INTO lib2_release_editions(release_group_id,is_default,title,spotify_id) VALUES(?,1,'Release Title','edition-id')",
                           (album_id,)).lastrowid
    recording = conn.execute("INSERT INTO lib2_recordings(title) VALUES('Recording')").lastrowid
    slot = conn.execute("INSERT INTO lib2_release_tracks(release_edition_id,recording_id,track_id,track_number,disc_number) VALUES(?,?,?,1,1)",
                        (edition, recording, tid)).lastrowid
    lookup = track_metadata_context(conn, tid, purpose="provider_lookup")
    assert (lookup["track_number"], lookup["disc_number"]) == (9, 2)
    assert lookup["album_source_ids"]["spotify"] == "edition-id"
    conn.execute("UPDATE lib2_release_tracks SET track_number=NULL,disc_number=NULL WHERE id=?", (slot,))
    lookup = track_metadata_context(conn, tid, purpose="provider_lookup")
    display = track_metadata_context(conn, tid, purpose="display")
    assert (lookup["track_number"], lookup["disc_number"]) == (9, 2)
    assert (display["track_number"], display["disc_number"]) == (9, 2)


def test_default_album_lookup_preserves_pin_when_edition_binding_is_stale(catalogue):
    from core.library2.metadata_context import album_metadata_context

    conn, _, _, album_id, _ = catalogue
    conn.execute("UPDATE lib2_albums SET spotify_id='base-release',canonical_source='spotify',canonical_album_id='pinned-release',canonical_locked=1 WHERE id=?", (album_id,))
    conn.execute("INSERT INTO lib2_release_editions(release_group_id,is_default,spotify_id) VALUES(?,1,'stale-release')", (album_id,))
    context = album_metadata_context(conn, album_id, purpose="provider_lookup")
    assert context["album_source_ids"]["spotify"] == "pinned-release"
    assert context["canonical_locked"] is True


def test_credit_name_and_provider_id_belong_to_the_same_artist(catalogue):
    from core.library2.metadata_context import track_metadata_context

    conn, _, tid, _, _ = catalogue
    other = conn.execute("INSERT INTO lib2_artists(name,spotify_id) VALUES('Singer','singer-id')").lastrowid
    conn.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (tid,))
    conn.execute("INSERT INTO lib2_track_artists(track_id,artist_id,role,position) VALUES(?,?,'primary',0)", (tid, other))
    set_field_override(conn, entity_type="artist", entity_id=other, field_name="name", value="My Singer")
    lookup = track_metadata_context(conn, tid, purpose="provider_lookup")
    display = track_metadata_context(conn, tid, purpose="display")
    assert lookup["artist_name"] == "Singer"
    assert display["artist_name"] == "My Singer"
    assert lookup["artist_source_ids"]["spotify"] == "singer-id"
    assert lookup["artist_id"] == other
    assert lookup["album_artist_name"] == "Provider Artist"


def test_maintenance_display_projects_prefixed_manual_fields(catalogue):
    conn, db, _, album_id, aid = catalogue
    set_field_override(conn, entity_type="release_group", entity_id=album_id, field_name="year", value=2024)
    set_field_override(conn, entity_type="release_group", entity_id=album_id, field_name="genres", value=["My Genre"])
    set_field_override(conn, entity_type="artist", entity_id=aid, field_name="summary", value="My Summary")
    conn.commit()
    file_subject = active_file_subjects(db, None, purpose="display")[0]
    album_subject = active_album_subjects(db, None, purpose="display")[0]
    assert file_subject["album_year"] == album_subject["album_year"] == 2024
    assert file_subject["album_genres"] == album_subject["album_genres"] == ["My Genre"]
    assert file_subject["artist_summary"] == "My Summary"


def test_bulk_lookup_batches_track_positions_and_credits(catalogue):
    from core.library2.metadata_context import track_metadata_contexts
    conn, _, _, _, _ = catalogue
    ids = [track(conn, 'Provider Artist', 'Provider Album', f'Song {number}') for number in range(80)]
    statements = []
    conn.set_trace_callback(statements.append)
    try:
        contexts = track_metadata_contexts(conn, ids, purpose='provider_lookup')
    finally:
        conn.set_trace_callback(None)
    assert len(contexts) == len(ids)
    assert len(statements) <= 12, f'{len(statements)} queries for one album lookup batch'
