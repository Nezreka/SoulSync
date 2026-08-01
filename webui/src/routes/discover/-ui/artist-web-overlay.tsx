import { useEffect, useState } from 'react';

import type { WebLens, WebSizeBy } from '../-discover.artist-web';
import type { WebKeyHost } from '../-discover.artist-web.lifecycle';
import type { WebLegendItem } from '../-discover.artist-web.panel';

import {
  webEdgeButtonLabel,
  webGenreRows,
  webSidebarHeading,
} from '../-discover.artist-web.controller';
import { attachWebKeys, webTeardown } from '../-discover.artist-web.lifecycle';

/**
 * The Artist Web's fullscreen overlay.
 *
 * Transcribed from `webui/index.html` 4455-4518 for the markup, and from
 * discover.js 6659-6683 / 7319-7351 for the lifecycle it owns.
 *
 * It does NOT mount sigma. The graph does not exist until a lens has been
 * fetched and built, which happens well after this renders, so the renderer is
 * the page's to mount into `hostRef` — and this component's job is to guarantee
 * it is released, whatever the page did with it.
 */

export interface ArtWebSidebarState {
  open: boolean;
  counts: Record<string, number>;
  /**
   * The active lens's colour function.
   *
   * Genre and Community colour the same name differently — Community's palette
   * is assigned by cluster rank, not by genre — so the dot has to come from the
   * lens's own map, not from a shared table.
   */
  colorOf: (group: string) => string;
}

export interface ArtWebOverlayProps {
  /** The free text right of the brand — counts, or "· settling…" while it settles. */
  stats: React.ReactNode;
  lens: WebLens;
  sizeBy: WebSizeBy;
  pathMode: boolean;
  edgeDeclutter: boolean;
  /** Where the page mounts sigma. Owned by the caller, released by us. */
  hostRef: React.RefObject<HTMLDivElement | null>;
  sidebar: ArtWebSidebarState;
  legend: WebLegendItem[];
  keyHost: WebKeyHost;
  onClose: () => void;
  onSearch: (query: string) => void;
  onSearchEnter: () => void;
  onSetLens: (lens: WebLens) => void;
  onSetSize: (size: WebSizeBy) => void;
  onTogglePath: () => void;
  onToggleEdges: () => void;
  onToggleFilter: () => void;
  onZoom: (ratio: number) => void;
  onFitToView: () => void;
  onShowHelp: () => void;
  onToggleGenre: (genre: string) => void;
  onClearGenreFilter: () => void;
  /** The side panel, tooltip, path hint and hint bubbles, which own their state. */
  children?: React.ReactNode;
}

const LENSES: { id: WebLens; label: string; title?: string }[] = [
  { id: 'genre', label: 'Genre' },
  { id: 'community', label: 'Communities' },
  { id: 'discovery', label: 'Discovery' },
];

const SIZES: { id: WebSizeBy; label: string; title?: string }[] = [
  { id: 'popularity', label: 'Popular' },
  { id: 'connections', label: 'Links' },
  { id: 'influence', label: 'Influence', title: 'Betweenness — who bridges the network' },
];

export function ArtWebOverlay({
  stats,
  lens,
  sizeBy,
  pathMode,
  edgeDeclutter,
  hostRef,
  sidebar,
  legend,
  keyHost,
  onClose,
  onSearch,
  onSearchEnter,
  onSetLens,
  onSetSize,
  onTogglePath,
  onToggleEdges,
  onToggleFilter,
  onZoom,
  onFitToView,
  onShowHelp,
  onToggleGenre,
  onClearGenreFilter,
  children,
}: ArtWebOverlayProps) {
  const [genreQuery, setGenreQuery] = useState('');

  // Bind the shortcuts on mount; on unmount, unbind AND release the graph.
  //
  // The dependency list is empty deliberately. `keyHost`'s methods read live
  // state through closures the page owns, so rebinding on every render would
  // churn a document-level listener for nothing — and a teardown in a
  // re-running effect would kill the renderer mid-session.
  useEffect(() => {
    const disposeKeys = attachWebKeys(keyHost);
    return () => {
      disposeKeys();
      webTeardown();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rows = sidebar.open ? webGenreRows(sidebar.counts, genreQuery) : [];
  const activeCount = rows.filter((r) => r.active).length;

  return (
    <div className="artist-map-container" id="artist-web-container" style={{ display: 'flex' }}>
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
              <circle cx="12" cy="12" r="2" />
              <circle cx="5" cy="5" r="1.6" />
              <circle cx="19" cy="5" r="1.6" />
              <circle cx="5" cy="19" r="1.6" />
              <circle cx="19" cy="19" r="1.6" />
              <line x1="12" y1="12" x2="5" y2="5" />
              <line x1="12" y1="12" x2="19" y2="5" />
              <line x1="12" y1="12" x2="5" y2="19" />
              <line x1="12" y1="12" x2="19" y2="19" />
            </svg>
            <span className="artmap-brand-text">Artist Web</span>
          </div>
          <div className="artmap-stats" id="artist-web-stats">
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
              id="artist-web-search"
              placeholder="Search artists... (S)"
              onChange={(e) => onSearch(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onSearchEnter();
              }}
            />
          </div>
        </div>

        <div className="artmap-nav-right">
          <div className="artweb-lens-toggle" title="How to organize the graph">
            {LENSES.map((l) => (
              <button
                type="button"
                key={l.id}
                className={l.id === lens ? 'artweb-lens-btn active' : 'artweb-lens-btn'}
                data-lens={l.id}
                onClick={() => onSetLens(l.id)}
              >
                {l.label}
              </button>
            ))}
          </div>
          <div className="artweb-lens-toggle" title="Size nodes by">
            {SIZES.map((s) => (
              <button
                type="button"
                key={s.id}
                className={
                  s.id === sizeBy
                    ? 'artweb-lens-btn artweb-size-btn active'
                    : 'artweb-lens-btn artweb-size-btn'
                }
                data-size={s.id}
                title={s.title}
                onClick={() => onSetSize(s.id)}
              >
                {s.label}
              </button>
            ))}
          </div>

          <button
            type="button"
            className={pathMode ? 'artmap-tool-btn active' : 'artmap-tool-btn'}
            id="artweb-path-btn"
            title="Trace how two artists connect"
            onClick={onTogglePath}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="5" cy="6" r="2.2" />
              <circle cx="19" cy="18" r="2.2" />
              <path d="M7 6h6a4 4 0 0 1 4 4v4" strokeDasharray="1 3" />
            </svg>
            <span>Path</span>
          </button>

          <button
            type="button"
            className={edgeDeclutter ? 'artmap-tool-btn active' : 'artmap-tool-btn'}
            id="artweb-edges-btn"
            title="Show only the stronger connections"
            onClick={onToggleEdges}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <line x1="4" y1="7" x2="20" y2="7" />
              <line x1="4" y1="12" x2="20" y2="12" strokeOpacity="0.4" />
              <line x1="4" y1="17" x2="20" y2="17" strokeOpacity="0.2" />
            </svg>
            <span>{webEdgeButtonLabel(edgeDeclutter)}</span>
          </button>

          <button
            type="button"
            className={sidebar.open ? 'artmap-tool-btn active' : 'artmap-tool-btn'}
            id="artweb-filter-btn"
            title="Filter by genre"
            onClick={onToggleFilter}
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3" />
            </svg>
            <span>Filter</span>
          </button>

          <div className="artmap-zoom-group">
            {/* A ratio BELOW one zooms IN — the labels are not swapped. */}
            <button
              type="button"
              className="artmap-tool-btn artmap-zoom"
              title="Zoom in (+)"
              aria-label="Zoom in"
              onClick={() => onZoom(0.7)}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
              >
                <line x1="12" y1="5" x2="12" y2="19" />
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </button>
            <button
              type="button"
              className="artmap-tool-btn artmap-zoom"
              title="Zoom out (-)"
              aria-label="Zoom out"
              onClick={() => onZoom(1.4)}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.5"
              >
                <line x1="5" y1="12" x2="19" y2="12" />
              </svg>
            </button>
            <button
              type="button"
              className="artmap-tool-btn artmap-zoom"
              title="Fit to view (F)"
              aria-label="Fit to view"
              onClick={onFitToView}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
              >
                <path d="M8 3H5a2 2 0 0 0-2 2v3" />
                <path d="M21 8V5a2 2 0 0 0-2-2h-3" />
                <path d="M3 16v3a2 2 0 0 0 2 2h3" />
                <path d="M16 21h3a2 2 0 0 0 2-2v-3" />
              </svg>
            </button>
          </div>

          <button
            type="button"
            className="artmap-tool-btn"
            title="Guide & shortcuts"
            aria-label="Guide and shortcuts"
            style={{ fontWeight: 700 }}
            onClick={onShowHelp}
          >
            ?
          </button>
        </div>
      </div>

      <div className="artmap-content-row">
        {sidebar.open && (
          <div
            className="artmap-genre-sidebar"
            id="artweb-genre-sidebar"
            style={{ display: 'flex' }}
          >
            <div className="artmap-genre-sidebar-header">
              <span>{webSidebarHeading(lens)}</span>
              <input
                type="text"
                className="artmap-genre-sidebar-search"
                placeholder="Filter..."
                value={genreQuery}
                onChange={(e) => setGenreQuery(e.target.value)}
              />
            </div>
            <div className="artmap-genre-sidebar-list" id="artweb-genre-sidebar-list">
              {activeCount > 0 && (
                <button type="button" className="artweb-genre-clear" onClick={onClearGenreFilter}>
                  ✕ Clear filter ({activeCount})
                </button>
              )}
              {rows.length === 0 ? (
                <div className="artweb-genre-empty">No genres</div>
              ) : (
                rows.map((row) => (
                  <button
                    type="button"
                    key={row.genre}
                    className={row.active ? 'artweb-genre-row active' : 'artweb-genre-row'}
                    onClick={() => onToggleGenre(row.genre)}
                  >
                    <span
                      className="artweb-genre-dot"
                      style={{ background: sidebar.colorOf(row.genre) }}
                    />
                    <span className="artweb-genre-name">{row.genre}</span>
                    <span className="artweb-genre-count">{row.count}</span>
                  </button>
                ))
              )}
            </div>
          </div>
        )}

        <div ref={hostRef} id="artist-web-canvas" style={{ flex: 1, minWidth: 0, minHeight: 0 }} />

        {legend.length > 0 && (
          <div id="artist-web-legend" className="artweb-legend">
            {legend.map((item) => (
              <div className="artweb-legend-row" key={item.label}>
                <span className="artweb-legend-dot" style={{ background: item.color }} />
                <span className="artweb-legend-label">{item.label}</span>
                {item.count != null && <span className="artweb-legend-count">{item.count}</span>}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="artist-map-search-results" id="artist-web-search-results" />
      <div className="artist-map-tooltip" id="artist-web-tooltip" />
      {children}
    </div>
  );
}
