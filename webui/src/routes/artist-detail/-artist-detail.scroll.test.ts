import { afterEach, describe, expect, it, vi } from 'vitest';

import { scrollArtistDetailToTop } from './-artist-detail.scroll';

afterEach(() => {
  document.body.innerHTML = '';
  vi.restoreAllMocks();
});

describe('scrollArtistDetailToTop', () => {
  it('scrolls the .main-content container, which is what actually scrolls', () => {
    const main = document.createElement('div');
    main.className = 'main-content';
    document.body.appendChild(main);
    main.scrollTop = 500;

    scrollArtistDetailToTop();
    expect(main.scrollTop).toBe(0);
  });

  it('does NOT rely on the window, which never scrolls here', () => {
    // body is overflow:hidden; height:100vh — window.scrollTo is a no-op.
    const main = document.createElement('div');
    main.className = 'main-content';
    document.body.appendChild(main);
    const windowScroll = vi.spyOn(window, 'scrollTo');

    scrollArtistDetailToTop();
    expect(windowScroll).not.toHaveBeenCalled();
  });

  it('falls back to the window with no shell around it', () => {
    const windowScroll = vi.spyOn(window, 'scrollTo');
    scrollArtistDetailToTop();
    expect(windowScroll).toHaveBeenCalledWith(0, 0);
  });
});
