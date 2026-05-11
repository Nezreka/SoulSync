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
    _looks_like_album_id_title,
    _unresolvable_reason,
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


class TestLooksLikeAlbumIdTitle:
    def test_long_numeric_string_is_album_id(self):
        """Reporter's case: album.title set to the numeric album_id
        by the pre-#524 manual-import bug."""
        assert _looks_like_album_id_title("1234567890") is True

    def test_six_digit_minimum(self):
        """Edge: 5 digits is too short to be a real album_id pattern
        — could just be an album titled '12345'. Cutoff is 6+."""
        assert _looks_like_album_id_title("12345") is False
        assert _looks_like_album_id_title("123456") is True

    def test_alphanumeric_is_not_album_id(self):
        """Real album titles with numbers (Blink-182, Sum 41, etc.)
        must not trigger."""
        assert _looks_like_album_id_title("Sum 41") is False
        assert _looks_like_album_id_title("1999") is False  # short

    def test_empty_string(self):
        assert _looks_like_album_id_title("") is False

    def test_none(self):
        assert _looks_like_album_id_title(None) is False

    def test_real_album_title(self):
        assert _looks_like_album_id_title("In Rainbows") is False

    def test_whitespace_stripped(self):
        """Defensive: leading/trailing whitespace shouldn't fool the
        detector."""
        assert _looks_like_album_id_title("  1234567890  ") is True


class TestUnresolvableReason:
    def test_unknown_artist_routes_to_fixer_hint(self):
        """Reporter's exact case — Unknown Artist row should point
        at the Fix Unknown Artists repair job, not generic
        enrichment advice."""
        reason = _unresolvable_reason(
            {'artist_name': 'Unknown Artist', 'title': 'Some Album'},
            primary_source='deezer',
            strict_source=False,
        )
        assert "Fix Unknown Artists" in reason
        assert "placeholder metadata" in reason

    def test_album_id_title_routes_to_fixer_hint(self):
        """Reverse case — album.title is a numeric album_id."""
        reason = _unresolvable_reason(
            {'artist_name': 'Real Artist', 'title': '9876543210'},
            primary_source='deezer',
            strict_source=False,
        )
        assert "Fix Unknown Artists" in reason

    def test_real_album_with_no_source_id_keeps_enrichment_hint(self):
        """Sanity: real artist + real title but no source ID still
        gets the generic enrichment hint. Don't mis-route normal
        no-source-ID albums into the fixer flow."""
        reason = _unresolvable_reason(
            {'artist_name': 'Radiohead', 'title': 'In Rainbows'},
            primary_source='deezer',
            strict_source=False,
        )
        assert "Fix Unknown Artists" not in reason
        assert "No metadata source ID" in reason

    def test_strict_source_path_keeps_strict_text(self):
        """When strict_source=True and the row is fine (real artist
        + real title), the existing strict-source message is
        preserved. Hint only fires for the bad-metadata shape."""
        reason = _unresolvable_reason(
            {'artist_name': 'Radiohead', 'title': 'In Rainbows'},
            primary_source='spotify',
            strict_source=True,
        )
        assert "Fix Unknown Artists" not in reason
        assert "spotify" in reason.lower()
        assert "tracklist" in reason

    def test_strict_source_with_unknown_artist_prefers_fixer_hint(self):
        """Bad-metadata shape wins over strict-source — Unknown
        Artist always needs the fixer regardless of source mode."""
        reason = _unresolvable_reason(
            {'artist_name': 'Unknown Artist', 'title': 'Whatever'},
            primary_source='spotify',
            strict_source=True,
        )
        assert "Fix Unknown Artists" in reason
