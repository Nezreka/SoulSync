"""Tests for ``core.imports.text_matching`` — MusicMatchingEngine wrappers."""

from __future__ import annotations

import pytest

from core.imports.text_matching import (
    album_similarity,
    artist_similarity,
    title_similarity,
)


class TestTextMatching:
    def test_artist_initials_collapsed(self):
        assert artist_similarity("A.R. Rahman", "A R Rahman") == pytest.approx(1.0)
        assert artist_similarity("A. R. Rahman", "A.R. Rahman") == pytest.approx(1.0)

    def test_album_near_variant_still_high(self):
        score = album_similarity("3 Nights 4 Days", "3 Nights And 4 Days")
        assert score >= 0.85

    def test_title_minor_spelling_difference(self):
        score = title_similarity("Sheesha", "Sheeshe")
        assert score >= 0.85

    def test_title_short_vs_long_stays_conservative(self):
        score = title_similarity("Sheesha", "Sheeshe Mein Nasha")
        assert score < 0.7
