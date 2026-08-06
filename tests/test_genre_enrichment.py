from core.genre_filter import _normalize_for_match
from core.metadata.genre_enrichment import propose_genres, translate_genre


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
