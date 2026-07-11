"""Server Playlists compare view — #1005 string contracts.

Pins the three fixes: the active filter pill is re-applied after every column
render (pill said Missing, rows showed all); a single Find & Add / remove
patches the pair in place instead of re-fetching + re-matching a 2k-track
playlist; and the in-place repaint keeps the scroll position."""

from __future__ import annotations

from pathlib import Path

_JS = (Path(__file__).resolve().parent.parent
       / "webui" / "static" / "pages-extra.js").read_text(encoding="utf-8")


def test_filter_is_reapplied_after_every_render():
    assert "function _applyServerEditorFilter(" in _JS
    assert "function _activeServerEditorFilter(" in _JS
    # the full-load path re-applies the active pill right after rendering
    load_block = _JS.split("function _openServerCompareView")[1].split("function _updateCompareStats")[0]
    assert "_renderCompareColumns(tracks);" in load_block
    assert "_applyServerEditorFilter(_activeServerEditorFilter())" in load_block


def test_find_and_add_patches_the_row_in_place():
    block = _JS.split("async function _serverSelectTrack")[1].split("async function _serverRemoveTrack")[0]
    assert "_serverEditorState._searchResults" in _JS      # picked track kept for the patch
    assert "track.match_status = 'matched'" in block
    assert "track.override = true" in block
    assert "_rerenderCompare()" in block
    # full reload survives only as the couldn't-identify fallback
    assert block.count("_openServerCompareView(") == 1


def test_remove_patches_the_row_in_place():
    block = _JS.split("async function _serverRemoveTrack")[1].split("\n}\n")[0]
    assert "track.match_status = 'missing'" in block
    assert ".splice(trackIndex, 1)" in block               # extra rows just disappear
    assert "_rerenderCompare()" in block
    assert "_openServerCompareView(" not in block


def test_rerender_keeps_scroll_and_filter():
    block = _JS.split("function _rerenderCompare")[1].split("\n}\n")[0]
    assert "scrollTop" in block
    assert "_applyServerEditorFilter(_activeServerEditorFilter())" in block
