import { beforeEach, describe, expect, it } from 'vitest';

import { artistNavigationOrigin, noteNavigation, resetNavigationHistory } from './last-navigation';

describe('artist navigation origin', () => {
  beforeEach(() => resetNavigationHistory());

  it('keeps the exact entry page through redirects, albums, and repeated readers', () => {
    const wishlist = { pathname: '/wishlist', searchStr: '?tab=music&q=Portishead' };
    const artist = { pathname: '/library', searchStr: '?artist=1&view=discovery' };
    const redirectedArtist = {
      pathname: '/library',
      searchStr: '?artist=1&view=discovery&header=compact',
    };
    const album = { pathname: '/library', searchStr: '?artist=1&album=2&header=compact' };

    noteNavigation(wishlist, artist);
    expect(artistNavigationOrigin()).toEqual(wishlist);
    expect(artistNavigationOrigin()).toEqual(wishlist);

    noteNavigation(artist, redirectedArtist);
    noteNavigation(redirectedArtist, album);
    noteNavigation(album, redirectedArtist);
    expect(artistNavigationOrigin()).toEqual(wishlist);

    noteNavigation(redirectedArtist, wishlist);
    expect(artistNavigationOrigin()).toBeNull();
  });

  it.each([
    ['/search', '?q=Portishead'],
    ['/watchlist', '?tab=artists'],
    ['/playlist-explorer', '?playlist=Roadtrip'],
    ['/discover', '?tab=for-you'],
    ['/dashboard', ''],
    ['/library', '?section=albums&page=3'],
  ])('replaces an old origin with a new entry from %s', (pathname, searchStr) => {
    noteNavigation(
      { pathname: '/discover', searchStr: '?tab=old' },
      { pathname: '/library', searchStr: '?artist=9' },
    );
    noteNavigation({ pathname: '/library', searchStr: '?artist=9' }, { pathname, searchStr });
    noteNavigation({ pathname, searchStr }, { pathname: '/library', searchStr: '?artist=1' });

    expect(artistNavigationOrigin()).toEqual({ pathname, searchStr });
  });
});
