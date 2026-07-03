"""Parse pasted Tidal/Qobuz track links for the manual download search (#813)."""

from core.downloads.track_link import parse_download_track_link as p


def test_tidal_track_url_with_region_suffix():
    assert p('https://tidal.com/track/434945950/u') == ('tidal', '434945950')


def test_tidal_browse_and_listen_hosts():
    assert p('https://tidal.com/browse/track/434945950') == ('tidal', '434945950')
    assert p('https://listen.tidal.com/track/434945950') == ('tidal', '434945950')


def test_qobuz_track_urls():
    assert p('https://open.qobuz.com/track/12345678') == ('qobuz', '12345678')
    assert p('https://play.qobuz.com/track/12345678') == ('qobuz', '12345678')


def test_scheme_less():
    assert p('tidal.com/track/999') == ('tidal', '999')


def test_id_with_slug_suffix():
    assert p('https://www.qobuz.com/track/555-some-slug') == ('qobuz', '555')


def test_non_track_links_rejected():
    assert p('https://tidal.com/album/123') is None       # album, not track
    assert p('https://tidal.com/artist/123') is None
    assert p('https://open.spotify.com/track/abc') is None  # unsupported source
    assert p('https://example.com/track/123') is None


def test_garbage_rejected():
    assert p('') is None
    assert p('just some text') is None
    assert p('Habbit (T-Mass Remix)') is None


# ── query_from_track_payload (pure per-source parsing) ──

from core.downloads.track_link import query_from_track_payload as q


def test_tidal_payload_appends_version():
    # Tidal attributes: title + version → remix link searches for the remix.
    raw = {'title': 'Habbit', 'version': 'T-Mass Remix',
           'artists': [{'name': 'Rain Man'}, {'name': 'Krysta Youngs'}]}
    assert q('tidal', raw) == 'Rain Man Habbit (T-Mass Remix)'


def test_tidal_payload_no_version_no_artist():
    assert q('tidal', {'title': 'Bloom'}) == 'Bloom'


def test_tidal_payload_singular_artist():
    assert q('tidal', {'title': 'X', 'artist': {'name': 'Jinco'}}) == 'Jinco X'


def test_tidal_version_already_in_title_not_doubled():
    raw = {'title': 'Bloom (Nurko Remix)', 'version': 'Nurko Remix',
           'artists': [{'name': 'Dabin'}]}
    assert q('tidal', raw) == 'Dabin Bloom (Nurko Remix)'


def test_qobuz_payload_performer():
    raw = {'title': "What's Good For Me", 'performer': {'name': 'Jinco'}}
    assert q('qobuz', raw) == "Jinco What's Good For Me"


def test_qobuz_payload_falls_back_to_album_artist():
    raw = {'title': 'Song', 'album': {'artist': {'name': 'Some Artist'}}}
    assert q('qobuz', raw) == 'Some Artist Song'


def test_payload_non_dict_or_empty():
    assert q('tidal', None) is None
    assert q('tidal', {}) is None
    assert q('qobuz', 'garbage') is None


# ── bubble the pasted-link track to the top (#932) ──

from types import SimpleNamespace

from core.downloads.track_link import linked_track_id, bubble_linked_track_first


def _result(track_id=None):
    """Mimic a TrackResult: NO top-level `id`, the source id lives in
    _source_metadata['track_id'] (or absent entirely)."""
    meta = {'source': 'qobuz', 'track_id': track_id} if track_id is not None else None
    return SimpleNamespace(_source_metadata=meta, title='t')


def test_linked_track_id_reads_source_metadata():
    assert linked_track_id(_result('296427754')) == '296427754'


def test_linked_track_id_empty_when_absent():
    # the exact #932 trap: there is no top-level `id`, so getattr(t,'id') would miss.
    r = _result()
    assert not hasattr(r, 'id')
    assert linked_track_id(r) == ''


def test_bubble_floats_exact_track_to_top():
    fuzzy_a, exact, fuzzy_b = _result('111'), _result('296427754'), _result('222')
    out = bubble_linked_track_first([fuzzy_a, exact, fuzzy_b], '296427754')
    assert out[0] is exact                      # exact track surfaced first
    assert out[1:] == [fuzzy_a, fuzzy_b]         # stable order for the rest


def test_bubble_handles_int_vs_str_id():
    out = bubble_linked_track_first([_result('999'), _result('296427754')], 296427754)
    assert linked_track_id(out[0]) == '296427754'


def test_bubble_noop_when_nothing_matches_or_empty():
    a, b = _result('1'), _result('2')
    assert bubble_linked_track_first([a, b], '296427754') == [a, b]   # unchanged
    assert bubble_linked_track_first([], '296427754') == []
    assert bubble_linked_track_first([a, b], '') == [a, b]


# ── inject the EXACT fetched track first (#932 reopen: text search misses obscure tracks) ──

from core.downloads.track_link import inject_linked_track_first


def test_inject_puts_fetched_track_first_when_search_missed_it():
    # The reported case: the linked track isn't among the fuzzy text-search
    # results at all, so the directly-fetched result is injected at the top.
    fetched = _result('296427754')
    search = [_result('111'), _result('222')]   # unrelated lookalikes
    out = inject_linked_track_first(search, fetched, '296427754')
    assert out[0] is fetched
    assert len(out) == 3


def test_inject_dedups_a_search_copy_of_the_linked_track():
    fetched = _result('296427754')
    dup = _result('296427754')                  # search also returned it
    search = [_result('111'), dup, _result('222')]
    out = inject_linked_track_first(search, fetched, '296427754')
    assert out[0] is fetched
    # exactly one element carries the linked id, and it's the injected one
    assert [linked_track_id(t) for t in out].count('296427754') == 1
    assert all(t is not dup for t in out)        # the search copy (by identity) was dropped
    assert len(out) == 3


def test_inject_falls_back_to_bubble_without_a_fetched_result():
    exact = _result('296427754')
    out = inject_linked_track_first([_result('111'), exact, _result('222')], None, '296427754')
    assert linked_track_id(out[0]) == '296427754'   # bubbled, not injected
    assert len(out) == 3


def test_inject_is_noop_without_a_link_id():
    search = [_result('111')]
    assert inject_linked_track_first(search, _result('x'), '') == search


def test_inject_int_track_id_is_str_safe():
    fetched = _result('296427754')
    out = inject_linked_track_first([_result('111')], fetched, 296427754)
    assert out[0] is fetched


# ── QobuzClient.get_track_result: fetch by id → downloadable TrackResult ──

from core.qobuz_client import QobuzClient


def _bare_qobuz():
    return QobuzClient.__new__(QobuzClient)   # no network / __init__


def test_get_track_result_converts_fetched_track(monkeypatch):
    client = _bare_qobuz()
    sentinel = object()
    monkeypatch.setattr(client, 'get_track', lambda tid: {'id': tid, 'streamable': True})
    monkeypatch.setattr(client, '_qobuz_to_track_result',
                        lambda track, qi, require_streamable=True: sentinel)
    assert client.get_track_result('296427754') is sentinel


def test_get_track_result_none_when_track_unavailable(monkeypatch):
    client = _bare_qobuz()
    monkeypatch.setattr(client, 'get_track', lambda tid: None)
    assert client.get_track_result('nope') is None


def test_get_track_result_none_on_exception(monkeypatch):
    client = _bare_qobuz()
    def _boom(_tid):
        raise RuntimeError('api down')
    monkeypatch.setattr(client, 'get_track', _boom)
    assert client.get_track_result('x') is None


# ── #932 hardening: a pasted-link fetch must not be dropped by a missing
#    'streamable' flag (track/get may omit it). Exercises the REAL converter. ──

_QOBUZ_TRACK = {'id': 296427754, 'title': 'foreign lavennew',
                'performer': {'name': 'colacola'}, 'duration': 180}


def test_converter_rejects_non_streamable_on_search_path():
    # Default (bulk search) still filters on streamable — unchanged behaviour.
    client = _bare_qobuz()
    qi = {'codec': 'flac', 'bitrate': 1411}
    assert client._qobuz_to_track_result(dict(_QOBUZ_TRACK), qi) is None  # no streamable flag


def test_converter_builds_for_link_fetch_without_streamable():
    client = _bare_qobuz()
    qi = {'codec': 'flac', 'bitrate': 1411}
    r = client._qobuz_to_track_result(dict(_QOBUZ_TRACK), qi, require_streamable=False)
    assert r is not None
    assert linked_track_id(r) == '296427754'   # carries the id → injectable + downloadable


def test_get_track_result_survives_missing_streamable_flag(monkeypatch):
    # The feared track/get shape (no 'streamable') must STILL yield the track —
    # this is the end-to-end gap that couldn't be confirmed against a live API.
    client = _bare_qobuz()
    monkeypatch.setattr(client, 'get_track', lambda _tid: dict(_QOBUZ_TRACK))
    r = client.get_track_result('296427754')
    assert r is not None
    assert linked_track_id(r) == '296427754'
