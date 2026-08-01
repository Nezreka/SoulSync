import { useEffect, useRef, useState } from 'react';

import type { ArtMapInteractionHost } from '../-discover.artist-map.interaction';

import { artMap } from '../-discover.artist-map';
import { attachArtMapInteraction } from '../-discover.artist-map.interaction';
import {
  artMapAttachCanvas,
  artMapMeasureFull,
  artMapMeasureResize,
  artMapMeasureWithSidebar,
  artMapResetWorld,
  artMapTeardown,
} from '../-discover.artist-map.lifecycle';
import { artMapIslandMenu, artMapIslandNav } from '../-discover.artist-map.panel';

/**
 * The Artist Map's fullscreen overlay.
 *
 * Transcribed from `webui/index.html` 4364-4421 for the markup, and from
 * discover.js 8273-8299 / 8422-8443 / 10227-10253 for the canvas lifecycle.
 *
 * The lifecycle goes through `-discover.artist-map.lifecycle`, not through
 * effect bodies written here. Its failures are silent — a backing store at the
 * wrong scale merely looks blurry, a missed teardown leaks a rAF loop into a
 * page whose canvas is gone — so it is tested code being called, rather than
 * code that only runs in a browser.
 */

export type ArtMapKind = 'watchlist' | 'genre' | 'explorer';

export interface ArtMapSidebarGenre {
  name: string;
  count: number;
}

export interface ArtMapOverlayProps {
  kind: ArtMapKind;
  /** The toolbar's brand text: 'Artist Map' | 'Genre Map' | 'Artist Explorer'. */
  title: string;
  /** The free text right of the brand — counts, or the genre switcher. */
  stats: React.ReactNode;
  /** Present only on the genre map, and hidden on mobile. */
  sidebarGenres?: ArtMapSidebarGenre[];
  selectedGenre?: string;
  /** A loading overlay while the payload lands and the first frame is built. */
  loading?: string | null;
  host: ArtMapInteractionHost;
  onClose: () => void;
  onSearch: (query: string) => void;
  onToggleSimilar: () => void;
  onZoom: (factor: number) => void;
  onFitToView: () => void;
  onShowShortcuts: () => void;
  onSwitchGenre: (genre: string) => void;
  onFocusIsland: (index: number) => void;
  onIslandNav: (dir: number) => void;
  /** The panel, tooltip and menus, which own their own state. */
  children?: React.ReactNode;
}

/** How the three entry points measure themselves differs; this picks the right one. */
function measureFor(
  kind: ArtMapKind,
  container: HTMLElement,
  canvas: HTMLCanvasElement,
  sidebar: HTMLElement | null,
) {
  const toolbar = container.querySelector<HTMLElement>('.artist-map-toolbar');
  const toolbarHeight = toolbar ? toolbar.offsetHeight : null;
  if (kind === 'genre') {
    const row = container.querySelector<HTMLElement>('.artmap-content-row');
    return artMapMeasureWithSidebar(
      canvas.clientWidth,
      row?.clientHeight ?? 0,
      container.clientWidth,
      container.clientHeight,
      sidebar?.offsetWidth ?? 0,
    );
  }
  return artMapMeasureFull(container.clientWidth, container.clientHeight, toolbarHeight);
}

export function ArtMapOverlay({
  kind,
  title,
  stats,
  sidebarGenres,
  selectedGenre,
  loading,
  host,
  onClose,
  onSearch,
  onToggleSimilar,
  onZoom,
  onFitToView,
  onShowShortcuts,
  onSwitchGenre,
  onFocusIsland,
  onIslandNav,
  children,
}: ArtMapOverlayProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const sidebarRef = useRef<HTMLDivElement | null>(null);
  const [genreFilter, setGenreFilter] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);

  // Mount: measure, size, centre, wire. Unmount: tear the whole map down.
  //
  // The dependency list is deliberately EMPTY. Re-measuring on every prop change
  // would recentre the camera mid-session, because artMapResetWorld belongs to
  // opening a map and not to a re-render. Resizes are handled by the host's
  // `resized`, which re-measures WITHOUT resetting the world.
  useEffect(() => {
    const container = containerRef.current;
    const canvas = canvasRef.current;
    if (!container || !canvas) return;

    artMapAttachCanvas(canvas, measureFor(kind, container, canvas, sidebarRef.current));
    artMapResetWorld();
    const dispose = attachArtMapInteraction(canvas, host);

    return () => {
      dispose();
      artMapTeardown();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const nav = artMapIslandNav();
  const menu = menuOpen ? artMapIslandMenu() : [];
  const genres = (sidebarGenres ?? []).filter((g) =>
    g.name.toLowerCase().includes(genreFilter.trim().toLowerCase()),
  );

  return (
    <div ref={containerRef} className="artist-map-container" id="artist-map-container" style={{ display: 'flex' }}>
      <div className="artist-map-toolbar">
        <div className="artmap-nav-left">
          <button
            type="button"
            className="artmap-back"
            title="Back to Discover (Esc)"
            aria-label="Back to Discover"
            onClick={onClose}
          >
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2.5"
            >
              <path d="M19 12H5" />
              <path d="M12 19l-7-7 7-7" />
            </svg>
          </button>
          <div className="artmap-brand">
            <svg
              className="artmap-brand-icon"
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            >
              <circle cx="12" cy="12" r="3" />
              <circle cx="4" cy="6" r="2" />
              <circle cx="20" cy="6" r="2" />
              <circle cx="4" cy="18" r="2" />
              <circle cx="20" cy="18" r="2" />
              <line x1="6" y1="7" x2="10" y2="10" />
              <line x1="14" y1="10" x2="18" y2="7" />
              <line x1="6" y1="17" x2="10" y2="14" />
              <line x1="14" y1="14" x2="18" y2="17" />
            </svg>
            <span className="artmap-brand-text">{title}</span>
          </div>
          <div className="artmap-stats" id="artist-map-stats">
            {stats}
          </div>
        </div>

        <div className="artmap-nav-center">
          <div className="artmap-search-wrap">
            <svg
              className="artmap-search-icon"
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="11" cy="11" r="8" />
              <line x1="21" y1="21" x2="16.65" y2="16.65" />
            </svg>
            <input
              type="text"
              id="artist-map-search"
              placeholder="Search artists... (S)"
              onChange={(e) => onSearch(e.target.value)}
            />
          </div>
        </div>

        <div className="artmap-nav-right">
          <button
            type="button"
            className="artmap-tool-btn"
            id="artmap-toggle-similar"
            title="Toggle similar artists (H)"
            style={{ opacity: artMap._hideSimilar ? 0.4 : 1 }}
            onClick={onToggleSimilar}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="5" r="3" />
              <circle cx="5" cy="19" r="3" />
              <circle cx="19" cy="19" r="3" />
            </svg>
            <span>Filter</span>
          </button>
          <div className="artmap-zoom-group">
            <button
              type="button"
              className="artmap-tool-btn artmap-zoom"
              title="Zoom in (+)"
              onClick={() => onZoom(1.3)}
            >
              +
            </button>
            <button
              type="button"
              className="artmap-tool-btn artmap-zoom"
              title="Zoom out (-)"
              onClick={() => onZoom(0.7)}
            >
              −
            </button>
            <button
              type="button"
              className="artmap-tool-btn artmap-zoom"
              title="Fit to view (F)"
              onClick={onFitToView}
            >
              ⤢
            </button>
          </div>
          <button
            type="button"
            className="artmap-tool-btn"
            title="Keyboard shortcuts"
            aria-label="Keyboard shortcuts"
            onClick={onShowShortcuts}
          >
            ⌨
          </button>
        </div>
      </div>

      <div className="artmap-content-row">
        {sidebarGenres && sidebarGenres.length > 0 && (
          <div ref={sidebarRef} className="artmap-genre-sidebar" id="artmap-genre-sidebar">
            <div className="artmap-genre-sidebar-header">
              <span>Genres</span>
              <input
                type="text"
                className="artmap-genre-sidebar-search"
                placeholder="Filter..."
                value={genreFilter}
                onChange={(e) => setGenreFilter(e.target.value)}
              />
            </div>
            <div className="artmap-genre-sidebar-list" id="artmap-genre-sidebar-list">
              {genres.map((g) => (
                <button
                  type="button"
                  key={g.name}
                  className={
                    g.name === selectedGenre
                      ? 'artmap-genre-sidebar-item active'
                      : 'artmap-genre-sidebar-item'
                  }
                  onClick={() => onSwitchGenre(g.name)}
                >
                  <span className="artmap-genre-sidebar-name">{g.name}</span>
                  <span className="artmap-genre-sidebar-count">{g.count}</span>
                </button>
              ))}
            </div>
          </div>
        )}
        <canvas ref={canvasRef} id="artist-map-canvas" />
      </div>

      {nav && (
        <div id="artmap-island-nav" className="artmap-island-nav">
          <button
            type="button"
            title="Previous genre (←)"
            aria-label="Previous genre"
            onClick={() => onIslandNav(-1)}
          >
            ◀
          </button>
          <button
            type="button"
            className="artmap-island-nav-current"
            title="Jump to a genre"
            onClick={() => setMenuOpen((open) => !open)}
          >
            <span className="artmap-island-nav-name" style={{ color: `hsl(${nav.hue},80%,80%)` }}>
              {nav.display} ▾
            </span>
            <span className="artmap-island-nav-meta">
              {nav.count} artists · {nav.position} / {nav.total}
            </span>
          </button>
          <button
            type="button"
            title="Next genre (→)"
            aria-label="Next genre"
            onClick={() => onIslandNav(1)}
          >
            ▶
          </button>
        </div>
      )}

      {menuOpen && menu.length > 0 && (
        <div id="artmap-island-menu" className="artmap-island-menu">
          {menu.map((row) => (
            <button
              type="button"
              key={row.name}
              className={row.active ? 'artmap-island-menu-row active' : 'artmap-island-menu-row'}
              onClick={() => {
                setMenuOpen(false);
                onFocusIsland(row.index);
              }}
            >
              <span
                className="artmap-island-menu-dot"
                style={{ background: `hsl(${row.hue},75%,62%)` }}
              />
              <span className="artmap-island-menu-name">{row.name}</span>
              <span className="artmap-island-menu-count">{row.count}</span>
            </button>
          ))}
        </div>
      )}

      {loading && (
        <div id="artist-map-loading">
          <div className="artist-map-loading-content">
            <div className="watch-all-loading-spinner" />
            <div className="artist-map-loading-text">{loading}</div>
          </div>
        </div>
      )}

      {/*
        The tooltip and the search dropdown are NOT rendered here. The vanilla's
        static markup declares them empty and its JS writes into them by id; in
        React they are ArtMapTooltipView and ArtMapSearchResults, which arrive
        through `children`. Declaring placeholders here as well put two elements
        on the page per id, and getElementById reached the empty one.
      */}
      {children}
    </div>
  );
}

/** The resize path: re-measure and re-size WITHOUT recentring the camera. */
export function artMapHandleResize(
  container: HTMLElement,
  canvas: HTMLCanvasElement,
  sidebar: HTMLElement | null,
): void {
  const toolbar = container.querySelector<HTMLElement>('.artist-map-toolbar');
  const visible = sidebar && sidebar.style.display !== 'none' ? sidebar.offsetWidth || 0 : 0;
  artMapAttachCanvas(
    canvas,
    artMapMeasureResize(
      container.clientWidth,
      container.clientHeight,
      visible,
      toolbar ? toolbar.offsetHeight : null,
    ),
  );
}
