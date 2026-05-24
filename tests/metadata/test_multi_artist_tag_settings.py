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
        """When search response has no artists list, the artist-resolution
        helper falls back to the artist_dict.name as a single-element list.
        Multi-value tag write still no-ops because len([primary]) == 1.
        """
        from core.metadata import source as src_module

        context = {
            "original_search_result": {"title": "T", "artists": None},
            "source": "spotify",
        }
        with patch.object(src_module, "get_config_manager", return_value=_make_cfg()):
            meta = src_module.extract_source_metadata(context, {"name": "X"}, {})
        assert meta.get("_artists_list") == ["X"]

    def test_soulseek_shape_falls_back_to_track_info_artists(self):
        """Soulseek matched-download regression: original_search_result
        carries 'artist' (singular string) but no 'artists' list, while
        track_info (the matched Spotify track object) carries the full
        multi-artist array. Helper should pull from track_info.
        """
        from core.metadata import source as src_module

        context = {
            "original_search_result": {
                "title": "DNA.",
                "artist": "Kendrick Lamar",
            },
            "track_info": {
                "name": "DNA.",
                "artists": [{"name": "Kendrick Lamar"}, {"name": "Rihanna"}],
            },
            "source": "spotify",
        }
        with patch.object(src_module, "get_config_manager", return_value=_make_cfg()):
            meta = src_module.extract_source_metadata(context, {"name": "Kendrick Lamar"}, {})
        assert meta.get("_artists_list") == ["Kendrick Lamar", "Rihanna"]
        assert meta.get("artist") == "Kendrick Lamar, Rihanna"


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


# ---------------------------------------------------------------------------
# Deezer-specific: upgrade single-artist search results via /track/<id>
# ---------------------------------------------------------------------------
#
# Deezer's `/search` endpoint only returns the primary artist for each
# track. The full contributors array (feat., remix collaborators,
# producers credited as artists) lives on `/track/<id>`. Reporter said
# their Retag flow worked because it called the per-track endpoint, but
# the initial enrichment used search-result data and missed the
# contributors. The fix: when source==deezer AND search returned only
# one artist AND a track_id is available, fetch the full track details
# and upgrade the artists list.


class TestDeezerContributorsUpgrade:
    def test_upgrades_when_deezer_search_returns_single_artist(self):
        """Reporter's exact case: Deezer track with multiple
        contributors, search returns just the primary, /track/<id>
        returns all 3. Upgrade path fetches the full set."""
        from core.metadata import source as src_module

        context = {
            "original_search_result": {
                "title": "Collab Track",
                "artists": [{"name": "Primary"}],  # Only one — search-response shape
            },
            "source": "deezer",
        }
        # track_id resolved via original_search_result.id by get_import_source_ids
        context["original_search_result"]["id"] = "12345"

        fake_deezer = SimpleNamespace(get_track_details=MagicMock(return_value={
            "id": "12345",
            "name": "Collab Track",
            "artists": ["Primary", "Featured1", "Featured2"],  # Full contributors
        }))

        cfg_overrides = {"metadata_enhancement.tags.artist_separator": "; "}

        with patch.object(src_module, "get_config_manager", return_value=_make_cfg(cfg_overrides)), \
             patch("core.metadata.get_deezer_client", return_value=fake_deezer):
            meta = src_module.extract_source_metadata(context, {"name": "Primary"}, {})

        # Upgraded list reaches the multi-value tag
        assert meta["_artists_list"] == ["Primary", "Featured1", "Featured2"]
        # And the joined ARTIST string respects the separator
        assert meta["artist"] == "Primary; Featured1; Featured2"
        fake_deezer.get_track_details.assert_called_once_with("12345")

    def test_no_upgrade_when_search_already_returned_multiple(self):
        """When search already has multiple artists, skip the upgrade —
        no extra API call needed."""
        from core.metadata import source as src_module

        context = {
            "original_search_result": {
                "title": "T",
                "artists": [{"name": "A"}, {"name": "B"}],  # Already multi
            },
            "source": "deezer",
        }
        # track_id resolved via original_search_result.id by get_import_source_ids
        context["original_search_result"]["id"] = "12345"

        fake_deezer = SimpleNamespace(get_track_details=MagicMock())

        with patch.object(src_module, "get_config_manager", return_value=_make_cfg()), \
             patch("core.metadata.get_deezer_client", return_value=fake_deezer):
            meta = src_module.extract_source_metadata(context, {"name": "A"}, {})

        assert meta["_artists_list"] == ["A", "B"]
        # No upgrade call — search already had what we needed
        fake_deezer.get_track_details.assert_not_called()

    def test_no_upgrade_for_non_deezer_sources(self):
        """Spotify/iTunes/Tidal already return multi-artist in search,
        so the Deezer-specific upgrade path must NOT fire for them.
        Otherwise we'd be making redundant API calls."""
        from core.metadata import source as src_module

        context = {
            "original_search_result": {
                "title": "T",
                "artists": [{"name": "A"}],
            },
            "source": "spotify",
            "source_track_id": "12345",
        }

        fake_deezer = SimpleNamespace(get_track_details=MagicMock())

        with patch.object(src_module, "get_config_manager", return_value=_make_cfg()), \
             patch("core.metadata.get_deezer_client", return_value=fake_deezer):
            meta = src_module.extract_source_metadata(context, {"name": "A"}, {})

        # Single artist preserved, no Deezer upgrade attempted
        assert meta["_artists_list"] == ["A"]
        fake_deezer.get_track_details.assert_not_called()

    def test_upgrade_failure_falls_through_to_search_result(self):
        """Defensive: if /track/<id> fails (network error, deezer
        client unavailable), fall through to the search-result list.
        Don't lose the single-artist data we already had."""
        from core.metadata import source as src_module

        context = {
            "original_search_result": {
                "title": "T",
                "artists": [{"name": "Primary"}],
            },
            "source": "deezer",
        }
        # track_id resolved via original_search_result.id by get_import_source_ids
        context["original_search_result"]["id"] = "12345"

        fake_deezer = SimpleNamespace(get_track_details=MagicMock(
            side_effect=RuntimeError("network down"),
        ))

        with patch.object(src_module, "get_config_manager", return_value=_make_cfg()), \
             patch("core.metadata.get_deezer_client", return_value=fake_deezer):
            meta = src_module.extract_source_metadata(context, {"name": "Primary"}, {})

        # Search-result list preserved
        assert meta["_artists_list"] == ["Primary"]
        assert meta["artist"] == "Primary"

    def test_upgrade_returns_same_count_no_change(self):
        """Edge: /track/<id> returns the same single artist (track
        genuinely has one artist on Deezer too). Should preserve the
        list without false-positive upgrade."""
        from core.metadata import source as src_module

        context = {
            "original_search_result": {
                "title": "T",
                "artists": [{"name": "Solo"}],
            },
            "source": "deezer",
        }
        # track_id resolved via original_search_result.id by get_import_source_ids
        context["original_search_result"]["id"] = "12345"

        fake_deezer = SimpleNamespace(get_track_details=MagicMock(return_value={
            "id": "12345",
            "artists": ["Solo"],  # Same single artist confirmed
        }))

        with patch.object(src_module, "get_config_manager", return_value=_make_cfg()), \
             patch("core.metadata.get_deezer_client", return_value=fake_deezer):
            meta = src_module.extract_source_metadata(context, {"name": "Solo"}, {})

        assert meta["_artists_list"] == ["Solo"]

    def test_no_upgrade_when_no_track_id(self):
        """Edge: source==deezer but no track_id. Can't call
        /track/<id> without an id. Don't attempt the upgrade."""
        from core.metadata import source as src_module

        context = {
            "original_search_result": {
                "title": "T",
                "artists": [{"name": "Primary"}],
            },
            "source": "deezer",
            "source_track_id": "",  # Missing
        }

        fake_deezer = SimpleNamespace(get_track_details=MagicMock())

        with patch.object(src_module, "get_config_manager", return_value=_make_cfg()), \
             patch("core.metadata.get_deezer_client", return_value=fake_deezer):
            meta = src_module.extract_source_metadata(context, {"name": "Primary"}, {})

        assert meta["_artists_list"] == ["Primary"]
        fake_deezer.get_track_details.assert_not_called()
