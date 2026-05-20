"""Tests for the canonical release-type mapper.

Covers issue #650 — MusicBrainz's `Other` and `Broadcast` primary
types previously defaulted to `album_type='album'`, hiding music
videos and one-off releases from artist discography views. The mapper
now routes them to `single` so they land in the Singles bucket of the
artist detail page.

Also pins the existing mappings (album/ep/single/compilation) so the
refactor of three sibling type-mappers into one shared helper doesn't
drift the historical behaviour.
"""

from __future__ import annotations

import pytest

from core.metadata.release_type import map_release_group_type


# ---------------------------------------------------------------------------
# Pin existing primary-type mappings (no regression from refactor)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("primary_type,expected", [
    ("album", "album"),
    ("Album", "album"),       # MB returns title-cased values
    ("ALBUM", "album"),
    ("single", "single"),
    ("Single", "single"),
    ("ep", "ep"),
    ("EP", "ep"),
    ("compilation", "compilation"),
    ("Compilation", "compilation"),
])
def test_known_primary_types_map_canonically(primary_type, expected):
    """Pin: case-insensitive primary-type mapping for the four
    canonical types every consumer relied on pre-refactor."""
    assert map_release_group_type(primary_type) == expected


# ---------------------------------------------------------------------------
# Issue #650 — 'Other' and 'Broadcast' primary types
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("primary_type", ["other", "Other", "OTHER"])
def test_other_primary_type_routes_to_singles(primary_type):
    """Issue #650: MB tags music videos and one-off web releases with
    `primary-type=Other`. They're functionally single-track releases,
    so route them to `single` (lands in Singles section). Pre-fix
    they fell through to the `album` default — placed in Albums view
    where they cluttered the LP list AND, paired with the API filter,
    were sometimes dropped from the discography entirely."""
    assert map_release_group_type(primary_type) == "single"


@pytest.mark.parametrize("primary_type", ["broadcast", "Broadcast"])
def test_broadcast_primary_type_routes_to_singles(primary_type):
    """Broadcasts (radio sessions, one-off live single transmissions)
    are also single-track in practice. Same routing as 'Other'."""
    assert map_release_group_type(primary_type) == "single"


# ---------------------------------------------------------------------------
# Secondary-type compilation handling
# ---------------------------------------------------------------------------


def test_compilation_secondary_type_overrides_album_primary():
    """MB's canonical compilation pattern is `primary=Album,
    secondary=[Compilation]`. The compilation secondary check must
    fire even when the primary is Album, so 'Greatest Hits' style
    releases land in the compilation bucket."""
    assert map_release_group_type("Album", ["Compilation"]) == "compilation"


def test_compilation_secondary_type_case_insensitive():
    """Secondary-type matching tolerates case + whitespace variations
    in the provider response."""
    assert map_release_group_type("Album", ["compilation"]) == "compilation"
    assert map_release_group_type("Album", ["  Compilation  "]) == "compilation"


def test_other_secondary_types_do_not_override_primary():
    """Only 'compilation' is checked as a secondary-type override.
    Other MB secondary types (Live, Remix, Soundtrack, etc.) belong
    to the discography filter at the search-adapter layer, not the
    type mapper."""
    assert map_release_group_type("Album", ["Live"]) == "album"
    assert map_release_group_type("Single", ["Remix"]) == "single"


def test_compilation_secondary_overrides_other_primary():
    """An 'Other' release tagged as Compilation lands in compilation,
    not singles — secondary-type compilation is the strongest
    classification signal."""
    assert map_release_group_type("Other", ["Compilation"]) == "compilation"


# ---------------------------------------------------------------------------
# Empty / unknown / defensive
# ---------------------------------------------------------------------------


def test_empty_primary_type_defaults_to_album():
    """Pin: empty / None primary-type still defaults to 'album' so
    consumers that build records without complete provider data don't
    suddenly land in a different bucket."""
    assert map_release_group_type("") == "album"
    assert map_release_group_type(None) == "album"


def test_unknown_primary_type_defaults_to_album():
    """Pin: a primary-type value we don't know about defaults to
    'album'. Matches the pre-refactor fall-through so new MB
    vocabulary doesn't accidentally cause a behaviour shift."""
    assert map_release_group_type("audiobook") == "album"
    assert map_release_group_type("video") == "album"


def test_secondary_types_none_is_safe():
    """Pin: omitting secondary_types (legacy types.py call site) still
    works — None and missing-arg both treated as no-secondary-types."""
    assert map_release_group_type("Album", None) == "album"
    assert map_release_group_type("Album") == "album"


def test_secondary_types_with_none_entries_skipped():
    """Defensive: provider responses occasionally include None or empty
    string in the secondary-types list. The mapper must not crash."""
    assert map_release_group_type("Album", [None, "", "Compilation"]) == "compilation"
    assert map_release_group_type("Album", [None, ""]) == "album"


def test_whitespace_in_primary_type_normalized():
    """Defensive: a stray-whitespace primary-type still classifies."""
    assert map_release_group_type("  single  ") == "single"
    assert map_release_group_type("  Other  ") == "single"
