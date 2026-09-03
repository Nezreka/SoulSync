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
    import time

    same = "https://cdn/same.jpg"

    class SlowSpotify(_Client):
        def search_artists(self, name, limit=1):
            time.sleep(0.02)
            return super().search_artists(name, limit)

    clients = {
        "deezer": _Client(search_hit=SimpleNamespace(image_url=same)),
        "musicbrainz": _Client(search_hit=SimpleNamespace(image_url="https://mb/x.jpg")),
    }
    _wire_registry(monkeypatch, clients, ["spotify", "deezer", "musicbrainz"])
    monkeypatch.setattr(ai.metadata_registry, "get_spotify_client",
                        lambda **kw: SlowSpotify(search_hit=SimpleNamespace(image_url=same)))

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
    from tests.support.catalogue_seed import seed_artist
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    conn = db._get_connection()
    artist_id = seed_artist(conn, server_id='ar1', name='Adele', image_url='')
    conn.commit()
    conn.close()

    assert db.set_artist_thumb_url(artist_id, "https://picked/photo.jpg") is True
    artist = db.get_artist(artist_id)
    assert artist.thumb_url == "https://picked/photo.jpg"

    # The enrichment workers' guard (image only filled when empty) must leave
    # a user pick alone — same WHERE clause every worker uses.
    conn = db._get_connection()
    conn.execute("UPDATE lib2_artists SET image_url = ? WHERE id = ? AND (image_url IS NULL OR image_url = '')",
                 ("https://worker/other.jpg", artist_id))
    conn.commit()
    conn.close()
    assert db.get_artist(artist_id).thumb_url == "https://picked/photo.jpg"

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
    # the artist family moved to api/artist_detail.py (aug 26 lift)
    ws = (Path(__file__).resolve().parent.parent / "api" / "artist_detail.py").read_text(
        encoding="utf-8", errors="replace")
    handler = ws.split("def get_artist_art_options")[1].split("\n@bp.route")[0]
    # FLIPPED (#1069, matvei4iz): this pin used to assert int(artist_id) — the
    # bug itself. artists.id is TEXT since the id-columns migration; Navidrome/
    # Jellyfin ids are strings and int() 400'd the whole picker for them.
    assert "cache_key = ('artist', artist_id)" in handler
    assert "int(artist_id)" not in handler
    assert "_ART_OPTIONS_EMPTY_TTL_S" in handler
    assert "_ART_OPTIONS_EMPTY_TTL_S = 60" in ws
    # the apply endpoint: no casts either, and the cache invalidation pops the
    # ID-keyed slot (it used to pop a NAME-keyed one — a dead pop)
    apply_h = ws.split("def set_artist_art")[1].split("\n@bp.route")[0]
    assert "int(artist_id)" not in apply_h
    assert "_ART_OPTIONS_CACHE.pop(('artist', artist_id), None)" in apply_h


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
    # the artist family moved to api/artist_detail.py (aug 26 lift)
    ws = (Path(__file__).resolve().parent.parent / "api" / "artist_detail.py").read_text(
        encoding="utf-8", errors="replace")
    handler = ws.split("def set_artist_art")[1].split("\n@bp.route")[0]
    assert "_looks_like_image(image_bytes)" in handler
    assert "doesn't point to an image" in handler
    # download+validate happens BEFORE the DB pin
    assert handler.index("_looks_like_image") < handler.index("set_artist_thumb_url")


def test_image_sniffer():
    from api import artist_detail as ws
    assert ws._looks_like_image(b"\xff\xd8\xff\xe0" + b"0" * 20) is True     # jpeg
    assert ws._looks_like_image(b"\x89PNG\r\n\x1a\n" + b"0" * 20) is True    # png
    assert ws._looks_like_image(b"RIFF\x00\x00\x00\x00WEBP" + b"0" * 8) is True
    assert ws._looks_like_image(b"<!DOCTYPE html><html>...") is False
    assert ws._looks_like_image(b"") is False


def test_spotify_403_falls_through_to_free_metadata(monkeypatch):
    """Live finding (Boulder's box): Spotify 403s dev apps whose owner lacks
    active Premium — token refresh still works, so auth LOOKS healthy and the
    wrapper's own free routing never engages. The picker falls through to the
    no-creds backend itself."""
    class _FreeMeta:
        def get_artist(self, sid):
            return {"images": [{"url": "https://i.scdn.co/free.jpg"}]}

    class _PremiumWalled:
        def get_artist(self, sid, **kw):
            raise RuntimeError("403 premium required")
        _free_meta = _FreeMeta()

    monkeypatch.setattr(ai.metadata_registry, "get_spotify_client",
                        lambda **kw: _PremiumWalled())
    _wire_registry(monkeypatch, {}, ["spotify"])

    cands = ai.gather_artist_image_candidates("Adele", {"spotify_artist_id": "sp1"})
    assert cands == [{"source": "spotify", "url": "https://i.scdn.co/free.jpg"}]

    # official returning None (not raising) falls through the same way
    class _NoneOfficial(_PremiumWalled):
        def get_artist(self, sid, **kw):
            return None
    monkeypatch.setattr(ai.metadata_registry, "get_spotify_client",
                        lambda **kw: _NoneOfficial())
    cands = ai.gather_artist_image_candidates("Adele", {"spotify_artist_id": "sp1"})
    assert cands[0]["url"] == "https://i.scdn.co/free.jpg"


# test_custom_row_check_icon_is_module_scope was retired with library.js: the
# hazard it pinned (a function-local const referenced from another function's
# markup — a silent ReferenceError) cannot exist in the React module system.


def test_musicbrainz_relations_contribute_a_candidate_when_mbid_is_known(monkeypatch):
    """iss27-03: MusicBrainz is excluded from the generic by-name search (see
    the skip-set test above), but the picker already has the artist's own
    MBID — its exact url-relations lookup must still be asked instead of
    treating "musicbrainz" as entirely unqueryable."""
    monkeypatch.setattr(ai, "_image_from_musicbrainz_relations",
                        lambda mbid: "https://mb-rel/photo.jpg" if mbid == "mb-1" else None)
    _wire_registry(monkeypatch, {}, ["spotify"])

    cands = ai.gather_artist_image_candidates(
        "Adele", {"musicbrainz_artist_id": "mb-1"})

    assert {"source": "musicbrainz", "url": "https://mb-rel/photo.jpg"} in cands


def test_musicbrainz_relations_skipped_without_an_mbid(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "_image_from_musicbrainz_relations",
                        lambda mbid: calls.append(mbid) or None)
    _wire_registry(monkeypatch, {}, ["spotify"])

    ai.gather_artist_image_candidates("Adele", {})

    assert calls == []


def test_one_slow_source_does_not_block_or_drop_the_others(monkeypatch):
    """iss27-03: a provider stuck past the shared time budget (module-level
    rate-limit backoff sleeping inside the worker thread, in production) must
    not blank out sources that already answered — only the slow one is
    missing from this round."""
    import threading as _threading

    released = _threading.Event()

    class _SlowClient(_Client):
        def search_artists(self, name, limit=1):
            released.wait(timeout=5)  # blocks well past the gather's budget
            return super().search_artists(name, limit=limit)

    monkeypatch.setattr(ai, "_CANDIDATE_GATHER_TIMEOUT_S", 0.2)
    clients = {
        "deezer": _Client(search_hit=SimpleNamespace(image_url="https://dz/img.jpg")),
        "itunes": _SlowClient(search_hit=SimpleNamespace(image_url="https://slow/img.jpg")),
    }
    _wire_registry(monkeypatch, clients, ["spotify", "deezer", "itunes"])

    try:
        cands = ai.gather_artist_image_candidates("Adele", {})
    finally:
        released.set()  # let the background thread finish so it doesn't leak

    assert cands == [{"source": "deezer", "url": "https://dz/img.jpg"}]


def test_server_native_string_ids_work_end_to_end(tmp_path):
    """#1069: the exact Navidrome shape from the report — a server whose own
    ids are opaque strings. The catalogue keeps those as `server_id` and mints
    its own row id, so the art endpoints address rows by the catalogue id and
    the string never reaches a lookup."""
    from database.music_database import MusicDatabase
    from tests.support.catalogue_seed import seed_album, seed_artist
    db = MusicDatabase(database_path=str(tmp_path / "m.db"))
    conn = db._get_connection()
    artist_id = seed_artist(conn, server_id='7dB07x8Q2P9jPvGeDHxIFa',
                            name='Ed Sheeran', server_source='navidrome')
    seed_album(conn, server_id='al-x', title='Divide', artist_id=artist_id,
               server_source='navidrome')
    conn.commit()
    conn.close()

    artist = db.get_artist(artist_id)
    assert artist is not None and artist.name == 'Ed Sheeran'
    assert db.set_artist_thumb_url(artist_id, 'https://x/p.jpg') is True
    assert db.get_artist(artist_id).thumb_url == 'https://x/p.jpg'
    albums = db.get_albums_by_artist(artist_id)
    assert [a.title for a in albums] == ['Divide']


def test_candidate_fan_out_uses_one_bounded_process_wide_pool(monkeypatch):
    """PR #1121 review: a fresh ThreadPoolExecutor per call, sized to the
    source list and abandoned with shutdown(wait=False), means nothing caps
    threads ACROSS calls — every picker open adds a full set while the
    previous stragglers are still sleeping inside a rate-limit backoff. Two
    picker opens must reuse the same bounded pool."""
    import threading as _threading

    seen_threads = set()

    class _RecordingClient(_Client):
        def search_artists(self, name, limit=1):
            seen_threads.add(_threading.current_thread().name)
            return super().search_artists(name, limit=limit)

    clients = {
        "deezer": _RecordingClient(search_hit=SimpleNamespace(image_url="https://dz/1.jpg")),
        "itunes": _RecordingClient(search_hit=SimpleNamespace(image_url="https://it/1.jpg")),
    }
    _wire_registry(monkeypatch, clients, ["deezer", "itunes"])

    rounds = 20
    for _ in range(rounds):
        ai.gather_artist_image_candidates("Adele", {})

    assert seen_threads, "no worker thread ran"
    # A per-call executor gives each round its own threads, so the distinct
    # count grows with the number of picker opens. A shared pool cannot exceed
    # its cap however many times the picker is opened.
    assert len(seen_threads) <= ai._CANDIDATE_POOL_MAX_WORKERS, (
        f"{rounds} picker opens produced {len(seen_threads)} distinct worker "
        f"threads: {sorted(seen_threads)}"
    )


def test_candidate_pool_is_capped():
    pool = ai._candidate_pool()
    assert ai._candidate_pool() is pool, "the pool must be process-wide"
    assert 0 < pool._max_workers <= ai._CANDIDATE_POOL_MAX_WORKERS
