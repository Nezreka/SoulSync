"""The Deezer download search must reach the original song.

Deezer's plain ``/search`` returns the Reprise and karaoke copies of "How Far
I'll Go" and leaves Auli'i Cravalho's original (track 136340808) out of the
page. The download client therefore also runs ``track:"title" artist``, with the
artist and title split taken from the plain results' own artist names.

The fixtures below are real Deezer responses (October 2026), trimmed to the
fields the client reads.
"""

from __future__ import annotations

import types

import pytest

import core.deezer_download_client as ddc
from core.deezer_track_query import plain_has_exact_title

ORIGINAL_ID = 136340808


def _item(track_id, title, artist, album="Moana (Original Motion Picture Soundtrack/Deluxe Edition)"):
    return {
        "id": track_id,
        "title": title,
        "duration": 163,
        "artist": {"id": 1, "name": artist},
        "album": {"id": 2, "title": album},
    }


# Real plain /search result for "aulii cravalho how far ill go": no original.
PLAIN = [
    _item(136340812, "How Far I'll Go (Reprise)", "Auli'i Cravalho"),
    _item(1434887672, "How Far I'll Go (原曲歌手:Auli'i Cravalho)", "歌っちゃ王", "カラオケ"),
    _item(3890762711, "How Far I'll Go (Originally Performed by Auli'i Cravalho) (Karaoke Version)", "karaoke SESH", "Karaoke"),
]

# Real track:"how far ill go" aulii cravalho result: original first.
SCOPED = [
    _item(ORIGINAL_ID, "How Far I'll Go", "Auli'i Cravalho"),
    _item(136340812, "How Far I'll Go (Reprise)", "Auli'i Cravalho"),
]


# ── client behaviour ─────────────────────────────────────────────────────────

class _Resp:
    ok = True

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _client(responder):
    client = ddc.DeezerDownloadClient.__new__(ddc.DeezerDownloadClient)
    client._authenticated = True
    calls = []

    def get(url, params=None, **_kw):
        calls.append(params["q"])
        return _Resp(responder(params["q"]))

    client._session = types.SimpleNamespace(get=get)
    client._config = types.SimpleNamespace(get=lambda *a, **k: None)
    client._quality = "flac"
    client._calls = calls
    return client


@pytest.fixture(autouse=True)
def _no_throttle(monkeypatch):
    import core.deezer_throttle as throttle

    monkeypatch.setattr(throttle, "wait_for_slot", lambda *a, **k: True)


def _responder(q):
    return {"data": SCOPED if q.startswith("track:") else PLAIN}



def test_the_original_reaches_the_candidate_list():
    client = _client(_responder)
    results, _ = client._search_sync("aulii cravalho how far ill go")

    assert client._calls == ["aulii cravalho how far ill go", 'track:"how far ill go" aulii cravalho']
    track_ids = {r._source_metadata["track_id"] for r in results}
    assert str(ORIGINAL_ID) in track_ids
    # nothing the plain search returned was lost
    assert {"136340812", "1434887672", "3890762711"} <= track_ids


def test_a_track_in_both_lists_is_returned_once():
    client = _client(_responder)
    results, _ = client._search_sync("aulii cravalho how far ill go")
    track_ids = [r._source_metadata["track_id"] for r in results]
    assert len(track_ids) == len(set(track_ids))


def test_title_only_query_still_gets_an_exact_title_search():
    client = _client(_responder)
    client._search_sync("how far ill go")
    assert client._calls[1] == 'track:"how far ill go"'


def test_a_failing_exact_title_search_leaves_the_plain_results_alone():
    def responder(q):
        if q.startswith("track:"):
            raise RuntimeError("boom")
        return {"data": PLAIN}

    client = _client(responder)
    results, _ = client._search_sync("aulii cravalho how far ill go")
    assert len(results) == 3


def test_not_authenticated_makes_no_request():
    client = _client(_responder)
    client._authenticated = False
    assert client._search_sync("aulii cravalho how far ill go") == ([], [])
    assert client._calls == []


# ── skipping the extra request when the plain search already has the song ────

def test_plain_has_exact_title_true_when_the_artists_own_exact_title_is_there():
    pairs = [("How Far I'll Go (Reprise)", "Auli'i Cravalho"), ("How Far I'll Go", "Auli'i Cravalho")]
    assert plain_has_exact_title("aulii cravalho how far ill go", pairs)


def test_plain_has_exact_title_false_for_reprises_and_karaoke_only():
    pairs = [(i["title"], i["artist"]["name"]) for i in PLAIN]
    assert not plain_has_exact_title("aulii cravalho how far ill go", pairs)


def test_plain_has_exact_title_false_when_the_exact_title_is_another_artists():
    pairs = [("How Far I'll Go", "Alessia Cara"), ("How Far I'll Go (Reprise)", "Auli'i Cravalho")]
    assert not plain_has_exact_title("aulii cravalho how far ill go", pairs)


def test_plain_has_exact_title_false_without_an_artist_in_the_query():
    # a bare title is shared by many songs, so the exact-title search still runs
    pairs = [("He Was You", "Mark Mancina")]
    assert not plain_has_exact_title("he was you", pairs)
    assert not plain_has_exact_title("", [])


def test_no_second_request_when_the_plain_search_already_has_the_song():
    plain_with_song = [_item(ORIGINAL_ID, "How Far I'll Go", "Auli'i Cravalho")] + PLAIN
    client = _client(lambda q: {"data": plain_with_song})
    results, _ = client._search_sync("aulii cravalho how far ill go")

    assert client._calls == ["aulii cravalho how far ill go"]
    assert str(ORIGINAL_ID) in {r._source_metadata["track_id"] for r in results}


def test_second_request_still_runs_when_the_song_is_missing_from_the_plain_search():
    client = _client(_responder)
    client._search_sync("aulii cravalho how far ill go")
    assert len(client._calls) == 2


# ── a download hint already does this search ─────────────────────────────────

def test_no_extra_search_when_a_download_hint_is_active():
    from core.downloads.track_hint import track_hint_context

    client = _client(_responder)
    hint = {"title": "How Far I'll Go", "artist": "Auli'i Cravalho", "deezer_id": None}
    with track_hint_context(hint):
        assert client._exact_title_items("aulii cravalho how far ill go", PLAIN) == []
    assert client._calls == []


def test_hint_with_a_deezer_id_also_skips_it():
    from core.downloads.track_hint import track_hint_context

    client = _client(_responder)
    with track_hint_context({"title": "", "artist": "", "deezer_id": "136340808"}):
        assert client._exact_title_items("aulii cravalho how far ill go", PLAIN) == []
    assert client._calls == []


def test_typed_search_without_a_hint_still_runs_it():
    client = _client(_responder)
    items = client._exact_title_items("aulii cravalho how far ill go", PLAIN)
    assert ORIGINAL_ID in {i["id"] for i in items}
    assert len(client._calls) == 1


def test_hint_without_an_artist_does_not_skip_it():
    from core.downloads.track_hint import track_hint_context

    client = _client(_responder)
    with track_hint_context({"title": "How Far I'll Go", "artist": "", "deezer_id": None}):
        client._exact_title_items("aulii cravalho how far ill go", PLAIN)
    assert len(client._calls) == 1
