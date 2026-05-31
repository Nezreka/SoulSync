import json
import os

from core.imports.quarantine import (
    approve_quarantine_entry,
    delete_quarantine_entry,
    entry_id_from_quarantined_filename,
    get_quarantine_entry_stream_info,
    get_quarantined_source_keys,
    list_quarantine_entries,
    recover_to_staging,
    serialize_quarantine_context,
)
from core.imports.pipeline import _should_skip_quarantine_check


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


# ──────────────────────────────────────────────────────────────────────
# get_quarantine_entry_stream_info — in-app "Listen" support
# ──────────────────────────────────────────────────────────────────────

def test_stream_info_resolves_path_and_extension_from_sidecar(tmp_path):
    qfile, _ = _write_entry(tmp_path, "20260514_120000", "song.flac", with_context=True)
    entry_id = entry_id_from_quarantined_filename(qfile.name)

    info = get_quarantine_entry_stream_info(str(tmp_path), entry_id)

    assert info is not None
    file_path, ext = info
    assert file_path == str(qfile)
    assert ext == ".flac"  # real audio ext, NOT ".quarantined"


def test_stream_info_recovers_extension_without_sidecar(tmp_path):
    # Orphan .quarantined with no sidecar — extension comes from the filename
    # convention so playback still gets a correct Content-Type.
    qfile = tmp_path / "20260514_120000_orphan.mp3.quarantined"
    qfile.write_bytes(b"X" * 100)
    entry_id = entry_id_from_quarantined_filename(qfile.name)

    info = get_quarantine_entry_stream_info(str(tmp_path), entry_id)

    assert info is not None
    file_path, ext = info
    assert file_path == str(qfile)
    assert ext == ".mp3"


def test_stream_info_returns_none_for_missing_entry(tmp_path):
    assert get_quarantine_entry_stream_info(str(tmp_path), "does_not_exist") is None


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


def test_entry_id_helper_handles_paths_and_quarantine_suffix():
    path = "/music/ss_quarantine/20260514_120000_song.flac.quarantined"
    assert entry_id_from_quarantined_filename(path) == "20260514_120000_song"


def test_quarantine_bypass_all_skips_every_gate():
    context = {"_skip_quarantine_check": "all"}
    assert _should_skip_quarantine_check(context, "integrity") is True
    assert _should_skip_quarantine_check(context, "acoustid") is True
    assert _should_skip_quarantine_check(context, "bit_depth") is True


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


# ──────────────────────────────────────────────────────────────────────
# get_quarantined_source_keys — issue #652 dedup primitive
# ──────────────────────────────────────────────────────────────────────


def _write_quarantine_sidecar_with_source(quarantine_dir, entry_id, *,
                                          username=None, filename=None):
    """Helper that writes a sidecar matching the shape `move_to_quarantine`
    produces — `context.original_search_result.{username, filename}` is
    the path `get_quarantined_source_keys` pulls from."""
    sidecar = {
        "original_filename": "song.flac",
        "quarantine_reason": "boom",
        "timestamp": "2026-05-14T12:00:00",
        "trigger": "acoustid",
    }
    if username is not None or filename is not None:
        sidecar["context"] = {
            "original_search_result": {
                "username": username or "",
                "filename": filename or "",
            }
        }
    path = quarantine_dir / f"{entry_id}.json"
    path.write_text(json.dumps(sidecar))
    return path


def test_source_keys_empty_for_missing_dir(tmp_path):
    """Defensive: caller may pass a path that doesn't exist (config not
    initialised, quarantine never used). Don't crash, just return an
    empty set — Soulseek filter then keeps every candidate."""
    assert get_quarantined_source_keys(str(tmp_path / "nope")) == set()


def test_source_keys_empty_for_empty_dir(tmp_path):
    """Empty quarantine dir → empty set."""
    assert get_quarantined_source_keys(str(tmp_path)) == set()


def test_source_keys_collects_username_filename_tuples(tmp_path):
    """Sidecars with `context.original_search_result.username` and
    `.filename` round-trip into `(username, filename)` tuples — that's
    the exact shape the Soulseek candidate filter looks up against."""
    _write_quarantine_sidecar_with_source(
        tmp_path, "20260514_120000_a",
        username="badpeer", filename="path/to/bad.flac",
    )
    _write_quarantine_sidecar_with_source(
        tmp_path, "20260514_120100_b",
        username="otherpeer", filename="other.mp3",
    )

    keys = get_quarantined_source_keys(str(tmp_path))

    assert ("badpeer", "path/to/bad.flac") in keys
    assert ("otherpeer", "other.mp3") in keys
    assert len(keys) == 2


def test_source_keys_skip_legacy_sidecars_without_context(tmp_path):
    """Sidecars written pre-Feb 2026 don't have the `context` field —
    can't gate against them since the originating source is unknown.
    Must skip silently rather than crashing the dedup path."""
    _write_quarantine_sidecar_with_source(tmp_path, "legacy_id")  # no username/filename

    assert get_quarantined_source_keys(str(tmp_path)) == set()


def test_source_keys_skip_sidecars_with_empty_source_fields(tmp_path):
    """Defensive: a sidecar with an empty string for username OR filename
    can't gate anything meaningfully — dropping every result whose
    username equals '' would catch unrelated downloads. Skip those
    entries entirely."""
    _write_quarantine_sidecar_with_source(tmp_path, "empty_user", username="", filename="x.flac")
    _write_quarantine_sidecar_with_source(tmp_path, "empty_file", username="u", filename="")

    assert get_quarantined_source_keys(str(tmp_path)) == set()


def test_source_keys_skip_corrupt_sidecars(tmp_path):
    """A corrupt JSON sidecar (truncated write, encoding glitch) must
    not propagate up and break the dedup path. Filesystem read errors
    are swallowed at debug level."""
    bad = tmp_path / "corrupt.json"
    bad.write_text("{not valid json")
    _write_quarantine_sidecar_with_source(
        tmp_path, "good", username="good_peer", filename="good.flac",
    )

    keys = get_quarantined_source_keys(str(tmp_path))

    assert keys == {("good_peer", "good.flac")}


def test_source_keys_dedup_repeated_sources(tmp_path):
    """If the SAME `(username, filename)` was quarantined twice (which
    is exactly the #652 bug — but until now wasn't being prevented),
    the set collapses to one entry. The Soulseek filter still acts as
    a single-membership check, so a single set entry is enough."""
    _write_quarantine_sidecar_with_source(
        tmp_path, "first", username="peer", filename="dupe.flac",
    )
    _write_quarantine_sidecar_with_source(
        tmp_path, "second", username="peer", filename="dupe.flac",
    )

    keys = get_quarantined_source_keys(str(tmp_path))

    assert keys == {("peer", "dupe.flac")}


# ──────────────────────────────────────────────────────────────────────
# _move_with_retry — resilient move (Windows file-lock case)
# ──────────────────────────────────────────────────────────────────────

def test_move_with_retry_succeeds(tmp_path):
    from core.imports.quarantine import _move_with_retry
    src = tmp_path / "a.flac"; src.write_bytes(b"x" * 10)
    dst = tmp_path / "out" / "a.flac"
    (tmp_path / "out").mkdir()
    assert _move_with_retry(str(src), str(dst)) is True
    assert dst.exists() and not src.exists()


def test_move_with_retry_returns_false_on_missing_source(tmp_path):
    from core.imports.quarantine import _move_with_retry
    # attempts=1 keeps the test fast (no retry sleeps)
    assert _move_with_retry(str(tmp_path / "nope.flac"), str(tmp_path / "dst.flac"),
                            attempts=1, delay=0) is False
