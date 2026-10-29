"""The library index's filters from the old page, on the v2 list routes.

The old library page had an alphabet bar, a has/no provider filter, a
"could be better" filter with a count on each card, and provider badges.
The Library v2 page lists through /api/library/v2/artists and /albums, so
those routes take the same params and hand back what the cards need.
"""

from core.library2 import queries
from tests.library2.test_api_routes import api  # noqa: F401  (fixture)
from tests.library2.test_artist_reader_quality import _seed, catalogue  # noqa: F401  (fixture)


def _names(payload, key):
    return [row.get("name") or row.get("title") for row in payload[key]]


def test_artists_take_the_alphabet_bar(api):  # noqa: F811
    client, _db, _ids = api
    assert _names(client.get("/api/library/v2/artists?letter=d").get_json(), "artists") == ["Drake"]
    assert _names(client.get("/api/library/v2/artists?letter=X").get_json(), "artists") == []
    assert _names(client.get("/api/library/v2/artists?letter=%23").get_json(), "artists") == []


def test_artists_take_has_and_has_no_provider(api):  # noqa: F811
    client, _db, _ids = api
    has = client.get("/api/library/v2/artists?source=spotify").get_json()
    assert _names(has, "artists") == ["Drake"]
    assert has["artists"][0]["provider_ids"]["spotify"] == "sp-drake"
    assert _names(client.get("/api/library/v2/artists?source=!spotify").get_json(), "artists") == []
    assert _names(client.get("/api/library/v2/artists?source=!deezer").get_json(), "artists") == ["Drake"]


def test_albums_take_the_alphabet_bar_and_provider(api):  # noqa: F811
    client, db, ids = api
    conn = db._get_connection()
    conn.execute("UPDATE lib2_albums SET monitored=1")
    conn.execute("UPDATE lib2_albums SET spotify_id='sp-views' WHERE id=?", (ids["views"],))
    conn.commit()
    conn.close()
    views = client.get("/api/library/v2/albums?letter=v").get_json()
    assert _names(views, "albums") == ["Views"]
    assert views["albums"][0]["provider_ids"] == {"spotify": "sp-views"}
    assert _names(client.get("/api/library/v2/albums?source=spotify").get_json(), "albums") == ["Views"]
    assert sorted(_names(client.get("/api/library/v2/albums?source=!spotify").get_json(), "albums")) == [
        "Best EP", "One Dance"]


def test_artists_route_passes_the_could_be_better_filter(api):  # noqa: F811
    client, _db, _ids = api
    # Drake's one file has no profile that wants more: nothing to upgrade
    payload = client.get("/api/library/v2/artists?quality=upgradable&include=upgradable").get_json()
    assert payload["success"] is True
    assert payload["artists"] == []


def test_each_card_counts_its_upgradable_tracks(catalogue):  # noqa: F811
    conn, _db = catalogue
    below, _ = _seed(conn, "A below", ("mp3", None, 44100, 128))
    above, _ = _seed(conn, "C above", ("flac", 24, 96000, 2500))
    conn.commit()
    artists, _total = queries.list_artists(conn, include_upgradable=True)
    counts = {a["id"]: a["upgradable_count"] for a in artists}
    assert counts[below] == 1
    assert counts[above] == 0
    # only asked for, so a plain page doesn't pay for the evaluator
    plain, _ = queries.list_artists(conn)
    assert all("upgradable_count" not in a for a in plain)


def test_upgradable_counts_route_answers_for_the_listed_ids(api):  # noqa: F811
    client, _db, ids = api
    payload = client.get(f"/api/library/v2/artists/upgradable-counts?ids={ids['artist']},999").get_json()
    assert payload == {"success": True, "counts": {str(ids["artist"]): 0, "999": 0}}
    assert client.get("/api/library/v2/artists/upgradable-counts?ids=x").status_code == 400
    assert client.get("/api/library/v2/artists/upgradable-counts").get_json()["counts"] == {}
