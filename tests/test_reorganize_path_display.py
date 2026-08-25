"""Both path columns of the reorganize preview are trimmed to the library root.

The trim was a raw ``startswith`` on two strings that were produced by different
code paths: the proposed path came from the path builder (rooted at the config
value) while the current path came from the resolver (absolute, symlinks
resolved). With a relative root configured the two never had a common prefix, so
"New path" showed a tidy relative path and "Current path" fell back to the raw
stored value — the same file, displayed two different ways, in adjacent columns.

A raw prefix compare is also wrong on its own terms: ``/music/Transfer2`` starts
with ``/music/Transfer``.
"""

import os

from core.library_reorganize import _display_relative_to_root, _trim_to_transfer


def test_a_path_inside_the_root_is_shown_relative():
    assert _display_relative_to_root(
        "/app/Transfer/Sawano/Album/02 - Apetitan.flac", "/app/Transfer"
    ) == "Sawano/Album/02 - Apetitan.flac"


def test_a_trailing_separator_on_the_root_is_tolerated():
    assert _display_relative_to_root(
        "/app/Transfer/Sawano/x.flac", "/app/Transfer/"
    ) == "Sawano/x.flac"


def test_a_sibling_root_is_not_treated_as_inside():
    """`/app/Transfer2` merely starts with `/app/Transfer` — it is not inside it."""
    assert _display_relative_to_root(
        "/app/Transfer2/Sawano/x.flac", "/app/Transfer"
    ) == "/app/Transfer2/Sawano/x.flac"


def test_a_path_outside_the_root_is_returned_whole():
    assert _display_relative_to_root(
        "/mnt/other/x.flac", "/app/Transfer"
    ) == "/mnt/other/x.flac"


def test_no_root_configured_returns_the_path_unchanged():
    assert _display_relative_to_root("/app/Transfer/x.flac", "") == "/app/Transfer/x.flac"
    assert _display_relative_to_root("", "/app/Transfer") == ""


def test_the_current_path_column_uses_the_same_trim():
    """`_trim_to_transfer` displayed the raw DB value whenever the resolved path
    did not literally start with the configured root."""
    assert _trim_to_transfer(
        "./Transfer/Sawano/x.flac",              # what the catalogue stored
        "/app/Transfer/Sawano/x.flac",           # what the resolver returned
        "/app/Transfer",
    ) == "Sawano/x.flac"


def test_the_current_path_column_falls_back_to_the_stored_value():
    assert _trim_to_transfer("/music/Sawano/x.flac", None, "/app/Transfer") \
        == "/music/Sawano/x.flac"
    assert _trim_to_transfer(None, None, "/app/Transfer") == "No file"
