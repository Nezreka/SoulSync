import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ArtMapIsland } from '../-discover.artist-map';
import type { ArtMapInteractionHost } from '../-discover.artist-map.interaction';

import { artMap } from '../-discover.artist-map';
import { ArtMapOverlay, artMapHandleResize } from './artist-map-overlay';

/**
 * The overlay shell.
 *
 * The chrome is ordinary rendering, but the canvas lifecycle is not: its
 * failures are silent in a browser and invisible in a snapshot. So the cases
 * that matter here are that mounting really sizes the canvas and wires the
 * interaction, and that unmounting really tears the map down — a leaked rAF loop
 * against a detached canvas is the specific thing React makes easy to ship.
 */

const noopHost = (): ArtMapInteractionHost => ({
  isVisible: () => true,
  render: vi.fn(),
  ensureAmbient: vi.fn(),
  emitRipple: vi.fn(),
  showTooltip: vi.fn(),
  showPanelArtist: vi.fn(),
  animateConstellation: vi.fn(),
  showContextMenu: vi.fn(),
  hideContextMenu: vi.fn(),
  close: vi.fn(),
  zoom: vi.fn(),
  fitToView: vi.fn(),
  focusSearch: () => false,
  toggleSimilar: vi.fn(),
  islandNav: vi.fn(),
  resized: vi.fn(),
});

function props(over: Partial<Parameters<typeof ArtMapOverlay>[0]> = {}) {
  return {
    kind: 'watchlist' as const,
    title: 'Artist Map',
    stats: '12 watchlist · 340 similar',
    host: noopHost(),
    onClose: vi.fn(),
    onSearch: vi.fn(),
    onToggleSimilar: vi.fn(),
    onZoom: vi.fn(),
    onFitToView: vi.fn(),
    onShowShortcuts: vi.fn(),
    onSwitchGenre: vi.fn(),
    onFocusIsland: vi.fn(),
    onIslandNav: vi.fn(),
    ...over,
  };
}

const scale = vi.fn();

beforeEach(() => {
  scale.mockClear();
  // jsdom has no 2D context, and every element measures 0 — give the container
  // and canvas real sizes so the measurement path is actually exercised.
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    scale,
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLDivElement.prototype, 'clientWidth', 'get').mockReturnValue(1400);
  // The canvas is NARROWER than the container on the genre map, by the sidebar —
  // equal widths would hide a port that measured the container instead.
  vi.spyOn(HTMLCanvasElement.prototype, 'clientWidth', 'get').mockReturnValue(1180);
  vi.spyOn(HTMLCanvasElement.prototype, 'clientHeight', 'get').mockReturnValue(844);
  // The content row is NOT the container: in a browser it is what is left below
  // the toolbar. Giving every div one height would make those two measurements
  // indistinguishable, and a port that read the wrong one would still pass.
  vi.spyOn(HTMLDivElement.prototype, 'clientHeight', 'get').mockImplementation(
    function (this: HTMLDivElement) {
      // 812, not 900 - 56: three distinct numbers, so reading the container, the
      // full measurement and the row can never be confused for one another.
      return this.classList.contains('artmap-content-row') ? 812 : 900;
    },
  );
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(56);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockImplementation(
    function (this: HTMLElement) {
      return this.classList.contains('artmap-genre-sidebar') ? 240 : 0;
    },
  );
  Object.assign(artMap, {
    placed: [],
    images: {},
    _islands: undefined,
    _oneIsland: false,
    _focusIdx: 0,
    _hideSimilar: false,
    _anim: { running: false, raf: null, last: 0 },
    _loadToken: 0,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ── The canvas lifecycle ─────────────────────────────────────────────────────

describe('mounting', () => {
  it('sizes the canvas from the measured container', () => {
    render(<ArtMapOverlay {...props()} />);
    // 900 container minus a 56px toolbar.
    expect([artMap.width, artMap.height]).toEqual([1400, 844]);
    expect(artMap.canvas).not.toBeNull();
    expect(scale).toHaveBeenCalled();
  });

  it('centres the camera and empties the world', () => {
    artMap.placed = [{ id: 1 } as never];
    render(<ArtMapOverlay {...props()} />);
    expect([artMap.offsetX, artMap.offsetY]).toEqual([700, 422]);
    expect(artMap.placed).toEqual([]);
  });

  it('wires the interaction to the canvas', () => {
    const host = noopHost();
    const { container } = render(<ArtMapOverlay {...props({ host })} />);
    const canvas = container.querySelector('canvas') as HTMLCanvasElement;
    // A PAN, not a bare mousemove. Moving over empty water changes nothing about
    // the hover, and the handler deliberately does not redraw for that — so a
    // mousemove alone would assert something the code correctly never does.
    fireEvent.mouseDown(canvas, { button: 0, clientX: 10, clientY: 10 });
    fireEvent.mouseMove(canvas, { clientX: 40, clientY: 30 });
    expect(host.render).toHaveBeenCalled();
    expect(artMap.offsetX).toBe(730); //  centred at 700, panned by 30
  });

  it('subtracts the sidebar when the canvas has not been laid out yet', () => {
    // First paint: the canvas reports 0, so the measurement falls back to the
    // container minus the sidebar. That fallback is the only consumer of the
    // sidebar ref, so nothing else proves the ref is even attached.
    vi.spyOn(HTMLCanvasElement.prototype, 'clientWidth', 'get').mockReturnValue(0);
    render(
      <ArtMapOverlay
        {...props({
          kind: 'genre',
          sidebarGenres: [{ name: 'Rock', count: 9 }],
        })}
      />,
    );
    expect(artMap.width).toBe(1160); //  1400 container - 240 sidebar
  });

  it('measures the GENRE map differently, allowing for its sidebar', () => {
    // The genre map prefers the canvas's own laid-out size and the content row's
    // height; using the full-width measurement would run it under the sidebar.
    render(
      <ArtMapOverlay
        {...props({
          kind: 'genre',
          sidebarGenres: [{ name: 'Rock', count: 9 }],
        })}
      />,
    );
    expect(artMap.width).toBe(1180); //  the canvas's own width, not the container's
    expect(artMap.height).toBe(812); //  the content ROW, not the container (900)
    //                                    nor the full measurement (900 - 56).
  });
});

describe('unmounting', () => {
  it('tears the map down', () => {
    const { unmount } = render(<ArtMapOverlay {...props()} />);
    unmount();
    expect(artMap.canvas).toBeNull();
    expect(artMap.ctx).toBeNull();
    expect(artMap._anim.running).toBe(false);
  });

  it('orphans an in-flight image stream', () => {
    // The vanilla relies on the next OPEN bumping the token; an unmount is not
    // an open, so without this a stream keeps writing into a dead world.
    const { unmount } = render(<ArtMapOverlay {...props()} />);
    const before = artMap._loadToken as number;
    unmount();
    expect(artMap._loadToken).toBe(before + 1);
  });

  it('detaches the interaction, so a later event does nothing', () => {
    const host = noopHost();
    const { container, unmount } = render(<ArtMapOverlay {...props({ host })} />);
    const canvas = container.querySelector('canvas') as HTMLCanvasElement;
    unmount();
    (host.render as ReturnType<typeof vi.fn>).mockClear();
    // The same pan that renders while mounted, plus a window-level shortcut —
    // the canvas listener and the window one are detached by different lines.
    fireEvent.mouseDown(canvas, { button: 0, clientX: 10, clientY: 10 });
    fireEvent.mouseMove(canvas, { clientX: 40, clientY: 30 });
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(host.render).not.toHaveBeenCalled();
    expect(host.close).not.toHaveBeenCalled();
  });

  it('does not stack listeners across a remount', () => {
    // The vanilla's guard is a flag on the canvas ELEMENT, which React recreates
    // per mount — so only the dispose keeps a remount from doubling up.
    const host = noopHost();
    const first = render(<ArtMapOverlay {...props({ host })} />);
    first.unmount();
    render(<ArtMapOverlay {...props({ host })} />);
    (host.close as ReturnType<typeof vi.fn>).mockClear();
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(host.close).toHaveBeenCalledTimes(1);
  });
});

// ── The chrome ───────────────────────────────────────────────────────────────

describe('the toolbar', () => {
  it('shows the title and stats it is given', () => {
    render(<ArtMapOverlay {...props({ title: 'Genre Map', stats: 'Rock · 812 artists' })} />);
    expect(screen.getByText('Genre Map')).toBeInTheDocument();
    expect(screen.getByText('Rock · 812 artists')).toBeInTheDocument();
  });

  it('keeps the ids and classes style.css targets', () => {
    const { container } = render(<ArtMapOverlay {...props()} />);
    for (const sel of [
      '#artist-map-container',
      '.artist-map-toolbar',
      '.artmap-nav-left',
      '.artmap-nav-center',
      '.artmap-nav-right',
      '#artist-map-search',
      '#artist-map-stats',
      '.artmap-content-row',
      '#artist-map-canvas',
      // NOT '#artist-map-tooltip' / '#artist-map-search-results': the chrome
      // components own those, and rendering them here too duplicated the ids.
    ]) {
      expect(container.querySelector(sel), sel).not.toBeNull();
    }
  });

  it('routes each control to its own callback', () => {
    const p = props();
    render(<ArtMapOverlay {...p} />);
    fireEvent.click(screen.getByLabelText('Back to Discover'));
    expect(p.onClose).toHaveBeenCalledTimes(1);
    fireEvent.change(screen.getByPlaceholderText('Search artists... (S)'), {
      target: { value: 'aphex' },
    });
    expect(p.onSearch).toHaveBeenCalledWith('aphex');
    fireEvent.click(screen.getByTitle('Toggle similar artists (H)'));
    expect(p.onToggleSimilar).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTitle('Zoom in (+)'));
    expect(p.onZoom).toHaveBeenCalledWith(1.3);
    fireEvent.click(screen.getByTitle('Zoom out (-)'));
    expect(p.onZoom).toHaveBeenCalledWith(0.7);
    fireEvent.click(screen.getByTitle('Fit to view (F)'));
    expect(p.onFitToView).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByLabelText('Keyboard shortcuts'));
    expect(p.onShowShortcuts).toHaveBeenCalledTimes(1);
  });

  it('dims the filter button while similar artists are hidden', () => {
    artMap._hideSimilar = true;
    render(<ArtMapOverlay {...props()} />);
    expect(screen.getByTitle('Toggle similar artists (H)').style.opacity).toBe('0.4');
  });
});

describe('the genre sidebar', () => {
  const sidebarGenres = [
    { name: 'Rock', count: 40 },
    { name: 'Jazz', count: 12 },
    { name: 'Folk', count: 3 },
  ];

  it('is absent unless genres were supplied', () => {
    const { container } = render(<ArtMapOverlay {...props()} />);
    expect(container.querySelector('#artmap-genre-sidebar')).toBeNull();
  });

  it('lists the genres and marks the selected one', () => {
    render(<ArtMapOverlay {...props({ kind: 'genre', sidebarGenres, selectedGenre: 'Jazz' })} />);
    expect(screen.getByText('Rock')).toBeInTheDocument();
    expect(screen.getByText('Jazz').closest('button')?.className).toContain('active');
    expect(screen.getByText('Rock').closest('button')?.className).not.toContain('active');
  });

  it('filters the list as you type, case-insensitively', () => {
    render(<ArtMapOverlay {...props({ kind: 'genre', sidebarGenres })} />);
    fireEvent.change(screen.getByPlaceholderText('Filter...'), {
      target: { value: 'o' },
    });
    expect(screen.getByText('Rock')).toBeInTheDocument();
    expect(screen.getByText('Folk')).toBeInTheDocument();
    expect(screen.queryByText('Jazz')).toBeNull();
  });

  it('switches genre on click', () => {
    const p = props({ kind: 'genre' as const, sidebarGenres });
    render(<ArtMapOverlay {...p} />);
    fireEvent.click(screen.getByText('Jazz'));
    expect(p.onSwitchGenre).toHaveBeenCalledWith('Jazz');
  });
});

describe('the island nav', () => {
  const islands: ArtMapIsland[] = [
    { name: 'Rock', cx: 0, cy: 0, r: 500, hue: 42, count: 900 },
    { name: 'hip hop', cx: 900, cy: 0, r: 200, hue: 180, count: 12 },
  ];

  it('is absent outside one-island mode', () => {
    artMap._islands = islands;
    artMap._oneIsland = false;
    const { container } = render(<ArtMapOverlay {...props()} />);
    expect(container.querySelector('#artmap-island-nav')).toBeNull();
  });

  it('shows the focused island, uppercased, with its position', () => {
    artMap._islands = islands;
    artMap._oneIsland = true;
    artMap._focusIdx = 1;
    render(<ArtMapOverlay {...props()} />);
    expect(screen.getByText(/HIP HOP/)).toBeInTheDocument();
    expect(screen.getByText('12 artists · 2 / 2')).toBeInTheDocument();
  });

  it('steps in both directions', () => {
    artMap._islands = islands;
    artMap._oneIsland = true;
    const p = props();
    render(<ArtMapOverlay {...p} />);
    fireEvent.click(screen.getByLabelText('Previous genre'));
    expect(p.onIslandNav).toHaveBeenCalledWith(-1);
    fireEvent.click(screen.getByLabelText('Next genre'));
    expect(p.onIslandNav).toHaveBeenCalledWith(1);
  });

  it('opens a jump menu, marks the current island, and closes on pick', () => {
    artMap._islands = islands;
    artMap._oneIsland = true;
    artMap._focusIdx = 0;
    const p = props();
    const { container } = render(<ArtMapOverlay {...p} />);
    expect(container.querySelector('#artmap-island-menu')).toBeNull();

    fireEvent.click(screen.getByTitle('Jump to a genre'));
    const rows = [...container.querySelectorAll('.artmap-island-menu-row')];
    expect(rows.map((r) => r.querySelector('.artmap-island-menu-name')?.textContent)).toEqual([
      'Rock',
      'hip hop',
    ]);
    expect(rows[0].className).toContain('active');

    fireEvent.click(rows[1]);
    expect(p.onFocusIsland).toHaveBeenCalledWith(1);
    expect(container.querySelector('#artmap-island-menu')).toBeNull();
  });

  it('toggles the menu shut on a second click', () => {
    artMap._islands = islands;
    artMap._oneIsland = true;
    const { container } = render(<ArtMapOverlay {...props()} />);
    fireEvent.click(screen.getByTitle('Jump to a genre'));
    expect(container.querySelector('#artmap-island-menu')).not.toBeNull();
    fireEvent.click(screen.getByTitle('Jump to a genre'));
    expect(container.querySelector('#artmap-island-menu')).toBeNull();
  });
});

describe('the loading overlay', () => {
  it('appears only while a message is set', () => {
    const { container, rerender } = render(<ArtMapOverlay {...props()} />);
    expect(container.querySelector('#artist-map-loading')).toBeNull();
    rerender(<ArtMapOverlay {...props({ loading: 'Building artist map...' })} />);
    expect(screen.getByText('Building artist map...')).toBeInTheDocument();
  });
});

// ── Resize ───────────────────────────────────────────────────────────────────

describe('artMapHandleResize', () => {
  it('re-sizes WITHOUT recentring the camera', () => {
    const { container } = render(<ArtMapOverlay {...props()} />);
    artMap.offsetX = 123;
    artMap.offsetY = 456;
    artMapHandleResize(
      container.querySelector('#artist-map-container') as HTMLElement,
      container.querySelector('canvas') as HTMLCanvasElement,
      null,
    );
    // A resize keeps what you were looking at; only an OPEN recentres.
    expect([artMap.offsetX, artMap.offsetY]).toEqual([123, 456]);
    expect([artMap.width, artMap.height]).toEqual([1400, 844]);
  });

  it('subtracts a visible sidebar and ignores a hidden one', () => {
    const { container } = render(
      <ArtMapOverlay
        {...props({
          kind: 'genre',
          sidebarGenres: [{ name: 'Rock', count: 1 }],
        })}
      />,
    );
    const el = container.querySelector('#artist-map-container') as HTMLElement;
    const canvas = container.querySelector('canvas') as HTMLCanvasElement;
    const sidebar = container.querySelector('#artmap-genre-sidebar') as HTMLElement;
    vi.spyOn(sidebar, 'offsetWidth', 'get').mockReturnValue(260);

    artMapHandleResize(el, canvas, sidebar);
    expect(artMap.width).toBe(1140);

    sidebar.style.display = 'none';
    artMapHandleResize(el, canvas, sidebar);
    expect(artMap.width).toBe(1400);
  });
});
