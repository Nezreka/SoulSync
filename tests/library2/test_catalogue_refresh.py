"""Provider → catalogue: the half of Re-tag that Library v2 was missing.

The legacy job pulled a fresh tracklist from the album's matched source and
wrote it into files. Its lib2 replacement only ever went the other way —
catalogue → file tags — so nothing on this branch could get a corrected title
INTO the catalogue in the first place. A manual match set an id and stopped
there: `enrich_native_entity_for_service` writes provider ids and artwork, not
titles or track numbers.

That gap is what made "match the right release, then reorganize" fail to
change anything. The chain has three steps and each one is visible:

    Manual match   → identity
    Refresh        → provider → catalogue     (this module)
    Re-tag         → catalogue → file tags

Rules that carry over from the tag side, because they are the same rule:

* a field a person set by hand is REPORTED, never silently replaced;
* an empty provider value is not a proposal — a blank must not erase a value
  the library already has;
* the preview computes, nothing is written until it is applied.
"""

from __future__ import annotations

import pytest

from core.library2 import catalogue_refresh


def _seed(conn, *, album_title="Views", year=2016, spotify_id="sp-alb",
          tracks=(("One Dance", 1, 1), ("Hotline Bling", 2, 1))):
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Drake')")
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, year, album_type, spotify_id)"
        " VALUES(?,?,?,'album',?)", (artist_id, album_title, year, spotify_id))
    album_id = cur.lastrowid
    track_ids = []
    for title, number, disc in tracks:
        cur.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number)"
            " VALUES(?,?,?,?)", (album_id, title, number, disc))
        track_ids.append(cur.lastrowid)
    conn.commit()
    return artist_id, album_id, track_ids


def _provider(monkeypatch, tracks, album=None):
    monkeypatch.setattr(
        "core.metadata.album_tracks.get_album_tracks_for_source",
        lambda _source, _id: tracks)
    monkeypatch.setattr(
        "core.metadata.album_tracks.get_album_for_source",
        lambda _source, _id, *a, **k: album or {})


def _changes(plan, track_id):
    row = next(t for t in plan["tracks"] if t["track_id"] == track_id)
    return {c["field"]: c for c in row["changes"]}


def test_a_corrected_title_at_the_source_is_proposed(imported_conn, monkeypatch):
    conn = imported_conn
    _, album_id, track_ids = _seed(conn)
    _provider(monkeypatch, [
        {"name": "One Dance (feat. Wizkid)", "track_number": 1, "disc_number": 1},
        {"name": "Hotline Bling", "track_number": 2, "disc_number": 1},
    ])

    plan = catalogue_refresh.refresh_preview(conn, album_id)

    assert plan["status"] == "planned"
    change = _changes(plan, track_ids[0])["title"]
    assert change["current"] == "One Dance"
    assert change["proposed"] == "One Dance (feat. Wizkid)"
    assert _changes(plan, track_ids[1]) == {}


def test_an_album_with_no_matched_source_says_so(imported_conn, monkeypatch):
    """Nothing to refresh FROM. Reported as its own outcome so the UI can send
    the user to the match dialog rather than showing an empty diff."""
    conn = imported_conn
    _, album_id, _ = _seed(conn, spotify_id=None)

    assert catalogue_refresh.refresh_preview(conn, album_id)["status"] == "no_source"


def test_an_empty_provider_value_is_not_a_proposal(imported_conn, monkeypatch):
    """A provider that returns a blank title has nothing to say about it. The
    library's value is not worse for the provider having lost it."""
    conn = imported_conn
    _, album_id, track_ids = _seed(conn)
    _provider(monkeypatch, [{"name": "", "track_number": 1, "disc_number": 1}])

    plan = catalogue_refresh.refresh_preview(conn, album_id)

    assert _changes(plan, track_ids[0]) == {}


def test_a_hand_set_title_is_reported_as_a_conflict_not_replaced(
        imported_conn, monkeypatch):
    from core.library2.metadata_overrides import set_field_override

    conn = imported_conn
    _, album_id, track_ids = _seed(conn)
    set_field_override(conn, entity_type="track", entity_id=track_ids[0],
                       field_name="title", value="One Dance (my edit)")
    conn.commit()
    _provider(monkeypatch, [
        {"name": "One Dance (feat. Wizkid)", "track_number": 1, "disc_number": 1}])

    plan = catalogue_refresh.refresh_preview(conn, album_id)
    change = _changes(plan, track_ids[0])["title"]

    assert change["manual"] is True
    assert change["current"] == "One Dance (my edit)"
    assert change["proposed"] == "One Dance (feat. Wizkid)"
    assert plan["has_manual_conflict"] is True


def test_a_track_the_source_does_not_have_is_left_alone(imported_conn, monkeypatch):
    conn = imported_conn
    _, album_id, track_ids = _seed(conn)
    _provider(monkeypatch, [{"name": "One Dance", "track_number": 1, "disc_number": 1}])

    plan = catalogue_refresh.refresh_preview(conn, album_id)
    row = next(t for t in plan["tracks"] if t["track_id"] == track_ids[1])

    assert row["matched"] is False
    assert row["changes"] == []


def test_the_album_row_gets_its_own_proposals(imported_conn, monkeypatch):
    conn = imported_conn
    _, album_id, _ = _seed(conn, album_title="Views", year=2016)
    _provider(monkeypatch, [{"name": "One Dance", "track_number": 1, "disc_number": 1}],
              album={"name": "Views (Deluxe)", "release_date": "2016-04-29"})

    plan = catalogue_refresh.refresh_preview(conn, album_id)
    changes = {c["field"]: c for c in plan["album"]["changes"]}

    assert changes["title"]["proposed"] == "Views (Deluxe)"
    assert changes["release_date"]["proposed"] == "2016-04-29"


# ── applying ────────────────────────────────────────────────────────────────

def test_applying_writes_the_proposal_into_the_catalogue(imported_conn, monkeypatch):
    conn = imported_conn
    _, album_id, track_ids = _seed(conn)
    _provider(monkeypatch, [
        {"name": "One Dance (feat. Wizkid)", "track_number": 1, "disc_number": 1}])

    stats = catalogue_refresh.apply_refresh(conn, album_id)
    conn.commit()

    assert stats["tracks_updated"] == 1
    row = conn.execute("SELECT title FROM lib2_tracks WHERE id=?", (track_ids[0],)).fetchone()
    assert row["title"] == "One Dance (feat. Wizkid)"


def test_applying_keeps_a_hand_set_value_by_default(imported_conn, monkeypatch):
    from core.library2.metadata_overrides import set_field_override

    conn = imported_conn
    _, album_id, track_ids = _seed(conn)
    set_field_override(conn, entity_type="track", entity_id=track_ids[0],
                       field_name="title", value="One Dance (my edit)")
    conn.commit()
    _provider(monkeypatch, [
        {"name": "One Dance (feat. Wizkid)", "track_number": 1, "disc_number": 1}])

    stats = catalogue_refresh.apply_refresh(conn, album_id)
    conn.commit()

    assert stats["kept_manual"] == 1
    from core.library2.metadata_overrides import get_field_overrides
    assert get_field_overrides(conn, entity_type="track",
                               entity_id=track_ids[0])["title"].value == "One Dance (my edit)"


def test_releasing_the_field_clears_the_override_so_the_new_value_shows(
        imported_conn, monkeypatch):
    """Writing the base row alone would change nothing the user can see — the
    override still wins on every read. Releasing has to remove it."""
    from core.library2.metadata_overrides import get_field_overrides, set_field_override

    conn = imported_conn
    _, album_id, track_ids = _seed(conn)
    set_field_override(conn, entity_type="track", entity_id=track_ids[0],
                       field_name="title", value="One Dance (my edit)")
    conn.commit()
    _provider(monkeypatch, [
        {"name": "One Dance (feat. Wizkid)", "track_number": 1, "disc_number": 1}])

    catalogue_refresh.apply_refresh(conn, album_id, overwrite_manual=True)
    conn.commit()

    assert "title" not in get_field_overrides(conn, entity_type="track",
                                              entity_id=track_ids[0])
    row = conn.execute("SELECT title FROM lib2_tracks WHERE id=?", (track_ids[0],)).fetchone()
    assert row["title"] == "One Dance (feat. Wizkid)"


def test_applying_nothing_when_the_source_agrees(imported_conn, monkeypatch):
    conn = imported_conn
    _, album_id, _ = _seed(conn)
    _provider(monkeypatch, [
        {"name": "One Dance", "track_number": 1, "disc_number": 1},
        {"name": "Hotline Bling", "track_number": 2, "disc_number": 1},
    ])

    stats = catalogue_refresh.apply_refresh(conn, album_id)

    assert stats["tracks_updated"] == 0
