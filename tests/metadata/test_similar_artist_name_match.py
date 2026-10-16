"""similar artists stored the wrong artist under a musicmap name.

each musicmap name was searched with limit=1 and the first hit kept, name or
not. deezer ranks namesakes first, so a reporter's Artists You'll Like showed
a thai pop act's photo as Korn, Colde for Cold, brandnew for Brand New.

the result lists below are deezer's real answers (search/artist, Oct 14).
"""

from __future__ import annotations

from types import SimpleNamespace

from core.metadata import similar_artists as sa


def _artist(id_, name, fans=0):
    return SimpleNamespace(id=str(id_), name=name, followers=fans,
                           popularity=0, genres=[], image_url=None)


DEEZER = {
    'Cold': [_artist(10607069, 'Cold', 16), _artist(13423415, 'Colde', 17471),
             _artist(318, 'Cold', 27822), _artist(892, 'Coldplay', 18420019)],
    'Korn': [_artist(240940521, 'Korn', 78), _artist(120677712, 'K.O.R.N.', 43),
             _artist(1327, 'KoЯn', 2627745), _artist(209009137, 'Korn & Sprite', 10)],
    'Brand New': [_artist(2111591, 'brandnew', 41), _artist(946, 'Brand New', 53563),
                  _artist(6337, 'The Brand New Heavies', 26356)],
    'Finch': [_artist(15334461, 'Finch & Henson', 24), _artist(242757491, 'Finch', 21),
              _artist(701, 'Finch', 19444)],
    'Vic Fuentes': [_artist(4942224, 'Víctor Fuentes', 196),
                    _artist(4627754, 'Vic Fuentes', 4729)],
    'Panic! at the Disco': [_artist(402, 'Panic! At the Disco', 1706783),
                            _artist(412845981, 'Panic! At The Disco', 0)],
    'Nobody Real': [_artist(5, 'Nobody Really', 900)],
    'Deftones': [_artist(464, 'Deftones', 3000000)],
}


class _Deezer:
    def __init__(self):
        self.limits = []

    def search_artists(self, query, limit=20):
        self.limits.append(limit)
        return DEEZER.get(query, [])[:limit]


def _match(monkeypatch, name, searched='Deftones'):
    client = _Deezer()
    monkeypatch.setattr(sa.metadata_registry, 'get_client_for_source',
                        lambda source: client)
    monkeypatch.setattr(sa, '_cached_artist_image_url', lambda url: url)
    ids = sa._resolve_musicmap_artist_source_ids(searched, ['deezer'])
    source, payload = sa._match_musicmap_similar_artist(name, ['deezer'], searched, ids)
    return client, payload


def test_a_namesake_ranked_first_loses_to_the_real_band(monkeypatch):
    for name, want in (('Cold', '318'), ('Brand New', '946'), ('Finch', '701'),
                       ('Vic Fuentes', '4627754'), ('Panic! at the Disco', '402')):
        _, payload = _match(monkeypatch, name)
        assert payload['id'] == want, name


def test_a_stylized_name_still_counts_as_the_name(monkeypatch):
    # KoЯn is the real band; the exact-spelled "Korn" is a 78-fan namesake
    _, payload = _match(monkeypatch, 'Korn')
    assert payload['id'] == '1327'


def test_a_near_spelling_is_not_a_match(monkeypatch):
    # no result IS the name: store nothing rather than somebody else
    _, payload = _match(monkeypatch, 'Nobody Real')
    assert payload is None


def test_more_than_the_first_result_is_read(monkeypatch):
    client, _ = _match(monkeypatch, 'Cold')
    assert min(client.limits) > 1


def test_the_searched_artist_resolves_by_name_too(monkeypatch):
    client = _Deezer()
    monkeypatch.setattr(sa.metadata_registry, 'get_client_for_source',
                        lambda source: client)
    assert sa._resolve_musicmap_artist_source_ids('Korn', ['deezer']) == {'deezer': '1327'}
    assert sa._resolve_musicmap_artist_source_ids('Nobody Real', ['deezer']) == {'deezer': None}
