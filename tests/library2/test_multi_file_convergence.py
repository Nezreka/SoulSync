"""Regression tests for the §27 Domain-D catalog-integrity findings.

dd28-08  a quality upgrade left the STALE file row primary/active while its
         file was already deleted, so every lib2 read path used a dead path
dd28-09  an alias-linked artist got a duplicate album (and a download loop)
dd28-10  a newly created track had no edition row at all
dd28-38  tags/ReplayGain/lyrics reached only the primary of a multi-file track
dd28-40  a retained lossless original was invisible to lib2 after a lossy copy
"""

from __future__ import annotations

import os

import pytest

from core.library2 import autolink
from core.library2.artist_aliases import link_artist_alias
from core.library2.editions import attach_track_to_edition
from core.library2.track_files import retire_replaced_files, writable_file_rows


@pytest.fixture
def artist_id(imported_conn):
    return imported_conn.execute("SELECT id FROM lib2_artists LIMIT 1").fetchone()[0]


@pytest.fixture
def track_id(imported_conn):
    """A track with NO pre-existing file rows, so each test owns the whole set."""
    tid = imported_conn.execute("SELECT id FROM lib2_tracks LIMIT 1").fetchone()[0]
    imported_conn.execute("DELETE FROM lib2_track_files WHERE track_id=?", (tid,))
    imported_conn.commit()
    return tid


def _add_file(conn, track_id, path, fmt="mp3", tier=None):
    cur = conn.execute(
        """INSERT INTO lib2_track_files(track_id, path, format, quality_tier,
               import_status)
           VALUES(?,?,?,?, 'imported')""",
        (track_id, path, fmt, tier),
    )
    return cur.lastrowid


# --------------------------------------------------------------------------
# dd28-08
# --------------------------------------------------------------------------


def test_replaced_file_row_is_retired_and_hands_over_primary(imported_conn, track_id, tmp_path):
    old_path = str(tmp_path / "Track.mp3")
    new_path = str(tmp_path / "Track.flac")
    open(new_path, "wb").close()

    old_id = _add_file(imported_conn, track_id, old_path, fmt="mp3")
    new_id = _add_file(imported_conn, track_id, new_path, fmt="flac")
    imported_conn.commit()

    # Reproduce the reported state: the stale row is still the active primary.
    imported_conn.execute("UPDATE lib2_track_files SET is_primary=0 WHERE track_id=?", (track_id,))
    imported_conn.execute("UPDATE lib2_track_files SET is_primary=1 WHERE id=?", (old_id,))
    imported_conn.commit()

    retired = retire_replaced_files(
        imported_conn, track_id, keep_path=new_path, removed_paths=[old_path],
    )
    imported_conn.commit()

    assert retired == 1
    old_row = imported_conn.execute(
        "SELECT file_state, is_primary FROM lib2_track_files WHERE id=?", (old_id,)
    ).fetchone()
    new_row = imported_conn.execute(
        "SELECT file_state, is_primary FROM lib2_track_files WHERE id=?", (new_id,)
    ).fetchone()
    assert old_row["file_state"] == "deleted"
    assert old_row["is_primary"] == 0
    assert new_row["is_primary"] == 1


def test_retirement_never_touches_the_file_that_was_kept(imported_conn, track_id, tmp_path):
    keep = str(tmp_path / "Keep.flac")
    open(keep, "wb").close()
    keep_id = _add_file(imported_conn, track_id, keep, fmt="flac")
    imported_conn.commit()

    # Even if the caller mistakenly names it, the kept path is never retired.
    retired = retire_replaced_files(
        imported_conn, track_id, keep_path=keep, removed_paths=[keep],
    )

    assert retired == 0
    assert imported_conn.execute(
        "SELECT COALESCE(file_state,'active') FROM lib2_track_files WHERE id=?", (keep_id,)
    ).fetchone()[0] == "active"


def test_an_unreachable_root_does_not_retire_anything(imported_conn, track_id, tmp_path, monkeypatch):
    """Guide §5: an unhealthy root must never confirm a miss."""
    keep = str(tmp_path / "New.flac")
    open(keep, "wb").close()
    gone = str(tmp_path / "unmounted" / "Old.flac")  # parent does not exist
    _add_file(imported_conn, track_id, keep, fmt="flac")
    old_id = _add_file(imported_conn, track_id, gone, fmt="flac")
    imported_conn.commit()

    # Not named as an explicit replacement, and the root is unhealthy.
    retired = retire_replaced_files(imported_conn, track_id, keep_path=keep)

    assert retired == 0
    assert imported_conn.execute(
        "SELECT COALESCE(file_state,'active') FROM lib2_track_files WHERE id=?", (old_id,)
    ).fetchone()[0] == "active"


def test_a_vanished_file_under_a_healthy_root_is_retired(imported_conn, track_id, tmp_path):
    keep = str(tmp_path / "New.flac")
    gone = str(tmp_path / "Old.flac")
    open(keep, "wb").close()  # the directory exists => absence is credible
    _add_file(imported_conn, track_id, keep, fmt="flac")
    old_id = _add_file(imported_conn, track_id, gone, fmt="flac")
    imported_conn.commit()

    retired = retire_replaced_files(imported_conn, track_id, keep_path=keep)
    imported_conn.commit()

    assert retired == 1
    assert imported_conn.execute(
        "SELECT file_state FROM lib2_track_files WHERE id=?", (old_id,)
    ).fetchone()[0] == "deleted"


def test_an_unmappable_media_server_path_is_left_alone(imported_conn, track_id, tmp_path):
    """A stored path this container cannot map is not evidence of deletion.

    ``resolve_lib2_path`` answers None for BOTH "cannot be mapped here" and
    "mapped, but absent" — so a media-server path on a correctly configured
    install would look exactly like a deleted file. That is the dd28-19 trap;
    only a sibling in the SAME, observable directory counts.
    """
    keep = str(tmp_path / "New.flac")
    open(keep, "wb").close()
    media_server_path = "/data/music/Artist/Album/Old.flac"
    _add_file(imported_conn, track_id, keep, fmt="flac")
    old_id = _add_file(imported_conn, track_id, media_server_path, fmt="flac")
    imported_conn.commit()

    retired = retire_replaced_files(imported_conn, track_id, keep_path=keep)

    assert retired == 0
    assert imported_conn.execute(
        "SELECT COALESCE(file_state,'active') FROM lib2_track_files WHERE id=?", (old_id,)
    ).fetchone()[0] == "active"


def test_an_explicitly_deleted_path_is_retired_wherever_it_lives(
    imported_conn, track_id, tmp_path,
):
    """The caller just deleted it — no inference needed, so no restrictions."""
    keep = str(tmp_path / "New.flac")
    open(keep, "wb").close()
    elsewhere = "/data/music/Artist/Album/Old.flac"
    _add_file(imported_conn, track_id, keep, fmt="flac")
    old_id = _add_file(imported_conn, track_id, elsewhere, fmt="flac")
    imported_conn.commit()

    retired = retire_replaced_files(
        imported_conn, track_id, keep_path=keep, removed_paths=[elsewhere],
    )
    imported_conn.commit()

    assert retired == 1
    assert imported_conn.execute(
        "SELECT file_state FROM lib2_track_files WHERE id=?", (old_id,)
    ).fetchone()[0] == "deleted"


# --------------------------------------------------------------------------
# dd28-09
# --------------------------------------------------------------------------


def test_album_lookup_spans_the_alias_group(imported_conn, artist_id):
    """A download booked on the alias must not mint a second album row."""
    album_row = imported_conn.execute(
        """SELECT al.id, al.title FROM lib2_albums al
           JOIN lib2_album_artists aa ON aa.album_id = al.id
          WHERE aa.artist_id=? LIMIT 1""",
        (artist_id,),
    ).fetchone()
    assert album_row is not None, "fixture needs an album on this artist"

    cur = imported_conn.execute(
        "INSERT INTO lib2_artists(name) VALUES(?)", ("Alias Spelling",)
    )
    alias_id = cur.lastrowid
    imported_conn.commit()
    link_artist_alias(imported_conn, alias_id, artist_id)
    imported_conn.commit()

    before = imported_conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0]
    resolved = autolink.find_or_create_album(
        imported_conn, alias_id, album_row["title"], album_type="album",
    )
    after = imported_conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0]

    assert resolved == album_row["id"]
    assert after == before, "the alias must reuse the canonical album, not duplicate it"


def test_a_genuinely_new_album_is_still_created(imported_conn, artist_id):
    before = imported_conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0]
    autolink.find_or_create_album(
        imported_conn, artist_id, "A Title Nobody Owns Yet", album_type="album",
    )
    after = imported_conn.execute("SELECT COUNT(*) FROM lib2_albums").fetchone()[0]
    assert after == before + 1


# --------------------------------------------------------------------------
# dd28-10
# --------------------------------------------------------------------------


def test_a_new_track_gets_its_edition_row_immediately(imported_conn, artist_id):
    album_id = autolink.find_or_create_album(
        imported_conn, artist_id, "Edition Probe Album", album_type="album",
    )
    track_id = autolink.find_or_create_track(
        imported_conn, album_id, artist_id, "Edition Probe Track", track_number=1,
    )
    imported_conn.commit()

    linked = imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_release_tracks WHERE track_id=?", (track_id,)
    ).fetchone()[0]
    assert linked == 1, "a new track was invisible to every edition-scoped consumer"


def test_edition_attachment_is_idempotent(imported_conn, artist_id):
    album_id = autolink.find_or_create_album(
        imported_conn, artist_id, "Idempotent Album", album_type="album",
    )
    track_id = autolink.find_or_create_track(
        imported_conn, album_id, artist_id, "Idempotent Track", track_number=1,
    )
    attach_track_to_edition(imported_conn, track_id)
    attach_track_to_edition(imported_conn, track_id)
    imported_conn.commit()

    assert imported_conn.execute(
        "SELECT COUNT(*) FROM lib2_release_tracks WHERE track_id=?", (track_id,)
    ).fetchone()[0] == 1


# --------------------------------------------------------------------------
# dd28-38
# --------------------------------------------------------------------------


def test_writable_file_rows_returns_every_usable_file_primary_first(
    imported_conn, track_id, tmp_path,
):
    mp3 = str(tmp_path / "Song.mp3")
    flac = str(tmp_path / "Song.flac")
    quarantined = str(tmp_path / "Song.bad.flac")
    mp3_id = _add_file(imported_conn, track_id, mp3, fmt="mp3")
    flac_id = _add_file(imported_conn, track_id, flac, fmt="flac")
    q_id = _add_file(imported_conn, track_id, quarantined, fmt="flac")
    imported_conn.execute(
        "UPDATE lib2_track_files SET file_state='quarantined' WHERE id=?", (q_id,)
    )
    imported_conn.commit()

    rows = writable_file_rows(imported_conn, track_id)
    ids = [r["id"] for r in rows]

    assert q_id not in ids, "a quarantined file must not be written to"
    assert set(ids) == {flac_id, mp3_id}, "every usable file has to be offered"
    assert rows[0]["is_primary"] == 1, "the primary is still processed first"


# --------------------------------------------------------------------------
# dd28-40
# --------------------------------------------------------------------------


def test_a_retained_lossless_original_is_linked_as_a_second_file(
    imported_conn, track_id, tmp_path,
):
    flac = tmp_path / "Song.flac"
    flac.write_bytes(b"\x00" * 16)

    file_id = autolink._link_companion_file(imported_conn, track_id, str(flac))
    imported_conn.commit()

    assert file_id is not None
    paths = {
        r["path"] for r in imported_conn.execute(
            "SELECT path FROM lib2_track_files WHERE track_id=?", (track_id,)
        )
    }
    assert str(flac) in paths


def test_companion_linking_is_idempotent(imported_conn, track_id, tmp_path):
    flac = tmp_path / "Song.flac"
    flac.write_bytes(b"\x00" * 16)

    first = autolink._link_companion_file(imported_conn, track_id, str(flac))
    second = autolink._link_companion_file(imported_conn, track_id, str(flac))

    assert first == second


def test_a_missing_companion_is_not_linked(imported_conn, track_id, tmp_path):
    assert autolink._link_companion_file(
        imported_conn, track_id, str(tmp_path / "nope.flac")
    ) is None
