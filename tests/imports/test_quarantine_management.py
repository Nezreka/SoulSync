import json
import os

from core.imports.quarantine import (
    approve_quarantine_entry,
    delete_quarantine_entry,
    list_quarantine_entries,
    recover_to_staging,
    serialize_quarantine_context,
)


# ──────────────────────────────────────────────────────────────────────
# serialize_quarantine_context — JSON-safe coercion
# ──────────────────────────────────────────────────────────────────────

def test_serialize_passes_scalar_dict_unchanged():
    ctx = {"title": "DNA.", "track_number": 2, "active": True, "missing": None, "duration_ms": 185000}
    out = serialize_quarantine_context(ctx)
    assert out == ctx


def test_serialize_walks_nested_dicts():
    ctx = {"track_info": {"name": "DNA.", "artists": [{"name": "Kendrick"}, {"name": "Rihanna"}]}}
    out = serialize_quarantine_context(ctx)
    assert out == ctx


def test_serialize_coerces_set_to_list():
    ctx = {"sources": {"spotify", "deezer"}}
    out = serialize_quarantine_context(ctx)
    assert sorted(out["sources"]) == ["deezer", "spotify"]


def test_serialize_coerces_tuple_to_list():
    ctx = {"pair": (1, 2, 3)}
    out = serialize_quarantine_context(ctx)
    assert out == {"pair": [1, 2, 3]}


def test_serialize_stringifies_unknown_objects():
    class Custom:
        def __str__(self):
            return "<custom obj>"
    out = serialize_quarantine_context({"obj": Custom()})
    assert out["obj"] == "<custom obj>"


def test_serialize_non_dict_returns_empty_dict():
    assert serialize_quarantine_context(None) == {}
    assert serialize_quarantine_context("string") == {}
    assert serialize_quarantine_context([1, 2, 3]) == {}


def test_serialize_round_trips_through_json():
    ctx = {
        "track_info": {"name": "X", "artists": [{"name": "A"}, {"name": "B"}]},
        "spotify_artist": {"name": "A", "id": "abc"},
        "duration_ms": 180000,
        "sources": {"spotify"},
    }
    serialized = serialize_quarantine_context(ctx)
    json.dumps(serialized)  # must not raise


# ──────────────────────────────────────────────────────────────────────
# list_quarantine_entries
# ──────────────────────────────────────────────────────────────────────

def _write_entry(quarantine_dir, entry_id, original_name, *, with_context=False, trigger="integrity", reason="boom", file_bytes=b"X" * 100):
    qfile = quarantine_dir / f"{entry_id}_{original_name}.quarantined"
    qfile.write_bytes(file_bytes)
    sidecar = {
        "original_filename": original_name,
        "quarantine_reason": reason,
        "expected_track": "Track",
        "expected_artist": "Artist",
        "timestamp": "2026-05-14T12:00:00",
        "trigger": trigger,
    }
    if with_context:
        sidecar["context"] = {"track_info": {"name": "Track"}, "context_key": entry_id}
    sidecar_path = quarantine_dir / f"{entry_id}_{os.path.splitext(original_name)[0]}.json"
    sidecar_path.write_text(json.dumps(sidecar))
    return qfile, sidecar_path


def test_list_returns_empty_for_missing_dir(tmp_path):
    assert list_quarantine_entries(str(tmp_path / "nope")) == []


def test_list_returns_empty_for_empty_dir(tmp_path):
    assert list_quarantine_entries(str(tmp_path)) == []


def test_list_returns_entry_with_sidecar_fields(tmp_path):
    _write_entry(tmp_path, "20260514_120000", "song.flac", reason="Duration mismatch")
    entries = list_quarantine_entries(str(tmp_path))
    assert len(entries) == 1
    e = entries[0]
    assert e["original_filename"] == "song.flac"
    assert e["reason"] == "Duration mismatch"
    assert e["expected_track"] == "Track"
    assert e["expected_artist"] == "Artist"
    assert e["has_full_context"] is False
    assert e["trigger"] == "integrity"
    assert e["size_bytes"] == 100


def test_list_flags_full_context_entries(tmp_path):
    _write_entry(tmp_path, "20260514_120000", "song.flac", with_context=True)
    entries = list_quarantine_entries(str(tmp_path))
    assert entries[0]["has_full_context"] is True


def test_list_handles_orphan_quarantined_file_without_sidecar(tmp_path):
    qfile = tmp_path / "20260514_120000_orphan.flac.quarantined"
    qfile.write_bytes(b"X")
    entries = list_quarantine_entries(str(tmp_path))
    assert len(entries) == 1
    assert entries[0]["reason"] == "Unknown reason"
    assert entries[0]["has_full_context"] is False


def test_list_skips_orphan_sidecars_without_file(tmp_path):
    sidecar = tmp_path / "20260514_120000_only.json"
    sidecar.write_text(json.dumps({"original_filename": "only.flac", "quarantine_reason": "x"}))
    assert list_quarantine_entries(str(tmp_path)) == []


def test_list_sorts_newest_first(tmp_path):
    _write_entry(tmp_path, "20260101_120000", "old.flac")
    _write_entry(tmp_path, "20260514_120000", "new.flac")
    entries = list_quarantine_entries(str(tmp_path))
    assert entries[0]["original_filename"] == "new.flac"
    assert entries[1]["original_filename"] == "old.flac"


def test_list_swallows_corrupt_sidecar_gracefully(tmp_path):
    qfile = tmp_path / "20260514_120000_song.flac.quarantined"
    qfile.write_bytes(b"X")
    sidecar = tmp_path / "20260514_120000_song.json"
    sidecar.write_text("{ this is not valid json")
    entries = list_quarantine_entries(str(tmp_path))
    assert len(entries) == 1
    assert entries[0]["reason"] == "Unknown reason"


# ──────────────────────────────────────────────────────────────────────
# delete_quarantine_entry
# ──────────────────────────────────────────────────────────────────────

def test_delete_removes_both_file_and_sidecar(tmp_path):
    _write_entry(tmp_path, "20260514_120000", "song.flac")
    assert delete_quarantine_entry(str(tmp_path), "20260514_120000_song") is True
    assert not (tmp_path / "20260514_120000_song.flac.quarantined").exists()
    assert not (tmp_path / "20260514_120000_song.json").exists()


def test_delete_returns_false_when_entry_missing(tmp_path):
    assert delete_quarantine_entry(str(tmp_path), "nonexistent") is False


def test_delete_handles_orphan_file_without_sidecar(tmp_path):
    qfile = tmp_path / "20260514_120000_orphan.flac.quarantined"
    qfile.write_bytes(b"X")
    assert delete_quarantine_entry(str(tmp_path), "20260514_120000_orphan") is True
    assert not qfile.exists()


# ──────────────────────────────────────────────────────────────────────
# approve_quarantine_entry — full-context path
# ──────────────────────────────────────────────────────────────────────

def test_approve_restores_file_and_returns_context_and_trigger(tmp_path):
    quarantine = tmp_path / "ss_quarantine"
    quarantine.mkdir()
    restore = tmp_path / "restore"

    _write_entry(quarantine, "20260514_120000", "song.flac", with_context=True, trigger="integrity")

    result = approve_quarantine_entry(str(quarantine), "20260514_120000_song", str(restore))
    assert result is not None
    restored_path, context, trigger = result
    assert os.path.basename(restored_path) == "song.flac"
    assert os.path.isfile(restored_path)
    assert context["track_info"]["name"] == "Track"
    assert trigger == "integrity"
    # Sidecar removed after approve
    assert not (quarantine / "20260514_120000_song.json").exists()


def test_approve_returns_none_for_thin_sidecar_without_context(tmp_path):
    _write_entry(tmp_path, "20260514_120000", "song.flac", with_context=False)
    result = approve_quarantine_entry(str(tmp_path), "20260514_120000_song", str(tmp_path / "restore"))
    assert result is None


def test_approve_returns_none_for_missing_entry(tmp_path):
    assert approve_quarantine_entry(str(tmp_path), "nope", str(tmp_path)) is None


def test_approve_avoids_filename_collision(tmp_path):
    quarantine = tmp_path / "q"
    quarantine.mkdir()
    restore = tmp_path / "r"
    restore.mkdir()
    (restore / "song.flac").write_bytes(b"existing")
    _write_entry(quarantine, "20260514_120000", "song.flac", with_context=True)
    result = approve_quarantine_entry(str(quarantine), "20260514_120000_song", str(restore))
    assert result is not None
    restored_path = result[0]
    assert os.path.basename(restored_path) == "song_(2).flac"
    assert (restore / "song.flac").read_bytes() == b"existing"


# ──────────────────────────────────────────────────────────────────────
# recover_to_staging — fallback for thin sidecars
# ──────────────────────────────────────────────────────────────────────

def test_recover_strips_prefix_and_suffix(tmp_path):
    quarantine = tmp_path / "q"
    quarantine.mkdir()
    staging = tmp_path / "s"

    qfile, _ = _write_entry(quarantine, "20260514_120000", "song.flac")

    target = recover_to_staging(str(quarantine), str(staging), "20260514_120000_song")
    assert target is not None
    assert os.path.basename(target) == "song.flac"
    assert os.path.isfile(target)
    assert not qfile.exists()


def test_recover_uses_sidecar_original_filename_when_available(tmp_path):
    quarantine = tmp_path / "q"
    quarantine.mkdir()
    staging = tmp_path / "s"
    qfile = quarantine / "20260514_120000_munged_name.flac.quarantined"
    qfile.write_bytes(b"X")
    sidecar = quarantine / "20260514_120000_munged_name.json"
    sidecar.write_text(json.dumps({"original_filename": "Pretty Track Name.flac"}))

    target = recover_to_staging(str(quarantine), str(staging), "20260514_120000_munged_name")
    assert target is not None
    assert os.path.basename(target) == "Pretty Track Name.flac"


def test_recover_returns_none_for_missing_entry(tmp_path):
    assert recover_to_staging(str(tmp_path / "q"), str(tmp_path / "s"), "nope") is None


def test_recover_avoids_filename_collision(tmp_path):
    quarantine = tmp_path / "q"
    quarantine.mkdir()
    staging = tmp_path / "s"
    staging.mkdir()
    (staging / "song.flac").write_bytes(b"existing")
    _write_entry(quarantine, "20260514_120000", "song.flac")

    target = recover_to_staging(str(quarantine), str(staging), "20260514_120000_song")
    assert target is not None
    assert os.path.basename(target) == "song_(2).flac"


def test_recover_removes_sidecar_after_move(tmp_path):
    quarantine = tmp_path / "q"
    quarantine.mkdir()
    staging = tmp_path / "s"
    _, sidecar = _write_entry(quarantine, "20260514_120000", "song.flac")

    recover_to_staging(str(quarantine), str(staging), "20260514_120000_song")
    assert not sidecar.exists()
