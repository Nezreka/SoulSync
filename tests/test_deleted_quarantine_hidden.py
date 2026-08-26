"""The quarantine folder is hidden from media servers.

Removed duplicates and dead files are recoverable: they move into a quarantine
under the transfer folder instead of being hard-deleted. That folder used to be
a bare ``deleted/`` — which Navidrome, Plex and Jellyfin all happily indexed, so
every "removed" track stayed in the user's library, playable, next to the copy
that replaced it (Discord, Jose: "the deleted folder that navidrome still picks
up"). Dot-prefixed folders are skipped by all three servers by default, so
``.deleted`` needs no per-server setup at all.
"""

from __future__ import annotations

import os

from core.repair_jobs.base import (
    DELETED_QUARANTINE_DIRNAME,
    LEGACY_DELETED_DIRNAME,
    deleted_quarantine_root,
    is_internal_transfer_dir,
)


def test_the_quarantine_is_a_hidden_folder(tmp_path):
    root = deleted_quarantine_root(str(tmp_path))
    assert os.path.basename(root) == ".deleted"
    assert DELETED_QUARANTINE_DIRNAME.startswith("."), \
        "the whole point is that media servers skip dot-folders"


def test_a_legacy_deleted_folder_is_renamed_not_abandoned(tmp_path):
    """One rename fixes every existing install: the files a user already has in
    quarantine leave their media server the moment any tool touches it again."""
    legacy = tmp_path / LEGACY_DELETED_DIRNAME
    (legacy / "Artist").mkdir(parents=True)
    (legacy / "Artist" / "song.flac").write_text("x")

    root = deleted_quarantine_root(str(tmp_path))

    assert os.path.basename(root) == ".deleted"
    assert not legacy.exists(), "the legacy folder was left for the media server to index"
    assert (tmp_path / ".deleted" / "Artist" / "song.flac").read_text() == "x"


def test_coexisting_folders_are_not_merged(tmp_path):
    """Someone who made .deleted by hand next to an old deleted/ keeps both —
    merging directory trees is how files get clobbered. New quarantined files go
    to the hidden one; both spellings stay recognised by the walker skips."""
    (tmp_path / LEGACY_DELETED_DIRNAME).mkdir()
    (tmp_path / ".deleted").mkdir()
    root = deleted_quarantine_root(str(tmp_path))
    assert os.path.basename(root) == ".deleted"
    assert (tmp_path / LEGACY_DELETED_DIRNAME).exists()


def test_every_walker_skip_recognises_both_spellings(tmp_path):
    base = str(tmp_path)
    for name in (".deleted", "deleted"):
        assert is_internal_transfer_dir(os.path.join(base, name), base), name
        assert is_internal_transfer_dir(os.path.join(base, name, "Artist", "x.flac"), base), name
    # ...without swallowing legitimately-named library content
    assert not is_internal_transfer_dir(os.path.join(base, "Undeleted"), base)
    assert not is_internal_transfer_dir(os.path.join(base, "Artist", "deleted scenes"), base)


def test_reorganize_skips_both_spellings(tmp_path):
    from core.library_reorganize import _is_in_deleted_quarantine
    base = str(tmp_path)
    for name in (".deleted", "deleted"):
        assert _is_in_deleted_quarantine(os.path.join(base, name, "Artist", "x.flac"), base), name
    assert not _is_in_deleted_quarantine(os.path.join(base, "Artist", "x.flac"), base)
