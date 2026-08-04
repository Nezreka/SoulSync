"""Pin orphan-format handling in library_reorganize.

Discord report (Foxxify): users with the lossy-copy feature enabled
end up with `track.flac` AND `track.opus` side-by-side. Reorganize is
DB-driven and only knows about ONE file per track (the lossy copy in
the library), so the other format used to get left behind in the old
location while the canonical moved to the new destination. Cleanup
never fired because the source dir still had audio.

Post-fix the reorganize finalisation step finds sibling-stem audio
files at the source and moves them to the same destination dir as
the canonical, preserving both formats.
"""

from __future__ import annotations

import os

from core.library_reorganize import (
    _find_sibling_audio_files,
    _move_sibling_to_destination,
)


# ---------------------------------------------------------------------------
# Sibling detection
# ---------------------------------------------------------------------------


class TestFindSiblingAudioFiles:
    def test_finds_flac_when_opus_is_canonical(self, tmp_path):
        """Reporter's exact case: lossy-copy `.opus` is the
        canonical (DB-tracked); `.flac` is the orphan to move."""
        opus = tmp_path / "01 Track.opus"
        flac = tmp_path / "01 Track.flac"
        opus.write_bytes(b"opus-data")
        flac.write_bytes(b"flac-data")

        siblings = _find_sibling_audio_files(str(opus))

        assert siblings == [str(flac)]

    def test_finds_opus_when_flac_is_canonical(self):
        """Symmetric direction — works either way."""
        # tmp_path fixture handled by next test inline
        import tempfile, pathlib
        tmp = pathlib.Path(tempfile.mkdtemp())
        flac = tmp / "X.flac"
        opus = tmp / "X.opus"
        flac.write_bytes(b"a"); opus.write_bytes(b"b")

        siblings = _find_sibling_audio_files(str(flac))
        assert siblings == [str(opus)]

    def test_excludes_canonical_itself(self, tmp_path):
        """Canonical must NOT appear in its own sibling list."""
        canonical = tmp_path / "X.opus"
        canonical.write_bytes(b"data")

        siblings = _find_sibling_audio_files(str(canonical))
        assert siblings == []

    def test_excludes_different_stem(self, tmp_path):
        """Different track in same dir shouldn't be flagged as
        sibling — only same-stem files."""
        canonical = tmp_path / "01 Track One.opus"
        other_track = tmp_path / "02 Track Two.flac"
        canonical.write_bytes(b"a"); other_track.write_bytes(b"b")

        siblings = _find_sibling_audio_files(str(canonical))
        assert siblings == []

    def test_excludes_non_audio_extensions(self, tmp_path):
        """Sidecars (.lrc, .nfo, .txt) handled by separate sidecar
        helper — must not appear in audio-sibling list."""
        canonical = tmp_path / "X.opus"
        sidecar = tmp_path / "X.lrc"
        nfo = tmp_path / "X.nfo"
        canonical.write_bytes(b"a")
        sidecar.write_bytes(b"lyrics")
        nfo.write_bytes(b"info")

        siblings = _find_sibling_audio_files(str(canonical))
        assert siblings == []

    def test_finds_multiple_siblings(self, tmp_path):
        """User could have 3+ formats: .flac + .opus + .mp3."""
        opus = tmp_path / "X.opus"
        flac = tmp_path / "X.flac"
        mp3 = tmp_path / "X.mp3"
        opus.write_bytes(b"a"); flac.write_bytes(b"b"); mp3.write_bytes(b"c")

        siblings = _find_sibling_audio_files(str(opus))
        # All formats other than canonical
        assert sorted(siblings) == sorted([str(flac), str(mp3)])

    def test_missing_source_dir_returns_empty(self, tmp_path):
        """Defensive: source dir vanished mid-reorganize. Return
        empty, don't raise."""
        siblings = _find_sibling_audio_files(str(tmp_path / "nonexistent" / "X.opus"))
        assert siblings == []


# ---------------------------------------------------------------------------
# Sibling move
# ---------------------------------------------------------------------------


class TestMoveSiblingToDestination:
    def test_moves_to_same_dir_as_canonical_with_renamed_stem(self, tmp_path):
        """Canonical's renamed stem propagates to siblings — so a
        renamed `.opus` (`01 Track.opus`) gets a matching `.flac`
        (`01 Track.flac`) at the new location, even if source was
        `track-original-name.flac`."""
        src_dir = tmp_path / "old"
        dst_dir = tmp_path / "Artist" / "Album"
        src_dir.mkdir()
        sibling_src = src_dir / "track-original-name.flac"
        sibling_src.write_bytes(b"flac-data")

        canonical_dst = dst_dir / "01 Track.opus"

        result = _move_sibling_to_destination(str(sibling_src), str(canonical_dst))

        # Sibling at new location with canonical's renamed stem +
        # sibling's original extension
        expected = dst_dir / "01 Track.flac"
        assert result == str(expected)
        assert expected.exists()
        assert expected.read_bytes() == b"flac-data"
        # Source removed
        assert not sibling_src.exists()

    def test_creates_destination_dir_if_missing(self, tmp_path):
        src = tmp_path / "old" / "X.flac"
        src.parent.mkdir()
        src.write_bytes(b"data")

        canonical_dst = tmp_path / "new" / "X.opus"

        result = _move_sibling_to_destination(str(src), str(canonical_dst))

        assert result is not None
        assert (tmp_path / "new" / "X.flac").exists()

    def test_no_op_when_source_equals_destination(self, tmp_path):
        """Defensive: if sibling is already at the destination (e.g.
        idempotent re-run), return path without raising."""
        f = tmp_path / "X.flac"
        f.write_bytes(b"data")
        canonical_dst = tmp_path / "X.opus"

        result = _move_sibling_to_destination(str(f), str(canonical_dst))
        # Sibling stays put (same dir as canonical destination)
        assert f.exists()
        assert result == str(f)

    def test_returns_none_on_failure(self, tmp_path, monkeypatch):
        """OS error on move → returns None, doesn't raise. Caller
        treats as best-effort (sibling stays at old location, user
        sees it next reorganize run)."""
        src = tmp_path / "old" / "X.flac"
        src.parent.mkdir()
        src.write_bytes(b"data")

        def fake_move(s, d):
            raise OSError("disk full")

        monkeypatch.setattr('core.library_reorganize.shutil.move', fake_move)

        result = _move_sibling_to_destination(
            str(src), str(tmp_path / "new" / "X.opus"),
        )
        assert result is None


# ---------------------------------------------------------------------------
# iss29-E02: the sibling move must not destroy an existing destination file
# ---------------------------------------------------------------------------


class TestSiblingMoveNeverOverwrites:
    """The canonical move refuses to overwrite; the sibling move must too.

    ``_rename_track_in_place`` checks the destination and returns
    'destination already exists' rather than clobbering — its docstring calls
    this out as "never silent data loss". But the sibling carry ran *before*
    that check and used a bare ``shutil.move``, which on POSIX resolves to
    ``os.rename`` for a regular file and overwrites without error, without a
    log line and without a counter.

    Reachable whenever lossy-copy is on and a previous partial run (or a second
    edition) already put a file with the canonical's post-rename stem in the
    destination.
    """

    def test_refuses_to_overwrite_an_existing_destination(self, tmp_path):
        src_dir = tmp_path / "Old"
        dst_dir = tmp_path / "New"
        src_dir.mkdir()
        dst_dir.mkdir()
        sibling_src = src_dir / "05 Song.mp3"
        sibling_src.write_bytes(b"the sibling being moved")
        # Someone else's file already occupies the destination name.
        occupied = dst_dir / "05 - Song.mp3"
        occupied.write_bytes(b"a DIFFERENT recording that must survive")

        canonical_dst = dst_dir / "05 - Song.flac"
        result = _move_sibling_to_destination(str(sibling_src), str(canonical_dst))

        assert result is None, "an occupied destination must be reported as a failure"
        assert occupied.read_bytes() == b"a DIFFERENT recording that must survive"
        assert sibling_src.exists(), "the source must be left in place, not silently lost"

    def test_still_moves_when_the_destination_is_free(self, tmp_path):
        src_dir = tmp_path / "Old"
        dst_dir = tmp_path / "New"
        src_dir.mkdir()
        sibling_src = src_dir / "05 Song.mp3"
        sibling_src.write_bytes(b"payload")

        canonical_dst = dst_dir / "05 - Song.flac"
        result = _move_sibling_to_destination(str(sibling_src), str(canonical_dst))

        assert result == str(dst_dir / "05 - Song.mp3")
        assert (dst_dir / "05 - Song.mp3").read_bytes() == b"payload"
        assert not sibling_src.exists()


class TestRenameInPlaceOrdersTheSiblingCarry:
    """A failed canonical rename must not leave the siblings moved.

    Siblings used to be carried across BEFORE the canonical's own rename. When
    that rename then failed, the album ended up split over two directories with
    no error path that could put it back.
    """

    def test_a_failing_canonical_rename_leaves_siblings_at_the_source(self, tmp_path, monkeypatch):
        from core import library_reorganize

        src_dir = tmp_path / "Old"
        dst_dir = tmp_path / "New"
        src_dir.mkdir()
        canonical = src_dir / "05 Song.flac"
        sibling = src_dir / "05 Song.mp3"
        canonical.write_bytes(b"flac")
        sibling.write_bytes(b"mp3")

        def _boom(*_args, **_kwargs):
            raise OSError("disk went away mid-rename")

        monkeypatch.setattr(library_reorganize.os, "rename", _boom)

        ok, err = library_reorganize._rename_track_in_place(
            str(canonical), str(dst_dir / "05 - Song.flac")
        )

        assert ok is False
        assert err
        assert sibling.exists(), "sibling must not be stranded in the destination"
        assert not (dst_dir / "05 - Song.mp3").exists()


# ---------------------------------------------------------------------------
# iss29-E05: lyrics Library V2 wrote must survive a reorganize
# ---------------------------------------------------------------------------


class TestSidecarsAreCarriedNotDeleted:
    """"Fetch Lyrics" then "Reorganize" used to empty the lyrics column.

    The full run stages only the audio, so the ``.lrc`` was never carried to the
    destination — and the finalisation step then deleted it at the source. lib2
    writes exactly these files, so reorganize was destroying its own
    application's data. Both other movers in the project (``_fix_path_mismatch``,
    ``_rename_to_basename``) carry the sidecar.
    """

    def test_lrc_follows_the_track_to_its_new_name(self, tmp_path):
        from core.library_reorganize import _carry_track_sidecars

        src_dir = tmp_path / "Old"
        dst_dir = tmp_path / "New"
        src_dir.mkdir()
        dst_dir.mkdir()
        (src_dir / "05 Song.lrc").write_text("[00:01.00]a lyric line")
        (src_dir / "05 Song.nfo").write_text("notes")

        _carry_track_sidecars(str(src_dir / "05 Song.flac"), str(dst_dir / "05 - Song.flac"))

        assert (dst_dir / "05 - Song.lrc").read_text() == "[00:01.00]a lyric line"
        assert (dst_dir / "05 - Song.nfo").read_text() == "notes"
        assert not (src_dir / "05 Song.lrc").exists()
        assert not (src_dir / "05 Song.nfo").exists()

    def test_a_sidecar_already_at_the_destination_is_not_clobbered(self, tmp_path):
        from core.library_reorganize import _carry_track_sidecars

        src_dir = tmp_path / "Old"
        dst_dir = tmp_path / "New"
        src_dir.mkdir()
        dst_dir.mkdir()
        (src_dir / "05 Song.lrc").write_text("source copy")
        (dst_dir / "05 - Song.lrc").write_text("destination copy")

        _carry_track_sidecars(str(src_dir / "05 Song.flac"), str(dst_dir / "05 - Song.flac"))

        # The destination wins, and the source folder is left prunable.
        assert (dst_dir / "05 - Song.lrc").read_text() == "destination copy"
        assert not (src_dir / "05 Song.lrc").exists()
