"""Regression tests for duplicate detector mount-path filter.

When a user binds the same host music directory into both SoulSync
and a media server (e.g. Plex at /media/Music, SoulSync at
/app/Transfer), the duplicate detector used to flag the two DB rows
that point at the same physical file as a duplicate group. The new
``_is_same_physical_file`` helper filters those pairs out.
"""

import os

import pytest

from core.repair_jobs.duplicate_detector import _is_same_physical_file


class TestIsSamePhysicalFile:
    def test_same_file_at_different_mount_roots_is_filtered(self) -> None:
        """The reported scenario: SoulSync container and Plex container
        bind the same host directory at different mount points."""
        p1 = "/app/Transfer/The Smashing Pumpkins/MACHINA _ The Machines of God/15 - With Every Light.flac"
        p2 = "/media/Music/The Smashing Pumpkins/MACHINA _ The Machines of God/15 - With Every Light.flac"
        assert _is_same_physical_file(p1, p2, 235.0, 235.0)

    def test_durations_within_one_second_pass(self) -> None:
        """Allow ±1 second slack — different metadata readers occasionally
        round duration slightly differently."""
        p1 = "/a/Artist/Album/track.flac"
        p2 = "/b/Artist/Album/track.flac"
        assert _is_same_physical_file(p1, p2, 120.5, 121.0)

    def test_durations_more_than_one_second_apart_does_not_match(self) -> None:
        """Two files with the same name but actually different audio
        content should NOT be filtered."""
        p1 = "/a/Artist/Album/track.flac"
        p2 = "/b/Artist/Album/track.flac"
        assert not _is_same_physical_file(p1, p2, 120.0, 130.0)

    def test_identical_paths_are_one_file_not_a_duplicate_pair(self) -> None:
        """Two rows carrying the SAME path are one file recorded twice.

        This asserted the opposite, on the reasoning that a shared root meant
        "duplicates of a re-download". A re-download to the same path
        overwrites the file, it cannot produce a second one. Flagging the pair
        made the job report a file as a duplicate of itself, and clicking keep
        moved the only copy to the deleted folder (#1210)."""
        p = "/app/Transfer/Artist/Album/track.flac"
        assert _is_same_physical_file(p, p, 200.0, 200.0)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX case-sensitivity only")
    def test_paths_differing_only_in_case_are_two_files_on_posix(self) -> None:
        """Linux is case-sensitive, and that is where this mostly runs (Docker).

        The identical-path short-circuit was first written with .lower(), which
        made these two look like one file and quietly hid a real duplicate.
        normcase is a no-op on POSIX, so the pair stays flaggable here."""
        assert not _is_same_physical_file(
            "/music/Artist/Album/Song.flac",
            "/music/Artist/Album/song.flac",
            200.0, 200.0,
        )

    def test_windows_drive_path_pair_still_matches(self) -> None:
        """Same file, written the way two different scanners would store it:
        backslashes vs forward slashes, different drive-letter case.

        Named for what it checks rather than for the equality short-circuit —
        on POSIX normcase is a no-op, so these two are NOT byte-identical and
        this actually lands on the mount-root branch below. Kept because that
        combination is worth pinning either way."""
        assert _is_same_physical_file(
            "C:\\Music\\Artist\\Album\\track.flac",
            "c:/music/artist/album/track.flac",
            200.0, 200.0,
        )

    def test_legit_duplicate_under_sibling_albums_is_not_filtered(self) -> None:
        """Two genuinely-duplicate downloads under different parent
        directories should still be flagged as a duplicate group."""
        p1 = "/app/Transfer/Artist/Album A/track.flac"
        p2 = "/app/Transfer/Artist/Album B/track.flac"
        # The trailing 3 segments differ (album folders), so the helper
        # short-circuits and the pair stays in the duplicate group.
        assert not _is_same_physical_file(p1, p2, 200.0, 200.0)

    def test_paths_too_short_returns_false(self) -> None:
        """Defensive: don't filter when there isn't enough path context."""
        assert not _is_same_physical_file("/a.flac", "/b.flac", 200.0, 200.0)
        assert not _is_same_physical_file("/a/b.flac", "/c/b.flac", 200.0, 200.0)

    def test_missing_paths_returns_false(self) -> None:
        assert not _is_same_physical_file(None, "/a/b/c.flac", 200.0, 200.0)
        assert not _is_same_physical_file("", "/a/b/c.flac", 200.0, 200.0)
        assert not _is_same_physical_file("/a/b/c.flac", None, 200.0, 200.0)

    def test_missing_durations_still_filters_when_paths_match(self) -> None:
        """If duration data is unavailable, fall back to path-only match
        because the path tail equality is itself a strong signal."""
        p1 = "/app/Transfer/Artist/Album/track.flac"
        p2 = "/media/Music/Artist/Album/track.flac"
        assert _is_same_physical_file(p1, p2, None, None)
        assert _is_same_physical_file(p1, p2, 200.0, None)
        assert _is_same_physical_file(p1, p2, None, 200.0)

    def test_windows_style_paths_normalize(self) -> None:
        """Mixed-separator paths from Windows hosts should still match."""
        p1 = "C:\\Music\\Artist\\Album\\track.flac"
        p2 = "/media/Music/Artist/Album/track.flac"
        assert _is_same_physical_file(p1, p2, 200.0, 200.0)

    def test_case_insensitive_match(self) -> None:
        """Filesystems vary in case sensitivity; treat tail comparison
        as case-insensitive so case differences don't defeat the filter."""
        p1 = "/app/Transfer/The Smashing Pumpkins/MACHINA/15 - With Every Light.flac"
        p2 = "/media/Music/the smashing pumpkins/machina/15 - with every light.flac"
        assert _is_same_physical_file(p1, p2, 200.0, 200.0)
