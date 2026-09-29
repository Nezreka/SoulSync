from types import SimpleNamespace

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


def test_version_annotation_on_another_release_stays_distinct():
    assert find_owned_match(
        _db('Runaway Train', 'Grave Dancers Union'),
        'Runaway Train (2022 Remaster)', [{'name': 'Soul Asylum'}],
        'Grave Dancers Union (2022 Remaster)', 'navidrome', strict_identity=True,
    ) is None


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


def test_single_nonversion_subtitle_can_match_bare_title():
    assert find_owned_match(
        _db('Song', 'Album'), 'Song (Serenade From the Stars)',
        [{'name': 'Soul Asylum'}], 'Album', 'navidrome',
        strict_identity=True, require_album=True,
    ) is not None


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
