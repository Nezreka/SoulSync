"""an artist MusicMap has no page for still gets similar artists.

"Gizmo & Mac" is a 404 on MusicMap. the stream sent one error event and the
artist page sat on "Finding similar artists..." for good. it answers from the
providers' own lists now (Last.fm's similar artists, Deezer's related).
"""

import json

import requests
from flask import Flask

import core.metadata.similar_artists as sim


def _payload(name):
    return {'id': f'id-{name}', 'name': name, 'image_url': '', 'source': 'deezer'}


def test_fallback_events_match_the_providers_names(monkeypatch):
    monkeypatch.setattr(sim, '_get_source_chain_for_lookup', lambda options: ['deezer'])
    monkeypatch.setattr(sim, '_resolve_musicmap_artist_source_ids', lambda name, chain: {'deezer': '42'})
    monkeypatch.setattr(sim, '_fallback_similar_artist_names', lambda name, ids: ['Bbno$', 'Yung Gravy', 'Bbno$'])
    monkeypatch.setattr(sim, '_match_musicmap_similar_artist',
                        lambda cand, chain, artist, ids: ('deezer', _payload(cand)))

    events = list(sim.iter_fallback_similar_artist_events('Gizmo & Mac'))

    assert events[0]['type'] == 'start' and events[0]['fallback'] is True
    assert [e['artist']['name'] for e in events if e['type'] == 'artist'] == ['Bbno$', 'Yung Gravy']
    assert events[-1] == {**events[-1], 'type': 'complete', 'total': 2}


def test_the_stream_falls_back_when_musicmap_has_no_page(monkeypatch):
    import api.artist_detail as ad

    def not_on_musicmap(name):
        response = requests.Response()
        response.status_code = 404
        raise requests.exceptions.HTTPError('404 Client Error', response=response)

    monkeypatch.setattr(sim, '_fetch_musicmap_similar_artist_names', not_on_musicmap)
    monkeypatch.setattr(sim, '_get_source_chain_for_lookup', lambda options: ['deezer'])
    monkeypatch.setattr(sim.metadata_registry, 'get_client_for_source', lambda source: object())
    monkeypatch.setattr(sim, '_resolve_musicmap_artist_source_ids', lambda name, chain: {'deezer': '42'})
    monkeypatch.setattr(sim, '_fallback_similar_artist_names', lambda name, ids: ['Bbno$'])
    monkeypatch.setattr(sim, '_match_musicmap_similar_artist',
                        lambda cand, chain, artist, ids: ('deezer', _payload(cand)))
    monkeypatch.setattr(ad.time, 'sleep', lambda s: None)

    app = Flask(__name__)
    app.register_blueprint(ad.bp)
    body = app.test_client().get('/api/artist/similar/Gizmo%20%26%20Mac/stream').get_data(as_text=True)
    events = [json.loads(chunk[6:]) for chunk in body.split('\n\n') if chunk.startswith('data: ')]

    assert not [e for e in events if e['type'] == 'error']
    assert [e['artist']['name'] for e in events if e['type'] == 'artist'] == ['Bbno$']
    assert events[-1]['type'] == 'complete'
