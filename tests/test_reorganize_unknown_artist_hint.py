"""Pin the unresolvable-reason hint in library_reorganize.

Discord report (Foxxify) — Phase B: stuck "Unknown Artist / <album_id>"
folders left over from the pre-#524 manual-import bug. Reorganize
couldn't move them (no usable metadata source ID) and emitted a
generic "run enrichment first" message — but enrichment can't fix
these rows. The right tool is the existing Unknown Artist Fixer
repair job (reads file tags, re-resolves metadata, re-tags + moves
file). These tests pin the detection helpers + reason text so the
hint stays correct as the file evolves.
"""

from __future__ import annotations

from core.library_reorganize import (
    _is_unknown_artist,
)


class TestIsUnknownArtist:
    def test_unknown_artist_string(self):
        assert _is_unknown_artist("Unknown Artist") is True

    def test_unknown_artist_lowercase(self):
        assert _is_unknown_artist("unknown artist") is True

    def test_unknown_artist_with_whitespace(self):
        assert _is_unknown_artist("  Unknown Artist  ") is True

    def test_unknown_alone(self):
        """Some import paths set just 'Unknown' (no 'Artist' suffix)."""
        assert _is_unknown_artist("Unknown") is True

    def test_empty_string(self):
        assert _is_unknown_artist("") is True

    def test_none(self):
        assert _is_unknown_artist(None) is True

    def test_real_artist(self):
        assert _is_unknown_artist("Radiohead") is False

    def test_artist_containing_unknown_substring(self):
        """Substring 'unknown' shouldn't trigger — only the exact
        placeholder names. Real artists can contain that word."""
        assert _is_unknown_artist("Unknown Mortal Orchestra") is False


