"""#1045 — Force download must actually replace the existing file.

diegocade1 force-re-downloaded a track for better quality; the download
succeeded and passed integrity, then the metadata-protection guard skipped
the overwrite and deleted the new file as "redundant". Force download forced
the DOWNLOAD but the import step had no concept of replace-intent.

Now an explicit user force (the batch's force_download_all flag) rides the
same replace path as a quality-enhance: the existing file is replaced —
still behind the short-file replacement guard (a forced download can never
swap a full track for a preview clip). No force flag → the protection
behaves exactly as before.
"""

from __future__ import annotations

from pathlib import Path

from core.imports import pipeline


class TestBatchForceReplace:
    def test_true_when_batch_carries_the_force_flag(self, monkeypatch):
        monkeypatch.setitem(pipeline.download_batches, "b1",
                            {"force_download_all": True})
        assert pipeline._batch_force_replace({"batch_id": "b1"}) is True

    def test_false_when_batch_not_forced(self, monkeypatch):
        monkeypatch.setitem(pipeline.download_batches, "b2",
                            {"force_download_all": False})
        assert pipeline._batch_force_replace({"batch_id": "b2"}) is False

    def test_false_without_a_batch(self):
        assert pipeline._batch_force_replace({}) is False
        assert pipeline._batch_force_replace({"batch_id": "nope-unknown"}) is False
        assert pipeline._batch_force_replace(None) is False


def test_force_is_wired_into_the_protection_branch():
    src = Path(pipeline.__file__).read_text(encoding="utf-8", errors="replace")
    # the protection skip must yield to an explicit force...
    assert "if has_metadata and not is_enhance_download and not force_replace:" in src
    # ...and force rides the enhance replace path
    assert "elif is_enhance_download or force_replace:" in src
    assert "User-forced re-download" in src


def test_force_replace_still_behind_the_length_guard():
    # ordering contract: the short-file replacement guard runs BEFORE any
    # branch that can os.remove(final_path) — a forced re-download can never
    # replace a full track with a preview clip
    src = Path(pipeline.__file__).read_text(encoding="utf-8", errors="replace")
    guard = src.index("_replacement_length_is_safe(final_path, file_path)")
    force_branch = src.index("elif is_enhance_download or force_replace:")
    assert guard < force_branch
