"""the new-music pool keeps room for artists similar to what you play,
not only for watchlist and library artists"""

from database.music_database import MusicDatabase

fav = MusicDatabase.favor_played


def test_played_side_gets_two_thirds_in_rotation_order():
    items = list("abcdefghij")
    flags = [False, False, True, False, True, True, False, True, True, False]
    got = fav(items, flags, 6)
    # four from played (c e f h), two from the rest (a b), back in rotation order
    assert got == ["a", "b", "c", "e", "f", "h"]


def test_a_short_side_leaves_its_room_to_the_other():
    items = list("abcdef")
    assert fav(items, [True, False, False, False, False, False], 3) == ["a", "b", "c"]
    assert fav(items, [True, True, True, True, True, False], 3) == ["a", "b", "f"]
    assert fav(items, [True] * 6, 3) == ["a", "b", "c"]
    assert fav(items, [False] * 6, 3) == ["a", "b", "c"]
    assert fav([], [], 5) == []
