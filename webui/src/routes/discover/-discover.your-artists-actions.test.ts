import { describe, expect, it } from 'vitest';

import type { ArtistPool, RelatedArtist } from './-discover.your-artists-actions';

import {
  YOUR_ALBUMS_SEARCH_DEBOUNCE_MS,
  DISCONNECTED_HINTS,
} from './-discover.your-albums-actions';
import { yourArtistsSubtitle } from './-discover.your-artists';
import {
  ARTISTS_DEFAULT_SOURCES,
  ARTISTS_MODAL_EMPTY,
  ARTISTS_MODAL_ERROR,
  ARTISTS_MODAL_FILTERS,
  ARTISTS_MODAL_PAGE_SIZE,
  ARTISTS_MODAL_SEARCH_DEBOUNCE_MS,
  ARTISTS_MODAL_SORTS,
  ARTISTS_REFRESH_MAX_ATTEMPTS,
  ARTISTS_REFRESH_POLL_MS,
  ARTISTS_SOURCE_INFO,
  BIO_MAX_LENGTH,
  INFO_EMPTY,
  INFO_ERROR,
  INFO_FETCH_TIMEOUT_MS,
  INFO_LOADING,
  INITIAL_ARTISTS_MODAL_STATE,
  ORIGIN_NAMES,
  REFRESH_TIMEOUT_REENABLES_BUTTON,
  RELATED_MAX,
  WATCHLIST_TOGGLE_FAILED,
  applyArtistsModalFilter,
  artistSourcesSavePayload,
  artistsModalPager,
  artistsModalQuery,
  artistsModalSubtitle,
  artistsRefreshSettled,
  artistsRefreshToast,
  cleanBio,
  formatStatValue,
  infoLookupId,
  infoMatchBadges,
  infoOriginText,
  infoStats,
  infoWatchButtonDone,
  infoWatchButtonLabel,
  initialArtistSourcesState,
  poolWatchlistValue,
  relatedIsWatchlist,
  relatedLabel,
  relatedOverflow,
  relatedVisible,
  savedArtistSourcesSubtitle,
  setArtistsModalPage,
  toggleArtistSource,
  truncateBio,
  watchlistIconFill,
  watchlistRequest,
  watchlistToast,
} from './-discover.your-artists-actions';

const pool = (over: Partial<ArtistPool> = {}): ArtistPool => ({
  id: 1,
  artist_name: 'Aphex Twin',
  ...over,
});

describe('the info modal lookup', () => {
  it('uses the active source id when there is one', () => {
    expect(infoLookupId('sp1', 'Aphex Twin')).toBe('sp1');
  });

  it('falls back to the ENCODED name, because it goes in the path', () => {
    // An unencoded "AC/DC" would be read as two path segments and 404.
    expect(infoLookupId(undefined, 'AC/DC')).toBe('AC%2FDC');
    expect(infoLookupId('', 'Sigur Rós')).toBe('Sigur%20R%C3%B3s');
  });
});

describe('the matched-source badges', () => {
  it('says "Matched on X", not the bare service name', () => {
    // On a card the badge means "we hold an id"; here it means "we resolved
    // this artist there". Reusing the card titles would lose that.
    const badges = infoMatchBadges(
      pool({ spotify_artist_id: 'sp', itunes_artist_id: 'it', discogs_artist_id: 'dc' }),
    );
    expect(badges.map((b) => b.title)).toEqual([
      'Matched on Spotify',
      'Matched on Apple Music',
      'Matched on Discogs',
    ]);
  });

  it('keeps the fixed order regardless of which ids are set', () => {
    const badges = infoMatchBadges(
      pool({ discogs_artist_id: 'dc', deezer_artist_id: 'dz', spotify_artist_id: 'sp' }),
    );
    expect(badges.map((b) => b.key)).toEqual(['spotify', 'deezer', 'discogs']);
  });

  it('emits none for an unmatched artist', () => {
    expect(infoMatchBadges(pool())).toEqual([]);
  });
});

describe('the origin line', () => {
  it('joins with a COMMA, unlike the section subtitle', () => {
    // The subtitle names services in a sentence ("Spotify and Last.fm"); this
    // lists an artist's origins. Unifying them would be wrong in one place.
    expect(infoOriginText(['spotify', 'lastfm'])).toBe('Spotify, Last.fm');
    expect(yourArtistsSubtitle([{ source_services: ['spotify', 'lastfm'] }], null)).toContain(
      'Spotify and Last.fm',
    );
  });

  it('maps keys to display names and passes unknowns through', () => {
    expect(infoOriginText(['tidal', 'bandcamp'])).toBe('Tidal, bandcamp');
  });

  it('is empty with no origins, so the caller omits the whole line', () => {
    expect(infoOriginText([])).toBe('');
    expect(infoOriginText(undefined)).toBe('');
  });
});

describe('the stats block', () => {
  it('prefers the Last.fm listener count over the follower count', () => {
    expect(infoStats({ lastfm_listeners: 900, followers: 100 }).listeners).toBe(900);
    expect(infoStats({ followers: 100 }).listeners).toBe(100);
  });

  it('is hidden only when all three are zero', () => {
    expect(infoStats({}).visible).toBe(false);
    expect(infoStats({ popularity: 1 }).visible).toBe(true);
    expect(infoStats({ lastfm_playcount: 1 }).visible).toBe(true);
    expect(infoStats({ followers: 1 }).visible).toBe(true);
  });

  it('formats counts with thousands separators', () => {
    expect(formatStatValue(1234567)).toBe((1234567).toLocaleString());
    expect(formatStatValue(1234567)).not.toBe('1234567');
  });

  it('times the enrichment fetch out at eight seconds', () => {
    expect(INFO_FETCH_TIMEOUT_MS).toBe(8000);
  });
});

describe('the bio', () => {
  it('strips anchors WITH their text', () => {
    // A Last.fm summary ends in "Read more on Last.fm" — meaningless here.
    const raw = 'Real bio text. <a href="https://last.fm/x">Read more on Last.fm</a>';
    expect(cleanBio(raw)).toBe('Real bio text.');
  });

  it('strips anchors before other tags, which is the load-bearing order', () => {
    // A single <[^>]+> pass would leave the anchor's TEXT behind.
    expect(cleanBio('<p>Bio</p> <a href="#">Read more</a>')).toBe('Bio');
  });

  it('strips remaining tags and trims', () => {
    expect(cleanBio('  <b>Bold</b> and <i>italic</i>  ')).toBe('Bold and italic');
  });

  it('is case-insensitive about the anchor tag', () => {
    expect(cleanBio('Text <A HREF="#">Link</A>')).toBe('Text');
  });

  it('truncates past 600 characters with an ellipsis', () => {
    const long = 'x'.repeat(700);
    const out = truncateBio(long);
    expect(out).toHaveLength(BIO_MAX_LENGTH + 3);
    expect(out.endsWith('...')).toBe(true);
  });

  it('leaves a bio at exactly the limit alone', () => {
    const exact = 'x'.repeat(BIO_MAX_LENGTH);
    expect(truncateBio(exact)).toBe(exact);
  });
});

describe('related artists', () => {
  const rel = (n: number): RelatedArtist[] =>
    Array.from({ length: n }, (_, i) => ({ id: i, name: `A${i}` }));

  it('labels similarity differently for a watchlisted artist', () => {
    // Watchlisted artists have real similarity data; everything else is map
    // adjacency, a weaker claim that gets a weaker label.
    expect(relatedLabel(true)).toBe('Similar Artists');
    expect(relatedLabel(1)).toBe('Similar Artists');
    expect(relatedLabel(0)).toBe('Connected To');
    expect(relatedLabel(undefined)).toBe('Connected To');
  });

  it('shows twelve and counts the rest', () => {
    expect(RELATED_MAX).toBe(12);
    expect(relatedVisible(rel(20))).toHaveLength(12);
    expect(relatedOverflow(rel(20))).toBe(8);
  });

  it('shows no overflow tail at or below the limit', () => {
    expect(relatedOverflow(rel(12))).toBe(0);
    expect(relatedOverflow(rel(3))).toBe(0);
    expect(relatedVisible(rel(3))).toHaveLength(3);
  });

  it('badges only entries typed as watchlist', () => {
    expect(relatedIsWatchlist({ type: 'watchlist' })).toBe(true);
    expect(relatedIsWatchlist({ type: 'pool' })).toBe(false);
    expect(relatedIsWatchlist({})).toBe(false);
  });

  it('keeps the info-modal copy', () => {
    expect(INFO_LOADING).toBe('Loading artist info...');
    expect(INFO_EMPTY).toBe('No additional info available');
    expect(INFO_ERROR).toBe('Could not load artist info');
  });
});

describe('the watchlist toggle', () => {
  it('removes with the id ALONE', () => {
    expect(watchlistRequest(true, { sourceId: 'sp1', artistName: 'A', source: 'spotify' })).toEqual(
      {
        url: '/api/watchlist/remove',
        body: { artist_id: 'sp1' },
      },
    );
  });

  it('adds with the name and source too', () => {
    // A watchlist row that arrives without them renders as an un-named entry.
    expect(
      watchlistRequest(false, { sourceId: 'sp1', artistName: 'A', source: 'spotify' }),
    ).toEqual({
      url: '/api/watchlist/add',
      body: { artist_id: 'sp1', artist_name: 'A', source: 'spotify' },
    });
  });

  it('toasts at different LEVELS for add and remove', () => {
    expect(watchlistToast(false, 'Aphex Twin')).toEqual({
      message: 'Added Aphex Twin to watchlist',
      level: 'success',
    });
    expect(watchlistToast(true, 'Aphex Twin')).toEqual({
      message: 'Removed Aphex Twin from watchlist',
      level: 'info',
    });
    expect(WATCHLIST_TOGGLE_FAILED).toBe('Failed to update watchlist');
  });

  it('fills the eye icon only when watched', () => {
    expect(watchlistIconFill(true)).toBe('currentColor');
    expect(watchlistIconFill(false)).toBe('none');
  });

  it('writes the pool flag as 1/0, not true/false', () => {
    // It arrives from SQLite as an integer and other code compares it loosely.
    expect(poolWatchlistValue(true)).toBe(1);
    expect(poolWatchlistValue(false)).toBe(0);
  });

  it('labels the footer button by current state, and after the click', () => {
    expect(infoWatchButtonLabel(1)).toBe('Remove from Watchlist');
    expect(infoWatchButtonLabel(0)).toBe('Add to Watchlist');
    expect(infoWatchButtonDone(1)).toBe('Done');
    expect(infoWatchButtonDone(0)).toBe('Added!');
  });
});

describe('the refresh poller', () => {
  it('polls every five seconds for five minutes', () => {
    expect(ARTISTS_REFRESH_POLL_MS).toBe(5000);
    expect(ARTISTS_REFRESH_MAX_ATTEMPTS).toBe(60);
  });

  it('settles only when the rebuild is done AND produced artists', () => {
    expect(artistsRefreshSettled({ stale: false, artists: [{}] })).toBe(true);
    expect(artistsRefreshSettled({ stale: true, artists: [{}] })).toBe(false);
    expect(artistsRefreshSettled({ stale: false, artists: [] })).toBe(false);
    expect(artistsRefreshSettled({ stale: false })).toBe(false);
    expect(artistsRefreshSettled(null)).toBe(false);
  });

  it('reports the count', () => {
    expect(artistsRefreshToast(412)).toBe('Found 412 artists from your services');
  });

  it('DIVERGENCE 1: re-enables the button when the poll times out', () => {
    // The vanilla's give-up path is `clearInterval(poll); return;` with no
    // re-enable (5590), so a refresh that never settles leaves the button dead
    // until a page reload. Your Albums re-enables from its timeout (1598).
    expect(REFRESH_TIMEOUT_REENABLES_BUTTON).toBe(true);
  });
});

describe('the artists sources modal', () => {
  it('offers Last.fm where the albums modal offers Discogs', () => {
    // You FOLLOW artists and you COLLECT albums — Last.fm has follows, Discogs
    // has a collection. Neither list is a subset of the other.
    expect(ARTISTS_SOURCE_INFO.map((s) => s.id)).toEqual(['spotify', 'tidal', 'lastfm', 'deezer']);
  });

  it('enables ALL FOUR by default, unlike the albums modal', () => {
    expect(ARTISTS_DEFAULT_SOURCES).toEqual(['spotify', 'tidal', 'lastfm', 'deezer']);
    expect(ARTISTS_DEFAULT_SOURCES).toHaveLength(ARTISTS_SOURCE_INFO.length);
  });

  it('gives every source a key', () => {
    expect(initialArtistSourcesState(['spotify', 'lastfm'])).toEqual({
      spotify: true,
      tidal: false,
      lastfm: true,
      deezer: false,
    });
  });

  it('toggles a connected source', () => {
    const { state, hint } = toggleArtistSource({ tidal: false }, 'tidal', ['tidal']);
    expect(state.tidal).toBe(true);
    expect(hint).toBeNull();
  });

  it('DIVERGENCE 2: explains a disconnected source instead of bailing silently', () => {
    // 5673/5680 just `return`. That is the exact complaint the Your Albums
    // hints were added to fix (1665-1667); the fix was never copied across.
    const before = { lastfm: false };
    const { state, hint } = toggleArtistSource(before, 'lastfm', ['spotify']);
    expect(state).toBe(before);
    expect(hint).toBeTruthy();
  });

  it('reuses the SHARED hint table, so the two modals cannot drift', () => {
    expect(toggleArtistSource({}, 'spotify', []).hint).toBe(DISCONNECTED_HINTS.spotify);
  });

  it('writes a different settings key from the albums modal', () => {
    expect(artistSourcesSavePayload(['spotify', 'lastfm'])).toEqual({
      discover: { your_artists_sources: 'spotify,lastfm' },
    });
  });

  it('rewrites the subtitle with "and", matching the section subtitle', () => {
    expect(savedArtistSourcesSubtitle(['spotify', 'lastfm'])).toBe(
      'Artists you follow on Spotify and Last.fm',
    );
  });

  it('has a display name for every offered source', () => {
    for (const s of ARTISTS_SOURCE_INFO) expect(ORIGIN_NAMES[s.id]).toBeTruthy();
  });
});

describe('the all-artists modal', () => {
  it('debounces faster than the albums grid, which is not a bug', () => {
    // 60 cached rows vs a paged library query — the cheaper request can afford
    // to fire sooner.
    expect(ARTISTS_MODAL_SEARCH_DEBOUNCE_MS).toBe(300);
    expect(ARTISTS_MODAL_SEARCH_DEBOUNCE_MS).toBeLessThan(YOUR_ALBUMS_SEARCH_DEBOUNCE_MS);
  });

  it('pages at sixty', () => {
    expect(ARTISTS_MODAL_PAGE_SIZE).toBe(60);
  });

  it('offers All plus one pill per source, in order', () => {
    expect(ARTISTS_MODAL_FILTERS.map((f) => f.source)).toEqual([
      '',
      'spotify',
      'tidal',
      'lastfm',
      'deezer',
    ]);
    expect(ARTISTS_MODAL_FILTERS[0].label).toBe('All');
  });

  it('offers the three sorts in their rendered order', () => {
    expect(ARTISTS_MODAL_SORTS.map((s) => s.value)).toEqual(['name', 'recent', 'source']);
    expect(ARTISTS_MODAL_SORTS.map((s) => s.label)).toEqual(['A-Z', 'Recently Added', 'By Source']);
  });

  it('omits an empty source or search rather than sending a blank filter', () => {
    expect(artistsModalQuery(INITIAL_ARTISTS_MODAL_STATE)).toEqual({
      page: '1',
      per_page: '60',
      sort: 'name',
    });
  });

  it('sends them once set', () => {
    const q = artistsModalQuery({ page: 2, source: 'tidal', sort: 'recent', search: 'aphex' });
    expect(q).toEqual({
      page: '2',
      per_page: '60',
      sort: 'recent',
      source: 'tidal',
      search: 'aphex',
    });
  });

  it('DIVERGENCE 3: resets to page 1 on SEARCH and SORT, not just the source', () => {
    // The vanilla only resets for the source pills (5772). Searching from page
    // 3 asks for page 3 of a smaller result set and renders an empty grid whose
    // only way out is the Prev button.
    const onPage3 = { ...INITIAL_ARTISTS_MODAL_STATE, page: 3 };
    expect(applyArtistsModalFilter(onPage3, { search: 'aphex' }).page).toBe(1);
    expect(applyArtistsModalFilter(onPage3, { sort: 'recent' }).page).toBe(1);
    expect(applyArtistsModalFilter(onPage3, { source: 'tidal' }).page).toBe(1);
  });

  it('does NOT reset the page when paging, which would be a loop', () => {
    expect(setArtistsModalPage({ ...INITIAL_ARTISTS_MODAL_STATE, page: 1 }, 2).page).toBe(2);
  });

  it('keeps the other filters when one changes', () => {
    const state = { page: 3, source: 'tidal', sort: 'recent' as const, search: 'x' };
    expect(applyArtistsModalFilter(state, { search: 'y' })).toEqual({
      page: 1,
      source: 'tidal',
      sort: 'recent',
      search: 'y',
    });
  });

  it('counts matches in the subtitle', () => {
    expect(artistsModalSubtitle(412)).toBe('412 artists matched');
  });

  it('hides the pager at one page or fewer', () => {
    expect(artistsModalPager(60, 1).visible).toBe(false);
    expect(artistsModalPager(0, 1).visible).toBe(false);
    expect(artistsModalPager(61, 1).visible).toBe(true);
  });

  it('disables the ends', () => {
    const first = artistsModalPager(200, 1);
    expect([first.prevDisabled, first.nextDisabled]).toEqual([true, false]);
    const last = artistsModalPager(200, 4);
    expect([last.prevDisabled, last.nextDisabled]).toEqual([false, true]);
    expect(last.label).toBe('Page 4 of 4');
  });

  it('keeps the modal copy', () => {
    expect(ARTISTS_MODAL_EMPTY).toBe('No artists found');
    expect(ARTISTS_MODAL_ERROR).toBe('Failed to load');
  });
});
