"""core.library.cleanup: the quarantine walk and the recycle bin purge, and
the sweep that runs both.

LettuceSnob (Sep 17 2026): "is there any way for me to manually trigger that
scrub myself?" - the recycle bin's retention only ran when someone opened the
Recycle Bin tab, and the quarantine walk was copied into three places (api
route, clear_quarantine, full_cleanup). These pin the ONE implementation all
of them now share. Every test builds its own folders in tmp_path; nothing
here touches a real database or the network.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from core.library import cleanup, deleted_quarantine as dq


def _quarantine(transfer, rel, deleted_at=None):
    """What a mover does: file into .deleted + manifest record."""
    root = os.path.join(transfer, ".deleted")
    dest = os.path.join(root, *rel.split("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(b"quarantined")
    dq.record_deleted_entry(root, dest, os.path.join(transfer, *rel.split("/")), "repair")
    if deleted_at is not None:
        manifest = dq._load_manifest(root)
        manifest[rel]["deleted_at"] = deleted_at
        dq._save_manifest(root, manifest)
    return dest


def _days_ago(n):
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


# ── clear_quarantine_folder ──────────────────────────────────────────────────

class TestClearQuarantineFolder:
    def test_a_missing_folder_is_an_empty_one(self, tmp_path):
        out = cleanup.clear_quarantine_folder(str(tmp_path / "nope"))
        assert out == {"existed": False, "removed": 0, "errors": []}

    def test_files_and_folders_each_count_once(self, tmp_path):
        q = tmp_path / "ss_quarantine"
        q.mkdir()
        (q / "a.flac").write_bytes(b"")
        (q / "album").mkdir()
        (q / "album" / "1.flac").write_bytes(b"")
        (q / "album" / "2.flac").write_bytes(b"")
        out = cleanup.clear_quarantine_folder(str(q))
        assert out == {"existed": True, "removed": 2, "errors": []}
        assert os.listdir(q) == []
        assert q.is_dir()          # the folder itself stays

    def test_a_symlink_is_removed_not_followed(self, tmp_path):
        # a link into the real library must not rmtree the library
        real = tmp_path / "library"
        real.mkdir()
        (real / "keep.flac").write_bytes(b"")
        q = tmp_path / "ss_quarantine"
        q.mkdir()
        os.symlink(real, q / "linked")
        out = cleanup.clear_quarantine_folder(str(q))
        assert out["removed"] == 1 and out["errors"] == []
        assert not os.path.lexists(q / "linked")
        assert (real / "keep.flac").exists()

    def test_one_bad_entry_is_reported_and_the_rest_still_go(self, tmp_path, monkeypatch):
        q = tmp_path / "ss_quarantine"
        q.mkdir()
        for n in ("a.flac", "b.flac", "c.flac"):
            (q / n).write_bytes(b"")
        real_remove = os.remove

        def _remove(p):
            if p.endswith("b.flac"):
                raise PermissionError("locked")
            real_remove(p)
        monkeypatch.setattr(cleanup.os, "remove", _remove)
        out = cleanup.clear_quarantine_folder(str(q))
        assert out["removed"] == 2
        assert out["errors"] == [{"entry": "b.flac", "error": "locked"}]
        assert sorted(os.listdir(q)) == ["b.flac"]


# ── purge_recycle_bin ────────────────────────────────────────────────────────

class TestPurgeRecycleBin:
    def test_a_keep_window_purges_only_what_is_older_than_it(self, tmp_path):
        transfer = str(tmp_path)
        old = _quarantine(transfer, "A/old.flac", deleted_at=_days_ago(10))
        fresh = _quarantine(transfer, "A/fresh.flac", deleted_at=_days_ago(2))
        out = cleanup.purge_recycle_bin(transfer, 7)
        assert out == {"mode": "expired", "keep_days": 7.0, "purged": 1, "errors": []}
        assert not os.path.exists(old) and os.path.exists(fresh)

    def test_a_keep_window_leaves_unmanifested_files_alone(self, tmp_path):
        # no deleted_at means no honest age: the tab never age-purges these
        # and neither does the automation
        transfer = str(tmp_path)
        root = os.path.join(transfer, ".deleted", "B")
        os.makedirs(root)
        bare = os.path.join(root, "bare.flac")
        with open(bare, "wb") as f:
            f.write(b"")
        out = cleanup.purge_recycle_bin(transfer, 7)
        assert out["purged"] == 0 and os.path.exists(bare)

    def test_keep_forever_means_empty_the_whole_bin(self, tmp_path):
        transfer = str(tmp_path)
        a = _quarantine(transfer, "A/a.flac", deleted_at=_days_ago(1))
        root = os.path.join(transfer, ".deleted", "B")
        os.makedirs(root)
        bare = os.path.join(root, "bare.flac")
        with open(bare, "wb") as f:
            f.write(b"")
        out = cleanup.purge_recycle_bin(transfer, 0)
        assert out["mode"] == "all" and out["purged"] == 2 and out["errors"] == []
        assert not os.path.exists(a) and not os.path.exists(bare)

    @pytest.mark.parametrize("bad", [None, "", "seven", -3])
    def test_a_bad_keep_value_reads_as_keep_forever(self, tmp_path, bad):
        transfer = str(tmp_path)
        a = _quarantine(transfer, "A/a.flac", deleted_at=_days_ago(1))
        out = cleanup.purge_recycle_bin(transfer, bad)
        assert out["mode"] == "all" and not os.path.exists(a)

    def test_an_empty_transfer_folder_is_fine(self, tmp_path):
        out = cleanup.purge_recycle_bin(str(tmp_path), 7)
        assert out["purged"] == 0
        out = cleanup.purge_recycle_bin(str(tmp_path), 0)
        assert out["purged"] == 0


# ── run_library_cleanup ──────────────────────────────────────────────────────

def _both(tmp_path):
    downloads = tmp_path / "downloads"
    q = downloads / "ss_quarantine"
    q.mkdir(parents=True)
    (q / "bad.flac").write_bytes(b"")
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    binned = _quarantine(str(transfer), "A/old.flac", deleted_at=_days_ago(30))
    return downloads, transfer, q / "bad.flac", binned


class TestRunLibraryCleanup:
    def test_both_halves_run_and_the_summary_adds_up(self, tmp_path):
        downloads, transfer, qfile, binned = _both(tmp_path)
        said = []
        out = cleanup.run_library_cleanup(
            download_path=str(downloads), transfer_folder=str(transfer), keep_days=7,
            say=lambda line, kind: said.append((line, kind)))
        assert out["removed"] == 2 and out["errors"] == []
        assert out["quarantine"]["removed"] == 1
        assert out["recycle_bin"] == {"mode": "expired", "keep_days": 7.0, "purged": 1, "errors": []}
        assert not qfile.exists() and not os.path.exists(binned)
        assert [k for _l, k in said] == ["success", "success"]
        assert said[0][0] == "Quarantine: removed 1 item(s)"
        assert said[1][0] == "Recycle bin: purged 1 file(s) older than 7 day(s)"

    def test_quarantine_only(self, tmp_path):
        downloads, transfer, qfile, binned = _both(tmp_path)
        out = cleanup.run_library_cleanup(
            download_path=str(downloads), transfer_folder=str(transfer), keep_days=0,
            empty_recycle_bin=False)
        assert out["recycle_bin"] is None and out["removed"] == 1
        assert not qfile.exists() and os.path.exists(binned)

    def test_recycle_bin_only(self, tmp_path):
        downloads, transfer, qfile, binned = _both(tmp_path)
        out = cleanup.run_library_cleanup(
            download_path=str(downloads), transfer_folder=str(transfer), keep_days=0,
            clear_quarantine=False)
        assert out["quarantine"] is None and out["removed"] == 1
        assert qfile.exists() and not os.path.exists(binned)

    def test_a_failing_quarantine_step_does_not_stop_the_bin(self, tmp_path, monkeypatch):
        downloads, transfer, qfile, binned = _both(tmp_path)

        def _boom(path):
            raise RuntimeError("disk fell off")
        monkeypatch.setattr(cleanup, "clear_quarantine_folder", _boom)
        said = []
        out = cleanup.run_library_cleanup(
            download_path=str(downloads), transfer_folder=str(transfer), keep_days=0,
            say=lambda line, kind: said.append((line, kind)))
        assert out["quarantine"]["removed"] == 0
        assert out["errors"] == [{"step": "quarantine", "entry": "", "error": "disk fell off"}]
        assert out["recycle_bin"]["purged"] == 1 and not os.path.exists(binned)
        assert out["removed"] == 1

    def test_a_failing_bin_step_is_reported_not_raised(self, tmp_path, monkeypatch):
        downloads, transfer, qfile, binned = _both(tmp_path)

        def _boom(transfer_folder, keep_days):
            raise RuntimeError("manifest unreadable")
        monkeypatch.setattr(cleanup, "purge_recycle_bin", _boom)
        said = []
        out = cleanup.run_library_cleanup(
            download_path=str(downloads), transfer_folder=str(transfer), keep_days=0,
            say=lambda line, kind: said.append((line, kind)))
        assert not qfile.exists()
        assert out["errors"] == [{"step": "recycle_bin", "id": "", "error": "manifest unreadable"}]
        assert ("Recycle bin: step failed, see log", "error") in said

    def test_a_broken_narrator_never_breaks_the_sweep(self, tmp_path):
        downloads, transfer, qfile, binned = _both(tmp_path)

        def _say(line, kind):
            raise ValueError("socket gone")
        out = cleanup.run_library_cleanup(
            download_path=str(downloads), transfer_folder=str(transfer), keep_days=0, say=_say)
        assert out["removed"] == 2 and out["errors"] == []

    def test_a_missing_quarantine_folder_is_narrated_as_nothing_to_clear(self, tmp_path):
        transfer = tmp_path / "Transfer"
        transfer.mkdir()
        said = []
        out = cleanup.run_library_cleanup(
            download_path=str(tmp_path / "downloads"), transfer_folder=str(transfer), keep_days=0,
            say=lambda line, kind: said.append((line, kind)))
        assert out["removed"] == 0
        assert said[0] == ("Quarantine: no folder yet, nothing to clear", "info")
        assert said[1] == ("Recycle bin: emptied, 0 file(s) deleted for good", "info")
