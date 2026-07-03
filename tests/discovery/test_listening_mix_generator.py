"""Listening Mix v2 generator (#913) — reads the scan's stored mix into Track records.

The generator is what lets the Listening Mix appear on the Sync page's SoulSync
Discovery tab + flow through the mirror/Auto-Sync pipeline like the other kinds. It
must hand back exactly what the scan stored under 'listening_recs_tracks_full', with
NO pool hydration, and degrade to empty (never raise) when there's no mix yet.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from core.personalized.generators.listening_mix import KIND, generate
from core.personalized.specs import get_registry
from core.personalized.types import PlaylistConfig


class _FakeDB:
    def __init__(self, meta):
        self._meta = meta

    def get_metadata(self, key):
        return self._meta.get(key)


def _full_track(tid, name, artist, source='spotify'):
    d = {'track_id': tid, 'name': name, 'track_name': name, 'artist_name': artist,
         'album_name': f'{name} - Album', 'album_cover_url': f'http://cdn/{tid}.jpg',
         'duration_ms': 200000, 'track_data_json': {'id': tid}, 'source': source,
         f'{source}_track_id': tid, '_seed_artist': artist}
    return d


def _deps(meta):
    return SimpleNamespace(database=_FakeDB(meta))


def test_generate_reads_stored_full_tracks():
    rows = [_full_track('s1', 'One', 'Arcangel'), _full_track('s2', 'Two', 'Maluma')]
    out = generate(_deps({'listening_recs_tracks_full': json.dumps(rows)}), '', PlaylistConfig(limit=50))
    assert [t.track_name for t in out] == ['One', 'Two']
    assert [t.artist_name for t in out] == ['Arcangel', 'Maluma']
    assert out[0].spotify_track_id == 's1'              # source id carried -> mirror can sync
    assert out[0].album_cover_url == 'http://cdn/s1.jpg'
    assert out[0].track_data_json == {'id': 's1'}       # full payload preserved for download


def test_generate_respects_limit():
    rows = [_full_track(f's{i}', f'T{i}', f'A{i}') for i in range(10)]
    out = generate(_deps({'listening_recs_tracks_full': json.dumps(rows)}), '', PlaylistConfig(limit=3))
    assert len(out) == 3


def test_generate_empty_when_no_mix_yet():
    assert generate(_deps({}), '', PlaylistConfig(limit=50)) == []
    assert generate(_deps({'listening_recs_tracks_full': ''}), '', PlaylistConfig(limit=50)) == []


def test_generate_tolerates_bad_json_and_non_dict_rows():
    assert generate(_deps({'listening_recs_tracks_full': 'not json'}), '', PlaylistConfig(limit=50)) == []
    rows = json.dumps([_full_track('s1', 'Good', 'A'), 'garbage', None])
    out = generate(_deps({'listening_recs_tracks_full': rows}), '', PlaylistConfig(limit=50))
    assert [t.track_name for t in out] == ['Good']      # junk rows skipped, valid kept


def test_generator_is_registered():
    # importing the package must register the kind so the manager + Sync tab discover it.
    import core.personalized.generators  # noqa: F401
    spec = get_registry().get(KIND)
    assert spec is not None and spec.requires_variant is False
    assert spec.display_name('') == 'Your Listening Mix'


def test_generate_supports_deezer_id_tracks():
    # iTunes/MusicBrainz users get Deezer-sourced tracks -> deezer_track_id must carry.
    rows = [_full_track('d9', 'Song', 'Artist', source='deezer')]
    out = generate(_deps({'listening_recs_tracks_full': json.dumps(rows)}), '', PlaylistConfig(limit=50))
    assert out[0].deezer_track_id == 'd9' and out[0].spotify_track_id is None
