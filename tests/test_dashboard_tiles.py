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
