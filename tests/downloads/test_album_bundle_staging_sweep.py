"""Tests for ``sweep_orphan_album_bundle_staging``.

The album-bundle staging dir (``storage/album_bundle_staging/<batch_id>/``)
accumulates orphan subdirs when:

- The app crashed mid-bundle (per-batch cleanup never fired).
- A batch errored on a code path the cleanup gate didn't catch.
- A pre-fix Soulseek bundle ran (the cleanup gate was torrent/usenet
  only — slskd bundle copies leaked).

The sweep runs once at server startup, BEFORE any new batch can
register a staging dir, so ``active_batch_ids`` is empty / pre-existing.
Tests pin: orphans removed, active dirs preserved, name-guard rejects
escape attempts, sweep no-ops gracefully on missing/empty roots,
non-dir entries skipped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.downloads.lifecycle import sweep_orphan_album_bundle_staging


def _make_batch_dir(root: Path, name: str, with_file: bool = True) -> Path:
    """Create ``root/name/`` with optional placeholder content."""
    bd = root / name
    bd.mkdir(parents=True, exist_ok=True)
    if with_file:
        (bd / 'leftover.flac').write_bytes(b'audio')
    return bd


# ---------------------------------------------------------------------------
# Happy path — orphans removed, active preserved.
# ---------------------------------------------------------------------------


def test_removes_orphan_dirs_when_no_active_batches(tmp_path):
    """No batches running → every dir under staging root is orphan."""
    root = tmp_path / 'album_bundle_staging'
    a = _make_batch_dir(root, 'b_abc123')
    b = _make_batch_dir(root, 'b_def456')

    removed = sweep_orphan_album_bundle_staging(str(root))

    assert removed == 2
    assert not a.exists()
    assert not b.exists()
    assert root.exists()  # Root itself preserved.


def test_preserves_active_batch_dirs(tmp_path):
    """Dirs whose batch_id is in the active set must NOT be removed.
    Belt-and-suspenders — sweep runs at startup before batches exist,
    but the active-set guard protects against a runtime re-sweep too."""
    root = tmp_path / 'album_bundle_staging'
    active = _make_batch_dir(root, 'b_running')
    orphan = _make_batch_dir(root, 'b_dead')

    removed = sweep_orphan_album_bundle_staging(
        str(root), active_batch_ids={'b_running'},
    )

    assert removed == 1
    assert active.exists()
    assert not orphan.exists()


def test_active_batch_id_with_special_chars_normalises_for_match(tmp_path):
    """The on-disk dirname is ``_safe_batch_dirname(batch_id)``
    (alphanumeric + ``-`` + ``_``). The sweep normalises the
    provided active batch ids the same way so a batch_id like
    ``user:123`` (which lands on disk as ``user_123``) still gets
    correctly excluded from the orphan set."""
    root = tmp_path / 'album_bundle_staging'
    active = _make_batch_dir(root, 'user_123')

    removed = sweep_orphan_album_bundle_staging(
        str(root), active_batch_ids={'user:123'},
    )

    assert removed == 0
    assert active.exists()


# ---------------------------------------------------------------------------
# Safe-by-design — defensive guards against escape / weird state.
# ---------------------------------------------------------------------------


def test_no_op_when_staging_root_missing(tmp_path):
    """Staging root doesn't exist (fresh install) → no-op, no error."""
    missing = tmp_path / 'nope'

    removed = sweep_orphan_album_bundle_staging(str(missing))

    assert removed == 0


def test_no_op_when_staging_root_empty(tmp_path):
    """Root exists but empty → returns 0 cleanly."""
    root = tmp_path / 'album_bundle_staging'
    root.mkdir()

    removed = sweep_orphan_album_bundle_staging(str(root))

    assert removed == 0


def test_no_op_when_staging_root_path_empty():
    """Empty string config value → no-op."""
    assert sweep_orphan_album_bundle_staging('') == 0


def test_skips_non_directory_entries(tmp_path):
    """Stray files in the staging root must NOT be removed — sweep
    only touches batch-id-shaped subdirs."""
    root = tmp_path / 'album_bundle_staging'
    root.mkdir()
    stray_file = root / 'README.txt'
    stray_file.write_text('do not delete')
    orphan = _make_batch_dir(root, 'b_orphan')

    removed = sweep_orphan_album_bundle_staging(str(root))

    assert removed == 1
    assert stray_file.exists()
    assert not orphan.exists()


def test_skips_dirs_with_unsafe_names(tmp_path):
    """A dir whose name doesn't round-trip through
    ``_safe_batch_dirname`` (e.g. contains ``..`` or a colon) must
    be left alone — defensive against hand-placed dirs the user
    might have created under the staging root for other purposes."""
    root = tmp_path / 'album_bundle_staging'
    root.mkdir()
    # ``.git`` would normalise to ``_git`` so the name doesn't
    # round-trip → sweep ignores it.
    hand_made = root / '.git'
    hand_made.mkdir()
    (hand_made / 'config').write_text('[core]')
    orphan = _make_batch_dir(root, 'b_orphan')

    removed = sweep_orphan_album_bundle_staging(str(root))

    assert removed == 1
    assert hand_made.exists()  # Unsafe-name dir preserved.
    assert not orphan.exists()


def test_partial_failure_does_not_abort_remaining(tmp_path, monkeypatch):
    """If shutil.rmtree fails on one dir (permission denied etc.),
    the sweep logs and continues — must not abort and leak the
    rest."""
    root = tmp_path / 'album_bundle_staging'
    blocking = _make_batch_dir(root, 'b_blocked')
    okay = _make_batch_dir(root, 'b_ok')

    import core.downloads.lifecycle as lc_mod
    real_rmtree = lc_mod.shutil.rmtree

    def _selective_rmtree(path, *args, **kwargs):
        if Path(path).name == 'b_blocked':
            raise OSError('permission denied')
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(lc_mod.shutil, 'rmtree', _selective_rmtree)

    removed = sweep_orphan_album_bundle_staging(str(root))

    assert removed == 1
    assert blocking.exists()
    assert not okay.exists()


def test_returns_zero_when_listdir_raises(tmp_path, monkeypatch):
    """If the staging root can't be iterated (rare, e.g. permission
    issue), sweep logs + returns 0 instead of crashing startup."""
    root = tmp_path / 'album_bundle_staging'
    root.mkdir()
    _make_batch_dir(root, 'b_orphan')

    import core.downloads.lifecycle as lc_mod
    real_iterdir = Path.iterdir

    def _broken_iterdir(self):
        if self == root:
            raise OSError('listdir blew up')
        return real_iterdir(self)

    monkeypatch.setattr(Path, 'iterdir', _broken_iterdir)

    removed = sweep_orphan_album_bundle_staging(str(root))

    assert removed == 0


# ---------------------------------------------------------------------------
# active_batch_ids edge cases.
# ---------------------------------------------------------------------------


def test_none_active_batch_ids_treated_as_empty(tmp_path):
    """``active_batch_ids=None`` (the default) → every dir is orphan."""
    root = tmp_path / 'album_bundle_staging'
    a = _make_batch_dir(root, 'b_a')
    b = _make_batch_dir(root, 'b_b')

    removed = sweep_orphan_album_bundle_staging(str(root), active_batch_ids=None)

    assert removed == 2
    assert not a.exists()
    assert not b.exists()


def test_active_set_ignores_empty_or_none_entries(tmp_path):
    """Defensive — caller may pass a set containing None / empty
    strings from a partially-initialised state. Skip them so they
    don't accidentally match the dirname ``batch`` (the
    ``_safe_batch_dirname`` fallback)."""
    root = tmp_path / 'album_bundle_staging'
    orphan = _make_batch_dir(root, 'b_orphan')

    removed = sweep_orphan_album_bundle_staging(
        str(root), active_batch_ids={'', None, 'b_other'},
    )

    assert removed == 1
    assert not orphan.exists()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
