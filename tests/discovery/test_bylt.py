"""Because You Listen To: seed identity, selection, allocation.

every test here traces to a finding in
docs/DISCOVER_STATIONS_AND_BYLT_ISSUES.md. the fixture at the bottom is the
observed failure itself - a Halogen album that filled two shelves with 90%
identical tracks - and it must not be reproducible.
"""

import pytest

from core.discovery.bylt import (
    EDGE_LEGACY_AMBIGUOUS,
    EDGE_LEGACY_PROVABLE,
    EDGE_NO_MATCH,
    EDGE_PROVIDER_MATCH,
    Candidate,
    SeedIdentity,
    allocate_shelves,
    build_generation,
    candidate_from_row,
    classify_edge,
    collect_candidates,
    collect_identities,
    genre_document_counts,
    genre_specificity,
    normalize_title,
    presentation_for,
    recording_key,
    related_from_edges,
    related_from_genres,
    section_from_shelf,
    seed_identities,
    select_shelf,
    shelf_overlap,
    shelf_reason,
    validate_generation,
)


def _row(track_id, artist, title, album='Album'):
    return {'track_id': track_id, 'track_name': title, 'artist_name': artist,
            'album_name': album, 'duration_ms': 180000, 'source': 'deezer'}


def _cand(track_id, artist, title, album='Album', score=1.5, seed='s'):
    return candidate_from_row(_row(track_id, artist, title, album), seed,
                              'direct', artist, score)


# ── B01: seed identity ──────────────────────────────────────────────────────


def test_identities_come_from_the_catalogue_not_only_the_watchlist():
    # the reported seeds are NOT watchlisted. the old lookup only read the
    # watchlist, so neither could ever resolve an edge.
    by_name, _ = collect_identities(
        artist_rows=[{'name': 'Katy Perry', 'deezer_id': '111'}],
        watchlist_rows=[])
    seeds = seed_identities(['Katy Perry'], by_name)
    assert seeds[0].ids == (('deezer', '111'),)
    assert seeds[0].key == 'deezer:111'


def test_watchlist_ids_are_additive_not_a_prerequisite():
    by_name, _ = collect_identities(
        artist_rows=[{'name': 'Ariana Grande', 'deezer_id': '222'}],
        watchlist_rows=[{'artist_name': 'Ariana Grande', 'spotify_artist_id': 'sp9'}])
    seeds = seed_identities(['Ariana Grande'], by_name)
    assert ('deezer', '222') in seeds[0].ids
    assert ('spotify', 'sp9') in seeds[0].ids


def test_a_seed_with_no_ids_still_gets_an_identity():
    seeds = seed_identities(['Nobody'], {})
    assert seeds[0].key == 'name:nobody'
    assert seeds[0].ids == ()


def test_seed_identity_is_never_an_ordinal():
    by_name, _ = collect_identities([{'name': 'A', 'deezer_id': '1'},
                                     {'name': 'B', 'deezer_id': '2'}], [])
    keys = [s.key for s in seed_identities(['B', 'A'], by_name)]
    # swapping the play ranking must not swap identities
    assert keys == ['deezer:2', 'deezer:1']


def test_provider_pairs_never_cross_match():
    # the exact collision the review calls out: one bare id, two providers
    by_name, ownership = collect_identities(
        [{'name': 'Katy Perry', 'deezer_id': '111'},
         {'name': 'Collider', 'itunes_artist_id': '111'}], [])
    katy = seed_identities(['Katy Perry'], by_name)[0]
    itunes_edge = {'source_artist_id': '111', 'source_provider': 'itunes',
                   'similar_artist_name': 'Wrong'}
    deezer_edge = {'source_artist_id': '111', 'source_provider': 'deezer',
                   'similar_artist_name': 'Right'}
    assert classify_edge(itunes_edge, katy, ownership) == EDGE_NO_MATCH
    assert classify_edge(deezer_edge, katy, ownership) == EDGE_PROVIDER_MATCH


def test_legacy_edge_is_usable_only_when_its_origin_is_provable():
    by_name, ownership = collect_identities(
        [{'name': 'Katy Perry', 'deezer_id': '111'}], [])
    katy = seed_identities(['Katy Perry'], by_name)[0]
    legacy = {'source_artist_id': '111', 'source_provider': None,
              'similar_artist_name': 'Halogen'}
    assert classify_edge(legacy, katy, ownership) == EDGE_LEGACY_PROVABLE


def test_ambiguous_legacy_edge_is_unusable_rather_than_guessed():
    by_name, ownership = collect_identities(
        [{'name': 'Katy Perry', 'deezer_id': '111'},
         {'name': 'Collider', 'itunes_artist_id': '111'}], [])
    katy = seed_identities(['Katy Perry'], by_name)[0]
    legacy = {'source_artist_id': '111', 'source_provider': None,
              'similar_artist_name': 'Halogen'}
    assert classify_edge(legacy, katy, ownership) == EDGE_LEGACY_AMBIGUOUS


def test_two_seeds_keep_their_own_edges():
    by_name, ownership = collect_identities(
        [{'name': 'A', 'deezer_id': '1'}, {'name': 'B', 'deezer_id': '2'}], [])
    a, b = seed_identities(['A', 'B'], by_name)
    edges = [
        {'source_artist_id': '1', 'source_provider': 'deezer',
         'similar_artist_name': 'Only A', 'similarity_rank': 1},
        {'source_artist_id': '2', 'source_provider': 'deezer',
         'similar_artist_name': 'Only B', 'similarity_rank': 1},
    ]
    a_rel, a_counts = related_from_edges(a, edges, ownership)
    b_rel, _ = related_from_edges(b, edges, ownership)
    assert [r['name'] for r in a_rel] == ['Only A']
    assert [r['name'] for r in b_rel] == ['Only B']
    assert a_counts[EDGE_PROVIDER_MATCH] == 1 and a_counts[EDGE_NO_MATCH] == 1


def test_edge_counts_report_what_was_discarded():
    by_name, ownership = collect_identities(
        [{'name': 'A', 'deezer_id': '1'}, {'name': 'C', 'itunes_artist_id': '1'}], [])
    a = seed_identities(['A'], by_name)[0]
    _, counts = related_from_edges(
        a, [{'source_artist_id': '1', 'similar_artist_name': 'X', 'similarity_rank': 1}],
        ownership)
    assert counts[EDGE_LEGACY_AMBIGUOUS] == 1


def test_closer_edges_outrank_far_ones():
    by_name, ownership = collect_identities([{'name': 'A', 'deezer_id': '1'}], [])
    a = seed_identities(['A'], by_name)[0]
    edges = [{'source_artist_id': '1', 'source_provider': 'deezer',
              'similar_artist_name': 'Far', 'similarity_rank': 10},
             {'source_artist_id': '1', 'source_provider': 'deezer',
              'similar_artist_name': 'Near', 'similarity_rank': 1}]
    related, _ = related_from_edges(a, edges, ownership)
    assert [r['name'] for r in related] == ['Near', 'Far']


# ── B02: genre evidence ─────────────────────────────────────────────────────


def test_generic_genres_are_weak_evidence():
    genres = {f'a{i}': {'pop'} for i in range(20)}
    genres['a0'] = {'pop', 'shoegaze'}
    genres['a1'] = {'pop', 'shoegaze'}
    counts = genre_document_counts(genres)
    pop = genre_specificity('pop', counts, len(genres))
    shoegaze = genre_specificity('shoegaze', counts, len(genres))
    assert shoegaze > pop
    assert pop < 0.1     # 'pop' on the whole catalogue says almost nothing


def test_unknown_genre_scores_zero():
    assert genre_specificity('nothing', {}, 10) == 0.0
    assert genre_specificity('', {'': 3}, 10) == 0.0


def test_genre_fallback_never_outranks_a_direct_relationship():
    # a real corpus: 'shoegaze' is specific only because most artists lack it
    genres = {f'filler{i}': {'pop'} for i in range(30)}
    genres.update({'seed': {'shoegaze'}, 'cand': {'shoegaze'}})
    counts = genre_document_counts(genres)
    seed = SeedIdentity(name='seed', ids=(('deezer', '1'),))
    genre_rel = related_from_genres(seed, genres, ['cand'], counts)
    direct, _ = related_from_edges(
        seed,
        [{'source_artist_id': '1', 'source_provider': 'deezer',
          'similar_artist_name': 'Other', 'similarity_rank': 10}],
        {'1': {'seed'}})
    assert genre_rel and direct
    assert direct[0]['weight'] > genre_rel[0]['weight']


# ── B02: selection ──────────────────────────────────────────────────────────


def test_one_album_cannot_monopolise_a_shelf():
    # the captured pattern: 8 of 10 tracks from Halogen's 'Baked'
    cands = [_cand(f'h{i}', 'Halogen', f'Track {i}', 'Baked') for i in range(12)]
    picked = select_shelf(cands)
    assert len(picked) == 1              # one album, one track
    assert {c.artist for c in picked} == {'Halogen'}


def test_artist_cap_holds_across_albums():
    cands = ([_cand(f'a{i}', 'Halogen', f'T{i}', f'Album {i}') for i in range(5)]
             + [_cand(f'b{i}', 'Drama', f'D{i}', f'D Album {i}') for i in range(5)])
    picked = select_shelf(cands)
    assert sum(1 for c in picked if c.artist == 'Halogen') == 2
    assert sum(1 for c in picked if c.artist == 'Drama') == 2


def test_selection_is_stable_for_the_same_inputs():
    cands = [_cand(f'x{i}', f'Artist {i}', f'T{i}', f'Al{i}', score=1.0)
             for i in range(20)]
    first = [c.track_id for c in select_shelf(cands)]
    second = [c.track_id for c in select_shelf(list(reversed(cands)))]
    assert first == second


def test_remixes_are_not_collapsed_into_the_original():
    a = recording_key('Ruth B.', 'If By Chance')
    b = recording_key('Ruth B.', 'If By Chance (slowed + reverb)')
    assert a != b
    assert normalize_title('  If   By Chance ') == 'if by chance'


def test_the_same_recording_is_taken_once():
    cands = [_cand('1', 'A', 'Song', 'One'), _cand('2', 'A', 'song', 'Two')]
    assert len(select_shelf(cands)) == 1


def test_per_related_artist_budget_bounds_a_flooded_pool():
    seed = SeedIdentity(name='S', ids=(('deezer', '1'),))
    pool = {'halogen': [_row(f'h{i}', 'Halogen', f'T{i}', 'Baked') for i in range(40)],
            'drama': [_row(f'd{i}', 'Drama', f'D{i}', 'Nine') for i in range(3)]}
    related = [{'name': 'Halogen', 'weight': 2.0, 'relation': 'direct', 'detail': 'Halogen'},
               {'name': 'Drama', 'weight': 1.9, 'relation': 'direct', 'detail': 'Drama'}]
    cands = collect_candidates(seed, related, pool)
    assert sum(1 for c in cands if c.artist == 'Halogen') == 6
    assert sum(1 for c in cands if c.artist == 'Drama') == 3


# ── B02: presentation, never padding ────────────────────────────────────────


def test_a_thin_shelf_is_compact_not_full():
    picked = [_cand(str(i), f'A{i}', 'T', f'Al{i}') for i in range(4)]
    assert presentation_for(picked) == 'compact'


def test_one_card_is_insufficient_not_a_shelf():
    assert presentation_for([_cand('1', 'A', 'T')]) == 'insufficient'
    assert presentation_for([]) == 'insufficient'


def test_a_full_shelf_needs_both_size_and_diversity():
    diverse = [_cand(str(i), f'A{i}', 'T', f'Al{i}') for i in range(10)]
    assert presentation_for(diverse) == 'full'
    narrow = [_cand(str(i), 'A' if i < 8 else f'B{i}', 'T', f'Al{i}') for i in range(9)]
    assert presentation_for(narrow) == 'compact'


# ── B02: cross-shelf allocation ─────────────────────────────────────────────


def test_two_seeds_do_not_produce_near_identical_shelves():
    """The captured defect: 9 of 10 tracks shared, Jaccard about 0.82."""
    shared = [_row(f's{i}', f'Shared {i}', f'T{i}', f'Album {i}') for i in range(12)]
    katy = SeedIdentity(name='Katy Perry', ids=(('deezer', '111'),))
    ari = SeedIdentity(name='Ariana Grande', ids=(('deezer', '222'),))
    k_only = [_row(f'k{i}', f'Katy Only {i}', f'T{i}', f'K Album {i}') for i in range(6)]
    a_only = [_row(f'a{i}', f'Ari Only {i}', f'T{i}', f'A Album {i}') for i in range(6)]
    k_c = [candidate_from_row(r, katy.key, 'direct', r['artist_name'], 2.0 - i * 0.01)
           for i, r in enumerate(shared + k_only)]
    a_c = [candidate_from_row(r, ari.key, 'direct', r['artist_name'], 1.5 - i * 0.01)
           for i, r in enumerate(shared + a_only)]
    shelves = allocate_shelves([(katy, k_c), (ari, a_c)])
    assert shelf_overlap(shelves[0].selected, shelves[1].selected) == 0
    # katy scores higher, so she claims the shared pool first; ariana keeps her
    # own six and takes only what is genuinely left over
    assert len(shelves[0].selected) == 10
    assert len(shelves[1].selected) == 8


def test_a_shared_candidate_goes_to_its_strongest_seed():
    row = _row('one', 'Halogen', 'Millicent', 'Baked')
    a = SeedIdentity(name='A', ids=(('deezer', '1'),))
    b = SeedIdentity(name='B', ids=(('deezer', '2'),))
    shelves = allocate_shelves([
        (a, [candidate_from_row(row, a.key, 'direct', 'Halogen', 1.2)]),
        (b, [candidate_from_row(row, b.key, 'direct', 'Halogen', 1.9)]),
    ])
    assert [c.track_id for c in shelves[0].selected] == []
    assert [c.track_id for c in shelves[1].selected] == ['one']


def test_a_shelf_is_never_filled_by_repeating_another_one():
    # the one-card Ariana shelf: nothing of its own, so it gets nothing, and
    # the generation drops it rather than repeating the shelf above it.
    rows = [_row(f's{i}', f'Shared {i}', 'T', f'Album {i}') for i in range(4)]
    a = SeedIdentity(name='A', ids=(('deezer', '1'),))
    b = SeedIdentity(name='B', ids=(('deezer', '2'),))
    shelves = allocate_shelves([
        (a, [candidate_from_row(r, a.key, 'direct', r['artist_name'], 1.9)
             for r in rows]),
        (b, [candidate_from_row(r, b.key, 'direct', r['artist_name'], 1.2)
             for r in rows]),
    ])
    assert shelf_overlap(shelves[0].selected, shelves[1].selected) == 0
    assert shelves[1].selected == []
    gen = build_generation([section_from_shelf(sh) for sh in shelves],
                           profile_id=1, source='deezer',
                           generation_id='g', generated_at='now')
    assert [s['seed_name'] for s in gen['sections']] == ['A']


def test_diagnostics_record_the_funnel():
    a = SeedIdentity(name='A', ids=(('deezer', '1'),))
    cands = [_cand(f'h{i}', 'Halogen', f'T{i}', 'Baked') for i in range(12)]
    shelf = allocate_shelves([(a, cands)])[0]
    assert shelf.diagnostics['candidates'] == 12
    assert shelf.diagnostics['selected'] == 1
    assert shelf.diagnostics['distinct_albums'] == 1


# ── B06: the reason must be true ────────────────────────────────────────────


def test_a_direct_shelf_says_so():
    reason = shelf_reason('Katy Perry', [_cand('1', 'Halogen', 'T')])
    assert reason['kind'] == 'direct'
    assert 'Katy Perry' in reason['label']


def test_a_genre_shelf_names_the_tag_it_shares():
    c = candidate_from_row(_row('1', 'X', 'T'), 's', 'genre', 'shoegaze', 0.8)
    reason = shelf_reason('Katy Perry', [c])
    assert reason['kind'] == 'genre'
    assert 'shoegaze' in reason['label']


def test_an_empty_shelf_claims_nothing():
    assert shelf_reason('Katy Perry', [])['kind'] == 'none'


# ── B03: the generation record ──────────────────────────────────────────────


def test_a_generation_drops_empty_sections_rather_than_showing_headings():
    a = SeedIdentity(name='A', ids=(('deezer', '1'),))
    from core.discovery.bylt import Shelf
    good = [_cand(str(i), f'X{i}', 'T', f'Al{i}') for i in range(4)]
    sections = [section_from_shelf(Shelf(seed=a, selected=[])),
                section_from_shelf(Shelf(
                    seed=SeedIdentity(name='B', ids=(('deezer', '2'),)),
                    selected=good))]
    gen = build_generation(sections, profile_id=1, source='deezer',
                           generation_id='g1', generated_at='now')
    assert [s['seed_name'] for s in gen['sections']] == ['B']


def test_a_one_card_section_never_reaches_the_generation():
    from core.discovery.bylt import Shelf
    section = section_from_shelf(Shelf(
        seed=SeedIdentity(name='Ariana Grande', ids=(('deezer', '222'),)),
        selected=[_cand('1', 'Scorpixter', 'Perfect Girl (EDM Remake)')]))
    assert section['presentation'] == 'insufficient'
    gen = build_generation([section], profile_id=1, source='deezer',
                           generation_id='g', generated_at='now')
    assert gen['sections'] == []


def test_a_duplicate_seed_fails_validation():
    section = {'seed_key': 'deezer:1', 'seed_name': 'A', 'tracks': [{}]}
    gen = build_generation([section, dict(section)], profile_id=1, source='d',
                           generation_id='g', generated_at='now')
    assert validate_generation(gen) is False


def test_a_successful_empty_generation_is_valid():
    gen = build_generation([], profile_id=1, source='deezer',
                           generation_id='g', generated_at='now')
    assert gen['sections'] == []
    assert validate_generation(gen) is True


def test_a_section_carries_its_identity_heading_and_tracks_together():
    from core.discovery.bylt import Shelf
    seed = SeedIdentity(name='Katy Perry', ids=(('deezer', '111'),))
    section = section_from_shelf(Shelf(seed=seed, selected=[_cand('1', 'Halogen', 'T')]),
                                 seed_image='http://x.jpg')
    assert section['seed_key'] == 'deezer:111'
    assert section['seed_name'] == 'Katy Perry'
    assert section['seed_image'] == 'http://x.jpg'
    assert section['tracks'][0]['relation'] == 'direct'
    assert section['tracks'][0]['seed_key'] == 'deezer:111'


@pytest.mark.parametrize('bad', [None, 'x', {}, {'schema': 99, 'sections': []},
                                 {'schema': 1, 'sections': 'no'}])
def test_garbage_never_validates(bad):
    assert validate_generation(bad) is False
