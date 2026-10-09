"""#1613: a tidal playlist identified 364 of 395 tracks and called it 100%.

the gap opened in get_playlist before discovery ever ran:
- every playlist call asked tidal for the US catalogue, and /tracks?filter[id]
  leaves out tracks that aren't in it, so a non-US account lost its
  home-region tracks
- filter[id] answers one track per id, so a track in the playlist twice came
  back once
- videos went to /tracks too and vanished without a count

these pin the fetch, the account-country lookup, the skipped counts the modal
shows, and the mirror sync handing each duplicate row its own result.
"""

from __future__ import annotations

import json

from core.tidal_client import Playlist, TidalClient, Track


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _Session:
    """playlist metadata for /playlists/{id}, the account for /users/me."""

    def __init__(self, country="SE", me_status=200):
        self.country = country
        self.me_status = me_status
        self.calls = []

    def get(self, url, params=None, **kwargs):
        self.calls.append((url, params))
        if url.endswith("/users/me"):
            return _Resp({"data": {"id": "u1", "attributes": {"country": self.country}}},
                         status_code=self.me_status)
        if url.endswith("/relationships/items"):
            return _Resp({"data": [{"type": "tracks", "id": "1"}], "links": {"meta": {}}})
        if url.endswith("/tracks"):
            return _Resp({"data": [], "included": []})
        return _Resp({"data": {"id": "PL1", "attributes": {"name": "Cam's playlist"},
                               "relationships": {}}})


def _make_client(session=None):
    client = object.__new__(TidalClient)
    client.base_url = "https://api.tidal.test"
    client.session = session or _Session()
    client.access_token = "tok"
    return client


def _page(items, cursor=None):
    return {"data": items, "links": {"meta": {"nextCursor": cursor} if cursor else {}}}


def test_playlist_keeps_duplicates_in_order_and_counts_the_rest(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(client, "_ensure_valid_token", lambda: True)
    pages = {
        None: _page([
            {"type": "tracks", "id": "1"},
            {"type": "tracks", "id": "2"},
            {"type": "videos", "id": "v1"},
            {"type": "tracks", "id": "1"},
        ], cursor="c2"),
        "c2": _page([
            {"type": "tracks", "id": "3"},  # not in the catalogue
            {"type": "tracks", "id": "4"},
        ]),
    }
    monkeypatch.setattr(client, "_get_playlist_tracks_page",
                        lambda playlist_id, cursor=None: pages[cursor])
    monkeypatch.setattr("core.tidal_client.time.sleep", lambda s: None)
    asked = []

    def fake_batch(ids):
        asked.append(list(ids))
        return [Track(id=i, name=f"t{i}", artists=["a"]) for i in ids if i != "3"]

    monkeypatch.setattr(client, "_get_tracks_batch", fake_batch)

    playlist = client.get_playlist("PL1")

    assert [t.id for t in playlist.tracks] == ["1", "2", "1", "4"]
    assert playlist.skipped_videos == 1
    assert playlist.unavailable_tracks == 1
    # one ask per id, never the video
    assert asked == [["1", "2"], ["3", "4"]]


def test_failed_batch_counts_as_unavailable(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(client, "_ensure_valid_token", lambda: True)
    monkeypatch.setattr(client, "_get_playlist_tracks_page",
                        lambda playlist_id, cursor=None: _page([{"type": "tracks", "id": str(i)}
                                                                for i in range(25)]))

    def fake_batch(ids):
        if "0" in ids:
            raise RuntimeError("boom")
        return [Track(id=i, name=i, artists=["a"]) for i in ids]

    monkeypatch.setattr(client, "_get_tracks_batch", fake_batch)

    playlist = client.get_playlist("PL1")

    assert len(playlist.tracks) == 5
    assert playlist.unavailable_tracks == 20


def test_playlist_calls_ask_for_the_account_country(monkeypatch):
    session = _Session(country="se")
    client = _make_client(session)
    monkeypatch.setattr(client, "_ensure_valid_token", lambda: True)
    monkeypatch.setattr(client, "_get_tracks_batch", lambda ids: [])

    # the real page fetch, so its params are on the session
    monkeypatch.setattr(TidalClient, "_get_playlist_tracks_page",
                        TidalClient._get_playlist_tracks_page.__wrapped__)
    client.get_playlist("PL1")

    sent = [p.get("countryCode") for url, p in session.calls if p]
    assert sent and set(sent) == {"SE"}


def test_tracks_batch_asks_for_the_account_country():
    session = _Session(country="DE")
    client = _make_client(session)
    TidalClient._get_tracks_batch.__wrapped__(client, ["1"])
    batch = [p for url, p in session.calls if url.endswith("/tracks")]
    assert batch[0]["countryCode"] == "DE"


def test_account_country_falls_back_to_us_and_is_cached_per_token():
    session = _Session(me_status=403)
    client = _make_client(session)
    assert client._get_account_country() == "US"
    assert client._get_account_country() == "US"
    assert sum(1 for url, _ in session.calls if url.endswith("/users/me")) == 1

    # a new token gets another go
    session.me_status = 200
    client.access_token = "tok2"
    assert client._get_account_country() == "SE"


def test_account_country_ignores_junk():
    client = _make_client(_Session(country="Sweden"))
    assert client._get_account_country() == "US"


def test_status_reports_skipped_entries_only_when_there_are_some():
    from core.discovery.endpoints import get_discovery_status

    def state(playlist):
        return {'phase': 'discovered', 'status': 'done', 'discovery_progress': 100,
                'spotify_matches': 364, 'spotify_total': 364,
                'discovery_results': [], 'playlist': playlist}

    gappy = Playlist(id="PL1", name="p", skipped_videos=2, unavailable_tracks=29)
    body, _ = get_discovery_status({'pl': state(gappy)}, 'pl', not_found_message='nf', error_label='Tidal')
    assert body['source_skipped'] == {'videos': 2, 'unavailable': 29}

    whole = Playlist(id="PL1", name="p")
    body, _ = get_discovery_status({'pl': state(whole)}, 'pl', not_found_message='nf', error_label='Tidal')
    assert 'source_skipped' not in body

    # other sources keep a dict or nothing there
    body, _ = get_discovery_status({'pl': state({'name': 'x'})}, 'pl', not_found_message='nf', error_label='Deezer')
    assert 'source_skipped' not in body


def test_mirror_sync_gives_each_duplicate_row_its_result(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_music.db")
    monkeypatch.setenv("DATABASE_PATH", test_db)
    from database.music_database import MusicDatabase
    db = MusicDatabase(test_db)
    monkeypatch.setattr("api.source_playlists.get_database", lambda: db)
    from api.source_playlists import _sync_discovery_results_to_mirrored

    with db._get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO mirrored_playlists (source, source_playlist_id, name, profile_id) VALUES (?, ?, ?, ?)",
            ("tidal", "PL1", "Cam's playlist", 1),
        )
        pl_id = cur.lastrowid
        for pos, sid in enumerate(["1", "2", "1"]):
            cur.execute(
                "INSERT INTO mirrored_playlist_tracks (playlist_id, position, track_name, artist_name, source_track_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (pl_id, pos, f"t{sid}", "a", sid),
            )
        conn.commit()

    results = [
        {'tidal_track': {'id': sid}, 'status': 'found', 'confidence': 0.9,
         'match_data': {'id': f"dz{sid}", 'name': f"t{sid}", 'source': 'deezer'}}
        for sid in ["1", "2", "1"]
    ]
    _sync_discovery_results_to_mirrored("tidal", "PL1", results, "deezer", profile_id=1)

    rows = sorted(db.get_mirrored_playlist_tracks(pl_id), key=lambda t: t["position"])
    matched = []
    for row in rows:
        extra = row["extra_data"]
        extra = json.loads(extra) if isinstance(extra, str) else (extra or {})
        matched.append((extra.get("matched_data") or {}).get("id"))
    assert matched == ["dz1", "dz2", "dz1"]
