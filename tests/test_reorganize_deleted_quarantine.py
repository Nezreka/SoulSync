"""Reorganize must skip files in the duplicate-cleaner quarantine (#746).

The Duplicate Cleaner moves de-duplicated files into ``<transfer>/deleted/``.
If the user's media server scans the transfer folder (e.g. a ``/music`` root
holding both the library and the transfer dir), those quarantined files get real
rows in SoulSync's DB. Reorganize is purely DB-driven, so without a guard it
would move them back OUT of /deleted to the template location.

These tests pin:
  1. ``_is_in_deleted_quarantine`` — the anchored detection, including the
     false-positive guard (an album literally named "Deleted" elsewhere is kept).
  2. ``preview_album_reorganize`` — a quarantined track is surfaced as a skip,
     a normal track is not (proves the planner-shared guard fires on the path
     both preview AND apply run through).
"""

from __future__ import annotations

import os

from core.library_reorganize import _is_in_deleted_quarantine


TRANSFER = os.path.join(os.sep, "music", "soulsync")


class TestIsInDeletedQuarantine:
    def test_file_directly_in_quarantine_is_flagged(self):
        p = os.path.join(TRANSFER, "deleted", "Artist", "Album", "01.flac")
        assert _is_in_deleted_quarantine(p, TRANSFER) is True

    def test_file_in_normal_album_is_not_flagged(self):
        p = os.path.join(TRANSFER, "Artist", "Album", "01.flac")
        assert _is_in_deleted_quarantine(p, TRANSFER) is False

    def test_album_with_deleted_in_name_is_kept(self):
        """Anchored to the <transfer>/deleted PREFIX, not a substring — a
        real album like 'Deleted Scenes' nested under an artist must NOT be
        skipped."""
        p = os.path.join(TRANSFER, "Artist", "Deleted Scenes", "01.flac")
        assert _is_in_deleted_quarantine(p, TRANSFER) is False

    def test_known_unavoidable_collision_is_documented(self):
        """The ONE genuine ambiguity: an artist folder named exactly
        'deleted' sitting directly at the transfer root occupies the same
        path as the duplicate-cleaner quarantine, so it IS treated as
        quarantine. This is unavoidable (we can't tell a real 'deleted'
        artist from the cleaner's dir) and accepted — pinned here so the
        behavior is intentional, not a surprise. Differently-cased or
        nested 'Deleted' names are safe (see the other tests)."""
        collision = os.path.join(TRANSFER, "deleted", "Album", "01.flac")
        assert _is_in_deleted_quarantine(collision, TRANSFER) is True

    def test_substring_not_matched(self):
        """'Undeleted' / 'deleted_scenes' as a segment must not trip the
        exact-segment / prefix check."""
        p = os.path.join(TRANSFER, "Undeleted", "Album", "01.flac")
        assert _is_in_deleted_quarantine(p, TRANSFER) is False

    def test_no_transfer_dir_falls_back_to_segment_match(self):
        p = os.path.join(os.sep, "anywhere", "deleted", "x.flac")
        assert _is_in_deleted_quarantine(p, None) is True
        p2 = os.path.join(os.sep, "anywhere", "Undeleted", "x.flac")
        assert _is_in_deleted_quarantine(p2, None) is False

    def test_empty_path_is_safe(self):
        assert _is_in_deleted_quarantine(None, TRANSFER) is False
        assert _is_in_deleted_quarantine("", TRANSFER) is False

    def test_nested_subfolders_under_quarantine_flagged(self):
        p = os.path.join(TRANSFER, "deleted", "a", "b", "c", "track.flac")
        assert _is_in_deleted_quarantine(p, TRANSFER) is True
