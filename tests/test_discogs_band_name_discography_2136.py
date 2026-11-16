"""a discogs artist with "&" in its name keeps its own releases (#2136).

the feature filter cut the release artist at the first "&", so every
"Above & Beyond" release read as "Above"'s and was skipped as a feature.
"""

from __future__ import annotations

import core.discogs_client as discogs
from core.discogs_client import DiscogsClient


def _albums(monkeypatch, name, releases):
    monkeypatch.setattr(discogs, "get_metadata_cache", lambda: None)
    monkeypatch.setattr(discogs, "get_cached_artist_album_payload", lambda *a, **k: None)
    monkeypatch.setattr(discogs, "store_artist_album_items", lambda *a, **k: None)
    client = DiscogsClient(token="test-token")

    def api_get(path, params=None):
        if path.endswith("/releases"):
            return {"releases": releases}
        return {"name": name}

    monkeypatch.setattr(client, "_api_get", api_get)
    return [a.name for a in client.get_artist_albums("1", album_type="album,single,compilation")]


def _release(rid, title, artist):
    return {"id": rid, "title": title, "artist": artist, "type": "master", "role": "Main",
            "format": "CD, Album", "year": 2018}


def test_a_band_with_an_ampersand_keeps_its_releases(monkeypatch):
    names = _albums(monkeypatch, "Above & Beyond", [
        _release(1, "Common Ground", "Above & Beyond"),
        _release(2, "Group Therapy", "Above & Beyond Feat. Zoë Johnston"),
    ])
    assert names == ["Common Ground", "Group Therapy"]


def test_someone_elses_release_still_drops(monkeypatch):
    names = _albums(monkeypatch, "Kendrick Lamar", [
        _release(1, "DAMN.", "Kendrick Lamar"),
        _release(2, "Lemonade", "Beyoncé Feat. Kendrick Lamar"),
        _release(3, "Collab", "Kendrick Lamar & SZA"),
    ])
    assert names == ["DAMN.", "Collab"]
