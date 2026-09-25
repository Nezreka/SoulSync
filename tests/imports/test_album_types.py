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


def test_ignore_va_drops_the_marker_for_a_various_artists_credit():
    """A VA compilation already lives under Compilations/; repeating it is noise.
    The same release NOT flagged as VA keeps its marker."""
    ctx = {"album_type": "album", "secondary_types": ["Compilation"]}
    assert format_album_types(ctx, BEETS_CONFIG, is_various_artists=True) == ""
    assert format_album_types(ctx, BEETS_CONFIG, is_various_artists=False) == "[Anthology]"


def test_ignore_va_only_drops_the_listed_types():
    ctx = {"album_type": "album", "secondary_types": ["Live", "Compilation"]}
    assert format_album_types(ctx, BEETS_CONFIG, is_various_artists=True) == "[Live]"


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


def test_atypes_and_album_do_not_collide():
    """They share only "$a", so neither can consume the other — unlike
    $albumtype, which really does start with $album and has to be replaced
    first. Pinned because a future variable named "$al..." would not be so
    lucky, and this is where that breaks."""
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


# --- review follow-ups (PR #1302) ---------------------------------------

def test_a_single_artist_anthology_keeps_its_compilation_label():
    """MusicBrainz gives Tool's Salival primary=Album, secondary=[Live,
    Compilation], and map_release_group_type turns that into
    album_type="compilation" — so keying ignore_va off the TYPE dropped
    [Anthology] from a release that is not various artists at all.

    Built from the real mapper's output, because the hand-written
    album_type="album" contexts elsewhere in this file are a shape the
    pipeline never actually produces."""
    from core.metadata.release_type import map_release_group_type

    album_type = map_release_group_type("Album", ["Live", "Compilation"])
    assert album_type == "compilation", "guards the premise, not the fix"

    salival = {"album_type": album_type, "secondary_types": ["Live", "Compilation"],
               "artists": [{"name": "Tool"}]}
    assert format_album_types(salival, BEETS_CONFIG, is_various_artists=False) == "[Live][Anthology]"


def test_a_various_artists_compilation_still_drops_the_label():
    """The rule still fires where it should: a release actually credited to
    Various Artists is already under Compilations/."""
    from core.metadata.release_type import map_release_group_type

    va = {"album_type": map_release_group_type("Album", ["Compilation"]),
          "secondary_types": ["Compilation"],
          "artists": [{"name": "Various Artists"}]}
    assert format_album_types(va, BEETS_CONFIG, is_various_artists=True) == ""


def test_the_va_signal_reads_the_credit_not_the_type():
    from core.imports.compilation import is_various_artists_credit
    from core.metadata.release_type import map_release_group_type

    assert is_various_artists_credit({"artists": [{"name": "Various Artists"}]}) is True
    assert is_various_artists_credit({"artists": [{"name": "Tool"}]}) is False
    # typed a compilation, credited to one artist -> not VA
    assert is_various_artists_credit(
        {"album_type": map_release_group_type("Album", ["Compilation"]),
         "artists": [{"name": "Tool"}]}) is False


def test_the_m3u_folder_uses_the_same_substitution():
    """web_server keeps a hand-maintained copy of the template replacer, so a
    new variable has to be added there too or the M3U lands in a directory
    literally named "[2019]$atypes Tokyo"."""
    import web_server

    ctx = {"artist": "Julien Baker", "albumartist": "Julien Baker", "album": "Tokyo",
           "title": "t", "track_number": 1, "disc_number": 1, "year": "2019", "quality": ""}
    tmpl = "$albumartist/[$year]$atypes $album"
    assert web_server._apply_path_template(tmpl, ctx) == "Julien Baker/[2019] Tokyo"
    assert web_server._apply_path_template(
        tmpl, dict(ctx, atypes="[EP][Live]", album="Audiotree Live", year="2017")
    ) == "Julien Baker/[2017][EP][Live] Audiotree Live"


def test_the_m3u_folder_follows_the_audio_not_the_template(tmp_path, monkeypatch):
    """_compute_m3u_folder only receives artist/album/year from its HTTP
    callers, so a template using $atypes renders it empty and the M3U would
    land in "[2017] Audiotree Live" while the audio sits in
    "[2017][EP][Live] Audiotree Live" — and os.makedirs would create the empty
    one. Resolving from a real track path cannot drift from the template."""
    import web_server

    album = tmp_path / "Slothrust" / "[2017][EP][Live] Audiotree Live"
    album.mkdir(parents=True)
    track = album / "01 - Horseshoe Crab.flac"
    track.write_bytes(b"AUDIO")

    monkeypatch.setattr(web_server, "_album_folder_from_track_path",
                        lambda p: str(album) if p else None)

    assert web_server._compute_m3u_folder(
        str(tmp_path), "album", "", "Slothrust", "Audiotree Live", "2017",
        sample_track_path=str(track)) == str(album)


def test_the_m3u_folder_falls_back_to_the_template_when_unlocatable(tmp_path, monkeypatch):
    """No track path, or one that does not resolve, must not break export."""
    import web_server

    monkeypatch.setattr(web_server, "_album_folder_from_track_path", lambda p: None)
    out = web_server._compute_m3u_folder(
        str(tmp_path), "album", "", "Slothrust", "Audiotree Live", "2017")
    assert out and str(tmp_path) in out


def test_first_m3u_entry_skips_the_directives():
    import web_server

    body = "#EXTM3U\n#EXTINF:210,A - B\n#STATUS:FOUND_IN_LIBRARY\n/music/A/Album/01 - B.flac\n"
    assert web_server._first_m3u_entry(body) == "/music/A/Album/01 - B.flac"
    assert web_server._first_m3u_entry("#EXTM3U\n# NOT AVAILABLE: x\n") is None
    assert web_server._first_m3u_entry("") is None


# --- reorganize parity ---------------------------------------------------

def test_reorganize_reads_a_multi_valued_releasetype_tag():
    """beets and Picard write releasetype as a LIST. The reader used to run
    str() over it, producing "['album', 'compilation', 'live']" — a string
    matching no canonical token — so every such file read as having no type."""
    from core.library.reorganize_tag_source import (
        _normalize_album_type, _secondary_album_types)

    tag = ["album", "compilation", "live"]
    primary = _normalize_album_type(tag)
    assert primary == "album", "MusicBrainz writes the primary FIRST"
    assert _secondary_album_types(tag, primary) == ["compilation", "live"]


def test_a_single_valued_tag_still_reads_as_before():
    from core.library.reorganize_tag_source import _normalize_album_type

    assert _normalize_album_type("compilation") == "compilation"
    assert _normalize_album_type("album") == "album"
    assert _normalize_album_type("nonsense") == ""
    assert _normalize_album_type(None) == ""


def test_slash_and_semicolon_separated_tags_split():
    """Some taggers pack several values into one string."""
    from core.library.reorganize_tag_source import _release_type_tokens

    assert _release_type_tokens("album/compilation") == ["album", "compilation"]
    assert _release_type_tokens("Album; Live") == ["album", "live"]


def test_atypes_is_identical_at_import_and_at_reorganize():
    """The whole point: reorganize is the convergence step, so if it renders
    $atypes differently from import it RENAMES every labelled folder. Salival
    through both routes must produce the same string."""
    from core.library.reorganize_tag_source import (
        _normalize_album_type, _secondary_album_types)

    at_import = {"album_type": "compilation", "secondary_types": ["Live", "Compilation"]}

    tag = ["album", "compilation", "live"]
    primary = _normalize_album_type(tag)
    at_reorganize = {"album_type": primary, "record_type": primary,
                     "secondary_types": _secondary_album_types(tag, primary)}

    assert format_album_types(at_reorganize, BEETS_CONFIG) == "[Live][Anthology]"
    assert format_album_types(at_import, BEETS_CONFIG) == \
        format_album_types(at_reorganize, BEETS_CONFIG)


def test_the_tag_reader_surfaces_secondary_types(tmp_path):
    """End to end through read_album_track_from_file, so the dict the
    reorganize planner consumes really carries the field."""
    from core.library.reorganize_tag_source import read_album_track_from_file

    album_meta, _track_meta, err = read_album_track_from_file(
        str(tmp_path / "x.flac"),
        read_embedded_tags_fn=lambda _p: {"available": True, "duration": 300, "tags": {
            "album": "Salival", "albumartist": "Tool", "artist": "Tool",
            "title": "Third Eye", "tracknumber": "1",
            "releasetype": ["album", "compilation", "live"],
        }})
    assert err is None
    assert album_meta["album_type"] == "album"
    assert album_meta["secondary_types"] == ["compilation", "live"]


def test_reading_the_tag_does_not_change_where_reorganize_files_an_album():
    """The regression this nearly shipped with. Teaching the reader about
    multi-valued tags means reorganize now SEES a type where it used to see
    nothing, and resolved_record_type routes 'compilation' to compilation_path.
    Taking the most specific token would therefore have swept every
    single-artist greatest-hits record into Compilations/ on the next
    reorganize — 38 folders on the library this was written against.

    The primary is the first token because that is the order MusicBrainz
    writes; the qualifiers are the rest.
    """
    from core.library.reorganize_tag_source import _normalize_album_type

    def routes_to(tag_album_type, raw_db_type="album"):
        # mirrors resolved_record_type in core/library_reorganize.py
        if raw_db_type in ("compilation",) or tag_album_type in ("compilation",):
            return "compilation_path"
        return "album_path"

    # a single artist's anthology stays with the artist, as it did before
    assert routes_to(_normalize_album_type(["album", "compilation"])) == "album_path"
    assert routes_to(_normalize_album_type(["album", "compilation", "live"])) == "album_path"
    # a release whose PRIMARY type is compilation still routes as it always did
    assert routes_to(_normalize_album_type("compilation")) == "compilation_path"
    assert routes_to(_normalize_album_type(["compilation"])) == "compilation_path"


def test_the_beets_folders_this_was_built_for_round_trip():
    """Real folder names from a beets-organised library, rebuilt from the tags
    those files actually carry."""
    from core.library.reorganize_tag_source import (
        _normalize_album_type, _secondary_album_types)

    cases = [
        (["album", "compilation", "live"], "[Live][Anthology]"),   # Tool / Salival
        (["album", "compilation"], "[Anthology]"),                 # 3 Doors Down / Greatest Hits
        (["ep", "live"], "[EP][Live]"),                            # Slothrust / Audiotree Live
        (["album"], ""),                                           # a plain album
    ]
    for tag, expected in cases:
        primary = _normalize_album_type(tag)
        ctx = {"album_type": primary, "record_type": primary,
               "secondary_types": _secondary_album_types(tag, primary)}
        assert format_album_types(ctx, BEETS_CONFIG) == expected, tag


def test_the_m3u_folder_refuses_a_path_outside_the_library(tmp_path, monkeypatch):
    """resolve_library_file_path also probes the slskd download folder. A track
    row still pointing there must not drop the playlist among the incoming
    files — fall back to the template instead."""
    import web_server

    library = tmp_path / "library"
    downloads = tmp_path / "downloads" / "Artist - Album"
    downloads.mkdir(parents=True)
    library.mkdir()
    stray = downloads / "01 - Song.flac"
    stray.write_bytes(b"AUDIO")

    monkeypatch.setattr(web_server, "docker_resolve_path", lambda p: str(library))
    monkeypatch.setattr("core.library.path_resolver.resolve_library_file_path",
                        lambda *a, **k: str(stray))

    assert web_server._album_folder_from_track_path(str(stray)) is None
