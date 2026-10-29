"""matching / release titles contracts."""
from __future__ import annotations

import pytest
from mutagen.flac import FLAC

from core.reconciliation import search as search
from core.reconciliation.display import (
    _album_titles_compatible,
    _display_album_title,
)
from core.reconciliation.selection import (
    _single_file_album_context_conflict,
)
from core.repair_jobs.library_reconciliation import LibraryReconciliationJob
from tests.reconciliation.support.scenarios import (
    _metadata_recommendation_album,
)


def test_album_title_cleanup_keeps_edition_and_drops_pressing_plant():
    from core.reconciliation.display import _clean_album_display_title

    assert _clean_album_display_title(
        'The Fame Monster (deluxe, EDC Blackburn Ltd.)') == 'The Fame Monster (deluxe)'
    assert _clean_album_display_title('Greatest Hits (Deluxe)') == 'Greatest Hits (Deluxe)'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_album_title_keeps_clean_local_name_when_source_repeats_its_qualifier():
    assert _album_titles_compatible(
        'Katy Kirby on Audiotree Live',
        'Katy Kirby on Audiotree Live (Audiotree Live)',
    )
    assert not _album_titles_compatible('Album', 'Album (Deluxe Edition)')
    assert not _album_titles_compatible('Olive', 'Olive (live)')


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_album_title_keeps_clean_name_when_source_adds_catalog_qualifier():
    assert _album_titles_compatible('Rio', 'Rio (Columbia House)')
    assert _album_titles_compatible('Album', 'Album (EDC Blackburn Ltd.)')
    assert not _album_titles_compatible('Album', 'Album (Deluxe Edition)')
    assert _display_album_title(
        [{'tags': {'album': 'Rio'}}, {'tags': {'album': 'Rio'}}],
        'Rio (Columbia House)', 'Rio (Columbia House)',
    ) == 'Rio'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_album_title_policy_keeps_clean_display_but_can_follow_exact_release():
    files = [{'tags': {'album': 'Greatest Hits'}} for _ in range(3)]
    assert _display_album_title(files, 'Greatest Hits',
                                'Greatest Hits (Deluxe Edition)') == 'Greatest Hits'
    assert _display_album_title(files, 'Greatest Hits',
                                'Greatest Hits (Deluxe Edition)',
                                'exact_release') == 'Greatest Hits (Deluxe Edition)'


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_album_title_compatibility_preserves_edition_detail_only_for_same_album():
    assert _album_titles_compatible(
        'New Miserable Experience (Deluxe Edition)', 'New Miserable Experience')
    assert _album_titles_compatible('The Courage of Others [Deluxe]',
                                    'The Courage of Others')
    assert _album_titles_compatible('Symbolic - Dutch Mediamotion pressing', 'Symbolic')
    assert _album_titles_compatible('Album - 24 bit/96 kHz', 'Album')
    assert not _album_titles_compatible('New Miserable Experience',
                                        'New Miserable Experience (Deluxe Edition)')
    assert not _album_titles_compatible('R', 'Rated R')


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
@pytest.mark.parametrize('blank', ['', '   '])
def test_missing_album_title_does_not_disable_preservation_of_useful_title(tmp_path, monkeypatch, blank):
    db, worker, context, paths, _ = _metadata_recommendation_album(tmp_path, monkeypatch)
    for index, path in enumerate(paths):
        audio = FLAC(str(path))
        if index == 0:
            audio['album'] = ['Ashes of the Wake']
        else:
            if blank:
                audio['album'] = [blank]
            else:
                del audio['album']
        audio.save()
    # A harmless provider suffix is not a reason to rewrite the useful title
    # on the first file just because the second file lacks an album tag.
    monkeypatch.setattr('core.reconciliation.selection.get_album_for_source', lambda *_args, **_kwargs: {
        'name': 'Ashes of the Wake (Remastered)', 'artists': [{'name': 'Lamb of God'}]})
    assert not LibraryReconciliationJob().scan(context).errors
    case = worker.get_review_case(worker.get_review_cases()['items'][0]['case_id'])
    items = {item['file_path']: item for item in case['items']
             if item['finding_type'] == 'reconciliation_metadata'}
    assert 'album' not in items[str(paths[0])]['details']['changes']
    assert items[str(paths[1])]['details']['changes']['album'] == {
        'old': '', 'new': 'Ashes of the Wake'}  # The tag reader normalizes whitespace-only values.
    assert all('album' not in item['details']['album_consistency'] for item in items.values())


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_lone_track_accepts_compatible_album_title_flavor():
    album = {'album_title': 'The Truman Show (Original Motion Picture Soundtrack)'}

    assert not _single_file_album_context_conflict(album, {
        'name': 'The Truman Show: Music From the Motion Picture',
    })


@pytest.mark.usefixtures('defer_background_refresh_in_synchronous_scan_tests')
def test_nested_pressing_qualifier_uses_unanimous_clean_file_title():
    files = [{'tags': {'album': 'Precambrian'}} for _ in range(14)]
    assert _display_album_title(files, 'Precambrian (deluxe limited edition (black disc))', 'Precambrian') == 'Precambrian'
    assert _display_album_title(files, 'Precambrian (Deluxe Edition)', 'Precambrian') == 'Precambrian (Deluxe Edition)'
    assert _display_album_title(files, 'Precambrian (live (black disc))', 'Precambrian') == 'Precambrian (live (black disc))'
    files[0]['tags']['album'] = 'Precambrian (Deluxe Edition)'
    assert _display_album_title(files, 'Precambrian (deluxe limited edition (black disc))', 'Precambrian') != 'Precambrian'
