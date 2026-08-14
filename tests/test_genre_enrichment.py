import json

from core.genre_filter import _normalize_for_match, filter_genres
from core.metadata.genre_enrichment import (
    collect_cached_candidates,
    collect_local_candidates,
    propose_genres,
    translate_genre,
)
from core.repair_worker import RepairWorker, job_category
from database.music_database import MusicDatabase


def test_provider_aliases_are_exact_matches():
    whitelist = ['Hip Hop', 'R&B']
    assert translate_genre('hip-hop', whitelist)['matched_genre'] == 'Hip Hop'
    assert translate_genre('HipHop', whitelist)['matched_genre'] == 'Hip Hop'
    assert translate_genre('RnB', whitelist)['matched_genre'] == 'R&B'
    assert _normalize_for_match('  R‑B  ') == 'r and b'


def test_conservative_match_records_ambiguous_and_rejected():
    whitelist = ['Alternative Rock', 'Indie Rock']
    ambiguous = translate_genre('alternative ro', whitelist)
    assert ambiguous['status'] == 'ambiguous'
    assert translate_genre('zzzz unrelated', whitelist)['status'] == 'rejected'


def test_ranking_preserves_existing_and_caps_additions():
    proposal = propose_genres(
        ['Rock'],
        [{'raw_genre': 'hip-hop', 'source': 'spotify'},
         {'raw_genre': 'hip-hop', 'source': 'discogs'},
         {'raw_genre': 'indie rock', 'source': 'lastfm'}],
        ['Rock', 'Hip Hop', 'Indie Rock'], 2)
    assert proposal['proposed_genres'] == ['Rock', 'Hip Hop']
    assert proposal['sources']['Hip Hop'] == ['discogs', 'spotify']
    over = propose_genres(['Rock', 'Hip Hop', 'Indie Rock'],
                          [{'raw_genre': 'pop', 'source': 'spotify'}],
                          ['Rock', 'Hip Hop', 'Indie Rock', 'Pop'], 2)
    assert over['proposed_genres'] == over['original_genres']
    assert over['omitted_due_to_cap'] == ['Pop']


def test_deezer_genre_id_is_mapped():
    from core.metadata.genre_enrichment import extract_provider_genres
    assert extract_provider_genres('deezer', 'album', {'genre_id': 73}) == ['Metal']


def test_deezer_library_id_is_used_for_cache_lookup():
    from core.metadata.genre_enrichment import collect_cached_candidates

    class Cache:
        def __init__(self): self.calls = []
        def get_entity(self, *args):
            self.calls.append(args)
            return {'genres': ['Metal']} if args == ('deezer', 'artist', '73') else None

    cache = Cache()
    collect_cached_candidates(cache, {'deezer_id': '73'}, 'artist')
    assert ('deezer', 'artist', '73') in cache.calls


def test_lastfm_local_candidates_do_not_require_a_lastfm_id_column():
    candidates = collect_local_candidates({
        'lastfm_tags': json.dumps(['indie rock']),
        'lastfm_id': 'must-not-be-read',
    })
    assert candidates == [{
        'raw_genre': 'indie rock',
        'source': 'lastfm',
        'source_entity_id': None,
        'origin': 'library',
    }]


def test_library_v2_provider_ids_and_enrichment_payload_are_collected():
    row = {
        'spotify_id': 'sp-artist',
        'external_ids': json.dumps({'discogs': 'dg-artist'}),
        'enrichment': json.dumps({
            'discogs': {'genres': ['Electronic'], 'styles': ['Trip Hop']},
            'lastfm': {'tags': ['Downtempo']},
        }),
    }
    assert collect_local_candidates(row) == [
        {'raw_genre': 'Electronic', 'source': 'discogs',
         'source_entity_id': 'dg-artist', 'origin': 'library'},
        {'raw_genre': 'Trip Hop', 'source': 'discogs',
         'source_entity_id': 'dg-artist', 'origin': 'library'},
        {'raw_genre': 'Downtempo', 'source': 'lastfm',
         'source_entity_id': None, 'origin': 'library'},
    ]

    class Cache:
        def get_entity(self, source, entity_type, source_id):
            if (source, entity_type, source_id) == ('spotify', 'artist', 'sp-artist'):
                return {'genres': ['Rock']}
            return None

    candidates, hits = collect_cached_candidates(Cache(), row, 'artist')
    assert hits == 1
    assert candidates[0]['raw_genre'] == 'Rock'


class _GenreConfig:
    def __init__(self, enabled, genres=None):
        self.values = {
            'genre_whitelist.enabled': enabled,
            'genre_whitelist.genres': genres,
        }

    def get(self, key, default=None):
        return self.values.get(key, default)


def test_strict_filter_keeps_existing_case_and_whitespace_matches():
    assert filter_genres([' rock ', 'R&B', 'unlisted'],
                         _GenreConfig(True, ['Rock', 'R&B'])) == [' rock ', 'R&B']
    assert filter_genres(['unlisted', 'Rock'],
                         _GenreConfig(False, ['Rock'])) == ['unlisted', 'Rock']


def test_ambiguous_only_fix_does_not_report_success(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    with db._get_connection() as conn:
        artist_id = conn.execute(
            "INSERT INTO lib2_artists (name, genres) VALUES (?, ?) RETURNING id",
            ('Artist', json.dumps(['Rock'])),
        ).fetchone()[0]
        conn.commit()

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    result = worker._fix_genre_enrichment(
        'artist', f'lib2:{artist_id}', None,
        {'added_genres': [], 'ambiguous_genres': [{'raw': 'alt rock'}]},
    )

    assert result['success'] is False
    with db._get_connection() as conn:
        assert json.loads(conn.execute(
            "SELECT genres FROM lib2_artists WHERE id = ?", (artist_id,)
        ).fetchone()['genres']) == ['Rock']


def test_genre_fix_updates_library_v2_and_refuses_a_legacy_subject(tmp_path):
    db = MusicDatabase(str(tmp_path / 'music.db'))
    with db._get_connection() as conn:
        artist_id = conn.execute(
            "INSERT INTO lib2_artists (name, genres) VALUES (?, ?) RETURNING id",
            ('Artist', json.dumps(['Rock'])),
        ).fetchone()[0]
        conn.commit()

    worker = RepairWorker.__new__(RepairWorker)
    worker.db = db
    applied = worker._fix_genre_enrichment(
        'artist', f'lib2:{artist_id}', None, {'added_genres': ['Hip Hop']})
    stale = worker._fix_genre_enrichment(
        'artist', str(artist_id), None, {'added_genres': ['Pop']})

    assert applied == {'success': True, 'action': 'genres_applied'}
    assert stale['success'] is False
    assert stale['stale_subject'] is True
    with db._get_connection() as conn:
        assert json.loads(conn.execute(
            "SELECT genres FROM lib2_artists WHERE id = ?", (artist_id,)
        ).fetchone()['genres']) == ['Rock', 'Hip Hop']


def test_genre_enrichment_belongs_to_tags_and_metadata_category():
    assert job_category('genre_enrichment') == 'Tags & metadata'
