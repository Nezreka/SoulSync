"""Regression tests for issue #1512 — genre merge dedupe by normalized identity.

`_write_embedded_metadata()` merges genre lists from MusicBrainz, AudioDB,
Last.fm and Bandcamp, but its dedupe key was `genre.strip().lower()`, so
spelling variants like "Hip-Hop" (AudioDB) and "Hip Hop" (MusicBrainz)
survived as two genres in the tag. The dedupe key is now
`_normalize_for_match()` from `core.genre_filter`, keeping the first
spelling seen (source genre, then MusicBrainz, AudioDB, Last.fm, Bandcamp).
"""

from __future__ import annotations

import pytest

from core.metadata import source as ms
from tests.metadata.test_metadata_enrichment import (
    _Config,
    _fake_symbols,
    _FakeAudio,
)


def _merge_genres(pp, metadata, cfg_values):
    audio = _FakeAudio()
    symbols = _fake_symbols(audio)
    cfg = _Config(cfg_values)
    ms._write_embedded_metadata(audio, metadata, pp, cfg, symbols)
    tcons = [f for f in audio.tags.added if f.kind == "TCON"]
    assert len(tcons) == 1, f"expected one TCON frame, got {len(tcons)}"
    return tcons[0].kwargs["text"]


def _pp(**overrides):
    pp = ms._blank_post_process_state()
    pp.update(overrides)
    return pp


_BASE_CFG = {
    "metadata_enhancement.tags.genre_merge": True,
    "musicbrainz.tags.genres": True,
    "audiodb.tags.genre": True,
    "lastfm.tags.genres": True,
    "bandcamp.tags.genre_merge": True,
}


def test_merge_collapses_hyphen_and_space_spelling_variants():
    """#1512: 'Hip Hop' (MusicBrainz) + 'Hip-Hop' (AudioDB) -> single genre."""
    pp = _pp(mb_genres=["Hip Hop"], audiodb_genre="Hip-Hop")
    text = _merge_genres(pp, {}, _BASE_CFG)
    assert text == ["Hip Hop"]


def test_merge_keeps_first_spelling_for_rnb_variant():
    """#1512: 'R&B' (source genre) wins over 'RnB' (MusicBrainz) — first wins."""
    pp = _pp(mb_genres=["RnB"])
    text = _merge_genres(pp, {"genre": "R&B"}, _BASE_CFG)
    assert text == ["R&B"]


def test_merge_still_keeps_distinct_genres():
    """Unrelated genres still merge side-by-side, in order."""
    pp = _pp(mb_genres=["Hip Hop", "Comedy"], audiodb_genre="Hip-Hop",
             lastfm_tags=["Hardcore Hip Hop"])
    text = _merge_genres(pp, {}, _BASE_CFG)
    # The reporter's own Encore example: variants collapse, distinct genres stay.
    assert text == ["Hip Hop, Comedy, Hardcore Hip Hop"]


def test_merge_cap_applies_after_dedupe():
    """#1512: a collapsed variant must not consume one of the 5 genre slots."""
    pp = _pp(mb_genres=["Hip Hop", "Rock", "Comedy", "Jazz", "Blues"],
             audiodb_genre="Hip-Hop", lastfm_tags=["Country"])
    text = _merge_genres(pp, {}, _BASE_CFG)
    assert text == ["Hip Hop, Rock, Comedy, Jazz, Blues"]


def test_merge_dedupe_still_case_insensitive():
    """Exact repeats differing only in case still collapse ('rock' + 'Rock')."""
    pp = _pp(mb_genres=["Rock"], audiodb_genre="rock")
    text = _merge_genres(pp, {}, _BASE_CFG)
    assert text == ["Rock"]


def test_merge_collapses_unicode_dash_variants():
    """#1512: MusicBrainz can emit U+2013 en dashes — still one genre."""
    pp = _pp(mb_genres=["Hip–Hop"], audiodb_genre="Hip Hop")
    text = _merge_genres(pp, {}, _BASE_CFG)
    assert text == ["Hip–Hop"]


def test_merge_first_wins_between_enrichment_sources():
    """#1512: first spelling wins regardless of which source carries it —
    AudioDB's 'Hip-Hop' comes before Last.fm's 'Hip Hop'."""
    pp = _pp(audiodb_genre="Hip-Hop", lastfm_tags=["Hip Hop"])
    text = _merge_genres(pp, {}, _BASE_CFG)
    assert text == ["Hip-Hop"]


def test_merge_ignores_none_and_empty_enrichment_items():
    """Defensive: None/blank entries never crash or land in the tag."""
    pp = _pp(mb_genres=["Rock", None, "  "], audiodb_genre="")
    text = _merge_genres(pp, {}, _BASE_CFG)
    assert text == ["Rock"]
