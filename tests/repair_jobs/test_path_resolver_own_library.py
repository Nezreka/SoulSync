"""#1504 — path resolver searches own-library roots.

``_collect_base_dirs`` used to omit own-library folders, so every job
resolving via ``resolve_library_file_path`` (Unknown Artist fixer, Dead
File Cleaner, Album Completeness, quality scanner…) silently missed
own-library tracks unless the user duplicated the path under Additional
Music Libraries.

Hermetic: own_library_supported / own_library_roots are faked; dirs are
real tmp_path trees so the isdir filter passes.
"""

from __future__ import annotations

import os

import pytest

import core.imports.paths as paths
import core.library_scope as library_scope
from core.library.path_resolver import (
    _collect_base_dirs,
    resolve_library_file_path,
)


@pytest.fixture
def own_libs(monkeypatch, tmp_path):
    own = tmp_path / "own"
    own.mkdir()
    monkeypatch.setattr(library_scope, "own_library_supported", lambda: True)
    monkeypatch.setattr(
        paths, "own_library_roots", lambda: [(2, str(own))])
    return own


def test_collect_base_dirs_includes_own_roots(own_libs, tmp_path):
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    dirs = _collect_base_dirs(str(transfer), None, None, None)
    assert os.path.normpath(str(own_libs)) in {os.path.normpath(d) for d in dirs}
    # shared folder keeps its first-place precedence
    assert os.path.normpath(dirs[0]) == os.path.normpath(str(transfer))


def test_collect_base_dirs_skips_own_roots_when_unsupported(monkeypatch, tmp_path):
    own = tmp_path / "own"
    own.mkdir()
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    monkeypatch.setattr(library_scope, "own_library_supported", lambda: False)
    monkeypatch.setattr(paths, "own_library_roots",
                        lambda: [(2, str(own))])
    dirs = _collect_base_dirs(str(transfer), None, None, None)
    assert all(os.path.normpath(d) != os.path.normpath(str(own)) for d in dirs)


def test_collect_base_dirs_pinned_root_excludes_everything_else(
        own_libs, tmp_path):
    pinned = tmp_path / "pinned"
    pinned.mkdir()
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    dirs = _collect_base_dirs(str(transfer), None, None, None,
                              library_root=str(pinned))
    normed = {os.path.normpath(d) for d in dirs}
    assert normed == {os.path.normpath(str(pinned))}


def test_collect_base_dirs_fail_open(monkeypatch, tmp_path):
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    def _boom():
        raise RuntimeError("db gone")
    monkeypatch.setattr(paths, "own_library_roots", _boom)
    dirs = _collect_base_dirs(str(transfer), None, None, None)
    assert os.path.normpath(dirs[0]) == os.path.normpath(str(transfer))


def test_resolve_finds_file_only_in_own_library(own_libs, tmp_path):
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    rel = os.path.join("Real Artist", "Real Album", "01 - Song.flac")
    target = own_libs / rel
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x00" * 64)
    resolved = resolve_library_file_path(rel, transfer_folder=str(transfer))
    assert resolved is not None
    assert os.path.normpath(resolved) == os.path.normpath(str(target))


def test_resolve_still_prefers_shared_when_both_exist(own_libs, tmp_path):
    transfer = tmp_path / "Transfer"
    transfer.mkdir()
    rel = os.path.join("Real Artist", "Real Album", "01 - Song.flac")
    for root in (transfer, own_libs):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\x00" * 64)
    resolved = resolve_library_file_path(rel, transfer_folder=str(transfer))
    assert os.path.normpath(resolved) == os.path.normpath(str(transfer / rel))
