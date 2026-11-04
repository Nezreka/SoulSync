"""#1696 on Library v2: a standalone Full Refresh must not drop a file.

Upstream's refresh minted ``_stable_id`` (md5 % 1e9) row ids, so two files
could share an id and the second INSERT OR IGNORE vanished while still being
counted as a success. The native refresh never mints ids: every audio file goes
through the normal import writer (``link_download_into_library_v2``), which keys
the file row by its path. What carries over from the fix is checked here: every
file is offered once, in a walk-independent order, and a file the writer could
not store counts as failed, never as imported. Grouping by release (the other
half of #1696) belongs to the import writer and is tested with it
(tests/imports/test_library_entry_distrust_1567.py, album grouping tests).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import api.database_admin as da
from database.music_database import MusicDatabase


@pytest.fixture()
def refresh(monkeypatch, tmp_path):
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    db = MusicDatabase(str(tmp_path / "music.db"))
    finished, imported = [], []
    answers = {}

    monkeypatch.setattr(da, "config_manager",
                        SimpleNamespace(get=lambda key, default=None: str(transfer)))
    monkeypatch.setattr(da, "docker_resolve_path", lambda p: p)
    monkeypatch.setattr(da, "get_database", lambda: db)
    monkeypatch.setattr(da, "_db_update_phase_callback", lambda *a: None)
    monkeypatch.setattr(da, "_db_update_error_callback",
                        lambda msg: (_ for _ in ()).throw(AssertionError(msg)))
    monkeypatch.setattr(da, "_db_update_finished_callback", lambda *a: finished.append(a))

    def link(context, raise_on_error=False):
        path = context["_final_processed_path"]
        imported.append(path)
        answer = answers.get(path.rsplit("/", 1)[-1], 1)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr("core.library2.autolink.link_download_into_library_v2", link)

    def run(files, **answer_for):
        for name in files:
            target = transfer / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"audio")
        answers.update(answer_for)
        da._run_soulsync_full_refresh()
        return finished[-1], imported

    return run


def test_every_file_is_offered_once_in_a_walk_independent_order(refresh):
    files = ["b/02.flac", "a/01.flac", "a/notes.txt", "c/03.mp3", "a/00.FLAC"]
    counts, imported = refresh(files)
    names = [p.split("Transfer/", 1)[1] for p in imported]
    assert names == sorted(names) == ["a/00.FLAC", "a/01.flac", "b/02.flac", "c/03.mp3"]
    _artists, _albums, total, successful, failed = counts
    assert (total, successful, failed) == (4, 4, 0)


def test_a_file_the_writer_could_not_store_counts_as_failed(refresh):
    counts, imported = refresh(["a/01.flac", "a/02.flac", "a/03.flac"],
                               **{"02.flac": None, "03.flac": RuntimeError("locked")})
    assert len(imported) == 3
    _artists, _albums, total, successful, failed = counts
    assert (total, successful, failed) == (3, 1, 2)  # only the one that landed counts
