"""Provider-backed discovery repairs generic search context without replacing pins."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from mutagen.flac import FLAC

from core.reconciliation.discovery import search_release_candidates
from tests.reconciliation.support.acceptance import build_album, scan_case


@pytest.mark.parametrize('locked', [False, True])
def test_live_album_discovery_uses_supported_provider_artist_and_preserves_pin(tmp_path, monkeypatch, locked):
    album = build_album(tmp_path, monkeypatch, 'supported')
    title = 'Awake The Best Of Live'
    with album.db._get_connection() as conn:
        conn.execute("UPDATE artists SET name='Various Artists' WHERE id=1")
        conn.execute('UPDATE albums SET title=?,canonical_source=\'deezer\',canonical_album_id=\'provider-release\',canonical_locked=?', (title, int(locked)))
    for path, slot in zip(album.paths, album.slots):
        tags = FLAC(str(path))
        tags['artist'] = tags['albumartist'] = ['Līve']
        tags['album'] = [title]
        del tags['musicbrainz_albumid']
        tags.save()
        album.context.acoustid_client.matches[path.name]['artist'] = 'Līve'
        slot['artists'] = [{'name':'LIVE'}]
    calls = []
    def search(name, *, artist_name, limit):
        calls.append((name, artist_name, limit))
        return [{'id':'mb-edition','title':title,'score':100}]
    album.context.mb_client = SimpleNamespace(search_release=search, get_recording=lambda *a, **k: {})
    monkeypatch.setattr('core.repair_jobs.library_reconciliation._search_release_candidates', search_release_candidates)
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda source, release, **kw: {
        'name':title,'artists':[{'name':'LIVE'}], 'media':[{'format':'Digital Media'}],
        **({'musicbrainz_release_id':'mb-edition'} if source=='musicbrainz' else {})})
    monkeypatch.setattr('core.reconciliation.selection.get_album_tracks_for_source', lambda *a, **k: deepcopy(album.slots))
    case = scan_case(album)
    assert calls == [(title,'LIVE',4)]
    plan = case['album_plan']
    assert any(row['source']=='musicbrainz' and row['release_id']=='mb-edition' for row in plan['candidates'])
    if locked:
        assert (plan['source'],plan['release_id']) == ('deezer','provider-release')
    assert album.db.get_album_canonical('10')['source'] == 'deezer'
