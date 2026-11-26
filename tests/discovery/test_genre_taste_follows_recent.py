"""the genre taste the personalized playlists draw by leans on this month,
not only on a lifetime of plays"""

from core.discovery.listening_recommendations import build_genre_taste_profile
from core.personalized_playlists import recency_weighted_genres

GENRES = {"metallica": ["metal"], "megadeth": ["metal"], "kavinsky": ["synthwave"]}
EVER = [{"name": "Metallica", "play_count": 5000}, {"name": "Megadeth", "play_count": 3000},
        {"name": "Kavinsky", "play_count": 40}]
MONTH = [{"name": "Kavinsky", "play_count": 120}, {"name": "Metallica", "play_count": 10}]


def test_a_genre_you_moved_into_this_month_leads():
    taste = build_genre_taste_profile(recency_weighted_genres(EVER, MONTH, GENRES))
    assert taste["synthwave"] > taste["metal"]


def test_lifetime_alone_when_nothing_was_played_lately():
    taste = build_genre_taste_profile(recency_weighted_genres(EVER, [], GENRES))
    assert taste["metal"] > taste["synthwave"]


def test_nothing_played_is_no_taste():
    assert recency_weighted_genres([], [], GENRES) == []
