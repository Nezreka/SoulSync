"""Artist photo picker seams: per-source candidate gathering + the DB pin.

The scenario this exists for (Discord report): an artist got mis-matched to
the wrong Deezer artist, SoulSync wrote the wrong photo to artist.jpg on
disk, and Navidrome kept showing it forever — re-matching fixed the metadata
but nothing ever offered a way to fix the PHOTO everywhere. The picker pulls
one candidate per CONNECTED metadata source and applying writes DB + server
+ artist.jpg.
"""

from __future__ import annotations

from types import SimpleNamespace

import core.metadata.artist_image as ai


class _Client:
    def __init__(self, image_url=None, search_hit=None, boom=False):
        self._image_url = image_url
        self._search_hit = search_hit
        self._boom = boom

    def get_artist(self, artist_id, **kwargs):    # spotify passes allow_fallback=False
        if self._boom:
            raise RuntimeError("source down")
        if self._image_url:
            return {"images": [{"url": self._image_url}]}
        return None

    def search_artists(self, name, limit=1):
        if self._boom:
            raise RuntimeError("source down")
        return [self._search_hit] if self._search_hit else []


def _wire_registry(monkeypatch, clients, priority):
    monkeypatch.setattr(ai.metadata_registry, "get_primary_source",
                        lambda spotify_client_factory=None: priority[0])
    monkeypatch.setattr(ai.metadata_registry, "get_source_priority",
                        lambda primary: list(priority))
    monkeypatch.setattr(ai.metadata_registry, "get_client_for_source",
                        lambda source, **kw: clients.get(source))


def test_gathers_one_candidate_per_connected_source(monkeypatch):
    clients = {
        "spotify": _Client(image_url="https://sp/img.jpg"),
        "deezer": _Client(search_hit=SimpleNamespace(image_url="https://dz/img.jpg")),
        "itunes": None,                                  # not connected -> skipped
        "audiodb": _Client(boom=True),                   # failing -> contributes nothing
    }
    _wire_registry(monkeypatch, clients, ["spotify", "deezer", "itunes", "audiodb"])

    cands = ai.gather_artist_image_candidates(
        "Adele", {"spotify_artist_id": "sp123"})

    assert {c["source"] for c in cands} == {"spotify", "deezer"}
    by = {c["source"]: c["url"] for c in cands}
    assert by["spotify"] == "https://sp/img.jpg"     # via stored id
    assert by["deezer"] == "https://dz/img.jpg"      # via name search


def test_duplicate_urls_dedupe_and_skip_sources_excluded(monkeypatch):
    same = "https://cdn/same.jpg"
    clients = {
        "spotify": _Client(search_hit=SimpleNamespace(image_url=same)),
        "deezer": _Client(search_hit=SimpleNamespace(image_url=same)),
        "musicbrainz": _Client(search_hit=SimpleNamespace(image_url="https://mb/x.jpg")),
    }
    _wire_registry(monkeypatch, clients, ["spotify", "deezer", "musicbrainz"])

    cands = ai.gather_artist_image_candidates("Adele", {})
    assert len(cands) == 1                            # deduped by url
    assert cands[0]["source"] == "spotify"            # chain order wins
    # musicbrainz is in the skip set — its client must never be offered
    assert all(c["source"] != "musicbrainz" for c in cands)


def test_no_sources_returns_empty(monkeypatch):
    _wire_registry(monkeypatch, {}, ["spotify"])
    assert ai.gather_artist_image_candidates("Adele", {}) == []


def test_set_artist_thumb_url_pins_and_workers_respect_it(tmp_path):
    from database.music_database import MusicDatabase
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    conn = db._get_connection()
    conn.execute("INSERT INTO artists (id, name, thumb_url) VALUES (1, 'Adele', '')")
    conn.commit()
    conn.close()

    assert db.set_artist_thumb_url(1, "https://picked/photo.jpg") is True
    artist = db.get_artist(1)
    assert artist.thumb_url == "https://picked/photo.jpg"

    # The enrichment workers' guard (thumb only filled when empty) must leave
    # a user pick alone — same WHERE clause every worker uses.
    conn = db._get_connection()
    conn.execute("UPDATE artists SET thumb_url = ? WHERE id = ? AND (thumb_url IS NULL OR thumb_url = '')",
                 ("https://worker/other.jpg", 1))
    conn.commit()
    conn.close()
    assert db.get_artist(1).thumb_url == "https://picked/photo.jpg"

    assert db.set_artist_thumb_url(999, "x") is False   # unknown artist -> False
