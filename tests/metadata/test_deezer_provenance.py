"""A deezer download already knows which deezer track it is.

Enrichment used to work that out again by text search: search_track(artist,
title) takes the FIRST hit of a query and both names then have to clear
_names_match. For a remix, a mashup or a differently credited artist that misses,
and the result was no DEEZER_TRACK_ID at all - on a track that had just been
downloaded from deezer, by id.

The id rides along on the search result as _source_metadata and reaches the
import context through candidate.__dict__, which is the same path tidal and hifi
already use for theirs.
"""

from __future__ import annotations

import types

import pytest

from core.metadata import source as ms


class _Config:
    def __init__(self, values=None):
        self.values = values or {}

    def get(self, key, default=None):
        return self.values.get(key, default)


class _DzClient:
    """Records what it was asked, so a test can prove a call did NOT happen."""

    def __init__(self, search_result=None, details=None):
        self.search_result = search_result
        self.details = details or {}
        self.search_calls = []
        self.detail_calls = []

    def search_track(self, artist_name, track_title):
        self.search_calls.append((artist_name, track_title))
        return self.search_result

    def get_track_details(self, track_id):
        self.detail_calls.append(str(track_id))
        return self.details


def _runtime(client):
    return types.SimpleNamespace(deezer_worker=types.SimpleNamespace(client=client))


def _pp():
    return {"id_tags": {}, "release_year": None, "deezer_bpm": None, "deezer_isrc": None}


DEEZER_PROV = {"source": "deezer", "track_id": "498543342", "artist_id": "14069",
               "album_id": "77", "": None}


# ---------------------------------------------------------------------------
# the fact beats the guess
# ---------------------------------------------------------------------------

def test_a_known_track_id_is_used_without_searching():
    client = _DzClient(details={"bpm": 128, "isrc": "USX123456789"})
    pp = _pp()

    ms._process_deezer_source(pp, {}, _Config({"deezer.embed_tags": True}), _runtime(client),
                              "Revolution 09 (Final Cut Pro Mix)", "Gene Farris",
                              provenance=DEEZER_PROV)

    assert pp["id_tags"]["DEEZER_TRACK_ID"] == "498543342"
    assert pp["id_tags"]["DEEZER_ARTIST_ID"] == "14069"
    assert client.search_calls == [], "it already knew the id; it must not search"
    assert client.detail_calls == ["498543342"]
    assert pp["deezer_bpm"] == 128
    assert pp["deezer_isrc"] == "USX123456789"


def test_the_remix_that_used_to_lose_its_id():
    """The whole point. A title the fuzzy gate rejects still gets its id.

    Left to search_track, this returns some other track, _names_match refuses
    it, and nothing at all is embedded.
    """
    wrong_hit = {"id": 99, "title": "Revolution", "artist": {"name": "Someone Else", "id": 1}}
    client = _DzClient(search_result=wrong_hit, details={})
    pp = _pp()

    ms._process_deezer_source(pp, {}, _Config({"deezer.embed_tags": True}), _runtime(client),
                              "Revolution 09 (Final Cut Pro Mix)", "Gene Farris",
                              provenance=DEEZER_PROV)

    assert pp["id_tags"]["DEEZER_TRACK_ID"] == "498543342"
    assert client.search_calls == []


def test_without_provenance_it_searches_exactly_as_before():
    hit = {"id": 555, "title": "Song One", "artist": {"name": "Artist One", "id": 7},
           "album": {"release_date": "2001-05-04"}}
    client = _DzClient(search_result=hit, details={"bpm": 100})
    pp = _pp()

    ms._process_deezer_source(pp, {}, _Config({"deezer.embed_tags": True}), _runtime(client),
                              "Song One", "Artist One")

    assert client.search_calls == [("Artist One", "Song One")]
    assert pp["id_tags"]["DEEZER_TRACK_ID"] == "555"
    assert pp["id_tags"]["DEEZER_ARTIST_ID"] == "7"
    assert pp["release_year"] == "2001"


def test_a_search_the_fuzzy_gate_rejects_still_embeds_nothing():
    """Unchanged behaviour on the no-provenance path: a bad hit is refused."""
    wrong_hit = {"id": 99, "title": "Something Else", "artist": {"name": "Nobody", "id": 1}}
    client = _DzClient(search_result=wrong_hit)
    pp = _pp()

    ms._process_deezer_source(pp, {}, _Config({"deezer.embed_tags": True}), _runtime(client),
                              "Song One", "Artist One")

    assert pp["id_tags"] == {}


# ---------------------------------------------------------------------------
# provenance from somewhere else is not deezer's
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("provenance", [
    {"source": "tidal", "track_id": "434945950"},
    {"source": "hifi", "track_id": "1"},
    {"source": "soundcloud", "track_id": "2"},
    {"source": "deezer"},                      # deezer, but no id
    {"track_id": "3"},                         # id, but no source
    {},
    None,
    "not-a-dict",
])
def test_only_a_deezer_id_is_trusted(provenance):
    """A tidal id handed to deezer would be a wrong answer, not a shortcut."""
    hit = {"id": 555, "title": "Song One", "artist": {"name": "Artist One", "id": 7}}
    client = _DzClient(search_result=hit)
    pp = _pp()

    ms._process_deezer_source(pp, {}, _Config({"deezer.embed_tags": True}), _runtime(client),
                              "Song One", "Artist One", provenance=provenance)

    assert client.search_calls == [("Artist One", "Song One")], (
        "anything that is not a deezer track id must fall back to searching"
    )
    assert pp["id_tags"]["DEEZER_TRACK_ID"] == "555"


def test_the_helper_reads_only_deezer_ids():
    assert ms._deezer_provenance_id({"source": "deezer", "track_id": "9"}) == "9"
    assert ms._deezer_provenance_id({"source": "DEEZER", "track_id": "9"}) == "9"
    assert ms._deezer_provenance_id({"source": "tidal", "track_id": "9"}) == ""
    assert ms._deezer_provenance_id({"source": "deezer"}) == ""
    assert ms._deezer_provenance_id(None) == ""


# ---------------------------------------------------------------------------
# the rest of the handler still behaves
# ---------------------------------------------------------------------------

def test_release_year_comes_off_the_details_call_on_the_id_path():
    client = _DzClient(details={"album": {"release_date": "2009-08-01"}})
    pp = _pp()

    ms._process_deezer_source(pp, {}, _Config({"deezer.embed_tags": True}), _runtime(client),
                              "T", "A", provenance=DEEZER_PROV)

    assert pp["release_year"] == "2009"


def test_a_release_year_already_found_is_not_overwritten():
    client = _DzClient(details={"album": {"release_date": "2009-08-01"}})
    pp = _pp()
    pp["release_year"] = "1999"

    ms._process_deezer_source(pp, {}, _Config({"deezer.embed_tags": True}), _runtime(client),
                              "T", "A", provenance=DEEZER_PROV)

    assert pp["release_year"] == "1999"


def test_embed_tags_off_still_wins_over_provenance():
    client = _DzClient()
    pp = _pp()

    ms._process_deezer_source(pp, {}, _Config({"deezer.embed_tags": False}), _runtime(client),
                              "T", "A", provenance=DEEZER_PROV)

    assert pp["id_tags"] == {}
    assert client.detail_calls == []


def test_no_client_is_a_quiet_no_op():
    pp = _pp()
    ms._process_deezer_source(pp, {}, _Config({"deezer.embed_tags": True}),
                              types.SimpleNamespace(deezer_worker=None), "T", "A",
                              provenance=DEEZER_PROV)
    assert pp["id_tags"] == {}


def test_a_missing_title_no_longer_blocks_a_known_id():
    """The old guard returned early without a title. With an id in hand there is
    nothing to search for, so a thin title must not throw the id away."""
    client = _DzClient(details={})
    pp = _pp()

    ms._process_deezer_source(pp, {}, _Config({"deezer.embed_tags": True}), _runtime(client),
                              "", "", provenance=DEEZER_PROV)

    assert pp["id_tags"]["DEEZER_TRACK_ID"] == "498543342"


def test_programmer_errors_still_surface():
    class _Boom:
        def search_track(self, *_a):
            raise ValueError("boom")

    with pytest.raises(ValueError):
        ms._process_deezer_source(_pp(), {}, _Config({"deezer.embed_tags": True}),
                                  _runtime(_Boom()), "Song One", "Artist One")


# ---------------------------------------------------------------------------
# the stamp itself, and the path it travels
# ---------------------------------------------------------------------------

def test_a_deezer_search_result_carries_the_ids(monkeypatch):
    """Without this stamp there is nothing for enrichment to trust."""
    import core.deezer_download_client as ddc

    payload = {"data": [{
        "id": 498543342,
        "title": "Revolution 09 (Final Cut Pro Mix)",
        "duration": 353,
        "artist": {"id": 14069, "name": "Gene Farris"},
        "album": {"id": 77, "title": "Revolution 09"},
        "track_position": 4,
    }]}

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return payload

    client = ddc.DeezerDownloadClient.__new__(ddc.DeezerDownloadClient)
    client._authenticated = True
    client._session = types.SimpleNamespace(get=lambda *a, **k: _Resp())
    client._config = types.SimpleNamespace(get=lambda *a, **k: None)
    client._quality = "flac"

    results, _albums = client._search_sync("gene farris revolution")

    assert len(results) == 1
    meta = results[0]._source_metadata
    assert meta["source"] == "deezer"
    assert meta["track_id"] == "498543342"
    assert meta["artist_id"] == "14069"
    assert meta["album_id"] == "77"


def test_the_stamp_survives_into_the_download_payload():
    """candidate.__dict__ is how it reaches the import context.

    core/downloads/candidates.py builds the payload as ``candidate.__dict__``
    and copies it into ``original_search_result``, which is where enrichment
    reads ``_source_metadata`` back out. If TrackResult ever stops carrying it
    as a plain attribute, every source's provenance goes quiet at once.
    """
    from core.download_plugins.types import TrackResult

    tr = TrackResult(
        username="deezer_dl", filename="498543342||Gene Farris - Revolution 09",
        size=1, bitrate=1411, duration=1000, quality="flac",
        free_upload_slots=1, upload_speed=1, queue_length=0,
        artist="Gene Farris", title="Revolution 09",
        _source_metadata={"source": "deezer", "track_id": "498543342"},
    )

    payload = tr.__dict__
    assert payload["_source_metadata"]["track_id"] == "498543342"

    original_search_result = payload.copy()
    from core.imports.context import get_import_original_search
    context = {"original_search_result": original_search_result}
    assert get_import_original_search(context).get("_source_metadata", {}).get("source") == "deezer"


# ---------------------------------------------------------------------------
# the old path must stay the old path
# ---------------------------------------------------------------------------

class _Recorder(_DzClient):
    """Records the ORDER of api calls, not just that they happened."""

    def __init__(self, search_result=None, details=None):
        super().__init__(search_result, details)
        self.calls = []

    def search_track(self, artist_name, track_title):
        self.calls.append(("search", artist_name, track_title))
        return super().search_track(artist_name, track_title)

    def get_track_details(self, track_id):
        self.calls.append(("details", str(track_id)))
        return super().get_track_details(track_id)


_GOOD_HIT = {"id": 555, "title": "Song One", "artist": {"name": "Artist One", "id": 7},
             "album": {"release_date": "2001-05-04"}}


@pytest.mark.parametrize("hit,details,title,artist,enabled,expected_calls", [
    (_GOOD_HIT, {"bpm": 100}, "Song One", "Artist One", True,
     [("search", "Artist One", "Song One"), ("details", "555")]),
    ({"id": 9, "title": "Nope", "artist": {"name": "Other", "id": 1}}, {},
     "Song One", "Artist One", True, [("search", "Artist One", "Song One")]),
    (None, {}, "Song One", "Artist One", True, [("search", "Artist One", "Song One")]),
    (_GOOD_HIT, {}, "", "Artist One", True, []),
    (_GOOD_HIT, {}, "Song One", "", True, []),
    (_GOOD_HIT, {}, "Song One", "Artist One", False, []),
])
def test_the_no_provenance_path_makes_exactly_the_calls_it_always_made(
        hit, details, title, artist, enabled, expected_calls):
    """Provenance is additive: with none, this is the pre-existing function.

    Pinned as a call SEQUENCE because that is what a refactor silently changes -
    an extra lookup here is somebody else's rate limit. Verified against the
    pre-change implementation, which produced these exact sequences.
    """
    client = _Recorder(hit, details)
    ms._process_deezer_source(_pp(), {}, _Config({"deezer.embed_tags": enabled}),
                              _runtime(client), title, artist)
    assert client.calls == expected_calls


def test_the_release_year_fallback_is_not_widened_for_the_search_path():
    """The details call carries an album too, but the search path must not start
    reading it - that would be a new behaviour on the path that runs for every
    non-deezer download."""
    hit_without_album = {"id": 5, "title": "Song One", "artist": {"name": "Artist One", "id": 7}}
    client = _DzClient(search_result=hit_without_album,
                       details={"album": {"release_date": "1999-01-01"}})
    pp = _pp()

    ms._process_deezer_source(pp, {}, _Config({"deezer.embed_tags": True}), _runtime(client),
                              "Song One", "Artist One")

    assert pp["release_year"] is None, "search path started using the details album"
