"""the browsable deleted quarantine - list, restore, purge, retention.

every test builds its own transfer folder in tmp_path. nothing here may
touch a real database or the network.
"""

import json
import os
from datetime import datetime, timedelta, timezone

from core.library import deleted_quarantine as dq


def _mk(transfer, rel, content=b"x"):
    path = os.path.join(transfer, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(content)
    return path


def _quarantine(transfer, rel, source="repair", deleted_at=None):
    """Simulate what a mover does: file into .deleted + manifest record."""
    root = os.path.join(transfer, ".deleted")
    dest = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b"quarantined")
    dq.record_deleted_entry(root, dest, os.path.join(transfer, *rel.split("/")), source)
    if deleted_at is not None:
        manifest = dq._load_manifest(root)
        manifest[rel]["deleted_at"] = deleted_at
        dq._save_manifest(root, manifest)
    return dest


class TestRecordAndList:
    def test_a_recorded_entry_lists_with_its_provenance(self, tmp_path):
        transfer = str(tmp_path)
        _quarantine(transfer, "Artist/Album/01 - Song.flac")
        result = dq.list_entries(transfer)
        assert result["count"] == 1
        entry = result["entries"][0]
        assert entry["id"] == "deleted:Artist/Album/01 - Song.flac"
        assert entry["source"] == "repair"
        assert entry["deleted_at"]
        assert entry["original_path"].endswith(os.path.join("Artist", "Album", "01 - Song.flac"))
        assert result["total_size"] == len(b"quarantined")

    def test_a_pre_manifest_file_still_lists_with_derived_original(self, tmp_path):
        transfer = str(tmp_path)
        root = os.path.join(transfer, ".deleted")
        _mk(root, "Old/legacy.mp3")
        entry = dq.list_entries(transfer)["entries"][0]
        assert entry["deleted_at"] is None
        assert entry["source"] is None
        assert entry["original_path"] == os.path.join(transfer, "Old", "legacy.mp3")

    def test_the_manifest_itself_never_lists(self, tmp_path):
        transfer = str(tmp_path)
        _quarantine(transfer, "a.mp3")
        names = [e["name"] for e in dq.list_entries(transfer)["entries"]]
        assert dq.MANIFEST_NAME not in names

    def test_a_legacy_bare_deleted_folder_is_listed_too(self, tmp_path):
        transfer = str(tmp_path)
        # both spellings exist -> deleted_quarantine_root leaves legacy alone
        os.makedirs(os.path.join(transfer, ".deleted"))
        _mk(os.path.join(transfer, "deleted"), "old.mp3")
        entries = dq.list_entries(transfer)["entries"]
        assert [e["id"] for e in entries] == ["legacy:old.mp3"]

    def test_an_empty_transfer_lists_nothing(self, tmp_path):
        assert dq.list_entries(str(tmp_path)) == {"entries": [], "total_size": 0, "count": 0}


class TestRestore:
    def test_restore_puts_the_file_back_at_its_original_path(self, tmp_path):
        transfer = str(tmp_path)
        _quarantine(transfer, "Artist/Album/song.flac")
        result = dq.restore_entries(transfer, ["deleted:Artist/Album/song.flac"])
        assert result["errors"] == []
        assert os.path.isfile(os.path.join(transfer, "Artist", "Album", "song.flac"))
        assert dq.list_entries(transfer)["count"] == 0

    def test_restore_refuses_to_overwrite_an_existing_file(self, tmp_path):
        transfer = str(tmp_path)
        _quarantine(transfer, "song.flac")
        _mk(transfer, "song.flac", b"the real one")
        result = dq.restore_entries(transfer, ["deleted:song.flac"])
        assert result["restored"] == []
        assert "already exists" in result["errors"][0]["error"]
        # the quarantined copy is untouched
        assert dq.list_entries(transfer)["count"] == 1
        with open(os.path.join(transfer, "song.flac"), "rb") as f:
            assert f.read() == b"the real one"

    def test_restore_prefers_the_manifest_original_over_the_derived_path(self, tmp_path):
        transfer = str(tmp_path)
        outside = str(tmp_path / "library" / "song.flac")
        root = os.path.join(transfer, ".deleted")
        dest = _mk(root, "song.flac")
        dq.record_deleted_entry(root, dest, outside, "repair")
        result = dq.restore_entries(transfer, ["deleted:song.flac"])
        assert result["errors"] == []
        assert os.path.isfile(outside)

    def test_restore_cleans_up_the_emptied_quarantine_subfolders(self, tmp_path):
        transfer = str(tmp_path)
        _quarantine(transfer, "Artist/Album/song.flac")
        dq.restore_entries(transfer, ["deleted:Artist/Album/song.flac"])
        assert not os.path.exists(os.path.join(transfer, ".deleted", "Artist"))
        assert os.path.isdir(os.path.join(transfer, ".deleted"))

    def test_a_traversal_id_is_refused(self, tmp_path):
        transfer = str(tmp_path)
        victim = _mk(transfer, "victim.mp3")
        os.makedirs(os.path.join(transfer, ".deleted"))
        for evil in ("deleted:../victim.mp3", "deleted:..\\victim.mp3",
                     "deleted:/etc/passwd", "nonsense", "deleted:", "legacy:x.mp3"):
            result = dq.restore_entries(transfer, [evil])
            assert result["restored"] == [], evil
            assert result["errors"][0]["error"] == "unknown entry", evil
        assert os.path.isfile(victim)


class TestPurge:
    def test_purge_deletes_for_real_and_drops_the_manifest_row(self, tmp_path):
        transfer = str(tmp_path)
        _quarantine(transfer, "a/b.mp3")
        result = dq.purge_entries(transfer, ["deleted:a/b.mp3"])
        assert result["errors"] == []
        assert dq.list_entries(transfer)["count"] == 0
        assert dq._load_manifest(os.path.join(transfer, ".deleted")) == {}

    def test_purge_all_empties_the_bin(self, tmp_path):
        transfer = str(tmp_path)
        _quarantine(transfer, "a.mp3")
        _quarantine(transfer, "b/c.mp3")
        result = dq.purge_entries(transfer, purge_all=True)
        assert len(result["purged"]) == 2
        assert dq.list_entries(transfer)["count"] == 0

    def test_purge_refuses_traversal_ids(self, tmp_path):
        transfer = str(tmp_path)
        victim = _mk(transfer, "victim.mp3")
        os.makedirs(os.path.join(transfer, ".deleted"))
        result = dq.purge_entries(transfer, ["deleted:../victim.mp3"])
        assert result["purged"] == []
        assert os.path.isfile(victim)


class TestRetention:
    def _age(self, days):
        return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")

    def test_expired_manifested_entries_are_purged_on_list(self, tmp_path):
        transfer = str(tmp_path)
        _quarantine(transfer, "old.mp3", deleted_at=self._age(30))
        _quarantine(transfer, "fresh.mp3", deleted_at=self._age(1))
        result = dq.list_entries(transfer, keep_days=7)
        assert [e["rel"] for e in result["entries"]] == ["fresh.mp3"]

    def test_unmanifested_files_never_age_out(self, tmp_path):
        """mtime survives the quarantine move, so it dates the FILE, not the
        deletion - aging by it would purge a fresh delete of an old file."""
        transfer = str(tmp_path)
        root = os.path.join(transfer, ".deleted")
        path = _mk(root, "ancient.mp3")
        ancient = (datetime.now(timezone.utc) - timedelta(days=400)).timestamp()
        os.utime(path, (ancient, ancient))
        result = dq.list_entries(transfer, keep_days=7)
        assert [e["rel"] for e in result["entries"]] == ["ancient.mp3"]

    def test_keep_days_zero_purges_nothing(self, tmp_path):
        transfer = str(tmp_path)
        _quarantine(transfer, "old.mp3", deleted_at=self._age(3000))
        assert dq.purge_expired(transfer, 0) == 0
        assert dq.list_entries(transfer)["count"] == 1


class TestManifestResilience:
    def test_a_corrupt_manifest_blocks_nothing(self, tmp_path):
        transfer = str(tmp_path)
        root = os.path.join(transfer, ".deleted")
        _mk(root, "song.mp3")
        with open(os.path.join(root, dq.MANIFEST_NAME), "w") as f:
            f.write("{not json")
        assert dq.list_entries(transfer)["count"] == 1
        result = dq.restore_entries(transfer, ["deleted:song.mp3"])
        assert result["errors"] == []

    def test_record_never_raises_even_on_a_readonly_root(self, tmp_path):
        # a bogus root path must not raise out of the mover's success path
        dq.record_deleted_entry("/nonexistent/root", "/nonexistent/root/x.mp3",
                                "/somewhere/x.mp3", "repair")
