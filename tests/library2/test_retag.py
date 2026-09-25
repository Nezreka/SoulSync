"""Phase C re-tag: lib2 → tag_writer db_data shaping, preview, batch write."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Dict, List

from core.library2 import retag


def _seed_album_with_files(conn, *, path: str | None = "/nope/track.flac"):
    cur = conn.cursor()
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Drake')")
    artist_id = cur.lastrowid
    cur.execute(
        """INSERT INTO lib2_albums(primary_artist_id, title, year, release_date,
               genres, expected_track_count) VALUES(?, 'Views', 2016, '2016-04-29',
               '["rap","pop"]', 2)""", (artist_id,))
    album_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_album_artists(album_id, artist_id) VALUES(?,?)",
                (album_id, artist_id))
    cur.execute(
        """INSERT INTO lib2_tracks(album_id, title, track_number, spotify_id)
           VALUES(?, 'One Dance', 1, 'sp1')""", (album_id,))
    track_id = cur.lastrowid
    cur.execute("INSERT INTO lib2_track_artists(track_id, artist_id, position) VALUES(?,?,0)",
                (track_id, artist_id))
    # Featured credit → artists_list should appear in db_data.
    cur.execute("INSERT INTO lib2_artists(name) VALUES('Wizkid')")
    feat_id = cur.lastrowid
    cur.execute(
        "INSERT INTO lib2_track_artists(track_id, artist_id, role, position) "
        "VALUES(?,?, 'featured', 1)", (track_id, feat_id))
    if path:
        cur.execute("INSERT INTO lib2_track_files(track_id, path) VALUES(?,?)",
                    (track_id, path))
    conn.commit()
    return artist_id, album_id, track_id


def test_db_data_shape(imported_conn):
    """The db_data handed to core/tag_writer carries lib2's full metadata."""
    conn = imported_conn
    _, album_id, track_id = _seed_album_with_files(conn)
    row = retag._track_rows(conn, [track_id])[0]
    data = retag._db_data_for_row(conn, row)
    assert data["title"] == "One Dance"
    assert data["album_title"] == "Views"
    assert data["artist_name"] == "Drake"
    assert data["track_artist"] == "Drake; Wizkid"
    assert data["artists_list"] == ["Drake", "Wizkid"]
    assert data["genres"] == ["rap", "pop"]
    assert data["release_date"] == "2016-04-29"
    assert data["track_count"] == 2
    assert data["spotify_track_id"] == "sp1"


def test_preview_carries_release_identity_for_stable_ui_grouping(imported_conn):
    conn = imported_conn
    _, album_id, track_id = _seed_album_with_files(conn, path=None)
    conn.execute(
        "UPDATE lib2_albums SET album_type='ep' WHERE id=?",
        (album_id,),
    )
    conn.commit()

    out = retag.tag_preview(retag.track_contexts(conn, [track_id]))

    assert out[0]["album_id"] == album_id
    assert out[0]["album_type"] == "ep"


def test_preview_reports_missing_file(imported_conn):
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn, path=None)
    out = retag.tag_preview(retag.track_contexts(conn, [track_id]))
    assert len(out) == 1
    assert out[0]["error"] == "No file"
    assert out[0]["has_changes"] is False


def test_preview_reports_unreadable_file(imported_conn):
    """A path that doesn't exist yields a per-track error, never an exception."""
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)  # /nope/track.flac
    out = retag.tag_preview(retag.track_contexts(conn, [track_id]))
    assert len(out) == 1
    assert out[0]["error"]
    assert out[0]["has_changes"] is False


def test_write_counts_unreadable_as_failed(imported_conn, legacy_db):
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)
    stats = retag.write_tags(legacy_db, [track_id], embed_cover=False)
    assert stats["failed"] == 1
    assert stats["written"] == 0
    assert stats["errors"][0]["track_id"] == track_id


def test_scope_helpers(imported_conn):
    conn = imported_conn
    artist_id, album_id, track_id = _seed_album_with_files(conn)
    assert track_id in retag.album_track_ids(conn, album_id)
    assert track_id in retag.artist_track_ids(conn, artist_id)


def test_artist_scope_helper_includes_linked_alias_release(imported_conn):
    conn = imported_conn
    canonical = conn.execute(
        "INSERT INTO lib2_artists(name) VALUES('Canonical')"
    ).lastrowid
    alias, _album_id, track_id = _seed_album_with_files(conn)
    from core.library2.artist_aliases import link_artist_alias
    link_artist_alias(conn, alias, canonical)

    assert track_id in retag.artist_track_ids(conn, canonical)


def test_unchanged_retag_refreshes_stale_gap_cache(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    conn = imported_conn
    file_path = tmp_path / "track.flac"
    file_path.write_bytes(b"fake")
    _, _, track_id = _seed_album_with_files(conn, path="/mapped/track.flac")
    file_tags = {
        "title": "One Dance", "artist": "Drake; Wizkid",
        "album_artist": "Drake", "album": "Views", "year": "2016-04-29",
        "genre": "rap, pop", "track_number": 1, "disc_number": 1,
        "has_cover_art": True, "error": None,
    }
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: str(file_path))
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: file_tags)
    monkeypatch.setattr("core.tag_writer.build_tag_diff", lambda *_args: [])

    stats = retag.write_tags(legacy_db, [track_id], embed_cover=False)

    cache = conn.execute(
        "SELECT tags_json, missing_tags_json FROM lib2_track_files WHERE track_id=?",
        (track_id,),
    ).fetchone()
    assert stats["skipped"] == 1
    assert json.loads(cache["tags_json"])["cover"] is True
    assert json.loads(cache["missing_tags_json"]) == []


def test_successful_retag_reloads_written_tags_instead_of_leaving_old_gaps(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    conn = imported_conn
    file_path = tmp_path / "track.flac"
    file_path.write_bytes(b"fake")
    _, _, track_id = _seed_album_with_files(conn, path="/mapped/track.flac")
    reads = iter([
        {"title": None, "error": None},
        {
            "title": "One Dance", "artist": "Drake; Wizkid",
            "album_artist": "Drake", "album": "Views", "year": "2016-04-29",
            "genre": "rap, pop", "track_number": 1, "disc_number": 1,
            "has_cover_art": True, "error": None,
        },
    ])
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: str(file_path))
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: next(reads))
    monkeypatch.setattr(
        "core.tag_writer.build_tag_diff",
        lambda *_args: [{"changed": True}],
    )
    monkeypatch.setattr(
        "core.tag_writer.write_tags_to_file",
        lambda *_args, **_kwargs: {"success": True},
    )

    stats = retag.write_tags(legacy_db, [track_id], embed_cover=False)

    cache = conn.execute(
        "SELECT missing_tags_json FROM lib2_track_files WHERE track_id=?", (track_id,)
    ).fetchone()
    assert stats["written"] == 1
    assert json.loads(cache["missing_tags_json"]) == []


def test_force_cover_embeds_even_when_text_tags_are_unchanged(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """A1: a picked cover must reach the file even when every text tag already
    matches — build_tag_diff never compares cover art, so without force_cover
    the unchanged-fastpath would skip the file and the new cover would never
    be embedded."""
    conn = imported_conn
    file_path = tmp_path / "track.flac"
    file_path.write_bytes(b"fake")
    _, album_id, track_id = _seed_album_with_files(conn, path="/mapped/track.flac")

    from core.library2.artwork import artwork_file
    cover_path = artwork_file(legacy_db, "album", album_id)
    cover_path.write_bytes(b"new-cover-bytes")

    file_tags = {
        "title": "One Dance", "artist": "Drake; Wizkid",
        "album_artist": "Drake", "album": "Views", "year": "2016-04-29",
        "genre": "rap, pop", "track_number": 1, "disc_number": 1,
        "has_cover_art": True, "error": None,
    }
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: str(file_path))
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: file_tags)
    monkeypatch.setattr("core.tag_writer.build_tag_diff", lambda *_args: [])
    captured = {}

    def _fake_write(path, db_data, *, embed_cover, cover_data):
        captured["embed_cover"] = embed_cover
        captured["cover_data"] = cover_data
        return {"success": True}

    monkeypatch.setattr("core.tag_writer.write_tags_to_file", _fake_write)

    stats = retag.write_tags(legacy_db, [track_id], embed_cover=True, force_cover=True)

    assert stats["written"] == 1
    assert stats["skipped"] == 0
    assert captured["embed_cover"] is True
    assert captured["cover_data"] == (b"new-cover-bytes", "image/jpeg")


def test_force_cover_without_a_cache_file_still_skips_unchanged(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """force_cover has nothing to embed if the album has no cached artwork
    yet — must fall back to the normal skip instead of a pointless write."""
    conn = imported_conn
    file_path = tmp_path / "track.flac"
    file_path.write_bytes(b"fake")
    _, _, track_id = _seed_album_with_files(conn, path="/mapped/track.flac")

    file_tags = {
        "title": "One Dance", "artist": "Drake; Wizkid",
        "album_artist": "Drake", "album": "Views", "year": "2016-04-29",
        "genre": "rap, pop", "track_number": 1, "disc_number": 1,
        "has_cover_art": True, "error": None,
    }
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: str(file_path))
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: file_tags)
    monkeypatch.setattr("core.tag_writer.build_tag_diff", lambda *_args: [])

    stats = retag.write_tags(legacy_db, [track_id], embed_cover=True, force_cover=True)

    assert stats["skipped"] == 1
    assert stats["written"] == 0


def test_write_closes_snapshot_connection_before_file_io(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    conn = imported_conn
    file_path = tmp_path / "track.flac"
    file_path.write_bytes(b"fake")
    _, _, track_id = _seed_album_with_files(conn, path="/mapped/track.flac")
    state = {"active": 0, "opened": 0}

    class _TrackedConnection:
        def __init__(self):
            self._conn = sqlite3.connect(legacy_db.path)
            self._conn.row_factory = sqlite3.Row
            state["active"] += 1
            state["opened"] += 1

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def close(self):
            self._conn.close()
            state["active"] -= 1

    class _Shim:
        def _get_connection(self):
            return _TrackedConnection()

    def _assert_closed(_path):
        assert state["active"] == 0
        return str(file_path)

    def _read_tags(_path):
        assert state["active"] == 0
        return {"error": None}

    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", _assert_closed)
    monkeypatch.setattr("core.tag_writer.read_file_tags", _read_tags)
    monkeypatch.setattr("core.tag_writer.build_tag_diff", lambda *_args: [])

    stats = retag.write_tags(_Shim(), [track_id], embed_cover=False)

    assert stats["skipped"] == 1
    assert state == {"active": 0, "opened": 2}


def test_db_data_carries_the_album_cover_for_the_preview(imported_conn, legacy_db):
    """issues.md T-04: build_tag_diff builds its Cover Art row from
    ``db_data['thumb_url']``. lib2 never supplied it, so Preview Retag reported
    'Cover Art: None -> None, unchanged' — i.e. "Tags match" — for a file that
    demonstrably had no embedded cover."""
    from core.tag_writer import build_tag_diff

    conn = imported_conn
    _, album_id, track_id = _seed_album_with_files(conn)
    conn.execute("UPDATE lib2_albums SET image_url='http://cdn/cover.jpg' WHERE id=?",
                 (album_id,))
    conn.commit()

    row = retag._track_rows(conn, [track_id])[0]
    data = retag._db_data_for_row(conn, row)
    assert data["thumb_url"]

    cover_row = next(
        d for d in build_tag_diff({"has_cover_art": False}, data)
        if d["file_key"] == "cover_art"
    )
    assert cover_row["changed"] is True


def test_missing_cover_is_embedded_without_an_explicit_force(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """issues.md T-03: clicking "N tag gaps" reached write_tags without
    force_cover, and the unchanged-text fastpath skipped the file — so the one
    gap the user clicked on could never close, while the UI reported success."""
    conn = imported_conn
    file_path = tmp_path / "track.flac"
    file_path.write_bytes(b"fake")
    _, album_id, track_id = _seed_album_with_files(conn, path="/mapped/track.flac")

    from core.library2.artwork import artwork_file
    artwork_file(legacy_db, "album", album_id).write_bytes(b"cover-bytes")

    file_tags = {
        "title": "One Dance", "artist": "Drake; Wizkid",
        "album_artist": "Drake", "album": "Views", "year": "2016-04-29",
        "genre": "rap, pop", "track_number": 1, "disc_number": 1,
        "has_cover_art": False, "error": None,
    }
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path", lambda _path: str(file_path))
    monkeypatch.setattr("core.tag_writer.read_file_tags", lambda _path: file_tags)
    monkeypatch.setattr("core.tag_writer.build_tag_diff", lambda *_args: [])
    captured = {}

    def _fake_write(path, db_data, *, embed_cover, cover_data):
        captured["cover_data"] = cover_data
        return {"success": True}

    monkeypatch.setattr("core.tag_writer.write_tags_to_file", _fake_write)

    stats = retag.write_tags(legacy_db, [track_id], embed_cover=True)

    assert stats["written"] == 1
    assert captured["cover_data"] == (b"cover-bytes", "image/jpeg")


def test_cover_source_falls_back_to_the_album_provider_image(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """issues.md T-05: ``artwork_file`` is a path builder, not a builder. On a
    cold artwork cache — including right after Refresh & Scan, which deletes
    those files — _album_cover_data returned None and no cover was ever
    embedded, although lib2_albums.image_url held a valid provider URL."""
    conn = imported_conn
    _, album_id, _track_id = _seed_album_with_files(conn)
    conn.execute("UPDATE lib2_albums SET image_url='http://cdn/cover.jpg' WHERE id=?",
                 (album_id,))
    conn.commit()

    built = []

    def _fake_build(database, db_conn, config_manager, kind, entity_id, **kwargs):
        built.append((kind, entity_id))
        path = retag_artwork.artwork_file(database, kind, entity_id)
        path.write_bytes(b"built-cover")
        return str(path)

    from core.library2 import artwork as retag_artwork
    monkeypatch.setattr(retag_artwork, "build_artwork", _fake_build)

    assert retag._album_cover_data(legacy_db, album_id) == (b"built-cover", "image/jpeg")
    assert built == [("album", album_id)]


def test_cover_build_is_not_attempted_for_an_album_without_any_source(
        imported_conn, legacy_db, monkeypatch):
    """No cover source anywhere → no provider walk, no write. Guards the
    routine full-library retag against paying a build per album."""
    conn = imported_conn
    _, album_id, _track_id = _seed_album_with_files(conn)

    from core.library2 import artwork as retag_artwork

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("build_artwork must not run without a cover source")

    monkeypatch.setattr(retag_artwork, "build_artwork", _must_not_run)
    assert retag._album_cover_data(legacy_db, album_id) is None


# ── user overrides beat the provider, everywhere ─────────────────────────────
#
# lib2 keeps a per-field override layer (`lib2_metadata_overrides`) and every
# read path projects it: `queries._serialize_track` hands the UI
# `effective["title"]`, so a title someone corrected by hand IS the library's
# title. Re-tag never learned that -- `_track_rows` selected `t.title` straight
# off the base row -- so running it wrote the OLD title into the file while the
# page kept showing the corrected one. Two truths again, this time with the
# file on the losing side.
#
# The rule, in all three places: hand beats provider. What the user may still
# do is override the override, per field, deliberately -- which is why the diff
# has to CARRY the provider's suggestion rather than silently drop it.

def _override(conn, *, entity_type, entity_id, field_name, value):
    from core.library2.metadata_overrides import set_field_override
    set_field_override(conn, entity_type=entity_type, entity_id=entity_id,
                       field_name=field_name, value=value)
    conn.commit()


def test_a_hand_set_title_is_what_gets_written(imported_conn):
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)
    _override(conn, entity_type='track', entity_id=track_id,
              field_name='title', value='One Dance (Radio Edit)')

    row = retag._track_rows(conn, [track_id])[0]
    data = retag._db_data_for_row(conn, row)

    assert data['title'] == 'One Dance (Radio Edit)'


def test_a_hand_set_album_title_is_what_gets_written(imported_conn):
    conn = imported_conn
    _, album_id, track_id = _seed_album_with_files(conn)
    _override(conn, entity_type='release_group', entity_id=album_id,
              field_name='title', value='Views (Deluxe)')

    row = retag._track_rows(conn, [track_id])[0]
    data = retag._db_data_for_row(conn, row)

    assert data['album_title'] == 'Views (Deluxe)'


def test_the_diff_shows_what_the_provider_wanted_instead(imported_conn):
    """Not a silent skip: the row has to say the field is hand-set AND what the
    catalogue would otherwise have written, or the user cannot choose."""
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)
    _override(conn, entity_type='track', entity_id=track_id,
              field_name='title', value='One Dance (Radio Edit)')

    row = retag._track_rows(conn, [track_id])[0]
    data = retag._db_data_for_row(conn, row)

    assert data['_manual_fields'] == {'title': 'One Dance'}


def test_a_field_nobody_touched_is_not_marked_manual(imported_conn):
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)

    row = retag._track_rows(conn, [track_id])[0]
    data = retag._db_data_for_row(conn, row)

    assert data['_manual_fields'] == {}


def _readable_file(monkeypatch, tmp_path, file_tags):
    """Point the resolver at a real path and hand the reader fixed tags — the
    same shape the other write tests use, so no audio fixture is needed."""
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake")
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path",
                        lambda _path: str(path))
    monkeypatch.setattr("core.tag_writer.read_file_tags",
                        lambda _path: {"error": None, **file_tags})
    return path


def test_the_preview_row_carries_the_conflict_for_the_user_to_settle(
        imported_conn, tmp_path, monkeypatch):
    """A hand-set field must arrive at the UI as a CHOICE: what is in the file,
    what will be written, and what the catalogue wanted instead. Dropping the
    third value silently decides for the user."""
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)
    _readable_file(monkeypatch, tmp_path, {"title": "One Dance"})
    _override(conn, entity_type='track', entity_id=track_id,
              field_name='title', value='One Dance (Radio Edit)')

    entry = retag.tag_preview(retag.track_contexts(conn, [track_id]))[0]
    row = next(d for d in entry['diff'] if d['file_key'] == 'title')

    assert row['manual'] is True
    assert row['db_value'] == 'One Dance (Radio Edit)'
    assert row['provider_value'] == 'One Dance'


def test_the_conflict_row_names_the_key_a_release_has_to_use(
        imported_conn, tmp_path, monkeypatch):
    """`field` is a display label ("Album") and `file_key` is the tag name
    ("album"); neither is what `overwrite_manual` looks up, which is the
    db_data key ("album_title"). Without it on the row the UI has to keep its
    own copy of that mapping, and a release would silently miss."""
    conn = imported_conn
    _, album_id, track_id = _seed_album_with_files(conn)
    _readable_file(monkeypatch, tmp_path, {"album": "Views"})
    _override(conn, entity_type='release_group', entity_id=album_id,
              field_name='title', value='Views (Deluxe)')

    entry = retag.tag_preview(retag.track_contexts(conn, [track_id]))[0]
    row = next(d for d in entry['diff'] if d['file_key'] == 'album')

    assert row['manual_key'] == 'album_title'


def test_a_row_without_an_override_says_so_rather_than_omitting_the_flag(
        imported_conn, tmp_path, monkeypatch):
    """The UI branches on `manual`; an absent key would read as undefined and
    render the conflict control for every row."""
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)
    _readable_file(monkeypatch, tmp_path, {"title": "Something Else"})

    entry = retag.tag_preview(retag.track_contexts(conn, [track_id]))[0]
    row = next(d for d in entry['diff'] if d['file_key'] == 'title')

    assert row['manual'] is False
    assert 'provider_value' not in row


def test_a_track_with_a_conflict_is_countable_without_walking_the_diff(
        imported_conn, tmp_path, monkeypatch):
    """The bulk prompt has to say "23 findings, 4 of them hand-set" before the
    user picks an action, so the count cannot require a nested scan."""
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)
    _readable_file(monkeypatch, tmp_path, {"title": "One Dance"})
    _override(conn, entity_type='track', entity_id=track_id,
              field_name='title', value='One Dance (Radio Edit)')

    entry = retag.tag_preview(retag.track_contexts(conn, [track_id]))[0]

    assert entry['has_manual_conflict'] is True


def _capture_write(monkeypatch, tmp_path, file_tags=None):
    """Run write_tags against a stubbed writer and hand back what it was told
    to write."""
    written: List[Dict[str, Any]] = []
    path = tmp_path / "track.flac"
    path.write_bytes(b"fake")
    monkeypatch.setattr("core.library2.paths.resolve_lib2_path",
                        lambda _p: str(path))
    monkeypatch.setattr("core.tag_writer.read_file_tags",
                        lambda _p: {"error": None, **(file_tags or {})})
    monkeypatch.setattr("core.tag_writer.build_tag_diff",
                        lambda *_a: [{"changed": True}])
    monkeypatch.setattr(
        "core.tag_writer.write_tags_to_file",
        lambda _p, db_data, **_kw: (written.append(dict(db_data)),
                                    {"success": True})[1])
    return written


def test_by_default_the_write_keeps_the_hand_set_value(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)
    _override(conn, entity_type='track', entity_id=track_id,
              field_name='title', value='One Dance (Radio Edit)')
    written = _capture_write(monkeypatch, tmp_path)

    retag.write_tags(legacy_db, [track_id], embed_cover=False)

    assert written[0]['title'] == 'One Dance (Radio Edit)'


def test_releasing_one_field_takes_the_catalogue_value_for_that_field_only(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """The release list is per (track, field) on purpose: settling the title
    must not silently hand the album title over too."""
    conn = imported_conn
    _, album_id, track_id = _seed_album_with_files(conn)
    _override(conn, entity_type='track', entity_id=track_id,
              field_name='title', value='One Dance (Radio Edit)')
    _override(conn, entity_type='release_group', entity_id=album_id,
              field_name='title', value='Views (Deluxe)')
    written = _capture_write(monkeypatch, tmp_path)

    retag.write_tags(legacy_db, [track_id], embed_cover=False,
                     overwrite_manual=[(track_id, 'title')])

    assert written[0]['title'] == 'One Dance'
    assert written[0]['album_title'] == 'Views (Deluxe)'


def test_release_everything_is_available_for_the_bulk_choice(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    conn = imported_conn
    _, album_id, track_id = _seed_album_with_files(conn)
    _override(conn, entity_type='track', entity_id=track_id,
              field_name='title', value='One Dance (Radio Edit)')
    _override(conn, entity_type='release_group', entity_id=album_id,
              field_name='title', value='Views (Deluxe)')
    written = _capture_write(monkeypatch, tmp_path)

    retag.write_tags(legacy_db, [track_id], embed_cover=False,
                     overwrite_manual=True)

    assert written[0]['title'] == 'One Dance'
    assert written[0]['album_title'] == 'Views'


def test_a_release_for_another_track_does_not_leak(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)
    _override(conn, entity_type='track', entity_id=track_id,
              field_name='title', value='One Dance (Radio Edit)')
    written = _capture_write(monkeypatch, tmp_path)

    retag.write_tags(legacy_db, [track_id], embed_cover=False,
                     overwrite_manual=[(track_id + 999, 'title')])

    assert written[0]['title'] == 'One Dance (Radio Edit)'


def test_a_released_numeric_field_keeps_its_type(
        imported_conn, legacy_db, tmp_path, monkeypatch):
    """`track_number` reaches the tag writer as an int. Round-tripping the
    displaced value through str() would hand it '1' and quietly change what
    lands in the file."""
    conn = imported_conn
    _, _, track_id = _seed_album_with_files(conn)
    _override(conn, entity_type='track', entity_id=track_id,
              field_name='track_number', value=7)
    written = _capture_write(monkeypatch, tmp_path)

    retag.write_tags(legacy_db, [track_id], embed_cover=False,
                     overwrite_manual=[(track_id, 'track_number')])

    assert written[0]['track_number'] == 1


def test_a_hand_set_artist_name_is_what_gets_written(imported_conn):
    """ARCH-04: the artist edit dialog writes a `name` override that every read
    path through `project_metadata` honours. Retag read `lib2_artists.name`
    straight out of its join instead, so a corrected name showed on the page
    while retag went on proposing — and writing back — the old ARTIST and
    ALBUMARTIST tags. The hand value was not even recognised as manual."""
    conn = imported_conn
    artist_id, _, track_id = _seed_album_with_files(conn)
    _override(conn, entity_type='artist', entity_id=artist_id,
              field_name='name', value='Corrected Artist')

    row = retag._track_rows(conn, [track_id])[0]
    data = retag._db_data_for_row(conn, row)

    assert data['artist_name'] == 'Corrected Artist'
    assert data['track_artist'] == 'Corrected Artist; Wizkid'
    assert data['artists_list'] == ['Corrected Artist', 'Wizkid']
