"""Regression cases derived from five read-only Prowlarr searches.

URLs/GUIDs/indexer names/artists/albums/groups are replacements. Sizes,
release-name structure, dates and category IDs retain the observed evidence.
Adversarial cases are synthetic.
"""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.download_plugins.torrent import TorrentDownloadPlugin
from core.download_plugins.usenet_search import prowlarr_search_with_variants
from core.download_plugins.usenet import UsenetDownloadPlugin
from core.downloads.candidates import dedupe_cross_source_pool, order_candidates
from core.prowlarr_client import ProwlarrClient
from core.quality.model import QualityTarget

FIXTURE = Path(__file__).parents[1] / 'fixtures/prowlarr/release_identity.json'


def observed():
    return [ProwlarrClient()._parse_result(d) for d in json.loads(FIXTURE.read_text())]


def result(*, indexer=1, protocol='usenet', title=None, size=1706459787, **fields):
    return ProwlarrClient()._parse_result({
        'guid': f'indexer-{indexer}-release', 'indexerId': indexer,
        'indexer': f'Indexer {indexer}', 'protocol': protocol,
        'title': title or 'Example_Artist-Example_Album-24BIT-96KHZ-WEB-FLAC-2023-GROUPA',
        'size': size, 'downloadUrl': f'https://indexer-{indexer}.invalid/release',
        'categories': [{'id': 3040}], **fields,
    })


def project(rows):
    plugin = TorrentDownloadPlugin() if rows[0].protocol == 'torrent' else UsenetDownloadPlugin()
    tracks, _ = plugin._project_results(rows)
    for t in tracks:
        t.confidence = 0.9
    return tracks


def test_variants_group_real_exact_name_size_hits_and_keep_other_sizes(monkeypatch):
    import core.download_plugins.usenet_search as module
    async def search(*args, **kwargs):
        return observed()
    monkeypatch.setattr(module, '_search_query', search)
    rows = asyncio.run(prowlarr_search_with_variants(None, 'artist album', 'usenet'))
    assert len(rows) == 3  # one rounded size remains separate; two duplicate groups
    scene = next(r for r in rows if r.size == 1706459787)
    assert len(getattr(scene, '_release_sources', [])) == 1


def test_project_keeps_safe_alternative_indexer_tokens_and_original_title():
    tracks = project([r for r in observed() if r.size == 1706459787])
    assert len(tracks) == 1
    sources = [tracks[0], *getattr(tracks[0], '_release_sources', [])]
    assert {s._source_metadata['indexer_id'] for s in sources} == {3, 6}
    assert all(s.duration is None and s.filename.startswith('ssc1-') for s in sources)
    assert all(s._source_metadata['release_title'].endswith('-GROUPA') for s in sources)
    assert '.invalid' not in json.dumps([s._source_metadata for s in sources])


def test_independently_projected_hits_merge_without_duration():
    raw = [r for r in observed() if r.size == 1706459787]
    tracks = [project([r])[0] for r in raw]
    assert len(dedupe_cross_source_pool(tracks)) == 1


@pytest.mark.parametrize('change', [
    {'title': 'Example_Artist-Example_Album-24BIT-96KHZ-WEB-FLAC-2023-REPACK-GROUPA'},
    {'title': 'Example_Artist-Example_Album-24BIT-96KHZ-WEB-FLAC-2023-PROPER-GROUPA'},
    {'title': 'Example_Artist-Example_Album-16BIT-44KHZ-WEB-FLAC-2023-GROUPA'},
    {'title': 'Example_Artist-Example_Album-24BIT-192KHZ-WEB-FLAC-2023-GROUPA'},
    {'title': 'Example_Artist-Example_Album-WEB-MP3-320-2023-GROUPA'},
    {'title': 'Example_Artist-Example_Album-Deluxe-24BIT-96KHZ-WEB-FLAC-2023-GROUPA'},
    {'title': 'Example_Artist-Example_Album-24BIT-96KHZ-WEB-FLAC-2022-GROUPA'},
    {'title': 'Example_Artist-Another_Album-24BIT-96KHZ-WEB-FLAC-2023-GROUPA'},
    {'size': 1706459788}, {'size': 0},
])
def test_different_releases_of_same_group_stay_separate(change):
    tracks = project([result(), result(indexer=2, **change)])
    assert len(order_candidates(tracks, quality_first=True)) == 2


@pytest.mark.parametrize('title', ['Example Artist - Example Album', 'Example Artist - Example Album [FLAC]', ''])
def test_generic_or_missing_original_names_do_not_prove_identity(title):
    rows = [result(indexer=i) for i in (1, 2)]
    for r in rows: r.title = title
    tracks = project(rows)
    if not title:  # preserve projection tests while exercising missing original evidence
        for t in tracks: t._source_metadata['release_title'] = ''
    assert len(dedupe_cross_source_pool(tracks)) == 2


def test_protocols_stay_separate_even_when_recording_duration_is_known():
    tracks = project([result(protocol='torrent')]) + project([result(indexer=2)])
    for t in tracks: t.duration = 210000
    assert len(dedupe_cross_source_pool(tracks)) == 2










def test_best_quality_profile_rank_and_distinct_same_group_before_limit():
    rows = [result(indexer=1), result(indexer=2),
            result(indexer=3, title='Example_Artist-Example_Album-REPACK-24BIT-96KHZ-WEB-FLAC-2023-GROUPA'),
            result(indexer=4, title='Example_Artist-Example_Album-WEB-MP3-320-2023-GROUPA', categories=[{'id': 3010}])]
    tracks = [project([r])[0] for r in rows]
    targets = [QualityTarget(label='MP3 first', format='mp3', min_bitrate=320),
               QualityTarget(label='FLAC', format='flac')]
    ranked = order_candidates(tracks, quality_first=True, targets=targets)
    assert [r.quality for r in ranked] == ['mp3', 'flac', 'flac']
    assert 'REPACK' in ranked[:3][2]._source_metadata['release_title']
    assert len(getattr(ranked[1], '_release_sources', [])) == 1


def test_known_quality_conflict_on_same_name_size_stays_separate():
    tracks = [project([result(indexer=i)])[0] for i in (1, 2)]
    tracks[1].sample_rate = 192000
    assert len(dedupe_cross_source_pool(tracks)) == 2


def test_source_preference_selects_indexer_priority_and_preserves_fallbacks():
    a, b = result(indexer=1), result(indexer=2)
    a.indexer_priority, b.indexer_priority = 25, 1
    tracks = project([a, b])
    assert len(tracks) == 1
    assert tracks[0]._source_metadata['indexer_id'] == 2
    assert [s._source_metadata['indexer_id'] for s in tracks[0]._release_sources] == [1]




def test_summary_dedupes_releases_before_alternative_limit():
    from core.downloads.candidate_pool import summarize_pool
    from core.downloads.decisions import accept
    tracks = [project([result(indexer=i)])[0] for i in (1, 2, 3)]
    repack = project([result(indexer=4, title='Example_Artist-Example_Album-REPACK-WEB-FLAC-2023-GROUPA')])[0]
    pairs = [(t, accept(0.9)) for t in [*tracks, repack]]
    summary = summarize_pool(pairs, chosen_key=('usenet', tracks[0].filename), limit=1)
    assert summary['accepted_total'] == 2
    assert len(summary['alternatives']) == 1
    assert 'REPACK' in summary['alternatives'][0]['display_name']






def test_same_guid_or_releasehash_on_different_indexers_is_not_content_evidence():
    rows = [result(indexer=i, title='Example Artist - Example Album', guid='same-local-guid', releaseHash="indexer-local-release-hash")
            for i in (1, 2)]
    assert len(project(rows)) == 2


def test_repeat_dedupe_keeps_sources_once_without_mutating_cached_hits():
    tracks = [project([result(indexer=i)])[0] for i in (1, 2)]
    grouped = dedupe_cross_source_pool(tracks)
    repeated = dedupe_cross_source_pool([*grouped, *tracks])
    assert len(repeated) == 1
    assert len(repeated[0]._release_sources) == 1
    assert tracks[0]._release_sources == [] and tracks[1]._release_sources == []


def test_missing_original_title_never_uses_short_artist_album_fields():
    tracks = [project([result(indexer=i)])[0] for i in (1, 2)]
    for t in tracks:
        t._source_metadata.pop('release_title')
        t.duration = 210000
    assert len(dedupe_cross_source_pool(tracks)) == 2




@pytest.mark.parametrize('protocol', ['usenet'])
def test_missing_download_url_does_not_hide_usable_duplicate(protocol):
    broken, usable = result(protocol=protocol), result(indexer=2, protocol=protocol)
    broken.download_url = None
    broken.indexer_priority = 1
    tracks = project([broken, usable])
    assert len(tracks) == 1
    assert tracks[0]._source_metadata['indexer_id'] == 2




def test_summary_resolves_selected_nested_indexer_before_capping():
    from core.downloads.candidate_pool import summarize_pool
    from core.downloads.decisions import accept
    root = project([result(), result(indexer=2)])[0]
    repack = project([result(indexer=3, title='Example_Artist-Example_Album-REPACK-WEB-FLAC-2023-GROUPA')])[0]
    alternate = root._release_sources[0]
    summary = summarize_pool([(root, accept(0.9)), (repack, accept(0.8))],
                             chosen_key=(alternate.username, alternate.filename), limit=1)
    assert summary['chosen'] is not None
    assert summary['chosen']['quality'] == 'flac'
    assert summary['accepted_total'] == 2
    assert 'REPACK' in summary['alternatives'][0]['display_name']


@pytest.mark.parametrize('protocol', ['usenet'])
@pytest.mark.parametrize('failure', ['declined', 'fetch_error'])
def test_album_grab_fetch_failure_tries_alternate_indexer(monkeypatch, tmp_path, protocol, failure):
    from core.torrent_clients import base
    import core.download_plugins.torrent as torrent
    import core.download_plugins.usenet as usenet
    module = torrent if protocol == 'torrent' else usenet
    plugin = TorrentDownloadPlugin() if protocol == 'torrent' else UsenetDownloadPlugin()
    rows = [result(indexer=i, protocol=protocol, seeders=10) for i in (1, 2)]
    calls = []
    source = tmp_path / 'release'
    source.mkdir()
    (source / 'Song.flac').write_bytes(b'fLaC-test-payload')
    async def search(*args, **kwargs): return rows
    async def add(*args, **kwargs):
        url = args[1] if protocol == 'torrent' else args[0]
        calls.append(url)
        if url == rows[0].download_url:
            if failure == 'fetch_error': raise ConnectionError('Indexer fetch failed')
            return None
        return 'client-job'
    async def status(*args): return SimpleNamespace(name='release', content_path=str(source))
    adapter = SimpleNamespace(is_configured=lambda: True, add_nzb=add, get_status=status)
    monkeypatch.setattr(plugin, 'is_configured', lambda: True)
    monkeypatch.setattr(module, 'get_active_torrent_adapter' if protocol == 'torrent' else 'get_active_usenet_adapter', lambda: adapter)
    monkeypatch.setattr('core.download_plugins.usenet_search._search_query', search)
    monkeypatch.setattr(module, 'run_async', asyncio.run)
    monkeypatch.setattr(module, 'poll_album_download', lambda **kwargs: str(source))
    monkeypatch.setattr(module, 'profile_allowed_formats', lambda *args: None)
    monkeypatch.setattr(module, 'profile_quality_targets', lambda *args: ([], True))
    monkeypatch.setattr(base, 'add_torrent_smart', add)
    outcome = plugin.download_album_to_staging('Example Album', 'Example Artist', str(tmp_path / 'staging'))
    assert outcome['success'] is True
    assert calls == [rows[0].download_url, rows[1].download_url]
    assert Path(outcome['files'][0]).read_bytes() == b'fLaC-test-payload'
