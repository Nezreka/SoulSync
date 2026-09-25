"""Edition-aware bundle matching tests (audit §13.4 steps 7-8).

Covers the phase-5 test gate cases: multi-disc albums, bonus tracks,
wrong editions and missing tracks — every ambiguity must end in
needs_review, never in a silent partial import.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.acquisition import ensure_acquisition_schema
from core.acquisition.bundle_matching import (
    AUTO_IMPORT_MIN_CONFIDENCE,
    CONFIDENCE_POSITION_AND_TITLE,
    CONFIDENCE_POSITION_DURATION,
    CONFIDENCE_POSITION_ONLY,
    CONFIDENCE_TITLE_EXACT,
    CONFIDENCE_TITLE_EXACT_DURATION,
    DECISION_IMPORT_READY,
    DECISION_NEEDS_REVIEW,
    ExpectedTrack,
    build_manual_matches,
    load_expected_tracks,
    match_bundle,
    normalize_title,
)
from core.acquisition.history import list_history_events
from core.acquisition.imports import (
    get_import,
    record_inventory_result,
    record_matching_result,
)

from tests.acquisition.test_bundle_inventory import _pending_import  # noqa: F401


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    ensure_acquisition_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def catalog_conn():
    """Editions/recordings tables without FK parents (read-only queries)."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    from core.library2.editions import (
        LIB2_RECORDINGS_DDL,
        LIB2_RELEASE_EDITIONS_DDL,
        LIB2_RELEASE_TRACKS_DDL,
    )
    connection.execute(LIB2_RELEASE_EDITIONS_DDL)
    connection.execute(LIB2_RECORDINGS_DDL)
    connection.execute(LIB2_RELEASE_TRACKS_DDL)
    yield connection
    connection.close()


def _seed_edition(
    conn,
    *,
    release_group_id=77,
    is_default=1,
    tracks=(),
):
    cursor = conn.execute(
        "INSERT INTO lib2_release_editions(release_group_id, is_default) "
        "VALUES(?,?)",
        (release_group_id, is_default),
    )
    edition_id = cursor.lastrowid
    for disc, number, title, duration_ms, track_id, override in tracks:
        rec = conn.execute(
            "INSERT INTO lib2_recordings(title, duration) VALUES(?,?)",
            (title, duration_ms),
        ).lastrowid
        conn.execute(
            """INSERT INTO lib2_release_tracks(
                   release_edition_id, recording_id, track_id,
                   disc_number, track_number, title_override)
               VALUES(?,?,?,?,?,?)""",
            (edition_id, rec, track_id, disc, number, override),
        )
    return edition_id


def _expected(
    title,
    *,
    disc=1,
    number=None,
    duration=None,
    release_track_id=None,
    recording_id=None,
    track_id=None,
):
    return ExpectedTrack(
        release_track_id=release_track_id,
        recording_id=recording_id,
        track_id=track_id,
        disc_number=disc,
        track_number=number,
        title=title,
        duration_seconds=duration,
    )


def _file(
    path,
    *,
    title=None,
    track=None,
    disc=None,
    duration=None,
):
    return {
        "relative_path": path,
        "title": title,
        "track_number": track,
        "disc_number": disc,
        "duration_seconds": duration,
        "tags_available": title is not None,
    }


def test_manual_matches_validate_bundle_and_expected_tracks(conn):
    pending, _request, _candidate = _pending_import(conn)
    record_inventory_result(
        conn,
        pending.id,
        [{"relative_path": "Disc 1/01.flac"}],
        resolved_path="/local",
    )
    record_matching_result(
        conn,
        pending.id,
        [],
        [{"code": "ambiguous_match"}],
        decision="needs_review",
    )
    record = get_import(conn, pending.id)
    expected = [_expected("Song", number=1, track_id=42)]

    matches = build_manual_matches(
        record,
        expected,
        [{"relative_path": "Disc 1/01.flac", "track_id": 42}],
    )

    assert matches[0]["strategy"] == "manual"
    assert matches[0]["track_id"] == 42
    with pytest.raises(ValueError, match="outside the bundle"):
        build_manual_matches(
            record,
            expected,
            [{"relative_path": "other.flac", "track_id": 42}],
        )


def test_manual_matches_reject_partial_bundle_resolution(conn):
    pending, _request, _candidate = _pending_import(conn)
    record_inventory_result(
        conn,
        pending.id,
        [
            {"relative_path": "01.flac"},
            {"relative_path": "02.flac"},
        ],
        resolved_path="/local",
    )
    record_matching_result(
        conn,
        pending.id,
        [],
        [{"code": "ambiguous_match"}],
        decision="needs_review",
    )
    record = get_import(conn, pending.id)
    expected = [
        _expected("One", number=1, track_id=41),
        _expected("Two", number=2, track_id=42),
    ]

    with pytest.raises(ValueError, match="every file"):
        build_manual_matches(
            record,
            expected,
            [{"relative_path": "01.flac", "track_id": 41}],
        )


# ---------------------------------------------------------------------------
# normalize_title
# ---------------------------------------------------------------------------


def test_normalize_strips_feat_and_punctuation_but_keeps_versions():
    assert normalize_title("Song (feat. Guest)") == "song"
    assert normalize_title("Song feat. Guest") == "song"
    assert normalize_title("Song!!!") == "song"
    assert normalize_title("Söng") == normalize_title("söng")
    # Live/Remaster markers are identity (ADR-04) and must survive.
    assert normalize_title("Song (Live)") == "song live"
    assert normalize_title("Song (2011 Remaster)") == "song 2011 remaster"


# ---------------------------------------------------------------------------
# load_expected_tracks
# ---------------------------------------------------------------------------


def test_load_release_edition_tracks_ordered_with_overrides(catalog_conn):
    edition = _seed_edition(catalog_conn, tracks=[
        (2, 1, "Disc Two Opener", 200_000, None, None),
        (1, 2, "Second", None, 42, None),
        (1, 1, "First", 180_000, None, "First (Album Mix)"),
    ])
    expected = load_expected_tracks(catalog_conn, "release_edition", edition)
    assert [(t.disc_number, t.track_number) for t in expected] == [
        (1, 1), (1, 2), (2, 1)]
    assert expected[0].title == "First (Album Mix)"
    assert expected[0].duration_seconds == 180.0
    assert expected[1].track_id == 42
    assert expected[1].duration_seconds is None
    assert expected[0].key.startswith("release_track:")


def test_load_release_group_uses_default_edition(catalog_conn):
    _seed_edition(
        catalog_conn, release_group_id=5, is_default=0,
        tracks=[(1, 1, "Deluxe Only", None, None, None)])
    _seed_edition(
        catalog_conn, release_group_id=5, is_default=1,
        tracks=[(1, 1, "Standard", None, None, None)])
    expected = load_expected_tracks(catalog_conn, "release_group", 5)
    assert [t.title for t in expected] == ["Standard"]


def test_load_release_group_without_default_edition_is_empty(catalog_conn):
    assert load_expected_tracks(catalog_conn, "release_group", 404) == ()


def test_load_recording_scope(catalog_conn):
    edition = _seed_edition(catalog_conn, tracks=[
        (1, 1, "The Song", 210_000, 9, None)])
    recording_id = catalog_conn.execute(
        "SELECT recording_id FROM lib2_release_tracks "
        "WHERE release_edition_id=?", (edition,),
    ).fetchone()[0]
    expected = load_expected_tracks(catalog_conn, "recording", recording_id)
    assert len(expected) == 1
    assert expected[0].title == "The Song"
    assert expected[0].track_id == 9
    assert expected[0].duration_seconds == 210.0
    assert expected[0].key == f"recording:{recording_id}"


def test_load_upgrade_scope_delegates_by_entity_type(catalog_conn):
    edition = _seed_edition(catalog_conn, tracks=[
        (1, 1, "Upgraded", None, None, None)])
    expected = load_expected_tracks(
        catalog_conn, "upgrade", edition,
        search_options={"entity_type": "release_edition"})
    assert [t.title for t in expected] == ["Upgraded"]
    assert load_expected_tracks(catalog_conn, "upgrade", edition) == ()


def test_load_artist_missing_scope_has_no_tracklist(catalog_conn):
    assert load_expected_tracks(catalog_conn, "artist_missing", 1) == ()


# ---------------------------------------------------------------------------
# match_bundle — auto-import paths
# ---------------------------------------------------------------------------


def test_fully_tagged_album_is_import_ready():
    expected = [
        _expected("Intro", number=1, duration=61.0, release_track_id=1),
        _expected("Song Two", number=2, duration=200.0, release_track_id=2),
    ]
    files = [
        _file("01 - Intro.flac", title="Intro", track=1, duration=61.4),
        _file("02 - Song Two.flac", title="Song Two (feat. Guest)",
              track=2, duration=199.0),
    ]
    report = match_bundle(expected, files)
    assert report.decision == DECISION_IMPORT_READY
    assert [m.confidence for m in report.matches] == [
        CONFIDENCE_POSITION_AND_TITLE, CONFIDENCE_POSITION_AND_TITLE]
    assert report.rejections == ()
    payload = report.matches_payload()
    assert payload[0]["relative_path"] == "01 - Intro.flac"
    assert payload[0]["expected_key"] == "release_track:1"


def test_multi_disc_bundle_matches_disc_from_folder_names():
    expected = [
        _expected("One", disc=1, number=1, release_track_id=1),
        _expected("Two", disc=2, number=1, release_track_id=2),
    ]
    files = [
        _file("CD1/01 - One.flac", title="One", track=1),
        _file("CD2/01 - Two.flac", title="Two", track=1),
    ]
    report = match_bundle(expected, files)
    assert report.decision == DECISION_IMPORT_READY
    assert {m.expected.disc_number for m in report.matches} == {1, 2}


def test_untagged_files_match_via_filename_and_duration():
    expected = [
        _expected("Opening", number=1, duration=100.0, release_track_id=1),
        _expected("Closing", number=2, duration=150.0, release_track_id=2),
    ]
    files = [
        _file("01 - Opening.flac", duration=101.0),
        _file("02 - Closing.flac", duration=149.5),
    ]
    report = match_bundle(expected, files)
    assert report.decision == DECISION_IMPORT_READY
    assert [m.strategy for m in report.matches] == [
        "position_and_title", "position_and_title"]


def test_numeric_only_filenames_need_duration_confirmation():
    expected = [
        _expected("Opening", number=1, duration=100.0, release_track_id=1),
    ]
    ready = match_bundle(expected, [_file("01.flac", duration=100.5)])
    assert ready.decision == DECISION_IMPORT_READY
    assert ready.matches[0].strategy == "position_duration"
    assert ready.matches[0].confidence == CONFIDENCE_POSITION_DURATION

    unverified = match_bundle(expected, [_file("01.flac")])
    assert unverified.decision == DECISION_NEEDS_REVIEW
    assert unverified.matches[0].confidence == CONFIDENCE_POSITION_ONLY
    assert unverified.matches[0].confidence < AUTO_IMPORT_MIN_CONFIDENCE
    codes = {item["code"] for item in unverified.rejections}
    assert codes == {"low_confidence"}


def test_recording_scope_title_with_duration_confirmation():
    expected = [_expected("The Song", duration=210.0, recording_id=7)]
    ready = match_bundle(
        expected, [_file("Artist - The Song.flac", title="The Song",
                         duration=209.0)])
    assert ready.decision == DECISION_IMPORT_READY
    assert ready.matches[0].strategy == "title_exact_duration"
    assert ready.matches[0].confidence == CONFIDENCE_TITLE_EXACT_DURATION

    bare = match_bundle(
        expected, [_file("Artist - The Song.flac", title="The Song")])
    assert bare.decision == DECISION_NEEDS_REVIEW
    assert bare.matches[0].confidence == CONFIDENCE_TITLE_EXACT


# ---------------------------------------------------------------------------
# match_bundle — review paths (phase-5 test gate)
# ---------------------------------------------------------------------------


def test_bonus_tracks_force_review_but_keep_matches():
    expected = [_expected("Only Track", number=1, release_track_id=1)]
    files = [
        _file("01 - Only Track.flac", title="Only Track", track=1),
        _file("02 - Bonus.flac", title="Bonus Cut", track=2),
    ]
    report = match_bundle(expected, files)
    assert report.decision == DECISION_NEEDS_REVIEW
    assert len(report.matches) == 1
    assert report.rejections == (
        {
            "code": "unmatched_file",
            "relative_path": "02 - Bonus.flac",
            "title": "Bonus Cut",
        },
    )


def test_missing_expected_track_forces_review():
    expected = [
        _expected("Here", number=1, release_track_id=1),
        _expected("Gone", number=2, release_track_id=2),
    ]
    report = match_bundle(
        expected, [_file("01 - Here.flac", title="Here", track=1)])
    assert report.decision == DECISION_NEEDS_REVIEW
    codes = [item["code"] for item in report.rejections]
    assert codes == ["missing_expected_track"]
    assert report.rejections[0]["expected_title"] == "Gone"


def test_wrong_edition_titles_do_not_match_by_position():
    expected = [
        _expected("Studio Song", number=1, duration=180.0,
                  release_track_id=1),
    ]
    files = [
        _file("01 - Completely Different.flac",
              title="Completely Different", track=1, duration=180.0),
    ]
    report = match_bundle(expected, files)
    assert report.decision == DECISION_NEEDS_REVIEW
    codes = {item["code"] for item in report.rejections}
    assert codes == {"missing_expected_track", "unmatched_file"}
    assert report.matches == ()


def test_live_version_never_auto_matches_studio_track():
    expected = [_expected("Song", number=1, release_track_id=1)]
    files = [_file("01 - Song (Live).flac", title="Song (Live)", track=1)]
    report = match_bundle(expected, files)
    assert report.decision == DECISION_NEEDS_REVIEW
    assert report.matches == ()


def test_multi_disc_bundle_without_disc_info_is_ambiguous():
    expected = [
        _expected("A", disc=1, number=1, release_track_id=1),
        _expected("B", disc=2, number=1, release_track_id=2),
    ]
    files = [_file("01 - Unknown.flac", track=1)]
    report = match_bundle(expected, files)
    assert report.decision == DECISION_NEEDS_REVIEW
    codes = {item["code"] for item in report.rejections}
    assert "ambiguous_position" in codes


def test_severe_duration_mismatch_blocks_auto_import():
    expected = [
        _expected("Song", number=1, duration=180.0, release_track_id=1)]
    files = [
        _file("01 - Song.flac", title="Song", track=1, duration=245.0)]
    report = match_bundle(expected, files)
    assert report.decision == DECISION_NEEDS_REVIEW
    match = report.matches[0]
    assert match.confidence == round(
        CONFIDENCE_POSITION_AND_TITLE - 0.3, 2)
    assert any(w.startswith("duration_mismatch") for w in match.warnings)


def test_ambiguous_similarity_is_reported_not_guessed():
    expected = [
        _expected("Interlude A", number=None, release_track_id=1),
        _expected("Interlude B", number=None, release_track_id=2),
    ]
    files = [_file("Interlude C.flac", title="Interlude C")]
    report = match_bundle(expected, files)
    assert report.decision == DECISION_NEEDS_REVIEW
    codes = {item["code"] for item in report.rejections}
    assert "ambiguous_title" in codes


def test_empty_tracklist_needs_review():
    report = match_bundle((), [_file("01 - Song.flac", title="Song")])
    assert report.decision == DECISION_NEEDS_REVIEW
    assert report.rejections == ({"code": "no_expected_tracklist"},)


# ---------------------------------------------------------------------------
# record_matching_result persistence
# ---------------------------------------------------------------------------


def _matching_import(conn, download_id="dl-m1"):
    pending, request, candidate = _pending_import(conn, download_id=download_id)
    record_inventory_result(
        conn,
        pending.id,
        [{"relative_path": "01 - Song.flac", "track_number": 1}],
        resolved_path="/local/album",
    )
    return pending, request, candidate


def test_record_matching_import_ready_enters_importing(conn):
    pending, request, _candidate = _matching_import(conn)
    matches = [{
        "expected_key": "release_track:1",
        "relative_path": "01 - Song.flac",
        "confidence": 1.0,
        "strategy": "position_and_title",
        "warnings": [],
    }]
    updated = record_matching_result(
        conn, pending.id, matches, [], decision="import_ready")
    assert updated.status == "importing"
    assert updated.matches == (matches[0],)
    events = [
        e.event_type for e in list_history_events(conn, request_id=request.id)]
    assert "import_needs_review" not in events


def test_record_matching_needs_review_records_event(conn):
    pending, request, _candidate = _matching_import(conn, download_id="dl-m2")
    rejections = [
        {"code": "unmatched_file", "relative_path": "02 - Bonus.flac"},
        {"code": "unmatched_file", "relative_path": "03 - Bonus.flac"},
        {"code": "missing_expected_track", "expected_key": "release_track:9"},
    ]
    updated = record_matching_result(
        conn, pending.id, [], rejections, decision="needs_review")
    assert updated.status == "needs_review"
    assert len(updated.rejections) == 3
    event = [
        e for e in list_history_events(conn, request_id=request.id)
        if e.event_type == "import_needs_review"
    ][0]
    assert event.reason_code == "missing_expected_track"
    assert event.payload["rejection_codes"] == {
        "unmatched_file": 2, "missing_expected_track": 1}


def test_record_matching_validates_decision_and_state(conn):
    pending, _request, _candidate = _matching_import(conn, download_id="dl-m3")
    with pytest.raises(ValueError, match="import_ready|needs_review"):
        record_matching_result(conn, pending.id, [], [], decision="yolo")
    with pytest.raises(ValueError, match="at least one track match"):
        record_matching_result(conn, pending.id, [], [], decision="import_ready")
    with pytest.raises(ValueError, match="cannot carry rejections"):
        record_matching_result(
            conn, pending.id,
            [{"expected_key": "release_track:1"}],
            [{"code": "unmatched_file"}],
            decision="import_ready")

    fresh, _request2, _candidate2 = _pending_import(conn, download_id="dl-m4")
    with pytest.raises(ValueError, match="matching import"):
        record_matching_result(
            conn, fresh.id,
            [{"expected_key": "release_track:1"}], [],
            decision="import_ready")
    assert get_import(conn, fresh.id).status == "pending"


def test_needs_review_can_rematch_after_new_inventory(conn):
    pending, _request, _candidate = _matching_import(conn, download_id="dl-m5")
    record_matching_result(
        conn, pending.id, [],
        [{"code": "unmatched_file", "relative_path": "x.flac"}],
        decision="needs_review")
    # A corrected mapping produces a fresh inventory, which re-enters
    # matching and can then become import_ready.
    record_inventory_result(
        conn, pending.id,
        [{"relative_path": "01 - Song.flac", "track_number": 1}],
        resolved_path="/local/fixed")
    updated = record_matching_result(
        conn, pending.id,
        [{"expected_key": "release_track:1",
          "relative_path": "01 - Song.flac"}],
        [], decision="import_ready")
    assert updated.status == "importing"
