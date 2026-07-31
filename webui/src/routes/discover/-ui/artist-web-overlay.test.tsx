import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ArtWebOverlayProps } from './artist-web-overlay';

import { artistWeb } from '../-discover.artist-web';
import { ArtWebOverlay } from './artist-web-overlay';

/**
 * The Artist Web's overlay shell.
 *
 * Most of this is chrome, and chrome is cheap to check. The two parts that are
 * not are the ones with no visible failure: the shortcuts have to be bound while
 * it is open and gone once it is not, and unmounting has to release the graph —
 * a leaked FA2 worker and an unkilled WebGL context look exactly like a working
 * page until the tab is slow.
 */

const noopKeyHost = () => ({
  pathMode: () => false,
  panelOpen: () => false,
  exitPath: vi.fn(),
  clearSelection: vi.fn(),
  close: vi.fn(),
  focusSearch: () => false,
  fitToView: vi.fn(),
  zoom: vi.fn(),
  showHelp: vi.fn(),
});

function props(over: Partial<ArtWebOverlayProps> = {}): ArtWebOverlayProps {
  return {
    stats: '900 artists · 40 genres',
    lens: 'genre',
    sizeBy: 'popularity',
    pathMode: false,
    edgeDeclutter: false,
    hostRef: createRef<HTMLDivElement>(),
    sidebar: { open: false, counts: {}, colorOf: () => '#1db954' },
    legend: [],
    keyHost: noopKeyHost(),
    onClose: vi.fn(),
    onSearch: vi.fn(),
    onSearchEnter: vi.fn(),
    onSetLens: vi.fn(),
    onSetSize: vi.fn(),
    onTogglePath: vi.fn(),
    onToggleEdges: vi.fn(),
    onToggleFilter: vi.fn(),
    onZoom: vi.fn(),
    onFitToView: vi.fn(),
    onShowHelp: vi.fn(),
    onToggleGenre: vi.fn(),
    onClearGenreFilter: vi.fn(),
    ...over,
  };
}

beforeEach(() => {
  Object.assign(artistWeb, {
    sigma: null,
    graph: null,
    fa2: null,
    fa2Timer: null,
    fxRAF: null,
    genreFilter: null,
    spreadRoot: null,
    spreadSet: null,
    spreadActive: null,
  });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

// ── The lifecycle it owns ────────────────────────────────────────────────────

describe('mounting', () => {
  it('binds the shortcuts', () => {
    const keyHost = noopKeyHost();
    render(<ArtWebOverlay {...props({ keyHost })} />);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(keyHost.close).toHaveBeenCalled();
  });

  it('hands the caller the element sigma mounts into', () => {
    const hostRef = createRef<HTMLDivElement>();
    const { container } = render(<ArtWebOverlay {...props({ hostRef })} />);
    expect(hostRef.current).toBe(container.querySelector('#artist-web-canvas'));
    // The renderer sizes itself from this element, so it has to be the flex
    // child that actually grows — not a wrapper that collapses to nothing.
    expect(hostRef.current?.style.flex).toBe('1 1 0%'); //  `flex: 1`, expanded
    expect(hostRef.current?.style.minHeight).toBe('0px');
  });
});

describe('unmounting', () => {
  it('releases the renderer, the worker and the frame loop', () => {
    const cancel = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    const kill = vi.fn();
    const { unmount } = render(<ArtWebOverlay {...props()} />);

    // Stand in for what the page mounted after this rendered.
    artistWeb.sigma = { kill };
    artistWeb.fa2 = { kill: vi.fn() };
    artistWeb.fa2Timer = setTimeout(() => {}, 10_000);
    artistWeb.fxRAF = 31;
    const fa2 = artistWeb.fa2;

    unmount();

    expect(kill).toHaveBeenCalled();
    expect(fa2.kill).toHaveBeenCalled();
    expect(cancel).toHaveBeenCalledWith(31);
    expect(artistWeb.sigma).toBeNull();
    expect(artistWeb.graph).toBeNull();
    expect(artistWeb.fa2Timer).toBeNull();
  });

  it('unbinds the shortcuts, so Escape on the page below does nothing', () => {
    const keyHost = noopKeyHost();
    const { unmount } = render(<ArtWebOverlay {...props({ keyHost })} />);
    unmount();
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(keyHost.close).not.toHaveBeenCalled();
  });

  it('does not stack shortcuts across a remount', () => {
    const keyHost = noopKeyHost();
    const first = render(<ArtWebOverlay {...props({ keyHost })} />);
    first.unmount();
    render(<ArtWebOverlay {...props({ keyHost })} />);
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(keyHost.close).toHaveBeenCalledTimes(1);
  });
});

// ── The toolbar ──────────────────────────────────────────────────────────────

describe('the toolbar', () => {
  it('keeps the ids and classes style.css targets', () => {
    const { container } = render(
      <ArtWebOverlay
        {...props({
          sidebar: { open: true, counts: { Rock: 9 }, colorOf: () => '#1db954' },
          legend: [{ color: '#1db954', label: 'Rock', count: 9 }],
        })}
      />,
    );
    for (const sel of [
      '#artist-web-container.artist-map-container',
      '.artist-map-toolbar',
      '.artmap-nav-left',
      '.artmap-brand-icon',
      '.artmap-brand-text',
      '#artist-web-stats.artmap-stats',
      '.artmap-nav-center',
      '.artmap-search-icon',
      '#artist-web-search',
      '.artmap-nav-right',
      '.artweb-lens-toggle',
      '#artweb-path-btn',
      '#artweb-edges-btn',
      '#artweb-filter-btn',
      '.artmap-zoom-group',
      '.artmap-content-row',
      '#artweb-genre-sidebar',
      '#artweb-genre-sidebar-list',
      '#artist-web-canvas',
      '#artist-web-legend.artweb-legend',
      '#artist-web-search-results',
      '#artist-web-tooltip',
    ]) {
      expect(container.querySelector(sel), sel).not.toBeNull();
    }
  });

  it('marks the active lens, and only that one', () => {
    const { container } = render(<ArtWebOverlay {...props({ lens: 'community' })} />);
    const active = [...container.querySelectorAll('.artweb-lens-btn.active')].filter(
      (b) => !b.classList.contains('artweb-size-btn'),
    );
    expect(active.map((b) => b.getAttribute('data-lens'))).toEqual(['community']);
  });

  it('marks the active size mode, and only that one', () => {
    const { container } = render(<ArtWebOverlay {...props({ sizeBy: 'influence' })} />);
    const active = container.querySelectorAll('.artweb-size-btn.active');
    expect([...active].map((b) => b.getAttribute('data-size'))).toEqual(['influence']);
  });

  it('reports lens and size picks', () => {
    const p = props();
    const { container } = render(<ArtWebOverlay {...p} />);
    fireEvent.click(container.querySelector('[data-lens="discovery"]')!);
    expect(p.onSetLens).toHaveBeenCalledWith('discovery');
    fireEvent.click(container.querySelector('[data-size="connections"]')!);
    expect(p.onSetSize).toHaveBeenCalledWith('connections');
  });

  it('relabels the edges button when decluttering is on', () => {
    const { rerender } = render(<ArtWebOverlay {...props()} />);
    expect(screen.getByText('Edges')).toBeInTheDocument();
    rerender(<ArtWebOverlay {...props({ edgeDeclutter: true })} />);
    expect(screen.getByText('Strong')).toBeInTheDocument();
    expect(document.getElementById('artweb-edges-btn')).toHaveClass('active');
  });

  it('lights the path button only in path mode', () => {
    const { rerender } = render(<ArtWebOverlay {...props()} />);
    expect(document.getElementById('artweb-path-btn')).not.toHaveClass('active');
    rerender(<ArtWebOverlay {...props({ pathMode: true })} />);
    expect(document.getElementById('artweb-path-btn')).toHaveClass('active');
  });

  it('lights the filter button only while the sidebar is open', () => {
    const { rerender } = render(<ArtWebOverlay {...props()} />);
    expect(document.getElementById('artweb-filter-btn')).not.toHaveClass('active');
    rerender(
      <ArtWebOverlay {...props({ sidebar: { open: true, counts: {}, colorOf: () => '#fff' } })} />,
    );
    expect(document.getElementById('artweb-filter-btn')).toHaveClass('active');
  });

  it('zooms IN with a ratio below one', () => {
    const p = props();
    render(<ArtWebOverlay {...p} />);
    fireEvent.click(screen.getByLabelText('Zoom in'));
    expect(p.onZoom).toHaveBeenCalledWith(0.7);
    fireEvent.click(screen.getByLabelText('Zoom out'));
    // Below one is IN and above one is OUT — the pair is easy to swap and the
    // only symptom is that the buttons feel backwards.
    expect(p.onZoom).toHaveBeenLastCalledWith(1.4);
  });

  it('reports search input and the Enter key separately', () => {
    const p = props();
    const { container } = render(<ArtWebOverlay {...p} />);
    const input = container.querySelector('#artist-web-search')!;
    fireEvent.change(input, { target: { value: 'aphex' } });
    expect(p.onSearch).toHaveBeenCalledWith('aphex');
    expect(p.onSearchEnter).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(p.onSearchEnter).toHaveBeenCalled();
  });

  it('does not fire the Enter handler for other keys', () => {
    const p = props();
    const { container } = render(<ArtWebOverlay {...p} />);
    fireEvent.keyDown(container.querySelector('#artist-web-search')!, { key: 'a' });
    expect(p.onSearchEnter).not.toHaveBeenCalled();
  });

  it('wires the remaining toolbar buttons', () => {
    const p = props();
    render(<ArtWebOverlay {...p} />);
    fireEvent.click(document.getElementById('artweb-path-btn')!);
    fireEvent.click(document.getElementById('artweb-edges-btn')!);
    fireEvent.click(document.getElementById('artweb-filter-btn')!);
    fireEvent.click(screen.getByLabelText('Fit to view'));
    fireEvent.click(screen.getByLabelText('Guide and shortcuts'));
    fireEvent.click(screen.getByLabelText('Back to Discover'));
    for (const fn of [
      p.onTogglePath,
      p.onToggleEdges,
      p.onToggleFilter,
      p.onFitToView,
      p.onShowHelp,
      p.onClose,
    ]) {
      expect(fn).toHaveBeenCalled();
    }
  });
});

// ── The genre sidebar ────────────────────────────────────────────────────────

describe('the genre sidebar', () => {
  const counts = { Rock: 40, Techno: 25, Jazz: 12 };
  const GENRE_COLORS: Record<string, string> = {
    Rock: '#1db954',
    Techno: '#e91e63',
    Jazz: '#3f8cff',
  };
  const withSidebar = (over: Partial<ArtWebOverlayProps> = {}) =>
    props({
      sidebar: { open: true, counts, colorOf: (g) => GENRE_COLORS[g] ?? '#000000' },
      ...over,
    });

  it('is absent until the filter is opened', () => {
    const { container } = render(<ArtWebOverlay {...props()} />);
    expect(container.querySelector('#artweb-genre-sidebar')).toBeNull();
  });

  it('lists genres largest first', () => {
    const { container } = render(<ArtWebOverlay {...withSidebar()} />);
    const names = [...container.querySelectorAll('.artweb-genre-name')].map((n) => n.textContent);
    expect(names).toEqual(['Rock', 'Techno', 'Jazz']);
  });

  it('titles itself for the lens', () => {
    // Scoped to the header, because "Communities" is also a lens BUTTON — an
    // unscoped query would pass on the toolbar even with the heading wrong.
    const heading = (c: HTMLElement) =>
      c.querySelector('.artmap-genre-sidebar-header span')?.textContent;
    const { container } = render(<ArtWebOverlay {...withSidebar()} />);
    expect(heading(container)).toBe('Genres');
    cleanup();
    const second = render(<ArtWebOverlay {...withSidebar({ lens: 'community' })} />);
    // Community clusters are not genres and are not named like them.
    expect(heading(second.container)).toBe('Communities');
  });

  it('filters the list as you type, without touching the graph', () => {
    const p = withSidebar();
    const { container } = render(<ArtWebOverlay {...p} />);
    fireEvent.change(container.querySelector('.artmap-genre-sidebar-search')!, {
      target: { value: 'ec' },
    });
    const names = [...container.querySelectorAll('.artweb-genre-name')].map((n) => n.textContent);
    expect(names).toEqual(['Techno']);
    // Typing narrows what is LISTED. It must not select anything.
    expect(p.onToggleGenre).not.toHaveBeenCalled();
  });

  it('says so when the query matches nothing', () => {
    const { container } = render(<ArtWebOverlay {...withSidebar()} />);
    fireEvent.change(container.querySelector('.artmap-genre-sidebar-search')!, {
      target: { value: 'zzz' },
    });
    expect(screen.getByText('No genres')).toBeInTheDocument();
  });

  it('colours each dot from the lens it belongs to', () => {
    const { container } = render(<ArtWebOverlay {...withSidebar()} />);
    const dots = [...container.querySelectorAll('.artweb-genre-dot')] as HTMLElement[];
    // Each dot takes ITS OWN genre's colour, in the listed order — a shared
    // fallback or an off-by-one would still produce three coloured dots.
    expect(dots.map((d) => d.style.background)).toEqual([
      'rgb(29, 185, 84)',
      'rgb(233, 30, 99)',
      'rgb(63, 140, 255)',
    ]);
  });

  it('reports a genre pick', () => {
    const p = withSidebar();
    render(<ArtWebOverlay {...p} />);
    fireEvent.click(screen.getByText('Techno'));
    expect(p.onToggleGenre).toHaveBeenCalledWith('Techno');
  });

  it('offers a clear row only while something is selected, counting it', () => {
    const { container, rerender } = render(<ArtWebOverlay {...withSidebar()} />);
    expect(container.querySelector('.artweb-genre-clear')).toBeNull();

    artistWeb.genreFilter = new Set(['Rock', 'Jazz']);
    rerender(<ArtWebOverlay {...withSidebar()} />);
    const clear = container.querySelector('.artweb-genre-clear')!;
    expect(clear.textContent).toContain('(2)');
    expect(container.querySelectorAll('.artweb-genre-row.active')).toHaveLength(2);
  });

  it('counts only the selections still visible under the query', () => {
    // The count belongs to the rows on screen; showing a total that does not
    // match what is highlighted reads as a bug in the filter.
    artistWeb.genreFilter = new Set(['Rock', 'Jazz']);
    const { container } = render(<ArtWebOverlay {...withSidebar()} />);
    fireEvent.change(container.querySelector('.artmap-genre-sidebar-search')!, {
      target: { value: 'rock' },
    });
    expect(container.querySelector('.artweb-genre-clear')!.textContent).toContain('(1)');
  });

  it('reports a clear', () => {
    artistWeb.genreFilter = new Set(['Rock']);
    const p = withSidebar();
    const { container } = render(<ArtWebOverlay {...p} />);
    fireEvent.click(container.querySelector('.artweb-genre-clear')!);
    expect(p.onClearGenreFilter).toHaveBeenCalled();
  });
});

// ── The legend ───────────────────────────────────────────────────────────────

describe('the legend', () => {
  it('is absent when there is nothing to decode', () => {
    const { container } = render(<ArtWebOverlay {...props({ legend: [] })} />);
    // An empty box over the canvas is worse than no box.
    expect(container.querySelector('#artist-web-legend')).toBeNull();
  });

  it('shows a row per entry, with counts only where there are counts', () => {
    const { container } = render(
      <ArtWebOverlay
        {...props({
          legend: [
            { color: '#5b8def', label: 'Your library' },
            { color: '#ffb74d', label: 'To discover' },
          ],
        })}
      />,
    );
    expect(container.querySelectorAll('.artweb-legend-row')).toHaveLength(2);
    // Discovery's two-item key is a legend WITHOUT counts; rendering "undefined"
    // or a zero would both be wrong.
    expect(container.querySelectorAll('.artweb-legend-count')).toHaveLength(0);
    expect(screen.getByText('Your library')).toBeInTheDocument();
  });

  it('shows counts for the grouped lenses', () => {
    const { container } = render(
      <ArtWebOverlay {...props({ legend: [{ color: '#1db954', label: 'Rock', count: 40 }] })} />,
    );
    expect(container.querySelector('.artweb-legend-count')!.textContent).toBe('40');
  });
});
