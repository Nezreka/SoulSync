import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ExplorerArtistSection } from './-explorer.types';

import {
  EXPLORER_WISHLIST_DEFAULT_FILTERS,
  EXPLORER_WISHLIST_FILTER_TYPES,
  explorerWishlistActive,
  explorerWishlistCards,
  explorerWishlistCardVisible,
  explorerWishlistDefaultChecked,
  explorerWishlistDoneText,
  explorerWishlistFooter,
  groupWishlistByArtist,
} from './-explorer.wishlist';
import { ExplorerWishlistModal } from './-ui/explorer-wishlist-modal';

/**
 * The Add-to-Wishlist modal. The rule worth pinning is that a card must be
 * ticked AND visible to count — the vanilla's counter and its submit collector
 * both skipped `display: none` cards, so filtering a release out excludes it
 * even though its box stays ticked.
 */

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  delete window.showToast;
});

const SECTIONS: ExplorerArtistSection[] = [
  {
    artistId: 'art1',
    name: 'Boards of Canada',
    image: 'boc.jpg',
    albums: [
      { spotify_id: 'a1', title: 'Geogaddi', album_type: 'album', track_count: 23, year: 2002 },
      { spotify_id: 'a2', title: 'Twoism', album_type: 'ep', track_count: 8, owned: true },
      { spotify_id: 'a3', title: 'Hi Scores', album_type: 'single', track_count: 5 },
    ],
  },
  {
    artistId: 'art2',
    name: 'Aphex Twin',
    image: null,
    albums: [{ spotify_id: 'b1', title: 'SAW II', album_type: 'compilation', track_count: 24 }],
  },
];

describe('explorerWishlistCards', () => {
  it('flattens the sections, keeping order and per-section positions', () => {
    const cards = explorerWishlistCards(SECTIONS);
    expect(cards.map((card) => card.albumId)).toEqual(['a1', 'a2', 'a3', 'b1']);
    expect(cards.map((card) => card.indexInSection)).toEqual([0, 1, 2, 0]);
    expect(cards.map((card) => card.sectionIndex)).toEqual([0, 0, 0, 1]);
    expect(cards.map((card) => card.type)).toEqual(['album', 'ep', 'single', 'compilation']);
    expect(cards.map((card) => card.typeLabel)).toEqual(['Album', 'EP', 'Single', 'Album']);
  });

  it('defaults every unowned release to ticked', () => {
    const cards = explorerWishlistCards(SECTIONS);
    expect([...explorerWishlistDefaultChecked(cards)].sort()).toEqual(['a1', 'a3', 'b1']);
  });
});

describe('the filters', () => {
  it('offers exactly the three the vanilla did', () => {
    expect([...EXPLORER_WISHLIST_FILTER_TYPES]).toEqual(['album', 'ep', 'single']);
    expect(EXPLORER_WISHLIST_DEFAULT_FILTERS).toEqual({ album: true, ep: true, single: true });
  });

  it('always shows a type no button targets', () => {
    // 'compilation' has no filter button, so nothing can ever hide it — the
    // vanilla hid cards by querying [data-type="<the filter>"].
    expect(explorerWishlistCardVisible('compilation', { album: false })).toBe(true);
    expect(explorerWishlistCardVisible('album', { album: false })).toBe(false);
    expect(explorerWishlistCardVisible('album', EXPLORER_WISHLIST_DEFAULT_FILTERS)).toBe(true);
  });

  it('excludes a ticked card that a filter has hidden', () => {
    const cards = explorerWishlistCards(SECTIONS);
    const checked = new Set(['a1', 'a3', 'b1']);
    expect(
      explorerWishlistActive(cards, checked, EXPLORER_WISHLIST_DEFAULT_FILTERS).map(
        (c) => c.albumId,
      ),
    ).toEqual(['a1', 'a3', 'b1']);
    expect(
      explorerWishlistActive(cards, checked, { album: true, ep: true, single: false }).map(
        (c) => c.albumId,
      ),
    ).toEqual(['a1', 'b1']);
  });
});

describe('explorerWishlistFooter', () => {
  it('counts releases and tracks, and locks the submit at zero', () => {
    const cards = explorerWishlistCards(SECTIONS);
    const active = explorerWishlistActive(
      cards,
      new Set(['a1', 'b1']),
      EXPLORER_WISHLIST_DEFAULT_FILTERS,
    );
    expect(explorerWishlistFooter(active)).toEqual({
      info: '2 releases · 47 tracks',
      submitText: 'Add 2 to Wishlist',
      disabled: false,
    });
    expect(explorerWishlistFooter([])).toEqual({
      info: '0 releases · 0 tracks',
      submitText: 'Select releases',
      disabled: true,
    });
  });
});

describe('groupWishlistByArtist', () => {
  it('regroups by artist, keeping first-seen order and dropping empty artists', () => {
    const cards = explorerWishlistCards(SECTIONS);
    const active = explorerWishlistActive(
      cards,
      new Set(['a1', 'b1']),
      EXPLORER_WISHLIST_DEFAULT_FILTERS,
    );
    const groups = groupWishlistByArtist(active);
    expect(groups.map((g) => [g.artistId, g.albums.length])).toEqual([
      ['art1', 1],
      ['art2', 1],
    ]);

    // Filtering everything of one artist away removes the artist entirely.
    const onlyBoc = explorerWishlistActive(cards, new Set(['a1']), {
      ...EXPLORER_WISHLIST_DEFAULT_FILTERS,
    });
    expect(groupWishlistByArtist(onlyBoc).map((g) => g.artistId)).toEqual(['art1']);
  });

  it('does not merge two artists that share a name', () => {
    const dupes: ExplorerArtistSection[] = [
      { artistId: 'x1', name: 'Nova', image: null, albums: [{ spotify_id: 'n1', title: 'One' }] },
      { artistId: 'x2', name: 'Nova', image: null, albums: [{ spotify_id: 'n2', title: 'Two' }] },
    ];
    const cards = explorerWishlistCards(dupes);
    const active = explorerWishlistActive(
      cards,
      new Set(['n1', 'n2']),
      EXPLORER_WISHLIST_DEFAULT_FILTERS,
    );
    expect(groupWishlistByArtist(active).map((g) => g.artistId)).toEqual(['x1', 'x2']);
  });
});

describe('explorerWishlistDoneText', () => {
  it('reports the total', () => {
    expect(explorerWishlistDoneText(0)).toBe('Done — 0 tracks added to wishlist');
    expect(explorerWishlistDoneText(12)).toBe('Done — 12 tracks added to wishlist');
  });
});

// ── The modal ─────────────────────────────────────────────────────────────

function ndjsonResponse(lines: string[]) {
  const encoder = new TextEncoder();
  let i = 0;
  return new Response(
    new ReadableStream({
      pull(controller) {
        if (i < lines.length) controller.enqueue(encoder.encode(lines[i++]));
        else controller.close();
      },
    }),
  );
}

function renderModal(overrides: Partial<Parameters<typeof ExplorerWishlistModal>[0]> = {}) {
  const onClose = vi.fn();
  const onFinished = vi.fn();
  const view = render(
    <ExplorerWishlistModal
      sections={SECTIONS}
      onClose={onClose}
      onFinished={onFinished}
      {...overrides}
    />,
  );
  return { view, onClose, onFinished };
}

describe('ExplorerWishlistModal', () => {
  it('renders a section header per artist and a card per release', () => {
    renderModal();
    expect(screen.getByText('Boards of Canada')).toBeTruthy();
    expect(screen.getByText('Aphex Twin')).toBeTruthy();
    expect(document.querySelectorAll('.discog-card')).toHaveLength(4);
    expect(screen.getByText('2 artists · 4 releases')).toBeTruthy();
  });

  it('ticks everything except what is already owned', () => {
    renderModal();
    const boxes = [...document.querySelectorAll<HTMLInputElement>('.discog-card-cb')];
    expect(boxes.map((box) => box.checked)).toEqual([true, false, true, true]);
    // The owned card carries the class and the tick badge.
    expect(document.querySelectorAll('.discog-card.owned')).toHaveLength(1);
  });

  it('hides a filtered type and drops it from the footer count', () => {
    renderModal();
    // a1 (23) + a3 (5) + b1 (24) = 52
    expect(screen.getByText('3 releases · 52 tracks')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Singles' }));
    const single = document.querySelector('.discog-card[data-type="single"]') as HTMLElement;
    expect(single.style.display).toBe('none');
    expect(screen.getByText('2 releases · 47 tracks')).toBeTruthy();
  });

  it('select-all ticks even the owned release; deselect empties and locks submit', () => {
    renderModal();
    fireEvent.click(screen.getByRole('button', { name: 'Select All' }));
    expect(screen.getByText('4 releases · 60 tracks')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Deselect' }));
    expect(screen.getByText('0 releases · 0 tracks')).toBeTruthy();
    expect(screen.getByRole('button', { name: /Select releases/ })).toHaveProperty(
      'disabled',
      true,
    );
  });

  it('streams a submit, marks each album, and settles on Close', async () => {
    window.showToast = vi.fn();
    const urls: string[] = [];
    const bodies = [
      '{"album_id":"a1","status":"done","tracks_added":20,"tracks_skipped":3}\n',
      '{"album_id":"b1","status":"error","message":"no match"}\n',
    ];
    let i = 0;
    vi.stubGlobal(
      'fetch',
      vi.fn(async (url: RequestInfo | URL) => {
        urls.push(String(url));
        return ndjsonResponse([bodies[i++]!]);
      }),
    );

    const { onFinished } = renderModal();
    // Drop the singles so the run is a1 + b1 across two artists.
    fireEvent.click(screen.getByRole('button', { name: 'Singles' }));
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /Add 2 to Wishlist/ }));
    });

    await waitFor(() => expect(screen.getByText(/^Done —/)).toBeTruthy());
    expect(urls).toEqual([
      '/api/artist/art1/download-discography',
      '/api/artist/art2/download-discography',
    ]);
    expect(screen.getByText('Added 20 tracks, 3 skipped')).toBeTruthy();
    expect(screen.getByText('no match')).toBeTruthy();
    expect(screen.getByText('Done — 20 tracks added to wishlist')).toBeTruthy();
    expect(document.querySelector('#explorer-prog-a1')?.className).toContain('done');
    expect(document.querySelector('#explorer-prog-b1')?.className).toContain('error');

    expect(onFinished).toHaveBeenCalledWith(20);
    expect(window.showToast).toHaveBeenCalledWith('Added 20 tracks to wishlist', 'success');
    // The grid and the submit are gone; Cancel has become Close.
    expect(document.querySelector('#explorer-wishlist-grid')).toBeNull();
    expect(document.querySelector('#explorer-wishlist-submit')).toBeNull();
    expect(screen.getByRole('button', { name: 'Close' })).toBeTruthy();
  });

  it('closes from the header cross and from Cancel', () => {
    const { onClose } = renderModal();
    fireEvent.click(document.querySelector('.discog-modal-close') as HTMLElement);
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('renders into document.body, not the tree that scales', () => {
    // The tree carries a CSS transform, which would become the containing
    // block for a fixed overlay rendered inside it.
    const { view } = renderModal();
    expect(view.container.querySelector('#explorer-wishlist-overlay')).toBeNull();
    expect(document.body.querySelector('#explorer-wishlist-overlay')).toBeTruthy();
  });
});
