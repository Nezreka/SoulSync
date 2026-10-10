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
