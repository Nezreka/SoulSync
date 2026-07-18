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

import pytest

import core.metadata.artist_image as ai


@pytest.fixture(autouse=True)
def _no_real_sources(monkeypatch):
    # the gather now ALWAYS asks TheAudioDB and the Spotify WRAPPER (which
    # serves Free-mode metadata) — without these stubs the legacy tests hit
    # real networks. Tests that want these sources override per-test.
    monkeypatch.setattr(ai, "_audiodb", lambda: None)
    monkeypatch.setattr(ai.metadata_registry, "get_spotify_client", lambda **kw: None)


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
        "deezer": _Client(search_hit=SimpleNamespace(image_url="https://dz/img.jpg")),
        "itunes": None,                                  # not connected -> skipped
        "audiodb": _Client(boom=True),                   # failing -> contributes nothing
    }
    _wire_registry(monkeypatch, clients, ["spotify", "deezer", "itunes", "audiodb"])
    # spotify rides the WRAPPER (auth by default, Free route otherwise)
    monkeypatch.setattr(ai.metadata_registry, "get_spotify_client",
                        lambda **kw: _Client(image_url="https://sp/img.jpg"))

    cands = ai.gather_artist_image_candidates(
        "Adele", {"spotify_artist_id": "sp123"})

    assert {c["source"] for c in cands} == {"spotify", "deezer"}
    by = {c["source"]: c["url"] for c in cands}
    assert by["spotify"] == "https://sp/img.jpg"     # via stored id
    assert by["deezer"] == "https://dz/img.jpg"      # via name search


def test_duplicate_urls_dedupe_and_skip_sources_excluded(monkeypatch):
    same = "https://cdn/same.jpg"
    clients = {
        "deezer": _Client(search_hit=SimpleNamespace(image_url=same)),
        "musicbrainz": _Client(search_hit=SimpleNamespace(image_url="https://mb/x.jpg")),
    }
    _wire_registry(monkeypatch, clients, ["spotify", "deezer", "musicbrainz"])
    monkeypatch.setattr(ai.metadata_registry, "get_spotify_client",
                        lambda **kw: _Client(search_hit=SimpleNamespace(image_url=same)))

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


class _AudioDbFake:
    def __init__(self, thumb=None):
        self.thumb = thumb
        self.searched = []
        self.looked_up = []

    def search_artist(self, name):
        self.searched.append(name)
        return {"strArtistThumb": self.thumb} if self.thumb else None

    def lookup_artist_by_id(self, sid):
        self.looked_up.append(sid)
        return {"strArtistThumb": self.thumb} if self.thumb else None


def test_audiodb_is_actually_queried(monkeypatch):
    """The endpoint docstring always promised AudioDB — but it wasn't in the
    priority chain, so the picker never asked it. Now it always does."""
    fake = _AudioDbFake(thumb="https://audiodb/adele.jpg")
    monkeypatch.setattr(ai, "_audiodb", lambda: fake)
    _wire_registry(monkeypatch, {"deezer": None}, ["deezer"])   # audiodb appended anyway

    cands = ai.gather_artist_image_candidates("Adele", {})
    assert cands == [{"source": "audiodb", "url": "https://audiodb/adele.jpg"}]
    assert fake.searched == ["Adele"]

    # a stored audiodb_id beats the name search
    fake2 = _AudioDbFake(thumb="https://audiodb/exact.jpg")
    monkeypatch.setattr(ai, "_audiodb", lambda: fake2)
    cands = ai.gather_artist_image_candidates("Adele", {"audiodb_id": "111239"})
    assert fake2.looked_up == ["111239"] and fake2.searched == []
    assert cands[0]["url"] == "https://audiodb/exact.jpg"


def test_imageless_search_hits_get_a_second_exact_fetch(monkeypatch):
    """iTunes returns NO image on search hits by design — the picker now does
    search → get_artist(top id) so iTunes finally contributes."""
    class _ITunes(_Client):
        def __init__(self):
            super().__init__(image_url="https://itunes/art.jpg",
                             search_hit=SimpleNamespace(id="it42", image_url=None))
    monkeypatch.setattr(ai, "_audiodb", lambda: None)
    _wire_registry(monkeypatch, {"itunes": _ITunes()}, ["itunes"])

    cands = ai.gather_artist_image_candidates("Adele", {})
    assert cands == [{"source": "itunes", "url": "https://itunes/art.jpg"}]


def test_endpoint_cache_is_id_keyed_and_forgives_empties():
    """Source pins: two same-name artists must not share a cache slot, and an
    empty result (one transient source hiccup) must not stick for 15 minutes."""
    from pathlib import Path
    ws = (Path(__file__).resolve().parent.parent / "web_server.py").read_text(
        encoding="utf-8", errors="replace")
    handler = ws.split("def get_artist_art_options")[1].split("\n@app.route")[0]
    assert "cache_key = ('artist', int(artist_id))" in handler
    assert "_ART_OPTIONS_EMPTY_TTL_S" in handler
    assert "_ART_OPTIONS_EMPTY_TTL_S = 60" in ws


def test_picker_grid_never_goes_silently_blank():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "webui" / "static" / "library.js").read_text(
        encoding="utf-8", errors="replace")
    # dead image URLs remove tiles — an emptied grid must SAY so
    assert "none of the images would load" in js


def test_spotify_free_mode_contributes(monkeypatch):
    """The registry gate requires FULL Spotify auth, but the wrapper serves
    artist metadata in Free mode — the picker asks the wrapper directly, so
    Spotify Free users finally get Spotify candidates."""
    wrapper = _Client(image_url="https://sp/free.jpg",
                      search_hit=SimpleNamespace(id="sp1", image_url="https://sp/free.jpg"))
    monkeypatch.setattr(ai.metadata_registry, "get_spotify_client", lambda **kw: wrapper)
    # registry gate says NO client (unauthenticated) — must not matter
    _wire_registry(monkeypatch, {"spotify": None}, ["spotify"])

    cands = ai.gather_artist_image_candidates("Adele", {})
    assert cands == [{"source": "spotify", "url": "https://sp/free.jpg"}]

    # stored spotify id path goes through the wrapper too
    cands = ai.gather_artist_image_candidates("Adele", {"spotify_artist_id": "sp123"})
    assert cands[0]["url"] == "https://sp/free.jpg"


def test_custom_url_apply_rejects_non_images():
    """Source pins: pasted URLs must not poison the thumb/poster/artist.jpg —
    downloaded bytes are magic-sniffed BEFORE anything is pinned."""
    from pathlib import Path
    ws = (Path(__file__).resolve().parent.parent / "web_server.py").read_text(
        encoding="utf-8", errors="replace")
    handler = ws.split("def set_artist_art")[1].split("\n@app.route")[0]
    assert "_looks_like_image(image_bytes)" in handler
    assert "doesn't point to an image" in handler
    # download+validate happens BEFORE the DB pin
    assert handler.index("_looks_like_image") < handler.index("set_artist_thumb_url")


def test_image_sniffer():
    import web_server as ws
    assert ws._looks_like_image(b"\xff\xd8\xff\xe0" + b"0" * 20) is True     # jpeg
    assert ws._looks_like_image(b"\x89PNG\r\n\x1a\n" + b"0" * 20) is True    # png
    assert ws._looks_like_image(b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 8) is True
    assert ws._looks_like_image(b"<!DOCTYPE html><html>...") is False
    assert ws._looks_like_image(b"") is False


def test_picker_has_the_custom_url_row():
    from pathlib import Path
    js = (Path(__file__).resolve().parent.parent / "webui" / "static" / "library.js").read_text(
        encoding="utf-8", errors="replace")
    assert "_artPickerCustomRow" in js
    assert "paste an image URL" in js
    # the row mounts AFTER the innerHTML reset that would wipe it
    seg = js.split("body.appendChild(grid);")[1][:400]
    assert "_artPickerCustomRow" in seg
