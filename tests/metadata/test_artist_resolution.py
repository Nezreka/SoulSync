from core.metadata.artist_resolution import resolve_track_artists


def test_prefers_original_search_artists_when_populated():
    original = {"artists": [{"name": "Kendrick Lamar"}, {"name": "Rihanna"}]}
    track = {"artists": [{"name": "Should Not Be Used"}]}
    artist = {"name": "Primary"}

    assert resolve_track_artists(original, track, artist) == ["Kendrick Lamar", "Rihanna"]


def test_falls_back_to_track_info_artists_when_original_lacks_list():
    # Soulseek context shape: original_search_result has 'artist' (string)
    # and no 'artists' (list). Full Spotify track object lives on track_info.
    original = {"artist": "Kendrick Lamar"}
    track = {"artists": [{"name": "Kendrick Lamar"}, {"name": "Rihanna"}]}
    artist = {"name": "Kendrick Lamar"}

    assert resolve_track_artists(original, track, artist) == ["Kendrick Lamar", "Rihanna"]


def test_falls_back_to_artist_dict_name_when_no_lists_available():
    original = {"artist": "Solo Artist"}
    track = {"name": "Track Title"}
    artist = {"name": "Solo Artist"}

    assert resolve_track_artists(original, track, artist) == ["Solo Artist"]


def test_returns_empty_list_when_everything_missing():
    assert resolve_track_artists(None, None, None) == []
    assert resolve_track_artists({}, {}, {}) == []


def test_handles_bare_string_artist_items():
    original = {"artists": ["Kendrick Lamar", "Rihanna"]}
    assert resolve_track_artists(original, None, None) == ["Kendrick Lamar", "Rihanna"]


def test_mixed_dict_and_string_items_normalized():
    original = {"artists": [{"name": "Kendrick Lamar"}, "Rihanna"]}
    assert resolve_track_artists(original, None, None) == ["Kendrick Lamar", "Rihanna"]


def test_strips_whitespace_and_drops_empty_entries():
    original = {"artists": [{"name": "  Kendrick  "}, {"name": ""}, "  ", "Rihanna"]}
    assert resolve_track_artists(original, None, None) == ["Kendrick", "Rihanna"]


def test_dict_item_without_name_key_skipped():
    original = {"artists": [{"id": "abc"}, {"name": "Rihanna"}]}
    assert resolve_track_artists(original, None, None) == ["Rihanna"]


def test_non_list_artists_value_falls_through():
    original = {"artists": "Kendrick Lamar"}  # string, not list
    track = {"artists": [{"name": "Kendrick Lamar"}, {"name": "Rihanna"}]}
    assert resolve_track_artists(original, track, None) == ["Kendrick Lamar", "Rihanna"]


def test_empty_original_artists_list_falls_through_to_track_info():
    original = {"artists": []}
    track = {"artists": [{"name": "Kendrick Lamar"}, {"name": "Rihanna"}]}
    assert resolve_track_artists(original, track, None) == ["Kendrick Lamar", "Rihanna"]


def test_artist_dict_name_blank_returns_empty():
    assert resolve_track_artists({}, {}, {"name": "   "}) == []
    assert resolve_track_artists({}, {}, {"name": ""}) == []


def test_non_string_artist_items_coerced_to_string():
    original = {"artists": [123, {"name": "Real Artist"}]}
    assert resolve_track_artists(original, None, None) == ["123", "Real Artist"]


def test_none_artist_items_dropped():
    original = {"artists": [None, {"name": "Real Artist"}, None]}
    assert resolve_track_artists(original, None, None) == ["Real Artist"]
