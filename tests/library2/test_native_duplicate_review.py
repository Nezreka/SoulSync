"""Duplicate review uses native files; approved removal is recoverable."""

from contextlib import closing
import os

import pytest

from core.library2.schema import ensure_library_v2_schema
from tests.lib2_seed import RowDb, row_conn, track


class Config:
    def __init__(self, root, **values):
        self.values = {"library.music_paths": [str(root)], **values}

    def get(self, key, default=None):
        return self.values.get(key, default)


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "music"
    root.mkdir()
    db = RowDb(str(tmp_path / "native.db"))
    with closing(db._get_connection()) as conn:
        ensure_library_v2_schema(conn)
        conn.commit()
    return db, Config(root), root, tmp_path / "Transfer"


def copy(library, album="Album", title="Song", fmt="flac", artist="Artist", **fields):
    db, _config, root, _transfer = library
    file_fields = {k: fields.pop(k) for k in list(fields) if k in {
        "bitrate", "bit_depth", "sample_rate", "owner_profile_id", "file_role",
        "source", "primary_manual", "derived_from_file_id", "retention_json", "content_hash",
        "profile_rank",
    }}
    with closing(db._get_connection()) as conn:
        tid = track(conn, artist, album, title, owned=False, duration=fields.pop("duration", 180000),
                    isrc=fields.pop("isrc", "CHABC1234567"), **fields)
        path = root / f"{tid}.{fmt}"
        path.write_bytes(f"audio-{tid}".encode())
        fid = conn.execute(
            "INSERT INTO lib2_track_files(track_id,path,format) VALUES(?,?,?)",
            (tid, str(path), fmt)).lastrowid
        for column, value in file_fields.items():
            conn.execute(f"UPDATE lib2_track_files SET {column}=? WHERE id=?", (value, fid))
        conn.commit()
    return tid, fid, path


def review(library, **kwargs):
    from core.library2.duplicate_review import find_duplicate_candidates
    db, config, *_ = library
    return find_duplicate_candidates(db, config, playlist_membership={}, **kwargs)


def apply(library, candidate, **kwargs):
    from core.library2.duplicate_review import apply_keep_best
    db, config, _root, transfer = library
    return apply_keep_best(db, candidate, config_manager=config,
                           transfer_folder=str(transfer), playlist_membership={}, **kwargs)


def states(library):
    with closing(library[0]._get_connection()) as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT id,file_state FROM lib2_track_files")}


def test_unlinked_cross_format_candidates_do_not_create_canonical_links(library):
    a, af, _ = copy(library, album="Single", fmt="mp3", bitrate=320)
    b, bf, _ = copy(library, album="Album", fmt="flac", bitrate=0)
    candidates = review(library)
    assert len(candidates) == 1
    assert {t["file_id"] for t in candidates[0]["tracks"]} == {af, bf}
    assert candidates[0]["recommended_file_id"] == bf
    with closing(library[0]._get_connection()) as conn:
        assert all(
            r[0] is None for r in conn.execute("SELECT canonical_track_id FROM lib2_tracks"))


@pytest.mark.parametrize('other_title,other_artist', [('Dreamin Tonight', 'Artist'),
    ('Dreming Tonight', 'Artist'), ('Dreming Tonight', 'Artest')])
def test_discovery_preserves_upstream_fuzzy_title_and_credit_review(library, other_title, other_artist):
    copy(library, title="Dreaming Tonight", artist="Guest; Artist", isrc=None)
    copy(library, title=other_title, artist=other_artist, isrc=None, album="Other")
    candidates = review(library)
    assert len(candidates) == 1
    assert candidates[0]["requires_recording_confirmation"] is True


def test_filename_suffix_candidates_do_not_bypass_hard_identity_conflicts(library):
    a, af, pa = copy(library, title="Broken tag", isrc=None)
    b, bf, pb = copy(library, title="Other broken tag", isrc=None)
    first = pa.with_name("Original.flac")
    second = pb.with_name("Original_639122324339578022.flac")
    pa.rename(first)
    pb.rename(second)
    with closing(library[0]._get_connection()) as conn:
        conn.execute("UPDATE lib2_track_files SET path=? WHERE id=?", (str(first), af))
        conn.execute("UPDATE lib2_track_files SET path=? WHERE id=?", (str(second), bf))
        conn.commit()
    assert len(review(library)) == 1
    with closing(library[0]._get_connection()) as conn:
        conn.execute("UPDATE lib2_tracks SET isrc='ONE' WHERE id=?", (a,))
        conn.execute("UPDATE lib2_tracks SET isrc='TWO' WHERE id=?", (b,))
        conn.commit()
    assert review(library) == []


@pytest.mark.parametrize("title1,title2", [
    ("Movement I", "Movement II"), ("Part 1", "Part 2"),
    ("Live 1977", "Live 1978"), ("夜の曲", "朝の曲"),
])
def test_distinct_numbered_performances_and_unicode_titles_are_not_grouped(library, title1, title2):
    copy(library, title=title1, isrc=None)
    copy(library, title=title2, isrc=None, album="Other")
    assert review(library) == []


def test_conflicting_hard_ids_and_durations_never_become_candidates(library):
    copy(library)
    copy(library, album="Other", isrc="OTHER")
    copy(library, album="Third", duration=240000)
    assert review(library) == []


def test_review_is_scoped_to_owner_and_requested_files(library):
    _a, af, pa = copy(library)
    _b, bf, pb = copy(library, album="Other")
    copy(library, album="Private", owner_profile_id=9)
    assert len(review(library)) == 1
    assert {t["file_id"] for t in review(library)[0]["tracks"]} == {af, bf}
    assert review(library, scope={"file_paths": [str(pa)]}) == []
    assert review(library, scope={"file_paths": []}) == []
    assert review(library, settings={"ignore_cross_album": True}) == []


def test_keep_best_requires_approval_and_moves_to_restorable_journal(library):
    a, af, pa = copy(library, fmt="mp3", bitrate=320)
    b, bf, pb = copy(library, album="Other", fmt="flac")
    candidate = review(library)[0]
    denied = apply(library, candidate)
    assert denied["success"] is False
    assert pa.exists() and pb.exists()
    result = apply(library, candidate, approved=True)
    assert result["success"] is True
    assert result["kept_file_id"] == bf
    assert not pa.exists() and pb.exists()
    assert states(library)[af] == "deleted"
    with closing(library[0]._get_connection()) as conn:
        assert conn.execute("SELECT canonical_track_id FROM lib2_tracks WHERE id=?", (a,)).fetchone()[0] == b
        operation = conn.execute("SELECT mode,actor,status FROM lib2_file_delete_operations").fetchone()
        assert tuple(operation) == ("quarantine", "repair:native_duplicate_detector", "completed")
    from core.library.deleted_quarantine import list_entries, restore_entries
    entries = list_entries(str(library[3]))["entries"]
    restored = restore_entries(str(library[3]), [entries[0]["id"]])
    assert restored["restored"]
    assert pa.read_bytes() == f"audio-{a}".encode()


def test_title_only_review_requires_explicit_recording_confirmation(library):
    _a, _af, pa = copy(library, isrc=None)
    _b, _bf, pb = copy(library, isrc=None, album="Other")
    candidate = review(library)[0]
    assert apply(library, candidate, approved=True)["success"] is False
    assert pa.exists() and pb.exists()
    assert apply(library, candidate, approved=True, confirm_recording=True)["success"] is True


def test_stale_finding_rechecks_identity_and_never_removes_replaced_files(library):
    _a, _af, pa = copy(library)
    b, _bf, pb = copy(library, album="Other")
    candidate = review(library)[0]
    pb.write_bytes(b"a replacement")
    assert apply(library, candidate, approved=True)["success"] is False
    assert pa.exists() and pb.exists()
    candidate = review(library)[0]
    with closing(library[0]._get_connection()) as conn:
        conn.execute("UPDATE lib2_tracks SET isrc='OTHER' WHERE id=?", (b,))
        conn.commit()
    assert apply(library, candidate, approved=True)["success"] is False
    assert pa.exists() and pb.exists()


def test_intentional_companions_manual_primary_and_shared_references_survive(library):
    a, af, pa = copy(library, primary_manual=1)
    _b, bf, pb = copy(library, album="Other", bit_depth=24)
    _c, cf, pc = copy(library, album="Companion", fmt="mp3", source="companion", file_role="derivative", derived_from_file_id=af)
    _d, df, pd = copy(library, album="Third")
    with closing(library[0]._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_track_files(track_id,path,owner_profile_id) VALUES(?,?,?)", (a, str(pd), 9))
        conn.commit()
    candidate = review(library)[0]
    result = apply(library, candidate, approved=True, keep_file_id=bf)
    assert result["success"] is True
    assert pa.exists() and pb.exists() and pc.exists() and pd.exists()
    assert states(library)[af] == states(library)[cf] == states(library)[df] == "active"


def test_configured_same_stem_lossy_pair_is_protected(library):
    _a, af, pa = copy(library)
    _b, bf, pb = copy(library, fmt="mp3", album="Other")
    companion = pa.with_suffix(".mp3")
    pb.rename(companion)
    with closing(library[0]._get_connection()) as conn:
        conn.execute("UPDATE lib2_track_files SET path=? WHERE id=?", (str(companion), bf))
        conn.commit()
    library[1].values["lossy_copy.enabled"] = True
    assert review(library) == []
    assert pa.exists() and companion.exists()


def test_playlist_recommendation_and_protection_use_native_server_mapping(library):
    from core.library2.duplicate_review import find_duplicate_candidates, apply_keep_best
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    _b, bf, pb = copy(library, album="Other", fmt="flac")
    with closing(library[0]._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_media_server_mappings(entity_type,entity_id,server_source,server_id) VALUES('track',?,'plex','server-a')", (a,))
        conn.commit()
    db, config, _root, transfer = library
    candidates = find_duplicate_candidates(db, config, server_source="plex", playlist_membership={"server-a": ["Road Trip"]})
    assert candidates[0]["recommended_file_id"] == af
    assert next(t for t in candidates[0]["tracks"] if t["file_id"] == af)["playlists"] == ["Road Trip"]
    result = apply_keep_best(db, candidates[0], config_manager=config, transfer_folder=str(transfer),
                             approved=True, keep_file_id=bf, server_source="plex", playlist_membership={"server-a": ["Road Trip"]})
    assert result["success"] is True
    assert pa.exists() and pb.exists()


def test_failed_quarantine_rolls_back_prior_moves_and_leaves_links_unchanged(library, monkeypatch):
    from core.library import deleted_quarantine
    a, af, pa = copy(library)
    b, bf, pb = copy(library, album="Other")
    c, cf, pc = copy(library, album="Best", bit_depth=24)
    candidate = review(library)[0]
    original = deleted_quarantine.shutil.move
    def move(source, target, *args, **kwargs):
        if source == str(pb):
            raise OSError("quarantine unavailable")
        return original(source, target, *args, **kwargs)
    monkeypatch.setattr(deleted_quarantine.shutil, "move", move)
    result = apply(library, candidate, approved=True, keep_file_id=cf)
    assert result["success"] is False
    assert pa.exists() and pb.exists() and pc.exists()
    assert set(states(library).values()) == {"active"}
    with closing(library[0]._get_connection()) as conn:
        assert all(r[0] is None for r in conn.execute("SELECT canonical_track_id FROM lib2_tracks"))


def test_confirmed_fuzzy_titles_can_be_consolidated_without_relaxing_hard_ids(library):
    _a, af, pa = copy(library, title="Dreaming Tonight", isrc=None)
    _b, _bf, pb = copy(library, title="Dreamin Tonight", album="Other", isrc=None)
    candidate = review(library)[0]
    result = apply(library, candidate, approved=True, confirm_recording=True, keep_file_id=af)
    assert result["success"] is True, result
    assert pa.exists() and not pb.exists()


def test_companion_outside_scope_protects_its_lossless_parent(library):
    _a, af, pa = copy(library)
    _b, bf, pb = copy(library, album="Better", bit_depth=24)
    companion = pa.with_suffix(".mp3")
    companion.write_bytes(b"intentional lossy")
    library[1].values["lossy_copy.enabled"] = True
    candidate = review(library, scope={"file_paths": [str(pa), str(pb)]})[0]
    result = apply(library, candidate, approved=True, keep_file_id=bf)
    assert result["success"] is True
    assert pa.exists() and pb.exists() and companion.exists()


def test_new_shared_reference_and_hand_tag_win_over_stale_review(library):
    _a, af, pa = copy(library)
    b, bf, pb = copy(library, album="Other", bit_depth=24)
    candidate = review(library)[0]
    with closing(library[0]._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_track_files(track_id,path,owner_profile_id) VALUES(?,?,9)", (b, str(pa)))
        conn.commit()
    from database.music_database import MusicDatabase
    library[0].manual_path_keys = lambda: {MusicDatabase.manual_path_key(str(pa))}
    result = apply(library, candidate, approved=True, keep_file_id=bf)
    assert result["success"] is True
    assert pa.exists() and pb.exists()
    assert states(library)[af] == "active"


@pytest.mark.parametrize("protection", ["override", "pin"])
def test_manual_metadata_and_edition_pins_are_preserved(library, protection):
    a, af, pa = copy(library)
    _b, bf, pb = copy(library, album="Other", bit_depth=24)
    with closing(library[0]._get_connection()) as conn:
        if protection == "override":
            conn.execute("INSERT INTO lib2_metadata_overrides(entity_type,entity_id,field_name,value_json) VALUES('track',?,'title','\"Hand-set\"')", (a,))
        else:
            conn.execute("UPDATE lib2_albums SET canonical_locked=1,canonical_source='spotify',canonical_album_id='pinned' WHERE id=(SELECT album_id FROM lib2_tracks WHERE id=?)", (a,))
        conn.commit()
    result = apply(library, review(library)[0], approved=True, keep_file_id=bf)
    assert result["success"] is True
    assert pa.exists() and pb.exists() and states(library)[af] == "active"


def test_active_server_mapping_alone_does_not_block_keep_best(library):
    from core.library2.duplicate_review import apply_keep_best, find_duplicate_candidates
    db, config, _root, transfer = library
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    b, bf, pb = copy(library, album="Other", fmt="flac")
    with closing(db._get_connection()) as conn:
        for tid, server, sid in ((a, "plex", "pa"), (b, "plex", "pb"), (b, "jellyfin", "jb")):
            conn.execute("INSERT INTO lib2_media_server_mappings(entity_type,entity_id,server_source,server_id) "
                         "VALUES('track',?,?,?)", (tid, server, sid))
        conn.commit()
    candidate = find_duplicate_candidates(db, config, server_source="plex", playlist_membership={"x": ["L"]})[0]
    reasons = {t["file_id"]: t["protected_reasons"] for t in candidate["tracks"]}
    # Plex playlists are readable, so its mapping is no protection; Jellyfin's are not.
    assert reasons[af] == [] and reasons[bf] == ["media_server_reference"]
    result = apply_keep_best(db, candidate, config_manager=config, transfer_folder=str(transfer), approved=True,
                             keep_file_id=bf, server_source="plex", playlist_membership={"x": ["L"]})
    assert result["success"] is True and result["removed_file_ids"] == [af]
    assert not pa.exists() and pb.exists()


def test_unreadable_playlists_at_apply_keep_the_scans_playlist_protection(library):
    from core.library2.duplicate_review import find_duplicate_candidates
    db, config, *_ = library
    a, af, pa = copy(library, fmt="mp3", bitrate=128)
    _b, bf, _pb = copy(library, album="Other", fmt="flac")
    with closing(db._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_media_server_mappings(entity_type,entity_id,server_source,server_id) "
                     "VALUES('track',?,'plex','pa')", (a,))
        conn.commit()
    candidate = find_duplicate_candidates(db, config, server_source="plex", playlist_membership={"pa": ["Road Trip"]})[0]
    result = apply(library, candidate, approved=True, keep_file_id=bf, server_source="plex")
    assert result["success"] is True and result["removed_file_ids"] == []
    assert pa.exists() and states(library)[af] == "active"


def test_keep_best_still_sees_a_hardlink_under_another_name(library):
    """applying one finding no longer walks every file in the library, only
    the rows that could be the same physical file: a hardlink under another
    name still protects it."""
    import os
    _a, af, pa = copy(library)
    _b, bf, pb = copy(library, album="Other", bit_depth=24)
    candidate = review(library)[0]
    alias = pa.parent / "renamed alias.flac"
    os.link(pa, alias)
    with closing(library[0]._get_connection()) as conn:
        conn.execute("INSERT INTO lib2_track_files(track_id,path,owner_profile_id,size) VALUES(?,?,9,?)",
                     (_b, str(alias), os.path.getsize(pa)))
        conn.commit()
    result = apply(library, candidate, approved=True, keep_file_id=bf)
    assert result["success"] is True
    assert pa.exists()
    assert states(library)[af] == "active"


def _typed(library, tid, album_type):
    with closing(library[0]._get_connection()) as conn:
        conn.execute("UPDATE lib2_albums SET album_type=? WHERE id=(SELECT album_id FROM lib2_tracks WHERE id=?)",
                     (album_type, tid))
        conn.commit()


@pytest.mark.parametrize("single_first", [True, False])
def test_the_album_copy_is_kept_over_the_single_of_the_same_quality(library, single_first):
    """3.6.1's Single/Album Dedup kept the album version. Same quality, so
    nothing is lost, and the album keeps its numbering whichever row is newer."""
    order = [("Song - Single", "single"), ("Album", "album")]
    if not single_first:
        order.reverse()
    made = {}
    for album, kind in order:
        tid, fid, _ = copy(library, album=album, fmt="flac", bit_depth=16, sample_rate=44100)
        _typed(library, tid, kind)
        made[kind] = fid
    candidates = review(library)
    assert len(candidates) == 1
    assert candidates[0]["recommended_file_id"] == made["album"]


def test_a_better_single_is_never_given_up_for_the_album(library):
    single, single_fid, _ = copy(library, album="Song - Single", fmt="flac", bit_depth=24, sample_rate=96000)
    album, album_fid, _ = copy(library, album="Album", fmt="flac", bit_depth=16, sample_rate=44100)
    _typed(library, single, "single")
    _typed(library, album, "album")
    candidates = review(library)
    assert candidates[0]["recommended_file_id"] == single_fid



def test_a_hand_tagged_file_is_never_one_of_a_pair(library):
    """3.6.1 left hand-tagged files out before grouping: the live take or the
    bootleg someone tagged by hand next to the studio copy is not a
    duplicate, and Keep Best keeping it would quarantine the studio copy."""
    from database.music_database import MusicDatabase
    _a, _af, studio = copy(library)
    _b, _bf, bootleg = copy(library, album="Live Bootleg", bit_depth=24)
    _c, _cf, _third = copy(library, album="Other")
    library[0].manual_path_keys = lambda: {MusicDatabase.manual_path_key(str(bootleg))}

    groups = review(library)

    assert len(groups) == 1
    paths = {t["file_path"] for t in groups[0]["tracks"]}
    assert str(bootleg) not in paths and str(studio) in paths


def test_keep_best_refuses_a_keeper_tagged_by_hand_after_the_review(library):
    from database.music_database import MusicDatabase
    _a, _af, studio = copy(library)
    _b, bf, bootleg = copy(library, album="Live Bootleg", bit_depth=24)
    candidate = review(library)[0]
    library[0].manual_path_keys = lambda: {MusicDatabase.manual_path_key(str(bootleg))}

    result = apply(library, candidate, approved=True, keep_file_id=bf)

    assert result["success"] is False and "tagged by hand" in result["error"]
    assert studio.exists() and bootleg.exists()


# --- #1263: "Various Artists" is never artist evidence --------------------------


def _performer(library, tid, name=None, tag=None):
    import json
    with closing(library[0]._get_connection()) as conn:
        if name is not None:
            conn.execute("UPDATE lib2_tracks SET track_artist=? WHERE id=?", (name, tid))
        if tag is not None:
            conn.execute("UPDATE lib2_track_files SET tags_json=? WHERE track_id=?",
                         (json.dumps({"artist": tag}), tid))
        conn.commit()


def test_two_performers_intro_on_one_compilation_are_not_duplicates(library):
    """every song on a compilation carries the album's "Various Artists"
    credit, so two different performers' "Intro" matched on it"""
    a, _af, _ = copy(library, artist="Various Artists", album="Now 99", title="Intro", isrc=None)
    b, _bf, _ = copy(library, artist="Various Artists", album="Now 99 Disc 2", title="Intro", isrc=None)
    _performer(library, a, "Kendrick Lamar")
    _performer(library, b, tag="Taylor Swift")
    assert review(library) == []


def test_a_compilation_song_still_pairs_with_its_performers_own_copy(library):
    """the #1263 match itself: the compilation copy and the artist's album copy"""
    comp, _cf, _ = copy(library, artist="Various Artists", album="Now 99", title="Alright", isrc=None)
    _own, _of, _ = copy(library, artist="Kendrick Lamar", album="To Pimp a Butterfly",
                        title="Alright", isrc=None)
    _performer(library, comp, tag="Kendrick Lamar")
    groups = review(library)
    assert len(groups) == 1 and groups[0]["count"] == 2


def test_compilation_songs_with_no_performer_named_never_pair_on_the_credit(library):
    copy(library, artist="Various Artists", album="Now 99", title="Intro", isrc=None)
    copy(library, artist="Various Artists", album="Now 100", title="Intro", isrc=None)
    assert review(library) == []


# --- Keep Best says what stopped it (3.6.1's PUID/PGID hint) ---------------------


def _failing_keep_best(library, monkeypatch, error):
    from core.library import deleted_quarantine
    _a, _af, pa = copy(library)
    _b, bf, pb = copy(library, album="Other", bit_depth=24)
    candidate = review(library)[0]

    def move(source, target, *args, **kwargs):
        raise error
    monkeypatch.setattr(deleted_quarantine.shutil, "move", move)
    result = apply(library, candidate, approved=True, keep_file_id=bf)
    return result, pa, pb


def test_a_permission_failure_names_the_file_and_the_puid_pgid_fix(library, monkeypatch):
    result, pa, pb = _failing_keep_best(
        library, monkeypatch, PermissionError(13, "Permission denied"))
    assert result["success"] is False
    assert str(pa) in result["error"] and "Permission denied" in result["error"]
    assert "PUID/PGID" in result["error"]
    assert "Nothing was removed" in result["error"]
    assert pa.exists() and pb.exists()


def test_another_failure_gives_its_reason_without_the_permission_hint(library, monkeypatch):
    result, _pa, _pb = _failing_keep_best(library, monkeypatch, OSError("No space left on device"))
    assert "No space left on device" in result["error"]
    assert "PUID" not in result["error"]


# --- slskd's "_<timestamp>" twin with no artist tag ------------------------------


def _named(library, tid, name):
    db, _config, root, _transfer = library
    with closing(db._get_connection()) as conn:
        old = conn.execute("SELECT path FROM lib2_track_files WHERE track_id=?", (tid,)).fetchone()[0]
        new = os.path.join(str(root), name)
        os.rename(old, new)
        conn.execute("UPDATE lib2_track_files SET path=? WHERE track_id=?", (new, tid))
        conn.commit()
    return new


@pytest.mark.parametrize("blank", ["", "Unknown Artist"])
def test_an_slskd_twin_with_no_artist_pairs_on_name_and_length(library, blank):
    """3.6.1 paired "Song.flac" with slskd's "Song_638662543210987654.flac" when the
    twin carried no artist: same name, same length"""
    a, _af, _ = copy(library, artist="M83", title="Midnight City", isrc=None)
    b, _bf, _ = copy(library, album="Unknown Album", artist=blank or "Unknown Artist",
                     title="midnight city (1)", isrc=None)
    _named(library, a, "Midnight City.flac")
    _named(library, b, "Midnight City_638662543210987654.flac")
    if blank == "":
        with closing(library[0]._get_connection()) as conn:
            conn.execute("DELETE FROM lib2_track_artists WHERE track_id=?", (b,))
            conn.execute("UPDATE lib2_tracks SET track_artist=NULL WHERE id=?", (b,))
            conn.commit()
    groups = review(library)
    assert len(groups) == 1 and groups[0]["count"] == 2


def test_a_blank_artist_twin_of_another_length_is_not_paired(library):
    a, _af, _ = copy(library, artist="M83", title="Midnight City", isrc=None)
    b, _bf, _ = copy(library, album="Unknown Album", artist="Unknown Artist",
                     title="midnight city (1)", isrc=None, duration=260000)
    _named(library, a, "Midnight City.flac")
    _named(library, b, "Midnight City_638662543210987654.flac")
    assert review(library) == []


def test_a_named_different_artist_twin_is_still_not_paired(library):
    a, _af, _ = copy(library, artist="M83", title="Intro", isrc=None)
    b, _bf, _ = copy(library, album="Other", artist="Someone Else", title="Intro (live)", isrc=None)
    _named(library, a, "Intro.flac")
    _named(library, b, "Intro_638662543210987654.flac")
    assert review(library) == []


@pytest.mark.parametrize("version", ["Smells Like Teen Spirit (Live)",
                                     "Smells Like Teen Spirit - Radio Edit",
                                     "Smells Like Teen Spirit (Acoustic)"])
def test_a_live_or_edit_take_is_not_the_studio_cut(library, version):
    """third #1062 review G12-5: 3.6.1's Single/Album Dedup never paired a
    version with the studio recording; a title score of 0.90 is not proof"""
    copy(library, title="Smells Like Teen Spirit", isrc=None)
    copy(library, title=version, isrc=None, album="Other")
    assert review(library) == []


def test_a_shared_isrc_still_pairs_whatever_the_title_says(library):
    copy(library, title="Song", isrc="SAME")
    copy(library, title="Song (Remastered)", isrc="SAME", album="Other")
    assert len(review(library)) == 1
