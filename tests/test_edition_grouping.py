"""Unit tests for the #1450 shared edition-grouping contract.

``core/edition_grouping.py`` is shared by the Watchlist scanner and (via
Worker B) the Download Discography path — both must group and pick
editions identically, so the pairs, pick semantics, box-set bound and
explicit tie-break are pinned here.
"""

import sys
import types


if "spotipy" not in sys.modules:
    spotipy = types.ModuleType("spotipy")
    oauth2 = types.ModuleType("spotipy.oauth2")

    class _Dummy:
        def __init__(self, *a, **kw):
            pass

    spotipy.Spotify = _Dummy
    oauth2.SpotifyOAuth = _Dummy
    oauth2.SpotifyClientCredentials = _Dummy
    spotipy.oauth2 = oauth2
    sys.modules["spotipy"] = spotipy
    sys.modules["spotipy.oauth2"] = oauth2

if "core.settings" not in sys.modules:
    config_pkg = types.ModuleType("config")
    settings_mod = types.ModuleType("core.settings")

    class _CM:
        def get(self, key, default=None):
            return default

        def get_active_media_server(self):
            return "plex"

    settings_mod.config_manager = _CM()
    config_pkg.settings = settings_mod
    sys.modules["config"] = config_pkg
    sys.modules["core.settings"] = settings_mod

if "core.matching_engine" not in sys.modules:
    me = types.ModuleType("core.matching_engine")

    class _ME:
        def clean_title(self, title):
            return title

    me.MusicMatchingEngine = _ME
    sys.modules["core.matching_engine"] = me

import pytest  # noqa: E402

from core.edition_grouping import (  # noqa: E402
    EDITION_PREFERENCE_ALL,
    EDITION_PREFERENCE_ONE_COMPLETE,
    EDITION_PREFERENCE_ONE_STANDARD,
    EDITION_PREFERENCE_VALUES,
    edition_group_key,
    reduce_edition_group,
)
from core.watchlist_scanner import (  # noqa: E402
    _group_albums_by_edition,
    _resolve_edition_preference,
)


def _rel(track_count, explicit=False, name="R"):
    return {"name": name, "track_count": track_count, "explicit": explicit}


# ---------------------------------------------------------------------------
# edition_group_key — grouping pairs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "a,b",
    [
        ("Infinito (Deluxe)", "Infinito"),
        ("In Utero (30th Anniversary Super Deluxe)", "In Utero"),
        ("Cracker Island", "Cracker Island (Deluxe)"),
        ("Aerosmith (Expanded Edition)", "Aerosmith"),
        ("Abbey Road (Remastered)", "Abbey Road"),
        ("Thriller - Remastered 2011", "Thriller"),
        ("MTV Unplugged In New York (25th Anniversary)", "MTV Unplugged In New York"),
    ],
)
def test_edition_pairs_group_together(a, b):
    assert edition_group_key(a) == edition_group_key(b)


@pytest.mark.parametrize(
    "a,b",
    [
        # Genuinely different releases that differ only by a real
        # parenthetical subtitle must NEVER fuse (the catch-all bracket
        # strip in _normalize_album_for_match would fuse these — the
        # grouping key deliberately does not).
        ("Greatest Hits", "Greatest Hits (Live)"),
        ("Album", "Album - Live in Berlin"),
        ("Infinito", "Infinito (Acoustic)"),  # acoustic is not an edition qualifier
        ("Nevermind", "Nevermind (Demos)"),
    ],
)
def test_non_edition_parentheticals_do_not_group(a, b):
    assert edition_group_key(a) != edition_group_key(b)


def test_wholly_qualifier_title_falls_back_to_plain_name():
    # An album literally named "Deluxe" must not fuse with every other
    # qualifier-named release on an empty key.
    assert edition_group_key("Deluxe") == "deluxe"
    assert edition_group_key("") == ""


def test_unknown_qualifier_stays_conservative():
    # "Legendary" is not a known edition qualifier, so the grouping key
    # deliberately does NOT fuse these (investigation flag #6: the
    # catch-all bracket strip would also fuse genuinely different
    # releases). Ownership still treats them as the same album via the
    # scanner's fuzzy _albums_likely_match — grouping and ownership are
    # separate matchers on purpose.
    assert edition_group_key("Aerosmith (Legendary Expanded Edition)") != edition_group_key("Aerosmith")


# ---------------------------------------------------------------------------
# reduce_edition_group — pick semantics
# ---------------------------------------------------------------------------


def test_all_returns_group_unchanged_and_in_order():
    releases = [_rel(40), _rel(13), _rel(75)]
    assert reduce_edition_group(releases, EDITION_PREFERENCE_ALL) == releases


def test_invalid_preference_falls_back_to_all():
    releases = [_rel(40), _rel(13)]
    assert reduce_edition_group(releases, "bogus") == releases
    assert reduce_edition_group(releases, None) == releases


def test_single_release_group_passes_through():
    releases = [_rel(33)]
    assert reduce_edition_group(releases, EDITION_PREFERENCE_ONE_STANDARD) == releases
    assert reduce_edition_group(releases, EDITION_PREFERENCE_ONE_COMPLETE) == releases


def test_one_standard_picks_fewest_tracks():
    picked = reduce_edition_group(
        [_rel(40), _rel(13), _rel(15)], EDITION_PREFERENCE_ONE_STANDARD
    )
    assert [r["track_count"] for r in picked] == [13]


def test_one_complete_picks_most_tracks_excluding_box_sets():
    # Nevermind: 13-track standard, 40-track deluxe, 70/75-track concert
    # boxes. The 4.5x bound (of the 13-track smallest) keeps the 40-track
    # deluxe and excludes the boxes.
    picked = reduce_edition_group(
        [_rel(13), _rel(40), _rel(70), _rel(75)],
        EDITION_PREFERENCE_ONE_COMPLETE,
    )
    assert [r["track_count"] for r in picked] == [40]


@pytest.mark.parametrize(
    "counts,expected",
    [
        ([12, 43, 60, 72], 43),  # In Utero: 60/72-track boxes excluded
        ([8, 33], 33),           # Aerosmith: 33 is 4.1x — kept, not a box
        ([10, 15], 15),          # Cracker Island
    ],
)
def test_one_complete_box_set_bound_cases(counts, expected):
    picked = reduce_edition_group(
        [_rel(n) for n in counts], EDITION_PREFERENCE_ONE_COMPLETE
    )
    assert [r["track_count"] for r in picked] == [expected]


def test_one_complete_2x_would_degenerate_so_bound_is_4_5x():
    # Regression pin for the spec deviation: with a 2x bound, Nevermind's
    # 40-track deluxe (2x of 13 = 26) would be excluded and one_complete
    # would pick the 13-track standard — identical to one_standard.
    picked = reduce_edition_group(
        [_rel(13), _rel(40)], EDITION_PREFERENCE_ONE_COMPLETE
    )
    assert [r["track_count"] for r in picked] == [40]


def test_explicit_tie_break_prefers_explicit_edition():
    releases = [_rel(10, explicit=False), _rel(10, explicit=True)]
    picked = reduce_edition_group(
        releases, EDITION_PREFERENCE_ONE_STANDARD, prefer_explicit=True
    )
    assert picked[0]["explicit"] is True


def test_explicit_tie_break_disabled_keeps_provider_order():
    releases = [_rel(10, explicit=False), _rel(10, explicit=True)]
    picked = reduce_edition_group(
        releases, EDITION_PREFERENCE_ONE_STANDARD, prefer_explicit=False
    )
    assert picked[0]["explicit"] is False  # first-listed wins


def test_explicit_tie_break_applies_to_one_complete():
    releases = [_rel(15, explicit=False), _rel(15, explicit=True)]
    picked = reduce_edition_group(
        releases, EDITION_PREFERENCE_ONE_COMPLETE, prefer_explicit=True
    )
    assert picked[0]["explicit"] is True


def test_track_count_read_from_alternate_keys():
    releases = [
        {"name": "a", "total_tracks": 20},
        {"name": "b", "tracks": 9},
    ]
    picked = reduce_edition_group(releases, EDITION_PREFERENCE_ONE_STANDARD)
    assert picked[0]["name"] == "b"


# ---------------------------------------------------------------------------
# scanner-side helpers
# ---------------------------------------------------------------------------


def test_resolve_edition_preference_validation():
    assert _resolve_edition_preference("one_standard") == EDITION_PREFERENCE_ONE_STANDARD
    assert _resolve_edition_preference("one_complete") == EDITION_PREFERENCE_ONE_COMPLETE
    assert _resolve_edition_preference("all") == EDITION_PREFERENCE_ALL
    # Anything else — including None-with-config-default — falls back to all.
    assert _resolve_edition_preference("bogus") == EDITION_PREFERENCE_ALL
    assert _resolve_edition_preference(42) == EDITION_PREFERENCE_ALL
    assert _resolve_edition_preference(None) == EDITION_PREFERENCE_ALL  # stubbed config


def test_group_albums_by_edition_preserves_provider_order():
    albums = [
        types.SimpleNamespace(id="a", name="Infinito"),
        types.SimpleNamespace(id="b", name="Other Album"),
        types.SimpleNamespace(id="c", name="Infinito (Deluxe)"),
        types.SimpleNamespace(id="d", name="Greatest Hits"),
        types.SimpleNamespace(id="e", name="Greatest Hits (Live)"),
        types.SimpleNamespace(id="f", name=""),
    ]
    groups = _group_albums_by_edition(albums)
    assert [[a.id for a in g] for g in groups] == [
        ["a", "c"],  # editions grouped, provider order kept
        ["b"],
        ["d"],       # live release NOT fused with the hits comp
        ["e"],
        ["f"],       # unnamed album never fused
    ]


def test_edition_preference_values_contract():
    assert set(EDITION_PREFERENCE_VALUES) == {"all", "one_standard", "one_complete"}
