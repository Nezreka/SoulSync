"""Pin multi-artist tag-write settings (issue: 'Multi artists settings not working').

Three settings under `metadata_enhancement.tags`:
  - `write_multi_artist` (bool) — write a separate multi-value tag
    listing every artist (TXXX:Artists for ID3, "artists" key for
    Vorbis). Picard convention.
  - `artist_separator` (string, default ", ") — delimiter used to
    join multiple artists into the single ARTIST/TPE1 string.
  - `feat_in_title` (bool) — when true, ARTIST/TPE1 carries ONLY
    the primary artist; featured artists get pulled out and
    appended to the title as " (feat. X, Y)".

Reporter (Netti93): all three were partially or completely
unimplemented.
  - Bug 1: `_artists_list` field read by enrichment.py was never
    populated by source.py → multi-value writes silently no-op'd.
  - Bug 2: `artist_separator` referenced in UI but ZERO Python code
    read it → always hardcoded ", ".
  - Bug 3: `feat_in_title` referenced in UI but ZERO Python code
    read it → no implementation at all.

These tests pin the fixed `extract_source_metadata` behavior:
  - `_artists_list` populated whenever search response has multiple artists
  - `artist_separator` config drives the join character for ARTIST string
  - `feat_in_title` pulls featured artists into title, leaves only
    primary in ARTIST string
  - Title-already-has-feat case isn't double-appended
  - Single-artist case unaffected by either setting
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_cfg(overrides=None):
    """Stub config_manager. Defaults match the unset-config case
    so each test can selectively override."""
    overrides = overrides or {}
    defaults = {
        "metadata_enhancement.enabled": True,
        "metadata_enhancement.tags.write_multi_artist": False,
        "metadata_enhancement.tags.feat_in_title": False,
        "metadata_enhancement.tags.artist_separator": ", ",
    }
    full = {**defaults, **overrides}

    cfg = MagicMock()
    cfg.get.side_effect = lambda key, default=None: full.get(key, default)
    return cfg


def _build_context(artists_list):
    """Minimal context dict matching what extract_source_metadata reads."""
    return {
        "original_search_result": {
            "title": "Sample Track",
            "artists": [{"name": a} for a in artists_list],
        },
        "source": "spotify",
    }


def _call_extract(artists_list, cfg_overrides=None):
    """Helper: patches config + calls extract_source_metadata, returns the
    metadata dict. Avoids the broader source-specific embedding loop by
    only using fields the multi-artist branch touches."""
    from core.metadata import source as src_module

    context = _build_context(artists_list)
    artist_dict = {"name": artists_list[0] if artists_list else ""}
    album_info = {"album_name": "Sample Album"}

    with patch.object(src_module, "get_config_manager", return_value=_make_cfg(cfg_overrides)):
        return src_module.extract_source_metadata(context, artist_dict, album_info)


# ---------------------------------------------------------------------------
# Bug 1: `_artists_list` populated
# ---------------------------------------------------------------------------


class TestArtistsListPopulated:
    def test_multiple_artists_populate_list(self):
        """Reporter's first bug — `_artists_list` field was always
        empty. Verify it now contains every artist from the search
        response."""
        meta = _call_extract(["Eminem", "Dr. Dre", "50 Cent"])
        assert meta.get("_artists_list") == ["Eminem", "Dr. Dre", "50 Cent"]

    def test_single_artist_still_populates_list(self):
        """Edge: even single-artist case populates the list (length 1).
        Avoids special-casing downstream — `len(_artists_list) > 1`
        check in enrichment.py is the gate."""
        meta = _call_extract(["Solo Artist"])
        assert meta.get("_artists_list") == ["Solo Artist"]

    def test_no_artists_falls_through(self):
        """When search response has no artists list, falls through to
        the single-artist branch — no `_artists_list` written."""
        from core.metadata import source as src_module

        context = {
            "original_search_result": {"title": "T", "artists": None},
            "source": "spotify",
        }
        with patch.object(src_module, "get_config_manager", return_value=_make_cfg()):
            meta = src_module.extract_source_metadata(context, {"name": "X"}, {})
        assert "_artists_list" not in meta or meta.get("_artists_list") in (None, [])


# ---------------------------------------------------------------------------
# Bug 2: artist_separator drives ARTIST string
# ---------------------------------------------------------------------------


class TestArtistSeparator:
    def test_default_separator_is_comma_space(self):
        """Default preserves historical behavior — joining with ', '
        so users who haven't set the config see no behavior change."""
        meta = _call_extract(["A", "B", "C"])
        assert meta["artist"] == "A, B, C"

    def test_semicolon_separator(self):
        """Reporter's exact case: artist_separator=';'. Picard convention."""
        meta = _call_extract(["A", "B", "C"], cfg_overrides={
            "metadata_enhancement.tags.artist_separator": ";",
        })
        assert meta["artist"] == "A;B;C"

    def test_separator_with_space(self):
        """Many users prefer '; ' (semi + space). Whatever string
        the user puts in the config gets used verbatim — no trimming."""
        meta = _call_extract(["A", "B"], cfg_overrides={
            "metadata_enhancement.tags.artist_separator": "; ",
        })
        assert meta["artist"] == "A; B"

    def test_separator_unused_for_single_artist(self):
        """Single-artist case: separator irrelevant, ARTIST is just
        the one name. No spurious trailing/leading separator."""
        meta = _call_extract(["Solo"], cfg_overrides={
            "metadata_enhancement.tags.artist_separator": ";",
        })
        assert meta["artist"] == "Solo"


# ---------------------------------------------------------------------------
# Bug 3: feat_in_title — pull featured into title
# ---------------------------------------------------------------------------


class TestFeatInTitle:
    def test_feat_in_title_pulls_featured_to_title(self):
        """Reporter's third bug. With feat_in_title=true, ARTIST holds
        only primary; title gets " (feat. ...)" appended for
        all-but-first."""
        meta = _call_extract(["Eminem", "Dr. Dre", "50 Cent"], cfg_overrides={
            "metadata_enhancement.tags.feat_in_title": True,
        })
        assert meta["artist"] == "Eminem"
        assert "(feat. Dr. Dre, 50 Cent)" in meta["title"]

    def test_feat_in_title_off_uses_separator(self):
        """When feat_in_title is off (default), all artists join the
        ARTIST string per `artist_separator`. Title stays unchanged."""
        meta = _call_extract(["A", "B", "C"], cfg_overrides={
            "metadata_enhancement.tags.feat_in_title": False,
            "metadata_enhancement.tags.artist_separator": " & ",
        })
        assert meta["artist"] == "A & B & C"
        assert "feat" not in meta["title"].lower()

    def test_feat_in_title_skips_when_only_one_artist(self):
        """Single-artist case: feat_in_title is a no-op. ARTIST = the
        single name, title untouched."""
        meta = _call_extract(["Solo"], cfg_overrides={
            "metadata_enhancement.tags.feat_in_title": True,
        })
        assert meta["artist"] == "Solo"
        assert "feat" not in meta["title"].lower()

    def test_feat_in_title_no_double_append_when_title_already_has_feat(self):
        """Defensive: if the source title already includes 'feat.' or
        '(ft.', don't append again. Common on remixes / collabs where
        the platform stores the featured artist in the track name."""
        from core.metadata import source as src_module

        context = {
            "original_search_result": {
                "title": "Track (feat. Already Listed)",
                "artists": [{"name": "Primary"}, {"name": "Featured"}],
            },
            "source": "spotify",
        }
        cfg_overrides = {"metadata_enhancement.tags.feat_in_title": True}
        with patch.object(src_module, "get_config_manager", return_value=_make_cfg(cfg_overrides)):
            meta = src_module.extract_source_metadata(context, {"name": "Primary"}, {})

        # Primary still pulled out of ARTIST...
        assert meta["artist"] == "Primary"
        # ...but title NOT double-appended (would be "(feat. X) (feat. Y)")
        assert meta["title"].count("feat.") == 1

    @pytest.mark.parametrize("source_title", [
        "Track (feat. X)",        # standard parens + period
        "Track (Feat. X)",        # capitalized
        "Track (FEAT X)",         # all caps, no period
        "Track (feat X)",         # no period, parens
        "Track (Featuring X)",    # full word
        "Track [feat. X]",        # square brackets
        "Track ft. X",            # ft + period, no parens/brackets
        "Track (ft X)",           # ft no period, parens
        "Track FT. X",            # FT all caps
    ])
    def test_double_append_guard_recognizes_feat_variants(self, source_title):
        """Defensive: source platforms (spotify / tidal / deezer) use
        wildly different title conventions for featured artists. Guard
        must recognize all of them so we never double-append."""
        from core.metadata import source as src_module

        context = {
            "original_search_result": {
                "title": source_title,
                "artists": [{"name": "Primary"}, {"name": "Featured"}],
            },
            "source": "spotify",
        }
        cfg_overrides = {"metadata_enhancement.tags.feat_in_title": True}
        with patch.object(src_module, "get_config_manager", return_value=_make_cfg(cfg_overrides)):
            meta = src_module.extract_source_metadata(context, {"name": "Primary"}, {})

        # Title left unchanged — no double-append for any variant
        assert meta["title"] == source_title, (
            f"Variant {source_title!r} got double-appended → {meta['title']!r}"
        )

    def test_double_append_guard_does_NOT_falsely_match_substrings(self):
        """Sanity: word-boundary regex must NOT match 'ft' or 'feat'
        as part of bigger words like 'aftermath', 'shaft', 'feature'.
        Otherwise titles containing those words would skip the
        legitimate (feat. X) append."""
        from core.metadata import source as src_module

        context = {
            "original_search_result": {
                "title": "Aftermath",  # contains 'ft' as substring
                "artists": [{"name": "Primary"}, {"name": "Featured"}],
            },
            "source": "spotify",
        }
        cfg_overrides = {"metadata_enhancement.tags.feat_in_title": True}
        with patch.object(src_module, "get_config_manager", return_value=_make_cfg(cfg_overrides)):
            meta = src_module.extract_source_metadata(context, {"name": "Primary"}, {})

        # Should APPEND because 'ft' inside 'Aftermath' isn't a
        # standalone "ft" feature marker
        assert "(feat. Featured)" in meta["title"]


# ---------------------------------------------------------------------------
# Integration — settings combine correctly
# ---------------------------------------------------------------------------


class TestSettingsCombination:
    def test_feat_in_title_overrides_separator_for_artist_string(self):
        """When BOTH settings are on, feat_in_title wins for the
        ARTIST string (primary only). Separator is irrelevant in
        that branch but `_artists_list` still carries every artist
        for the multi-value tag write."""
        meta = _call_extract(["A", "B", "C"], cfg_overrides={
            "metadata_enhancement.tags.feat_in_title": True,
            "metadata_enhancement.tags.artist_separator": ";",
        })
        assert meta["artist"] == "A"
        assert "(feat. B, C)" in meta["title"]
        # Multi-value list still complete — write_multi_artist would
        # use this regardless of feat_in_title.
        assert meta["_artists_list"] == ["A", "B", "C"]

    def test_all_three_off_default_behavior_preserved(self):
        """Sanity: unset config → joined ARTIST, no title change,
        list still populated. Picks up no behavior change for users
        who haven't touched the settings."""
        meta = _call_extract(["A", "B"])
        assert meta["artist"] == "A, B"
        assert meta["title"] == "Sample Track"
        assert meta["_artists_list"] == ["A", "B"]
