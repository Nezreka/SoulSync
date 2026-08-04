"""Tests for ``core.library_reorganize._prune_empty_source_dirs``.

Split out from the old ``tests/test_library_reorganize.py`` (#985) when the
``core/repair_jobs/library_reorganize.py`` repair-job wrapper was retired and
deleted (docs/library-v2-tool-integration-audit-2026-07-18.md §7 P3
checklist point 2). ``_prune_empty_source_dirs`` itself lives in the
still-active ``core/library_reorganize.py`` engine (used by
``core/reorganize_runner.py`` and ``core/library2/reorganize_bridge.py``)
and is unrelated to the retired job — only this coverage needed to survive.
"""

from __future__ import annotations

from core.library_reorganize import _prune_empty_source_dirs


def test_prune_removes_empty_disc_and_album_keeps_artist(tmp_path):
    """The reported case: reorganize moved the file out of Album/Disc 1 into a new
    album dir. The empty Disc 1 AND the now-empty old album dir must be pruned; the
    artist dir (holding the new album) is kept."""
    artist = tmp_path / "local_music" / "Ne-Yo"
    disc = artist / "Because Of You (2007)" / "Disc 1"
    new_album = artist / "Because of You (Radio Edit) (2007)"
    disc.mkdir(parents=True)
    new_album.mkdir(parents=True)
    (new_album / "01. Because of You.flac").write_text("audio")   # file now lives here

    _prune_empty_source_dirs(str(disc))

    assert not disc.exists()                              # empty disc pruned
    assert not (artist / "Because Of You (2007)").exists()  # now-empty old album pruned
    assert artist.exists() and new_album.exists()         # artist + new album kept


def test_prune_stops_at_a_dir_with_real_content(tmp_path):
    leaf = tmp_path / "Artist" / "Album" / "Disc 1"
    leaf.mkdir(parents=True)
    (tmp_path / "Artist" / "keep.flac").write_text("x")   # Artist has other content
    _prune_empty_source_dirs(str(leaf))
    assert not leaf.exists() and not (tmp_path / "Artist" / "Album").exists()
    assert (tmp_path / "Artist").exists()                 # stopped — real content


def test_prune_clears_hidden_junk_and_prunes_album_for_a_disc(tmp_path):
    album = tmp_path / "Artist" / "Album"
    disc = album / "CD 2"
    disc.mkdir(parents=True)
    (disc / ".DS_Store").write_text("junk")               # hidden-only → still "empty"
    _prune_empty_source_dirs(str(disc))
    assert not disc.exists()                              # disc pruned (hidden cleared)
    assert not album.exists()                            # album pruned (disc source)
    assert (tmp_path / "Artist").exists()                # never climbs to the artist


def test_prune_never_climbs_above_the_album_or_into_the_library_root(tmp_path):
    """The safety property: even a flat library (album directly under an UNprotected
    root) must never lose the root. A non-disc source prunes only its own dir."""
    root = tmp_path / "local_music"                       # not a configured/protected root
    old_album = root / "Some Album"
    old_album.mkdir(parents=True)
    _prune_empty_source_dirs(str(old_album))              # album-level source, not a disc
    assert not old_album.exists()                        # emptied album pruned
    assert root.exists()                                 # library root NEVER removed


def test_prune_missing_dir_is_safe(tmp_path):
    _prune_empty_source_dirs(str(tmp_path / "does" / "not" / "exist"))   # no raise
