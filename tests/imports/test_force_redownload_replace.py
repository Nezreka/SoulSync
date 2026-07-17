"""#1045 — Force download must actually replace the existing file.

diegocade1 force-re-downloaded a track for better quality; the download
succeeded and passed integrity, then the metadata-protection guard skipped
the overwrite and deleted the new file as "redundant". Force download forced
the DOWNLOAD but the import step had no concept of replace-intent.

Replace-intent is a DISTINCT batch key (``force_replace``), set by web_server
only for a user-checked Force toggle. It is deliberately NOT
``force_download_all``: the wishlist sets that on every background batch
(there it means "skip ownership checks") and Wing It auto-enables it —
neither may ever overwrite library files. The replace still runs behind the
short-file replacement guard (a forced download can never swap a full track
for a preview clip).
"""

from __future__ import annotations

from pathlib import Path

from core.imports import pipeline

_ROOT = Path(__file__).resolve().parent.parent.parent


class TestBatchForceReplace:
    def test_true_when_batch_carries_replace_intent(self, monkeypatch):
        monkeypatch.setitem(pipeline.download_batches, "b1",
                            {"force_replace": True})
        assert pipeline._batch_force_replace({"batch_id": "b1"}) is True

    def test_false_when_batch_not_forced(self, monkeypatch):
        monkeypatch.setitem(pipeline.download_batches, "b2",
                            {"force_replace": False})
        assert pipeline._batch_force_replace({"batch_id": "b2"}) is False

    def test_wishlist_style_batch_never_replaces(self, monkeypatch):
        # the wishlist sets force_download_all=True on EVERY background batch
        # (meaning "skip ownership checks") and never sets force_replace — a
        # background download must never overwrite a library file
        monkeypatch.setitem(pipeline.download_batches, "wl",
                            {"force_download_all": True})
        assert pipeline._batch_force_replace({"batch_id": "wl"}) is False

    def test_false_without_a_batch(self):
        assert pipeline._batch_force_replace({}) is False
        assert pipeline._batch_force_replace({"batch_id": "nope-unknown"}) is False
        assert pipeline._batch_force_replace(None) is False


def test_web_server_sets_replace_intent_only_for_user_force():
    # the batch key is derived as force AND NOT wing_it — Wing It auto-ors the
    # force flag in client-side, and that must not read as replace-intent
    src = (_ROOT / "web_server.py").read_text(encoding="utf-8", errors="replace")
    assert "'force_replace': bool(force_download_all and not wing_it)" in src


def test_wishlist_batches_carry_no_replace_intent():
    src = (_ROOT / "core" / "wishlist" / "processing.py").read_text(
        encoding="utf-8", errors="replace")
    assert "force_replace" not in src, \
        "wishlist batches must never carry replace-intent"


def test_force_is_wired_into_the_protection_branch():
    src = Path(pipeline.__file__).read_text(encoding="utf-8", errors="replace")
    # the protection skip must yield to an explicit force...
    assert "if has_metadata and not is_enhance_download and not force_replace:" in src
    # ...and force rides the enhance replace path
    assert "elif is_enhance_download or force_replace:" in src
    assert "User-forced re-download" in src
    # the pipeline reads the dedicated key, not the overloaded skip-checks flag
    assert ".get('force_replace')" in src


def test_force_replace_still_behind_the_length_guard():
    # ordering contract: the short-file replacement guard runs BEFORE any
    # branch that can os.remove(final_path) — a forced re-download can never
    # replace a full track with a preview clip
    src = Path(pipeline.__file__).read_text(encoding="utf-8", errors="replace")
    guard = src.index("_replacement_length_is_safe(final_path, file_path)")
    force_branch = src.index("elif is_enhance_download or force_replace:")
    assert guard < force_branch
