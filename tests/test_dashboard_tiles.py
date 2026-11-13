"""The dashboard's two count tiles show a few of what they count."""

from __future__ import annotations

from types import SimpleNamespace

from core import dashboard_tiles as T


def _artist(name, image):
    return SimpleNamespace(artist_name=name, image_url=image)


def _wish(name, *images, image_url=None):
    album = {"images": [{"url": u} for u in images]}
    if image_url:
        album["image_url"] = image_url
    return {"spotify_data": {"name": name, "album": album}}


class _DB:
    def __init__(self, artists=(), tracks=()):
        self.artists, self.tracks, self.asked = list(artists), list(tracks), []

    def get_watchlist_artists(self, profile_id=1):
        self.asked.append(("watch", profile_id))
        return self.artists

    def get_wishlist_tracks(self, profile_id=1, **_):
        self.asked.append(("wish", profile_id))
        return self.tracks


def test_faces_are_the_newest_watched_artists_that_have_a_picture():
    db = _DB(artists=[
        _artist("Hotel Pools", "https://cdn.example/hp.jpg"),
        _artist("No Picture", None),
        _artist("Server Path", "/library/metadata/9/thumb"),  # not loadable as it stands
        _artist("Own Route", "/api/library/v2/artwork/artist/4"),
        *[_artist(f"More {n}", f"https://cdn.example/{n}.jpg") for n in range(6)],
    ])
    faces = T.watched_faces(db, 3)
    assert db.asked == [("watch", 3)]
    assert [f["name"] for f in faces] == ["Hotel Pools", "Own Route", "More 0", "More 1", "More 2"]
    assert faces[0] == {"name": "Hotel Pools", "image_url": "https://cdn.example/hp.jpg"}


def test_sleeves_are_the_newest_wishes_one_per_album():
    # the wishlist reads oldest first; the tile shows the newest
    db = _DB(tracks=[
        _wish("oldest", "https://cdn.example/a.jpg"),
        _wish("same album, track 2", "https://cdn.example/a.jpg"),
        _wish("relative first", "/api/library/v2/artwork/album/7", "https://cdn.example/b.jpg"),
        _wish("no art"),
        _wish("album field only", image_url="https://cdn.example/c.jpg"),
        {"spotify_data": "not a dict"},
        _wish("newest", "https://cdn.example/d.jpg"),
    ])
    sleeves = T.wished_sleeves(db, 1)
    assert [s["image_url"] for s in sleeves] == [
        "https://cdn.example/d.jpg",
        "https://cdn.example/c.jpg",
        "/api/library/v2/artwork/album/7",
        "https://cdn.example/a.jpg",
    ]
    assert sleeves[0]["name"] == "newest"


def test_five_at_most():
    db = _DB(tracks=[_wish(f"t{n}", f"https://cdn.example/{n}.jpg") for n in range(9)])
    assert len(T.wished_sleeves(db, 1)) == 5


def test_a_failure_is_an_empty_tile_never_an_error():
    class Broken:
        def get_watchlist_artists(self, profile_id=1):
            raise RuntimeError("locked")

        def get_wishlist_tracks(self, profile_id=1, **_):
            raise RuntimeError("locked")

    assert T.watched_faces(Broken(), 1) == []
    assert T.wished_sleeves(Broken(), 1) == []


def _typed_wish(name, artist, album, kind, image=None):
    data = {"name": name, "artists": [{"name": artist}],
            "album": {"name": album, "album_type": kind,
                      "images": [{"url": image}] if image else []}}
    return {"spotify_data": data}


def test_wishlist_facts_say_what_the_count_is_made_of():
    db = _DB(tracks=[
        _typed_wish("a1", "Tame Impala", "Currents", "album"),
        _typed_wish("a2", "Tame Impala", "Currents", "album"),
        _typed_wish("c1", "Various", "Hits", "compilation"),
        _typed_wish("s1", "Caribou", "Odessa", "single"),
        _typed_wish("e1", "Caribou", "Swim EP", "ep"),
        {"spotify_data": "not a dict"},
        _typed_wish("newest", "Four Tet", "Rounds", None, "https://cdn.example/r.jpg"),
    ])
    facts = T.wishlist_facts(db, 2)
    assert db.asked == [("wish", 2)]
    assert facts["kinds"] == {"album": 3, "single": 2, "ep": 1}
    assert facts["albums"] == 5 and facts["artists"] == 4
    assert facts["newest"] == {"name": "newest", "artist": "Four Tet",
                               "image_url": "https://cdn.example/r.jpg"}


def test_facts_are_zeroes_when_the_data_cannot_be_read():
    class Broken:
        def get_wishlist_tracks(self, **_):
            raise RuntimeError("no table")

        def _get_connection(self):
            raise RuntimeError("no db")

    assert T.wishlist_facts(Broken(), 1) == {
        "albums": 0, "artists": 0, "kinds": {"album": 0, "single": 0, "ep": 0}, "newest": None}
    assert T.watchlist_facts(Broken(), 1) == {
        "released_month": 0, "releases_month": 0, "added_week": 0, "newest": None}


def test_watchlist_facts_count_this_months_releases_and_this_weeks_adds(tmp_path):
    from datetime import date, timedelta

    from database.music_database import MusicDatabase
    db = MusicDatabase(str(tmp_path / "music.db"))
    day = lambda back: (date.today() - timedelta(days=back)).isoformat()  # noqa: E731
    with db._get_connection() as conn:
        ids = {}
        for name, added in (("Fresh", 2), ("Old", 90), ("Quiet", 90)):
            ids[name] = conn.execute(
                "INSERT INTO watchlist_artists(spotify_artist_id, artist_name, date_added, profile_id) "
                "VALUES(?,?,datetime('now', ?),1)", (f"sp-{name}", name, f"-{added} days")).lastrowid
        rows = [(ids["Fresh"], "a1", "New One", day(3), "https://cdn.example/n.jpg"),
                (ids["Fresh"], "a2", "Also New", day(10), None),
                (ids["Old"], "a3", "Recent", day(20), None),
                (ids["Old"], "a4", "Last Year", day(300), None),
                (ids["Old"], "a5", "Announced", day(-40), None)]
        for artist_id, album_id, title, released, cover in rows:
            conn.execute(
                "INSERT INTO recent_releases(watchlist_artist_id, album_spotify_id, album_name, "
                "release_date, album_cover_url) VALUES(?,?,?,?,?)",
                (artist_id, album_id, title, released, cover))
        conn.commit()
    facts = T.watchlist_facts(db, 1)
    assert facts["released_month"] == 2
    assert facts["releases_month"] == 3
    assert facts["added_week"] == 1
    # an announced album is not the newest release: it is not out
    assert facts["newest"] == {"album": "New One", "artist": "Fresh", "date": day(3),
                               "image_url": "https://cdn.example/n.jpg"}
    assert T.watchlist_facts(db, 7)["releases_month"] == 0
