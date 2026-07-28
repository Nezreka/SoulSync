import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
  type BackLabelEntry,
  backButtonLabel,
  backLabelStack,
  popBackOrigin,
  pushArtistOrigin,
  pushPageOrigin,
  resetGoingBackForTests,
} from './-artist-detail.back-label';

type W = {
  artistDetailLabelStack?: BackLabelEntry[];
  artistDetailBackLabels?: Record<string, string>;
};

const install = (stack: BackLabelEntry[] = []) => {
  (window as W).artistDetailLabelStack = stack;
  return stack;
};

beforeEach(() => resetGoingBackForTests());

afterEach(() => {
  delete (window as W).artistDetailLabelStack;
  delete (window as W).artistDetailBackLabels;
});

describe('backButtonLabel', () => {
  it('names the page you came from', () => {
    install([{ type: 'page', pageId: 'search' }]);
    expect(backButtonLabel()).toBe('← Back to Search');
  });

  it('names the ARTIST you came from', () => {
    install([{ type: 'artist', name: 'Aphex Twin' }]);
    expect(backButtonLabel()).toBe('← Back to Aphex Twin');
  });

  it('reads the TOP of the stack, not the bottom', () => {
    install([
      { type: 'page', pageId: 'search' },
      { type: 'artist', name: 'Boards of Canada' },
    ]);
    expect(backButtonLabel()).toBe('← Back to Boards of Canada');
  });

  it('is a plain Back on a cold load', () => {
    install([]);
    expect(backButtonLabel()).toBe('← Back');
  });

  it('falls back to Library for an unrecognised page', () => {
    install([{ type: 'page', pageId: 'something-new' }]);
    expect(backButtonLabel()).toBe('← Back to Library');
  });

  it('prefers the vanilla label map when library.js exported one', () => {
    // Keeps the two in step if Boulder renames a page label there.
    (window as W).artistDetailBackLabels = { search: 'Back to Finder' };
    install([{ type: 'page', pageId: 'search' }]);
    expect(backButtonLabel()).toBe('← Back to Finder');
  });

  it('works with no exported stack at all', () => {
    expect(backButtonLabel()).toBe('← Back');
  });
});

describe('the stack across hops', () => {
  it('pushes the previous artist on a forward hop', () => {
    const stack = install([{ type: 'page', pageId: 'search' }]);
    pushArtistOrigin('Aphex Twin');
    expect(stack).toHaveLength(2);
    expect(backButtonLabel()).toBe('← Back to Aphex Twin');
  });

  it('pops on a back navigation', () => {
    install([
      { type: 'page', pageId: 'search' },
      { type: 'artist', name: 'Aphex Twin' },
    ]);
    popBackOrigin();
    expect(backButtonLabel()).toBe('← Back to Search');
  });

  it('does NOT re-push when the back navigation re-renders the old artist', () => {
    // Going back looks exactly like a forward hop to the page effect; without
    // the guard the pop and the push cancel and the label never moves.
    const stack = install([
      { type: 'page', pageId: 'search' },
      { type: 'artist', name: 'A' },
    ]);
    popBackOrigin();
    pushArtistOrigin('A');
    expect(stack).toEqual([{ type: 'page', pageId: 'search' }]);
    expect(backButtonLabel()).toBe('← Back to Search');
  });

  it('only swallows ONE push after a back', () => {
    const stack = install([
      { type: 'page', pageId: 'search' },
      { type: 'artist', name: 'A' },
    ]);
    popBackOrigin();
    pushArtistOrigin('A');
    pushArtistOrigin('B');
    expect(stack).toHaveLength(2);
    expect(backButtonLabel()).toBe('← Back to B');
  });

  it('ignores a push with no artist name', () => {
    const stack = install([]);
    pushArtistOrigin(null);
    pushArtistOrigin('');
    expect(stack).toHaveLength(0);
  });

  it('shares the SAME array the vanilla pushes into', () => {
    // library.js clears it in place for exactly this reason; a reassignment
    // there would leave React reading a detached copy.
    const stack = install([]);
    stack.push({ type: 'page', pageId: 'search' });
    expect(backLabelStack()).toBe(stack);
    expect(backButtonLabel()).toBe('← Back to Search');
  });
});

describe('pushPageOrigin — arrivals that never touch navigateToArtistDetail', () => {
  it('names the React page you came from', () => {
    // The Library card is a plain <a href>, so nothing pushed for it and the
    // button read a bare "Back" — the whole reason this function exists.
    const stack = install();
    pushPageOrigin('/library');
    expect(stack).toEqual([{ type: 'page', pageId: 'library' }]);
    expect(backButtonLabel()).toBe('← Back to Library');
  });

  it('reads a nested path back to its page', () => {
    install();
    pushPageOrigin('/watchlist/artist/42');
    expect(backButtonLabel()).toBe('← Back to Watchlist');
  });

  it('leaves a stack that already has an origin alone', () => {
    // navigateToArtistDetail got there first; overwriting would relabel an
    // arrival that was already recorded correctly.
    const stack = install([{ type: 'page', pageId: 'search' }]);
    pushPageOrigin('/library');
    expect(stack).toEqual([{ type: 'page', pageId: 'search' }]);
  });

  it('never overwrites an artist chain', () => {
    const stack = install();
    pushArtistOrigin('Aphex Twin');
    pushPageOrigin('/library');
    expect(stack).toEqual([{ type: 'artist', name: 'Aphex Twin' }]);
  });

  it('ignores an artist-detail path, which is a hop not an origin', () => {
    // Otherwise every similar-artist hop would push "Back to Artist Detail".
    const stack = install();
    pushPageOrigin('/artist-detail/library/42');
    expect(stack).toEqual([]);
  });

  it('stays silent on a cold load and on an unmappable path', () => {
    const stack = install();
    pushPageOrigin(undefined);
    pushPageOrigin('/not-a-page');
    expect(stack).toEqual([]);
    expect(backButtonLabel()).toBe('← Back');
  });
});
