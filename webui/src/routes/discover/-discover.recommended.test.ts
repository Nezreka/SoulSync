import { describe, expect, it } from 'vitest';

import type { RecommendedArtist } from './-discover.recommended';

import {
  DEFAULT_REC_SOURCE,
  ENRICH_ENDPOINT,
  RECOMMENDED_CARD_LIMIT,
  RECOMMENDED_SECTIONS,
  REC_WATCH_ADD_LABEL,
  REC_WATCH_ON_LABEL,
  WHY_CHIP_LIMIT,
  enrichIdKey,
  enrichIds,
  enrichUpdates,
  recSource,
  recWatchlistClickable,
  recWatchlistNextState,
  recWatchlistRequest,
  recommendedCard,
  recommendedMatches,
  recommendedVisible,
  shouldEnrich,
  watchingIdsFrom,
  watchlistCheckIds,
} from './-discover.recommended';

const artist = (over: Partial<RecommendedArtist> = {}): RecommendedArtist => ({
  artist_id: 'a1',
  artist_name: 'Aphex Twin',
  ...over,
});

describe('the two sections', () => {
  it('both HIDE when empty rather than showing an empty box', () => {
    // A user who has not run a scan should not get a titled box explaining
    // that they have nothing.
    expect(RECOMMENDED_SECTIONS.recommended.hideWhenEmpty).toBe(true);
    expect(RECOMMENDED_SECTIONS.listening.hideWhenEmpty).toBe(true);
  });

  it('keeps each section’s copy distinct and verbatim', () => {
    expect(RECOMMENDED_SECTIONS.recommended.emptyMessage).toBe(
      'No recommendations yet — let the Similar Artists worker run',
    );
    expect(RECOMMENDED_SECTIONS.listening.emptyMessage).toBe(
      'Play more music and run a watchlist scan to see picks based on your listening',
    );
    expect(RECOMMENDED_SECTIONS.recommended.loadingMessage).toBe('Finding recommendations...');
    expect(RECOMMENDED_SECTIONS.listening.loadingMessage).toBe('Reading your listening...');
  });

  it('reads different endpoints', () => {
    expect(RECOMMENDED_SECTIONS.recommended.fetchUrl).toBe('/api/discover/similar-artists');
    expect(RECOMMENDED_SECTIONS.listening.fetchUrl).toBe('/api/discover/listening-recommendations');
  });

  it('shows at most 18 cards', () => {
    expect(RECOMMENDED_CARD_LIMIT).toBe(18);
    expect(recommendedVisible(Array.from({ length: 30 }, () => artist()))).toHaveLength(18);
    expect(recommendedVisible([artist()])).toHaveLength(1);
  });

  it('falls back to spotify as the source', () => {
    expect(recSource({ source: 'deezer' })).toBe('deezer');
    expect(recSource({})).toBe(DEFAULT_REC_SOURCE);
    expect(recSource(null)).toBe('spotify');
  });
});

describe('the shared card', () => {
  it('lets the ARTIST’s own source win over the section’s', () => {
    // A mixed-source response still links each card to the right provider.
    expect(recommendedCard(artist({ source: 'deezer' }), 'spotify').source).toBe('deezer');
    expect(recommendedCard(artist(), 'spotify').source).toBe('spotify');
    expect(recommendedCard(artist(), '').source).toBe('');
  });

  it('lowercases a separate filter name', () => {
    const card = recommendedCard(artist({ artist_name: 'Aphex Twin' }), 'spotify');
    expect(card.artistName).toBe('Aphex Twin');
    expect(card.filterName).toBe('aphex twin');
  });

  it('shows at most TWO why-chips', () => {
    expect(WHY_CHIP_LIMIT).toBe(2);
    const card = recommendedCard(
      artist({
        why: [
          { type: 'genre', label: 'a' },
          { type: 'consensus', label: 'b' },
          { type: 'recency', label: 'c' },
        ],
      }),
      'spotify',
    );
    expect(card.chips).toHaveLength(2);
    expect(card.chips.map((c) => c.label)).toEqual(['a', 'b']);
  });

  it('gives each chip an icon from its type', () => {
    const card = recommendedCard(artist({ why: [{ type: 'genre', label: 'techno' }] }), 'spotify');
    expect(card.chips[0].icon).toBeTruthy();
  });

  it('chips REPLACE the reason line, they do not sit alongside it', () => {
    // They are the reason, just clearer — rendering both duplicates the claim.
    expect(
      recommendedCard(artist({ why: [{ type: 'genre', label: 'x' }] }), 'spotify').showChips,
    ).toBe(true);
    expect(recommendedCard(artist(), 'spotify').showChips).toBe(false);
    expect(recommendedCard(artist({ why: [] }), 'spotify').showChips).toBe(false);
  });

  it('uses a DIFFERENT reason function per section', () => {
    const a = artist({ similar_to: 'Autechre', match_count: 3 } as Partial<RecommendedArtist>);
    const rec = recommendedCard(a, 'spotify', 'recommended');
    const lis = recommendedCard(a, 'spotify', 'listening');
    expect(rec.reason).toBeTypeOf('string');
    expect(lis.reason).toBeTypeOf('string');
  });

  it('defaults a nameless artist to empty strings rather than undefined', () => {
    const card = recommendedCard({}, 'spotify');
    expect([card.artistId, card.artistName, card.filterName]).toEqual(['', '', '']);
    expect(card.image).toBeNull();
  });
});

describe('image enrichment', () => {
  it('picks the id field from the source', () => {
    expect(enrichIdKey('spotify')).toBe('spotify_artist_id');
    expect(enrichIdKey('deezer')).toBe('deezer_artist_id');
    expect(enrichIdKey('itunes')).toBe('itunes_artist_id');
  });

  it('treats an UNKNOWN source as itunes, which is the vanilla fallthrough', () => {
    // There is no separate branch — it asks the itunes field and finds nothing.
    expect(enrichIdKey('bandcamp')).toBe('itunes_artist_id');
    expect(enrichIdKey('')).toBe('itunes_artist_id');
  });

  it('requests only artists that have NO image yet', () => {
    // The list endpoint returns cached images only; re-asking for those would
    // be most of the batch.
    const items = [
      artist({ spotify_artist_id: 's1', image_url: '/have.jpg' }),
      artist({ spotify_artist_id: 's2' }),
      artist({ spotify_artist_id: 's3' }),
    ];
    expect(enrichIds(items, 'spotify')).toEqual(['s2', 's3']);
  });

  it('drops artists with no id for that source', () => {
    expect(enrichIds([artist({ deezer_artist_id: 'd1' })], 'spotify')).toEqual([]);
  });

  it('makes no request at all when nothing needs enriching', () => {
    expect(shouldEnrich([])).toBe(false);
    expect(shouldEnrich(['s1'])).toBe(true);
    expect(ENRICH_ENDPOINT).toBe('/api/discover/similar-artists/enrich');
  });

  it('SKIPS entries with no image rather than blanking the card', () => {
    // The fallback glyph already rendered and beats an empty box.
    expect(
      enrichUpdates({
        success: true,
        artists: { a1: { image_url: '/a.jpg' }, a2: {}, a3: { image_url: '' } },
      }),
    ).toEqual([{ artistId: 'a1', imageUrl: '/a.jpg' }]);
  });

  it('ignores an unsuccessful enrich response', () => {
    expect(enrichUpdates({ success: false, artists: { a1: { image_url: '/a.jpg' } } })).toEqual([]);
    expect(enrichUpdates({ success: true })).toEqual([]);
    expect(enrichUpdates(null)).toEqual([]);
  });
});

describe('the search filter', () => {
  it('matches a SUBSTRING, not just a prefix', () => {
    expect(recommendedMatches('aphex twin', 'twin')).toBe(true);
    expect(recommendedMatches('aphex twin', 'aphex')).toBe(true);
  });

  it('is case-insensitive on the query side too', () => {
    expect(recommendedMatches('aphex twin', 'TWIN')).toBe(true);
  });

  it('matches everything for an empty query, restoring the list', () => {
    expect(recommendedMatches('aphex twin', '')).toBe(true);
  });

  it('rejects a non-match', () => {
    expect(recommendedMatches('aphex twin', 'autechre')).toBe(false);
  });
});

describe('the watchlist button', () => {
  it('ignores a click with no id or no name', () => {
    expect(recWatchlistClickable('a1', 'A')).toBe(true);
    expect(recWatchlistClickable('', 'A')).toBe(false);
    expect(recWatchlistClickable('a1', '')).toBe(false);
  });

  it('adds with id + name but NO source', () => {
    // The Your Artists toggle DOES send a source. Adding one here would change
    // which provider the watchlist row points at.
    expect(recWatchlistRequest(false, 'a1', 'A')).toEqual({
      url: '/api/watchlist/add',
      body: { artist_id: 'a1', artist_name: 'A' },
    });
    expect(recWatchlistRequest(false, 'a1', 'A').body).not.toHaveProperty('source');
  });

  it('removes with the id alone', () => {
    expect(recWatchlistRequest(true, 'a1', 'A')).toEqual({
      url: '/api/watchlist/remove',
      body: { artist_id: 'a1' },
    });
  });

  it('flips ONLY on success', () => {
    // A failed request leaves the button as it was — no optimistic lie.
    expect(recWatchlistNextState(false, { success: true })).toEqual({
      watching: true,
      label: REC_WATCH_ON_LABEL,
    });
    expect(recWatchlistNextState(true, { success: true })).toEqual({
      watching: false,
      label: REC_WATCH_ADD_LABEL,
    });
    expect(recWatchlistNextState(false, { success: false })).toBeNull();
    expect(recWatchlistNextState(false, null)).toBeNull();
  });

  it('keeps both labels', () => {
    expect(REC_WATCH_ADD_LABEL).toBe('Add to Watchlist');
    expect(REC_WATCH_ON_LABEL).toBe('Watching');
  });
});

describe('the batch status check', () => {
  it('collects the ids it has', () => {
    expect(
      watchlistCheckIds([artist({ artist_id: 'a1' }), artist({ artist_id: undefined })]),
    ).toEqual(['a1']);
  });

  it('only ever marks buttons AS watching, never un-marks', () => {
    // Cards render as "Add to Watchlist", so there is nothing to clear — and
    // clearing on a partial response would drop state the batch did not cover.
    expect(watchingIdsFrom({ success: true, results: { a1: true, a2: false, a3: true } })).toEqual([
      'a1',
      'a3',
    ]);
  });

  it('is silent on an unsuccessful check', () => {
    expect(watchingIdsFrom({ success: false, results: { a1: true } })).toEqual([]);
    expect(watchingIdsFrom({ success: true })).toEqual([]);
    expect(watchingIdsFrom(null)).toEqual([]);
  });
});
