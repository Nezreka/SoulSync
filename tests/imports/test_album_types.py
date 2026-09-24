"""beets-compatible ``$atypes`` release-type labels.

The point of the variable is what it does NOT emit: a plain album gets no
marker, so ``[$year]$atypes $album`` produces ``[2019] Tokyo`` and not
``[2019][Album] Tokyo``. That is the difference between reproducing a
beets-organised library and drifting into a second convention beside it.
"""

from __future__ import annotations

import core.imports.paths as paths
from core.imports.album_types import (
    DEFAULT_TYPES,
    format_album_types,
    normalize_types,
    release_types,
)


BEETS_CONFIG = {
    "types": {
        "ep": "EP",
        "single": "Single",
        "soundtrack": "OST",
        "live": "Live",
        "compilation": "Anthology",
        "remix": "Remix",
    },
    "bracket": "[]",
    "ignore_va": ["compilation"],
}


# --- the emission rule --------------------------------------------------

def test_a_plain_album_emits_nothing():
    assert format_album_types({"album_type": "album"}, BEETS_CONFIG) == ""


def test_an_unknown_type_emits_nothing():
    assert format_album_types({"album_type": "broadcast"}, BEETS_CONFIG) == ""


def test_no_album_context_emits_nothing():
    assert format_album_types(None, BEETS_CONFIG) == ""
    assert format_album_types({}, BEETS_CONFIG) == ""


def test_a_single_qualifier_is_bracketed():
    assert format_album_types({"album_type": "ep"}, BEETS_CONFIG) == "[EP]"


def test_secondary_types_count_too():
    """MusicBrainz puts Live/Compilation/Soundtrack in secondary-types, which is
    where most qualifiers actually live."""
    ctx = {"album_type": "album", "secondary_types": ["Live"]}
    assert format_album_types(ctx, BEETS_CONFIG) == "[Live]"


def test_multiple_qualifiers_concatenate_in_configured_order():
    """A live EP is both things. Order follows the CONFIG, not the source, so
    the same release always produces the same folder name."""
    ctx = {"album_type": "ep", "secondary_types": ["Live"]}
    assert format_album_types(ctx, BEETS_CONFIG) == "[EP][Live]"
    # the source listing them the other way round must not change the output
    ctx_reversed = {"album_type": "EP", "secondary_types": ["live"]}
    assert format_album_types(ctx_reversed, BEETS_CONFIG) == "[EP][Live]"


def test_real_library_shapes():
    """Folder names taken from a library beets organised, reproduced exactly."""
    cases = [
        ({"album_type": "ep", "secondary_types": ["Live"]}, "[EP][Live]"),
        ({"album_type": "album", "secondary_types": ["Live", "Compilation"]}, "[Live][Anthology]"),
        ({"album_type": "album", "secondary_types": ["Compilation", "Remix"]}, "[Anthology][Remix]"),
        ({"album_type": "ep", "secondary_types": ["Remix"]}, "[EP][Remix]"),
        ({"album_type": "single"}, "[Single]"),
        ({"album_type": "album", "secondary_types": ["Soundtrack"]}, "[OST]"),
    ]
    for ctx, expected in cases:
        assert format_album_types(ctx, BEETS_CONFIG) == expected, ctx


def test_ignore_va_drops_the_compilation_marker_for_various_artists():
    """A VA compilation already lives under Compilations/; repeating it is noise.
    The same release NOT flagged as VA keeps its marker."""
    ctx = {"album_type": "album", "secondary_types": ["Compilation"]}
    assert format_album_types(ctx, BEETS_CONFIG, is_compilation=True) == ""
    assert format_album_types(ctx, BEETS_CONFIG, is_compilation=False) == "[Anthology]"


def test_ignore_va_only_drops_the_listed_types():
    ctx = {"album_type": "album", "secondary_types": ["Live", "Compilation"]}
    assert format_album_types(ctx, BEETS_CONFIG, is_compilation=True) == "[Live]"


# --- config shapes ------------------------------------------------------

def test_beets_list_of_single_key_maps_is_accepted_verbatim():
    """So a beets config block can be pasted across without translation."""
    cfg = dict(BEETS_CONFIG, types=[{"ep": "EP"}, {"live": "Live"}])
    assert format_album_types({"album_type": "ep", "secondary_types": ["Live"]}, cfg) == "[EP][Live]"


def test_custom_bracket_and_no_bracket():
    ctx = {"album_type": "ep"}
    assert format_album_types(ctx, dict(BEETS_CONFIG, bracket="()")) == "(EP)"
    assert format_album_types(ctx, dict(BEETS_CONFIG, bracket="")) == "EP"
    assert format_album_types(ctx, dict(BEETS_CONFIG, bracket="_")) == "_EP_"


def test_empty_types_disables_the_variable():
    assert format_album_types({"album_type": "ep"}, dict(BEETS_CONFIG, types={})) == ""


def test_absent_config_falls_back_to_the_documented_defaults():
    assert normalize_types(None) == list(DEFAULT_TYPES)
    assert format_album_types({"album_type": "ep"}, {}) == "[EP]"


def test_malformed_config_does_not_raise():
    for bad in ("nonsense", 42, {"types": "nope"}, {"types": [1, 2]}):
        cfg = bad if isinstance(bad, dict) else {"types": bad}
        assert isinstance(format_album_types({"album_type": "ep"}, cfg), str)


def test_release_types_reads_every_key_a_source_might_use():
    assert release_types({"primary_type": "EP"}) == {"ep"}
    assert release_types({"record_type": "Single"}) == {"single"}
    assert release_types({"album_type": "album", "secondary_type": "Live"}) == {"album", "live"}
    assert release_types(None) == set()


# --- the template itself ------------------------------------------------

def _render(template, ctx):
    return paths._replace_template_variables(template, ctx)


def test_atypes_is_substituted_before_album():
    """"$atypes" starts with "$album", so a naive left-to-right replace turns it
    into the album name followed by a stray "type s"."""
    out = _render("$albumartist/[$year]$atypes $album", {
        "artist": "Slothrust", "album": "Audiotree Live", "year": "2017",
        "atypes": "[EP][Live]", "title": "t",
    })
    assert out == "Slothrust/[2017][EP][Live] Audiotree Live"


def test_braced_form_works_too():
    out = _render("${atypes}${album}", {"artist": "A", "album": "X", "atypes": "[Live]", "title": "t"})
    assert out == "[Live]X"


def test_a_plain_album_leaves_no_double_space():
    """With $atypes empty, "[$year]$atypes $album" must not render
    "[2019]  Tokyo" — beets produces exactly one space."""
    out = _render("$albumartist/[$year]$atypes $album", {
        "artist": "Julien Baker", "album": "Tokyo", "year": "2019",
        "atypes": "", "title": "t",
    })
    assert out == "Julien Baker/[2019] Tokyo"


def test_missing_atypes_key_renders_empty_not_literal():
    out = _render("[$year]$atypes $album", {"artist": "A", "album": "X", "year": "2020", "title": "t"})
    assert "$atypes" not in out


def test_an_empty_year_collapses_but_the_type_labels_survive():
    """"[$year]$atypes $album" on a release with no year must drop the empty
    brackets and keep the real ones — the segment cleaner strips "[]" by regex,
    and a greedier rule would eat "[EP]" with it."""
    part = paths._clean_folder_segment("[][EP][Live] Audiotree Live", "", "", False)
    assert part == "[EP][Live] Audiotree Live"


def test_an_empty_year_and_no_types_leaves_just_the_album():
    assert paths._clean_folder_segment("[] Schmilco", "", "", False) == "Schmilco"


def test_a_leaked_atypes_token_never_reaches_a_directory_name():
    """Defensive: the global pass already substituted it, but a raw token in a
    folder name is the one failure worth a spare replace."""
    assert paths._clean_folder_segment("$atypes Album", "", "", False) == "Album"
