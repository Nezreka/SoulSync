import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ArtMapNode } from '../-discover.artist-map';
import type { ArtMapContextMenu, ArtMapTooltip } from '../-discover.artist-map.panel';

import { artMap } from '../-discover.artist-map';
import {
  ArtMapContextMenuView,
  ArtMapSearchResults,
  ArtMapShortcutsModal,
  ArtMapTooltipView,
  ARTMAP_CTX_MARGIN_X,
  ARTMAP_CTX_MARGIN_Y,
  ARTMAP_SEARCH_SHOWN,
  ARTMAP_TIP_IMG,
} from './artist-map-chrome';

/**
 * The measurements, pinned to the values in the source rather than to
 * themselves. Every other assertion in this file spells the number out, so a
 * changed constant fails there too rather than quietly moving the expectation.
 */
describe('the measurements', () => {
  it('match the vanilla', () => {
    expect(ARTMAP_TIP_IMG).toBe(88); //  9318
    expect(ARTMAP_SEARCH_SHOWN).toBe(8); //  9261
    expect(ARTMAP_CTX_MARGIN_X).toBe(200); //  10074
    expect(ARTMAP_CTX_MARGIN_Y).toBe(200); //  10075
  });
});

/**
 * The Artist Map's floating chrome.
 *
 * These four are positioned from POINTER coordinates rather than from layout, so
 * the interesting cases are all at the edges of the viewport — a tooltip that
 * hangs off the right, a menu opened in the bottom-right corner. Those are
 * invisible in a snapshot and obvious to a user.
 */

const node = (over: Partial<ArtMapNode> = {}): ArtMapNode =>
  ({
    id: 'n1',
    name: 'Aphex Twin',
    type: 'similar',
    genres: ['idm', 'ambient'],
    image_url: '/img/aphex.jpg',
    ...over,
  }) as ArtMapNode;

const tooltip = (over: Partial<ArtMapTooltip> = {}): ArtMapTooltip => ({
  name: 'Aphex Twin',
  genres: ['idm', 'ambient'],
  badge: '',
  connectionText: '4 connections',
  connections: 4,
  imageUrl: '/img/aphex.jpg',
  hasBitmap: false,
  ...over,
});

beforeEach(() => {
  Object.assign(artMap, { images: {}, edges: [] });
  Object.defineProperty(window, 'innerWidth', { value: 1000, configurable: true });
  Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ── The tooltip ──────────────────────────────────────────────────────────────

describe('the tooltip', () => {
  /** jsdom measures everything as zero; give the box a real size. */
  function sizeTooltip(w: number, h: number) {
    vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(w);
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(h);
  }

  it('renders nothing when nothing is hovered', () => {
    const { container } = render(
      <ArtMapTooltipView tip={null} node={null} clientX={0} clientY={0} />,
    );
    expect(container.querySelector('#artist-map-tooltip')).toBeNull();
  });

  it('shows the name, connections and up to three genres', () => {
    render(<ArtMapTooltipView tip={tooltip()} node={node()} clientX={10} clientY={10} />);
    expect(screen.getByText('Aphex Twin')).toBeInTheDocument();
    expect(screen.getByText('4 connections')).toBeInTheDocument();
    expect(screen.getByText('idm')).toBeInTheDocument();
  });

  it('omits the connection line entirely when there are none', () => {
    const { container } = render(
      <ArtMapTooltipView
        tip={tooltip({ connections: 0, connectionText: '' })}
        node={node()}
        clientX={10}
        clientY={10}
      />,
    );
    // "0 connections" is noise; the vanilla drops the row.
    expect(container.querySelector('.artmap-tip-conn')).toBeNull();
  });

  it('badges a watchlist artist only', () => {
    const { container, rerender } = render(
      <ArtMapTooltipView tip={tooltip()} node={node()} clientX={0} clientY={0} />,
    );
    expect(container.querySelector('.artmap-tip-badge')).toBeNull();
    rerender(
      <ArtMapTooltipView
        tip={tooltip({ badge: '★ Watchlist' })}
        node={node({ type: 'watchlist' })}
        clientX={0}
        clientY={0}
      />,
    );
    expect(container.querySelector('.artmap-tip-badge')!.textContent).toBe('★ Watchlist');
  });

  it('follows the pointer, offset clear of the cursor', () => {
    sizeTooltip(200, 120);
    const { container } = render(
      <ArtMapTooltipView tip={tooltip()} node={node()} clientX={300} clientY={400} />,
    );
    const box = container.querySelector('#artist-map-tooltip') as HTMLElement;
    expect(box.style.left).toBe('316px'); //  300 + 16, clear of the cursor
    expect(box.style.top).toBe('390px'); //  400 - 10
  });

  it('is held back from the right and bottom edges by its own size', () => {
    sizeTooltip(200, 120);
    const { container } = render(
      <ArtMapTooltipView tip={tooltip()} node={node()} clientX={980} clientY={790} />,
    );
    const box = container.querySelector('#artist-map-tooltip') as HTMLElement;
    // Measured AFTER the content exists — positioning during render would use a
    // zero-sized box and hang the tooltip off screen for a frame.
    expect(box.style.left).toBe('790px'); //  1000 - 200 - 10
    expect(box.style.top).toBe('670px'); //  800 - 120 - 10
  });

  it('paints the cached bitmap instead of reloading an image', () => {
    // A fresh <img src> churn-blanks while sweeping across dense bubbles; the
    // decoded bitmap is already in hand and cannot.
    const drawImage = vi.fn();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      drawImage,
    } as unknown as CanvasRenderingContext2D);
    artMap.images = { n1: { width: 88, height: 88 } as unknown as ImageBitmap };

    const { container } = render(
      <ArtMapTooltipView
        tip={tooltip({ hasBitmap: true })}
        node={node()}
        clientX={0}
        clientY={0}
      />,
    );
    expect(container.querySelector('img.artmap-tip-img')).toBeNull();
    const canvas = container.querySelector('canvas.artmap-tip-img') as HTMLCanvasElement;
    expect(canvas.width).toBe(88); //  9318 — the tooltip artwork's size
    expect(drawImage).toHaveBeenCalledWith(artMap.images.n1, 0, 0, 88, 88);
  });

  it('falls back to the url, then to a glyph', () => {
    const { container, rerender } = render(
      <ArtMapTooltipView tip={tooltip()} node={node()} clientX={0} clientY={0} />,
    );
    expect(container.querySelector('img.artmap-tip-img')).toHaveAttribute('src', '/img/aphex.jpg');
    rerender(
      <ArtMapTooltipView
        tip={tooltip({ imageUrl: '' })}
        node={node({ image_url: '' })}
        clientX={0}
        clientY={0}
      />,
    );
    expect(container.querySelector('.artmap-tip-img-fallback')).not.toBeNull();
  });

  it('survives a bitmap that will not draw', () => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
      drawImage: () => {
        throw new Error('detached bitmap');
      },
    } as unknown as CanvasRenderingContext2D);
    artMap.images = { n1: {} as unknown as ImageBitmap };
    // Throwing here would tear down the page mid-hover.
    expect(() =>
      render(
        <ArtMapTooltipView
          tip={tooltip({ hasBitmap: true })}
          node={node()}
          clientX={0}
          clientY={0}
        />,
      ),
    ).not.toThrow();
  });
});

// ── The context menu ─────────────────────────────────────────────────────────

describe('the context menu', () => {
  const menu = (over: Partial<ArtMapContextMenu> = {}): ArtMapContextMenu => ({
    hasId: true,
    bestId: 'sp123',
    bestSource: 'spotify',
    watchLabel: 'Add to Watchlist',
    ...over,
  });

  const handlers = () => ({
    onArtistInfo: vi.fn(),
    onToggleWatchlist: vi.fn(),
    onClose: vi.fn(),
    buildDetailPath: (id: string, source: string) => `/artist-detail/${source}/${id}`,
  });

  const show = (over: Partial<ArtMapContextMenu> = {}, x = 100, y = 100, h = handlers()) =>
    render(
      <ArtMapContextMenuView menu={menu(over)} node={node()} clientX={x} clientY={y} {...h} />,
    );

  it('renders nothing with no menu', () => {
    const { container } = render(
      <ArtMapContextMenuView menu={null} node={null} clientX={0} clientY={0} {...handlers()} />,
    );
    expect(container.querySelector('#artist-map-context')).toBeNull();
  });

  it('opens at the pointer', () => {
    const { container } = show({}, 120, 240);
    const el = container.querySelector('#artist-map-context') as HTMLElement;
    expect([el.style.left, el.style.top]).toEqual(['120px', '240px']);
  });

  it('is held back from the corner so it does not open off screen', () => {
    const { container } = show({}, 990, 795);
    const el = container.querySelector('#artist-map-context') as HTMLElement;
    expect(el.style.left).toBe('800px'); //  1000 viewport - the 200px margin
    expect(el.style.top).toBe('600px'); //  800 viewport - the 200px margin
  });

  it('links the discography to the best available provider', () => {
    const { container } = show();
    expect(container.querySelector('a.artmap-ctx-item')).toHaveAttribute(
      'href',
      '/artist-detail/spotify/sp123',
    );
  });

  it('disables the discography link when no provider id is known', () => {
    const { container } = show({ hasId: false, bestId: '' });
    const link = container.querySelector('a.artmap-ctx-item') as HTMLElement;
    expect(link).toHaveAttribute('href', '#');
    expect(link).toHaveAttribute('aria-disabled', 'true');
    // A live '#' link would scroll the page to the top under the overlay.
    expect(link.style.pointerEvents).toBe('none');
  });

  it('disables Artist Info without an id, and does not call it', () => {
    const h = handlers();
    render(
      <ArtMapContextMenuView
        menu={menu({ hasId: false, bestId: '' })}
        node={node()}
        clientX={0}
        clientY={0}
        {...h}
      />,
    );
    const info = screen.getByText('Artist Info').closest('button')!;
    expect(info).toBeDisabled();
    fireEvent.click(info);
    expect(h.onArtistInfo).not.toHaveBeenCalled();
  });

  it('opens Artist Info and closes the menu', () => {
    const h = handlers();
    show({}, 10, 10, h);
    fireEvent.click(screen.getByText('Artist Info').closest('button')!);
    expect(h.onArtistInfo).toHaveBeenCalledWith(node());
    expect(h.onClose).toHaveBeenCalled();
  });

  it('says whether the artist is already watched', () => {
    const { rerender } = show();
    expect(screen.getByText('Add to Watchlist')).toBeInTheDocument();
    rerender(
      <ArtMapContextMenuView
        menu={menu({ watchLabel: 'On Watchlist' })}
        node={node({ type: 'watchlist' })}
        clientX={0}
        clientY={0}
        {...handlers()}
      />,
    );
    expect(screen.getByText('On Watchlist')).toBeInTheDocument();
  });

  it('reports a watchlist toggle with the node and the resolved ids', () => {
    const h = handlers();
    show({}, 10, 10, h);
    fireEvent.click(screen.getByText('Add to Watchlist').closest('button')!);
    expect(h.onToggleWatchlist).toHaveBeenCalledWith(node(), menu());
    // Every row dismisses the menu; leaving it open over the result is the kind
    // of thing that only shows up when you actually use the map.
    expect(h.onClose).toHaveBeenCalled();
  });

  it('closes on the next click anywhere — but not on the one that opened it', () => {
    vi.useFakeTimers();
    const h = handlers();
    show({}, 10, 10, h);
    // The opening right-click is still propagating; closing now would make the
    // menu flash and vanish.
    window.dispatchEvent(new MouseEvent('click'));
    expect(h.onClose).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(20);
    });
    window.dispatchEvent(new MouseEvent('click'));
    expect(h.onClose).toHaveBeenCalledTimes(1);
    vi.useRealTimers();
  });

  it('leaves no window listener behind when it unmounts', () => {
    vi.useFakeTimers();
    const h = handlers();
    const { unmount } = show({}, 10, 10, h);
    act(() => {
      vi.advanceTimersByTime(20);
    });
    unmount();
    window.dispatchEvent(new MouseEvent('click'));
    expect(h.onClose).not.toHaveBeenCalled();
    vi.useRealTimers();
  });
});

// ── The shortcuts sheet ──────────────────────────────────────────────────────

describe('the shortcuts sheet', () => {
  it('lists every shortcut the map advertises', () => {
    const { container } = render(<ArtMapShortcutsModal onClose={vi.fn()} />);
    expect(container.querySelectorAll('.artmap-shortcut')).toHaveLength(10);
    expect(screen.getByText('Toggle similar artists')).toBeInTheDocument();
    expect(screen.getByText('Show connections')).toBeInTheDocument();
  });

  it('renders a two-key row as two separate keycaps', () => {
    const { container } = render(<ArtMapShortcutsModal onClose={vi.fn()} />);
    const zoom = [...container.querySelectorAll('.artmap-shortcut')].find((r) =>
      r.textContent?.includes('Zoom in / out'),
    )!;
    expect([...zoom.querySelectorAll('kbd')].map((k) => k.textContent)).toEqual(['+', '-']);
  });

  it('closes on the backdrop but not on the card', () => {
    const onClose = vi.fn();
    const { container } = render(<ArtMapShortcutsModal onClose={onClose} />);
    fireEvent.click(container.querySelector('.artmap-shortcuts-modal')!);
    expect(onClose).not.toHaveBeenCalled();
    fireEvent.click(container.querySelector('#artmap-shortcuts-overlay')!);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('closes on the × button', () => {
    const onClose = vi.fn();
    render(<ArtMapShortcutsModal onClose={onClose} />);
    fireEvent.click(screen.getByLabelText('Close'));
    expect(onClose).toHaveBeenCalled();
  });
});

// ── The search dropdown ──────────────────────────────────────────────────────

describe('the search dropdown', () => {
  it('is absent below the minimum query length', () => {
    const { container } = render(
      <ArtMapSearchResults state={{ kind: 'hidden' }} onExplore={vi.fn()} />,
    );
    expect(container.querySelector('#artist-map-search-results')).toBeNull();
  });

  it.each([
    ['searching', 'Searching…'],
    ['empty', 'No artists found'],
    ['failed', 'Search failed — try again'],
  ] as const)('says %s', (kind, text) => {
    render(<ArtMapSearchResults state={{ kind }} onExplore={vi.fn()} />);
    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it('distinguishes a failure from an empty result', () => {
    // Both are "no rows"; conflating them tells a user their library is empty
    // when the request actually failed.
    const { rerender } = render(
      <ArtMapSearchResults state={{ kind: 'failed' }} onExplore={vi.fn()} />,
    );
    expect(screen.queryByText('No artists found')).toBeNull();
    rerender(<ArtMapSearchResults state={{ kind: 'empty' }} onExplore={vi.fn()} />);
    expect(screen.queryByText('Search failed — try again')).toBeNull();
  });

  it('lists at most eight hits, however many arrived', () => {
    const artists = Array.from({ length: 20 }, (_, i) => ({ name: `Artist ${i}` }));
    const { container } = render(
      <ArtMapSearchResults state={{ kind: 'results', artists }} onExplore={vi.fn()} />,
    );
    expect(container.querySelectorAll('.artist-map-search-item')).toHaveLength(8); //  9261
    expect(screen.getByText('Artist 0')).toBeInTheDocument();
    expect(screen.queryByText('Artist 8')).toBeNull();
  });

  it('explores by NAME — the map has no notion of a search id', () => {
    const onExplore = vi.fn();
    render(
      <ArtMapSearchResults
        state={{ kind: 'results', artists: [{ name: 'Boards of Canada' }] }}
        onExplore={onExplore}
      />,
    );
    fireEvent.click(screen.getByText('Boards of Canada'));
    expect(onExplore).toHaveBeenCalledWith('Boards of Canada');
  });
});
