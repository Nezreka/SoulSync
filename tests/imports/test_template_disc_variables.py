"""Disc variables must behave the same in a FOLDER segment as in the filename.

The settings page documents exactly one filename-only variable, ``$quality``.
But the per-segment cleanup also stripped ``$disc`` and ``$discnum`` out of
folder parts, while ``$cdnum`` and the ``${...}`` bracket forms were substituted
globally and therefore did work. The result was three different behaviours for
one family of variables, none of them documented:

    $albumartist/$album/$disc/$track - $title        -> the folder vanished
    $albumartist/$album/Disc $discnum/$track - $title-> a folder literally "Disc"
    $albumartist/$album/$cdnum/$track - $title       -> "CD01"          (worked)
    $albumartist/$album/${discnum}/$track - $title   -> "1"             (worked)

"a folder literally named Disc" is the worst of them: every disc of the album
collides in one folder, silently.
"""

from __future__ import annotations

import core.imports.paths as import_paths


def _ctx(disc_number=2, total_discs=2):
    return {
        "albumartist": "Sawano Hiroyuki", "artist": "Sawano Hiroyuki",
        "album": "AoT OST", "title": "Apetitan",
        "track_number": 3, "disc_number": disc_number, "total_discs": total_discs,
        "year": "2017", "quality": "FLAC",
    }


def _folder(template, **kw):
    return import_paths.get_file_path_from_template_raw(template, _ctx(**kw))[0]


def _filename(template, **kw):
    return import_paths.get_file_path_from_template_raw(template, _ctx(**kw))[1]


# ── multi-disc: the folder segment gets the value ────────────────────────────

def test_bare_disc_works_in_a_folder_segment():
    assert _folder("$albumartist/$album/$disc/$track - $title") \
        == "Sawano Hiroyuki/AoT OST/02"


def test_bare_discnum_works_in_a_folder_segment():
    assert _folder("$albumartist/$album/Disc $discnum/$track - $title") \
        == "Sawano Hiroyuki/AoT OST/Disc 2"


def test_cdnum_is_unchanged():
    assert _folder("$albumartist/$album/$cdnum/$track - $title") \
        == "Sawano Hiroyuki/AoT OST/CD02"


def test_bracket_forms_are_unchanged():
    assert _folder("$albumartist/$album/${discnum}/$track - $title") \
        == "Sawano Hiroyuki/AoT OST/2"


# ── single disc: the segment disappears, exactly like $cdnum already did ─────

def test_disc_folders_vanish_on_a_single_disc_album():
    for template in ("$albumartist/$album/$disc/$track - $title",
                     "$albumartist/$album/$cdnum/$track - $title"):
        assert _folder(template, disc_number=1, total_discs=1) \
            == "Sawano Hiroyuki/AoT OST", template


def test_a_label_left_alone_by_an_empty_disc_number_is_dropped_whole():
    """"Disc $discnum" with nothing to fill in must not leave a folder called
    "Disc" that every disc of the album then shares."""
    assert _folder("$albumartist/$album/Disc $discnum/$track - $title",
                   disc_number=1, total_discs=1) == "Sawano Hiroyuki/AoT OST"


# ── the filename side keeps working ─────────────────────────────────────────

def test_the_filename_still_substitutes_disc():
    assert _filename("$albumartist/$album/$disc-$track - $title") == "02-03 - Apetitan"
    assert _filename("$albumartist/$album/$disc-$track - $title",
                     disc_number=1, total_discs=1) == "03 - Apetitan"


def test_quality_stays_filename_only():
    """The one variable the settings page really does document as filename-only."""
    assert _folder("$albumartist/$quality/$album/$track - $title") \
        == "Sawano Hiroyuki/AoT OST"
    assert _filename("$albumartist/$album/$track - $title [$quality]") \
        == "03 - Apetitan [FLAC]"


# ── every spelling of a disc variable, not just the bare ones ────────────────
#
# `${disc}`, `${discnum}` and `$cdnum` are substituted globally, before the path
# is split into segments, so by the time the segment cleanup looked for "$disc"
# they were already gone and the dangling-label drop never fired. A label next
# to one of those on a single-disc album was left standing:
#
#     $artist/$album/Disc ${discnum}/...  ->  A/B/Disc
#     $artist/$album/CD $cdnum/...        ->  A/B/CD
#
# which is the same collapse-every-disc-into-one-folder problem the bare form
# had, and the settings page now tells users all three forms work in a folder.

def test_a_bracket_form_label_is_dropped_on_a_single_disc_album():
    assert _folder("$albumartist/$album/Disc ${discnum}/$track - $title",
                   disc_number=1, total_discs=1) == "Sawano Hiroyuki/AoT OST"


def test_a_cdnum_label_is_dropped_on_a_single_disc_album():
    assert _folder("$albumartist/$album/CD $cdnum/$track - $title",
                   disc_number=1, total_discs=1) == "Sawano Hiroyuki/AoT OST"


def test_those_forms_still_render_on_a_multi_disc_album():
    assert _folder("$albumartist/$album/Disc ${discnum}/$track - $title") \
        == "Sawano Hiroyuki/AoT OST/Disc 2"
    assert _folder("$albumartist/$album/CD $cdnum/$track - $title") \
        == "Sawano Hiroyuki/AoT OST/CD CD02"


def test_a_template_without_any_disc_variable_keeps_a_literal_label():
    """The drop is scoped to templates that actually use a disc variable, so a
    folder someone deliberately called "CD" is never swallowed."""
    assert _folder("$albumartist/CD/$album/$track - $title",
                   disc_number=1, total_discs=1) == "Sawano Hiroyuki/CD/AoT OST"
