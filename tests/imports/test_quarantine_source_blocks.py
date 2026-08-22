"""#652 round two: the "don't pick this bad upload again" list has to outlive
the review queue.

The gate used to be derived by listing ss_quarantine and reading the sidecars.
So the review queue WAS the blocklist, and approve / delete / clear all delete
sidecars. Emptying the queue erased the memory of what had already failed, the
picker is deterministic, so the same file won again and quarantined again.
TheHomeGuy approved the same songs about five times.

These pin the durable half: the block is written when the file is quarantined,
it survives delete and clear all, and ONLY approve takes it away.

Hermetic: tmp MusicDatabase, tmp quarantine dir, no network.
"""

from __future__ import annotations

import ast
import json
import os

import pytest

from core.imports.quarantine import (
    approve_quarantine_entry,
    count_quarantine_entries,
    delete_quarantine_entry,
    get_quarantined_source_keys,
)

USER = "uploader7"
FILE = r"@@music\Kendrick Lamar\DNA.mp3"


@pytest.fixture()
def mdb(tmp_path, monkeypatch):
    """A tmp db, wired in as the one the lazy `MusicDatabase()` calls build.

    guards.py and quarantine.py both do `from database.music_database import
    MusicDatabase` INSIDE the function, so patching the class attribute on the
    module is enough and there is no import-order trap.
    """
    import database.music_database as mod

    db = mod.MusicDatabase(database_path=str(tmp_path / "music.db"))
    monkeypatch.setattr(mod, "MusicDatabase", lambda *a, **k: db)
    return db


@pytest.fixture()
def qdir(tmp_path):
    d = tmp_path / "ss_quarantine"
    d.mkdir()
    return str(d)


def _write_entry(qdir, stem="20260821_120000_DNA", *, username=USER, filename=FILE,
                 with_context=True):
    """One quarantined file + its sidecar, the shape move_to_quarantine writes."""
    open(os.path.join(qdir, f"{stem}.mp3.quarantined"), "w").close()
    sidecar = {
        "original_filename": "DNA.mp3",
        "quarantine_reason": "acoustid mismatch",
        "trigger": "acoustid",
    }
    if with_context:
        sidecar["context"] = {
            "original_search_result": {"username": username, "filename": filename},
        }
    with open(os.path.join(qdir, f"{stem}.json"), "w", encoding="utf-8") as f:
        json.dump(sidecar, f)
    return stem


class TestTheBlockOutlivesTheQueue:
    def test_delete_leaves_the_block_standing(self, mdb, qdir):
        """deleting is the user saying the file is bad. best possible reason to
        keep blocking it, and the old code forgot it instead."""
        _write_entry(qdir)
        mdb.add_quarantine_source_block(USER, FILE)

        assert delete_quarantine_entry(qdir, "20260821_120000_DNA") is True
        assert os.listdir(qdir) == []

        assert (USER, FILE) in get_quarantined_source_keys(qdir)

    def test_clear_all_leaves_the_block_standing(self, mdb, qdir):
        for i in range(3):
            _write_entry(qdir, stem=f"20260821_12000{i}_DNA", filename=f"{FILE}{i}")
            mdb.add_quarantine_source_block(USER, f"{FILE}{i}")

        for name in os.listdir(qdir):
            os.remove(os.path.join(qdir, name))
        assert os.listdir(qdir) == []

        keys = get_quarantined_source_keys(qdir)
        for i in range(3):
            assert (USER, f"{FILE}{i}") in keys

    def test_gate_works_with_the_folder_gone_entirely(self, mdb, tmp_path):
        mdb.add_quarantine_source_block(USER, FILE)
        missing = str(tmp_path / "not_here")
        assert (USER, FILE) in get_quarantined_source_keys(missing)


class TestApproveDoesNotUnblockEither:
    """The one he actually complained about.

    Approve looks like it should lift the block, and that is the trap. If the
    re-import lands, the track is in the library and the block never matters. If
    it doesn't land, the block is the only reason the next search picks someone
    else. Lifting it here is what let the same file come back five times.
    """

    def test_approve_leaves_the_block_standing(self, mdb, qdir, tmp_path):
        stem = _write_entry(qdir)
        mdb.add_quarantine_source_block(USER, FILE)

        result = approve_quarantine_entry(qdir, stem, str(tmp_path / "restore"))
        assert result is not None, "approve should still succeed"
        assert not os.path.exists(os.path.join(qdir, f"{stem}.json")), "sidecar gone"

        # the sidecar is gone, so this can only be the db row answering.
        assert (USER, FILE) in get_quarantined_source_keys(qdir)

    def test_approving_every_entry_leaves_every_block(self, mdb, qdir, tmp_path):
        """'Approve all' on 72 files must not empty the blocklist."""
        for i in range(5):
            _write_entry(qdir, stem=f"2026082{i}_120000_DNA", filename=f"{FILE}{i}")
            mdb.add_quarantine_source_block(USER, f"{FILE}{i}")

        for i in range(5):
            approve_quarantine_entry(qdir, f"2026082{i}_120000_DNA", str(tmp_path / "restore"))

        keys = get_quarantined_source_keys(qdir)
        for i in range(5):
            assert (USER, f"{FILE}{i}") in keys


class TestSidecarsStillCount:
    def test_legacy_sidecar_with_no_db_row_still_gates(self, mdb, qdir):
        """installs that quarantined things before the table existed keep working."""
        _write_entry(qdir, username="olduser", filename="old/file.mp3")
        assert mdb.get_quarantine_source_blocks() == set()
        assert ("olduser", "old/file.mp3") in get_quarantined_source_keys(qdir)


class TestWiring:
    def test_move_to_quarantine_records_the_block(self, mdb, tmp_path, monkeypatch):
        """the call site, not the helper. a fix that never gets called is the
        shape that has bitten this repo before."""
        import core.imports.guards as guards

        downloads = tmp_path / "downloads"
        downloads.mkdir()
        monkeypatch.setattr(
            guards, "_get_config_manager",
            lambda: type("C", (), {"get": staticmethod(lambda k, d=None: str(downloads) if k == "soulseek.download_path" else d)})(),
        )

        src = tmp_path / "DNA.mp3"
        src.write_bytes(b"not really audio")

        guards.move_to_quarantine(
            str(src),
            {"original_search_result": {"username": USER, "filename": FILE}},
            "acoustid mismatch",
            trigger="acoustid",
        )

        assert (USER, FILE) in mdb.get_quarantine_source_blocks()


class TestCount:
    def test_counts_quarantined_files_only(self, qdir):
        _write_entry(qdir, stem="a")
        _write_entry(qdir, stem="b")
        assert count_quarantine_entries(qdir) == 2

    def test_missing_dir_counts_zero(self, tmp_path):
        assert count_quarantine_entries(str(tmp_path / "nope")) == 0


class TestCap:
    def test_oldest_blocks_prune_past_the_cap(self, mdb, monkeypatch):
        monkeypatch.setattr(type(mdb), "QUARANTINE_BLOCK_CAP", 5)
        for i in range(8):
            mdb.add_quarantine_source_block(USER, f"file{i}.mp3")
        assert len(mdb.get_quarantine_source_blocks()) == 5
        assert (USER, "file7.mp3") in mdb.get_quarantine_source_blocks()


class TestSummaryEndpointWiring:
    """The route is glue over two functions that ARE tested directly, and the
    suite's flask fixture serves a synthetic app rather than web_server, so
    there is no way to actually call it here.

    AST rather than "is this string in the file": a substring check also matches
    the def line and the docstring, which is how a guard ends up unable to fail.
    """

    @staticmethod
    def _handler():
        import ast

        with open("web_server.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "review_queue_summary":
                return node
        return None

    def test_route_is_registered_at_the_path_the_ui_calls(self):
        node = self._handler()
        assert node is not None, "review_queue_summary handler is gone"

        routes = [
            d.args[0].value
            for d in node.decorator_list
            if isinstance(d, ast.Call) and getattr(d.func, "attr", "") == "route"
        ]
        assert "/api/review-queue/summary" in routes

    def test_handler_actually_calls_both_counters(self):
        node = self._handler()
        called = {
            n.func.id
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        } | {
            n.func.attr
            for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert "count_quarantine_entries" in called
        assert "count_library_history_unverified" in called
