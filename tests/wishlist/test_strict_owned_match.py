from types import SimpleNamespace

import pytest

from core.wishlist.library_match import find_owned_match


def _db(title, album, artist='Soul Asylum', track_artist=None):
    row = SimpleNamespace(title=title, album_title=album, artist_name=artist,
                          track_artist=track_artist, file_path='/music/owned.flac')
    return SimpleNamespace(check_track_exists=lambda *args, **kwargs: (row, 0.99))


def test_fuzzy_song_cannot_clear_a_wishlist_request():
    assert find_owned_match(
        _db('The Sun Maid (2022 Remaster)', 'Grave Dancers Union (2022 Remaster)'),
        'Runaway Train (2022 Remaster)', [{'name': 'Soul Asylum'}],
        'Grave Dancers Union (2022 Remaster)', 'navidrome', strict_identity=True,
        require_album=True,
    ) is None


def test_album_request_requires_the_chosen_release():
    assert find_owned_match(
        _db('Chasing Cars', 'Eyes Open', artist='Snow Patrol'),
        'Chasing Cars', [{'name': 'Snow Patrol'}],
        'Up To Now', 'navidrome', strict_identity=True, require_album=True,
    ) is None


def test_spotify_punctuation_variation_still_matches():
    assert find_owned_match(
        _db('Runaway Train - 2022 Remaster', 'Grave Dancers Union (2022 Remaster)'),
        'Runaway Train (2022 Remaster)', [{'name': 'Soul Asylum'}],
        'Grave Dancers Union (2022 Remaster)', 'navidrome', strict_identity=True,
        require_album=True,
    ) is not None


def test_release_annotation_repeated_in_album_is_not_a_distinct_song():
    assert find_owned_match(
        _db('Runaway Train', 'Grave Dancers Union (2022 Remaster)'),
        'Runaway Train (2022 Remaster)', [{'name': 'Soul Asylum'}],
        'Grave Dancers Union (2022 Remaster)', 'navidrome', strict_identity=True,
        require_album=True,
    ) is not None


def test_remaster_annotation_does_not_make_a_track_wish_missing():
    assert find_owned_match(
        _db('Runaway Train', 'Grave Dancers Union'),
        'Runaway Train (2022 Remaster)', [{'name': 'Soul Asylum'}],
        'Grave Dancers Union (2022 Remaster)', 'navidrome', strict_identity=True,
    ) is not None


def test_album_fallback_cannot_accept_same_title_by_different_artist():
    assert find_owned_match(
        _db('Time', 'Greatest Hits', artist='Aril Brikha'),
        'Time', [{'name': 'Deeparture'}], 'Greatest Hits', 'navidrome',
        strict_identity=True, require_album=True,
    ) is None


def test_track_artist_takes_precedence_over_compilation_album_artist():
    assert find_owned_match(
        _db('Time', 'Greatest Hits', artist='Deeparture', track_artist='Aril Brikha'),
        'Time', [{'name': 'Deeparture'}], 'Greatest Hits', 'navidrome',
        strict_identity=True, require_album=True,
    ) is None


def test_apostrophe_wording_does_not_create_an_extra_request():
    assert find_owned_match(
        _db('Dont Let Me Down', 'Album'), "Don't Let Me Down",
        [{'name': 'Soul Asylum'}], 'Album', 'navidrome',
        strict_identity=True, require_album=True,
    ) is not None


def test_different_subtitles_cannot_clear_one_another():
    assert find_owned_match(
        _db('Song (Chorus)', 'Album'), 'Song (Verse)',
        [{'name': 'Soul Asylum'}], 'Album', 'navidrome',
        strict_identity=True, require_album=True,
    ) is None


@pytest.mark.parametrize('subtitle', [
    'Llamando a la tierra (Serenade From the Stars)',
    'Llamando a la tierra [Serenade From the Stars]',
    'Llamando a la tierra (Serenade From the Stars) - Remastered 2009',
])
@pytest.mark.parametrize('reverse', [False, True])
def test_m_clan_subtitle_does_not_reintroduce_missing_owned_track(subtitle, reverse):
    requested, owned = subtitle, 'Llamando a la tierra'
    if reverse:
        requested, owned = owned, requested
    assert find_owned_match(
        _db(owned, 'Usar y tirar', artist='M-Clan'), requested,
        [{'name': 'M-Clan'}], 'Usar y tirar', 'navidrome',
        strict_identity=True, require_album=True,
    ) is not None


def test_subtitle_compatibility_still_requires_the_requested_album():
    assert find_owned_match(
        _db('Llamando a la tierra', 'Other Album', artist='M-Clan'),
        'Llamando a la tierra (Serenade From the Stars)',
        [{'name': 'M-Clan'}], 'Usar y tirar', 'navidrome',
        strict_identity=True, require_album=True,
    ) is None


def test_unknown_album_qualifier_is_not_a_track_subtitle():
    assert find_owned_match(
        _db('Song', 'Album'), 'Song', [{'name': 'Soul Asylum'}],
        'Album (Unrelated Subtitle)', 'navidrome',
        strict_identity=True, require_album=True,
    ) is None


def test_foreign_language_version_qualifiers_are_not_dropped():
    for requested in ('Song (Versión Acústica)', 'Song (ライブ)'):
        assert find_owned_match(
            _db('Song', 'Album'), requested,
            [{'name': 'Soul Asylum'}], 'Album', 'navidrome',
            strict_identity=True, require_album=True,
        ) is None


def test_artist_punctuation_uses_existing_library_identity_key():
    assert find_owned_match(
        _db('Song', 'Album', artist='AC/DC'), 'Song',
        [{'name': 'ACDC'}], 'Album', 'navidrome',
        strict_identity=True, require_album=True,
    ) is not None


def test_punctuation_only_titles_do_not_share_an_empty_identity_key():
    assert find_owned_match(
        _db('&', 'Album'), '-', [{'name': 'Soul Asylum'}],
        'Album', 'navidrome', strict_identity=True, require_album=True,
    ) is None
    assert find_owned_match(
        _db('-', 'Album'), '-', [{'name': 'Soul Asylum'}],
        'Album', 'navidrome', strict_identity=True, require_album=True,
    ) is not None


def test_wrong_release_winner_does_not_hide_owned_copy_on_requested_album():
    wrong_release = SimpleNamespace(
        title='Chasing Cars', album_title='Eyes Open', artist_name='Snow Patrol',
        track_artist=None, file_path='/music/eyes-open.flac', server_source='navidrome')
    chosen_release = SimpleNamespace(
        title='Chasing Cars', album_title='Up To Now', artist_name='Snow Patrol',
        track_artist=None, file_path='/music/up-to-now.flac', server_source='navidrome')
    calls = []
    db = SimpleNamespace(
        check_track_exists=lambda *args, **kwargs: (wrong_release, 1.0),
        search_albums=lambda **kwargs: calls.append(('albums', kwargs)) or [
            SimpleNamespace(id='album-1', title='Up To Now')],
        get_candidate_tracks_for_albums=lambda ids: calls.append(('tracks', ids)) or [chosen_release],
    )

    match = find_owned_match(
        db, 'Chasing Cars', [{'name': 'Snow Patrol'}], 'Up To Now',
        'navidrome', strict_identity=True, require_album=True,
    )

    assert match == (chosen_release, 1.0, 'Snow Patrol')
    assert calls[0][0] == 'albums'
    assert calls[0][1]['title'] == 'Up To Now'
    assert calls[0][1]['server_source'] == 'navidrome'
    assert calls[1] == ('tracks', ['album-1'])


def test_valid_first_match_does_not_run_album_fallback():
    owned = SimpleNamespace(title='Song', album_title='Album', artist_name='Artist',
                            track_artist=None, file_path='/music/song.flac')
    db = SimpleNamespace(
        check_track_exists=lambda *args, **kwargs: (owned, 1.0),
        search_albums=lambda **kwargs: (_ for _ in ()).throw(AssertionError('unneeded album query')),
    )
    assert find_owned_match(
        db, 'Song', [{'name': 'Artist'}], 'Album', 'navidrome',
        strict_identity=True, require_album=True,
    ) is not None


def test_missing_fuzzy_match_does_not_run_album_fallback():
    db = SimpleNamespace(
        check_track_exists=lambda *args, **kwargs: (None, 0.0),
        search_albums=lambda **kwargs: (_ for _ in ()).throw(AssertionError('unneeded album query')),
    )
    assert find_owned_match(
        db, 'Song', [{'name': 'Artist'}], 'Album', 'navidrome',
        strict_identity=True, require_album=True,
    ) is None


def test_album_fallback_checks_per_track_artist_and_server():
    wrong_release = SimpleNamespace(title='Time', album_title='Elsewhere',
                                    artist_name='Deeparture', track_artist=None,
                                    file_path='/music/other.flac')
    wrong_artist = SimpleNamespace(title='Time', album_title='Time',
                                   artist_name='Deeparture', track_artist='Aril Brikha',
                                   file_path='/music/wrong-artist.flac', server_source='navidrome')
    wrong_server = SimpleNamespace(title='Time', album_title='Time',
                                   artist_name='Deeparture', track_artist='Deeparture',
                                   file_path='/music/wrong-server.flac', server_source='plex')
    db = SimpleNamespace(
        check_track_exists=lambda *args, **kwargs: (wrong_release, 1.0),
        search_albums=lambda **kwargs: [SimpleNamespace(id='album-1', title='Time')],
        get_candidate_tracks_for_albums=lambda ids: [wrong_artist, wrong_server],
    )
    assert find_owned_match(
        db, 'Time', [{'name': 'Deeparture'}], 'Time', 'navidrome',
        strict_identity=True, require_album=True,
    ) is None


@pytest.mark.parametrize(('first', 'second'), [
    ('Come Together - Remastered 2009', 'Come Together'),
    ('Stay', 'Stay (feat. Justin Bieber)'),
    ('Stay', 'Stay feat. Justin Bieber'),
    ('Blinding Lights', 'Blinding Lights (Single Version)'),
    ('Song (2011 Remaster)', 'Song (Remastered 2009)'),
    ('Song (Deluxe Edition)', 'Song'),
    ('Song (Deluxe)', 'Song'),
    ('Song (Live)', 'Song - Live'),
    ('Song (Live) - Remastered 2011', 'Song - Live'),
    ('Live Forever', 'Live Forever (2011 Remaster)'),
    ('[Rhubarb] - Remastered 2011', 'Rhubarb'),
    ('[Live] - Remastered 2011', 'Live'),
    ('Song (Pt. 1)', 'Song, Pt. 1'),
    ('Song (feat. Live)', 'Song'),
])
def test_harmless_title_annotations_match_in_both_directions(first, second):
    for requested, owned in ((first, second), (second, first)):
        assert find_owned_match(_db(owned, 'Album'), requested, ['Soul Asylum'], 'Album',
                                'navidrome', strict_identity=True, require_album=True) is not None


@pytest.mark.parametrize('qualifier', ['Live', 'Acoustic', 'Remix', 'Demo', 'Instrumental'])
def test_recording_version_asymmetry_is_not_ownership(qualifier):
    for requested, owned in (('Song', f'Song ({qualifier})'), (f'Song - {qualifier}', 'Song')):
        assert find_owned_match(_db(owned, 'Album'), requested, ['Soul Asylum'], 'Album',
                                'navidrome', strict_identity=True, require_album=True) is None


@pytest.mark.parametrize(('first', 'second'), [
    ('Song (Pt. 1)', 'Song (Pt. 2)'),
    ('Song (Part I)', 'Song (Part II)'),
    ('Song (Interlude)', 'Song'),
    ('Song (1977)', 'Song (1978)'),
    ('Song (Verse)', 'Song (Chorus)'),
    ('Runaway Train', 'The Sun Maid'),
    ('Song (Live 1977)', 'Song (Live 1978)'),
    ('Song (Alice Remix)', 'Song (Bob Remix)'),
])
def test_normalization_does_not_erase_distinct_song_identity(first, second):
    assert find_owned_match(_db(second, 'Album'), first, ['Soul Asylum'], 'Album',
                            'navidrome', strict_identity=True, require_album=True) is None


@pytest.mark.parametrize(('requested', 'owned'), [
    ('Abbey Road (Remastered 2009)', 'Abbey Road'),
    ('Abbey Road', 'Abbey Road - Remastered 2009'),
    ('Abbey Road [Deluxe Edition]', 'Abbey Road'),
    ('Abbey Road (50th Anniversary)', 'Abbey Road'),
])
def test_album_edition_annotations_do_not_force_duplicate_downloads(requested, owned):
    assert find_owned_match(_db('Song', owned), 'Song', ['Soul Asylum'], requested,
                            'navidrome', strict_identity=True, require_album=True) is not None


@pytest.mark.parametrize('owned_album', ['Elsewhere', 'Album (Live)', 'Album (Part II)'])
def test_album_normalization_retains_release_identity(owned_album):
    assert find_owned_match(_db('Song', owned_album), 'Song', ['Soul Asylum'], 'Album',
                            'navidrome', strict_identity=True, require_album=True) is None


@pytest.mark.parametrize('credit', ['Lil Nas X', 'Lil Nas X ', 'Lil Nas X & Billy Ray Cyrus',
                                   'Lil Nas X feat. Billy Ray Cyrus'])
def test_artist_name_ending_in_x_is_preserved_in_a_credit(credit):
    assert find_owned_match(_db('Old Town Road', 'Album', artist=credit), 'Old Town Road',
                            ['Lil Nas X'], 'Album', 'navidrome', strict_identity=True) is not None
    assert find_owned_match(_db('Old Town Road', 'Album', artist=credit), 'Old Town Road',
                            ['Lil Nas'], 'Album', 'navidrome', strict_identity=True) is None


def test_explicit_x_collaboration_still_matches():
    assert find_owned_match(_db('Song', 'Album', artist='Artist A x Artist B'), 'Song',
                            ['Artist B'], 'Album', 'navidrome', strict_identity=True) is not None


@pytest.mark.parametrize('edition', ['(Remastered 2009)', '[Deluxe Edition]', '- 2011 Remaster'])
@pytest.mark.parametrize('album', ["Don't Look Back", 'Foo - Bar'])
def test_album_fallback_searches_the_base_name_without_losing_punctuation(edition, album):
    owned = SimpleNamespace(title='Song', album_title=album, artist_name='Artist',
                            file_path='/music/song.flac')
    wrong_release = SimpleNamespace(title='Song', album_title='Elsewhere', artist_name='Artist')
    db = SimpleNamespace(
        check_track_exists=lambda *args, **kwargs: (wrong_release, 1.0),
        # search_albums is a substring query; an edition absent from the DB
        # title must not prevent this candidate from being retrieved.
        search_albums=lambda **kw: [SimpleNamespace(id='owned', title=album)]
        if kw['title'].lower() in album.lower() else [],
        get_candidate_tracks_for_albums=lambda ids: [owned],
    )
    assert find_owned_match(db, 'Song', ['Artist'], f'{album} {edition}', 'navidrome',
                            strict_identity=True, require_album=True) == (owned, 1.0, 'Artist')


@pytest.mark.parametrize(('requested', 'owned'), [('The Beatles', 'Beatles'), ('Beatles', 'The Beatles')])
def test_artist_check_keeps_existing_leading_the_equivalence(requested, owned):
    assert find_owned_match(_db('Song', 'Album', artist=owned), 'Song', [requested], 'Album',
                            'navidrome', strict_identity=True) is not None


# ---------------------------------------------------------------------------
# wishlist_row_requires_album (#1447)
# ---------------------------------------------------------------------------

from core.wishlist.library_match import ALBUM_SCOPED_SOURCE_TYPES, wishlist_row_requires_album


@pytest.mark.parametrize('source_type', ['album', 'discography', 'watchlist', 'watchlist_label'])
def test_wishlist_row_requires_album_for_album_scoped_source_types(source_type):
    assert wishlist_row_requires_album({
        'source_type': source_type,
        'album': {'name': 'Requested Album'},
    }) is True


def test_wishlist_row_requires_album_also_accepts_bare_album_string():
    assert wishlist_row_requires_album({
        'source_type': 'watchlist',
        'album': 'Requested Album',
    }) is True


@pytest.mark.parametrize('source_type', ['discography', 'watchlist', 'watchlist_label'])
@pytest.mark.parametrize('album', [None, {}, {'name': ''}, ''])
def test_wishlist_row_without_an_album_name_keeps_old_behavior(source_type, album):
    # Album-less rows must never get stuck: they keep the old behavior.
    assert wishlist_row_requires_album({'source_type': source_type, 'album': album}) is False


@pytest.mark.parametrize('source_type', ['playlist', 'unknown', 'manual', 'enhance', None, ''])
def test_wishlist_row_requires_album_false_for_track_scoped_rows(source_type):
    assert wishlist_row_requires_album({
        'source_type': source_type,
        'album': {'name': 'Some Album'},
    }) is False


def test_wishlist_row_requires_album_tolerates_missing_track():
    assert wishlist_row_requires_album(None) is False
    assert wishlist_row_requires_album({}) is False


def test_album_scoped_source_types_inventory():
    assert ALBUM_SCOPED_SOURCE_TYPES == frozenset({'album', 'discography', 'watchlist', 'watchlist_label'})
