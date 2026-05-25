"""Tests for `_artist_name` in services/sync_service.py — the helper
that pulls a string name out of Spotify's bare-string / dict / fallback
artist representations."""

from services.sync_service import _artist_name


def test_bare_string_returned_as_is():
    assert _artist_name('Drake') == 'Drake'


def test_dict_with_name_field():
    assert _artist_name({'name': 'Drake', 'id': '3TVXt'}) == 'Drake'


def test_dict_without_name_field_falls_back_to_str_repr():
    """Missing name field shouldn't crash — caller should still get a
    string back, even if it's the awkward dict repr."""
    out = _artist_name({'id': '3TVXt'})
    assert isinstance(out, str)
    assert out != ''


def test_dict_with_non_string_name_falls_back():
    """Defensive — if some endpoint ever returns {name: None} or a list,
    the helper must not propagate the bad type."""
    out = _artist_name({'name': None})
    assert isinstance(out, str)


def test_none_returns_empty_string():
    assert _artist_name(None) == ''


def test_unexpected_type_returns_string_repr():
    """A weird type (int, custom object) must coerce to a string instead
    of raising — sync iterates a lot of inputs and one bad row shouldn't
    crash the whole loop."""
    assert _artist_name(12345) == '12345'


def test_empty_string_stays_empty():
    assert _artist_name('') == ''
