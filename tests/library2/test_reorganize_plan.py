"""Reorganize plans a PATH. It does not ask a provider what the album is called.

Until now the destination came from a live provider tracklist
(`get_album_tracks_for_source`), with the library's own values pulled back in
one special case at a time — `_keep_user_casing` for the album name, again for
the track title, `_keep_user_year` for the year. Three patches, each added
after a bug report, each saying the same thing: when the catalogue and the
provider disagree, the catalogue was right.

So the catalogue is the source, and the provider is not consulted at all. What
that buys:

* albums with no stored source id become reorganizable instead of being
  refused outright (`status: 'no_source_id'`),
* the plan is offline — no 4.4s preview, no `Invalid base62 id` 400s from
  candidate ids that were never Spotify's,
* the filename matches what the Library page displays, including a title
  someone corrected by hand, because both read through the same override
  projection.

What it costs is stated plainly: a manual match no longer changes the path on
its own. Re-tag moves the provider's values into the catalogue; reorganize
applies the template to what the catalogue says. Two steps, each visible.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from core.library2 import reorganize_plan


def _seed(conn, *, album_title="Views", year=2016, tracks=(("One Dance", 1, 1),),
          source_id=None):
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Drake')")
    artist_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_albums(primary_artist_id, title, year, album_type, spotify_id)"
        " VALUES(?,?,?,'album',?)", (artist_id, album_title, year, source_id))
    album_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                (album_id, artist_id))
    track_ids = []
    for title, number, disc in tracks:
        cur.execute(
            "INSERT INTO lib2_tracks(album_id, title, track_number, disc_number)"
            " VALUES(?,?,?,?)", (album_id, title, number, disc))
        track_id = cur.lastrowid
        cur.execute(
            "INSERT INTO lib2_track_artists(track_id, artist_id, position) VALUES(?,?,0)",
            (track_id, artist_id))
        cur.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
                    (track_id, f"/music/Drake/{album_title}/{number:02d} - {title}.flac"))
        track_ids.append(track_id)
    conn.commit()
    return artist_id, album_id, track_ids


def _recorder():
    """Stands in for `build_final_path_for_track` and keeps what it was fed —
    the assertions are about the DATA that reaches the path builder."""
    seen: List[Dict[str, Any]] = []

    def build(context, artist, album_info, file_ext, create_dirs=True):
        seen.append({"context": context, "artist": artist,
                     "album_info": album_info, "ext": file_ext,
                     "create_dirs": create_dirs})
        title = context["track_info"]["name"]
        return f"/music/out/{title}{file_ext}", True

    return build, seen


def _plan(conn, album_id, build, **kwargs):
    return reorganize_plan.plan_album_reorganize(
        conn, album_id,
        build_final_path_fn=build,
        transfer_dir=kwargs.pop("transfer_dir", "/music"),
        resolve_file_path_fn=kwargs.pop("resolve_file_path_fn", lambda p: p),
        **kwargs,
    )


def test_an_album_with_no_source_id_can_be_reorganized(imported_conn):
    """The old planner refused outright with 'No metadata source ID — run
    enrichment first'. Nothing about moving a file needs a provider."""
    conn = imported_conn
    _, album_id, _ = _seed(conn, source_id=None)
    build, seen = _recorder()

    plan = _plan(conn, album_id, build)

    assert plan["status"] == "planned"
    assert len(seen) == 1
    assert plan["tracks"][0]["matched"] is True


def test_the_provider_is_never_asked(imported_conn, monkeypatch):
    conn = imported_conn
    _, album_id, _ = _seed(conn, source_id="sp-album-1")
    build, _seen = _recorder()

    def _boom(*_a, **_k):
        raise AssertionError("reorganize must not call a metadata provider")

    monkeypatch.setattr("core.metadata.album_tracks.get_album_tracks_for_source", _boom)
    monkeypatch.setattr("core.metadata.album_tracks.get_album_for_source", _boom)

    assert _plan(conn, album_id, build)["status"] == "planned"


def test_the_path_uses_the_title_the_page_shows(imported_conn):
    """A hand-set title is the library's title (`lib2_metadata_overrides`), and
    the file has to be named after what the user sees — not after the base row
    the page stopped showing."""
    from core.library2.metadata_overrides import set_field_override

    conn = imported_conn
    _, album_id, track_ids = _seed(conn)
    set_field_override(conn, entity_type="track", entity_id=track_ids[0],
                       field_name="title", value="One Dance (Radio Edit)")
    conn.commit()
    build, seen = _recorder()

    _plan(conn, album_id, build)

    assert seen[0]["context"]["track_info"]["name"] == "One Dance (Radio Edit)"


def test_the_path_uses_the_album_title_the_page_shows(imported_conn):
    from core.library2.metadata_overrides import set_field_override

    conn = imported_conn
    _, album_id, _ = _seed(conn)
    set_field_override(conn, entity_type="release_group", entity_id=album_id,
                       field_name="title", value="Views (Deluxe)")
    conn.commit()
    build, seen = _recorder()

    _plan(conn, album_id, build)

    assert seen[0]["context"]["spotify_album"]["name"] == "Views (Deluxe)"


def test_disc_count_comes_from_the_catalogue(imported_conn):
    """`total_discs` decides whether a `Disc N` folder is created at all, and
    reading it off a live provider tracklist is how a half-downloaded album's
    layout oscillated (#1080)."""
    conn = imported_conn
    _, album_id, _ = _seed(conn, tracks=(("A", 1, 1), ("B", 1, 2)))
    build, seen = _recorder()

    _plan(conn, album_id, build)

    assert seen[0]["context"]["spotify_album"]["total_discs"] == 2


def test_the_preview_never_creates_a_folder(imported_conn):
    """#767: a dry run that mkdir'd its destination left empty folders all over
    the library."""
    conn = imported_conn
    _, album_id, _ = _seed(conn)
    build, seen = _recorder()

    _plan(conn, album_id, build)

    assert seen[0]["create_dirs"] is False


def test_two_tracks_landing_on_one_path_are_flagged_as_a_collision(imported_conn):
    conn = imported_conn
    _, album_id, _ = _seed(conn, tracks=(("Same", 1, 1), ("Same", 2, 1)))
    build, _seen = _recorder()

    plan = _plan(conn, album_id, build)

    assert [t["collision"] for t in plan["tracks"]] == [True, True]


def test_a_track_whose_file_is_unreachable_is_reported_not_planned(imported_conn):
    """`current_path_abs = ''` reaching the mover is how a failed rename created
    the empty destination folder it then could not fill."""
    conn = imported_conn
    _, album_id, _ = _seed(conn)
    build, _seen = _recorder()

    plan = _plan(conn, album_id, build, resolve_file_path_fn=lambda _p: None)

    track = plan["tracks"][0]
    assert track["file_exists"] is False
    assert track["matched"] is False
    assert track["current_path_abs"] == ""


def test_an_album_with_no_files_says_so(imported_conn):
    conn = imported_conn
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Drake')")
    artist_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_albums(primary_artist_id, title) VALUES(?, 'Empty')",
                (artist_id,))
    album_id = cur.lastrowid
    conn.commit()
    build, _seen = _recorder()

    assert _plan(conn, album_id, build)["status"] == "no_tracks"


def test_disc_count_counts_discs_that_have_no_file_yet(imported_conn):
    """ARCH-03: the disc count is a property of the ALBUM, but it was computed
    after the tracks with no file had been filtered out.

    A known two-disc album with only disc 1 downloaded therefore declared
    itself single-disc — and `total_discs_declared=True` tells the shared path
    builder to suppress its own disc detection, so disc 1 was moved OUT of
    `Disc 1/`. The moment the first file of disc 2 landed, the same run moved
    it straight back. That is the #1080 oscillation from the other direction."""
    conn = imported_conn
    _, album_id, track_ids = _seed(conn, tracks=(("A", 1, 1), ("B", 1, 2)))
    # Disc 2's file has not arrived yet; its catalogue row still exists.
    conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (track_ids[1],))
    conn.commit()
    build, seen = _recorder()

    _plan(conn, album_id, build)

    assert len(seen) == 1, "only the track that has a file is planned"
    assert seen[0]["context"]["spotify_album"]["total_discs"] == 2


def test_a_corrected_artist_name_reaches_the_path(imported_conn):
    """ARCH-04: the artist edit dialog writes a `name` override that the page
    honours. Reorganize read `lib2_artists.name` out of the join instead, so it
    kept building folders under the old name."""
    from core.library2.metadata_overrides import set_field_override

    conn = imported_conn
    artist_id, album_id, _ = _seed(conn)
    set_field_override(conn, entity_type="artist", entity_id=artist_id,
                       field_name="name", value="Corrected Artist")
    conn.commit()
    build, seen = _recorder()

    plan = _plan(conn, album_id, build)

    assert plan["artist"] == "Corrected Artist"
    assert seen[0]["artist"]["name"] == "Corrected Artist"
    assert seen[0]["context"]["track_info"]["artists"][0]["name"] == "Corrected Artist"


def test_a_track_landing_on_an_unchanged_file_is_a_collision(imported_conn):
    """An `unchanged` track is one already sitting at its destination. It never
    moves — but it is still the file another track would land on top of, and
    the detection used to skip unchanged rows outright, so the single most
    destructive shape was the one it could not see: a second track replacing a
    file the plan reported as needing no work at all.

    The occupant stays unflagged (it has nothing to skip); the mover is the one
    that must not run."""
    conn = imported_conn
    _, album_id, track_ids = _seed(conn, tracks=(("Keep", 1, 1), ("Move", 2, 1)))
    conn.execute("UPDATE lib2_track_files SET path='/music/out/Keep.flac' "
                 "WHERE track_id=?", (track_ids[0],))
    conn.commit()

    def build(_context, _artist, _album_info, _file_ext, create_dirs=True):
        return "/music/out/Keep.flac", True

    plan = _plan(conn, album_id, build)

    by_title = {t["title"]: t for t in plan["tracks"]}
    assert by_title["Keep"]["unchanged"] is True
    assert by_title["Keep"]["collision"] is False
    assert by_title["Move"]["collision"] is True
