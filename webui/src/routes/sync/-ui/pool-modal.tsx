/**
 * The shell both pool modals share (stats-automations.js 1246-1305 and
 * 1398-1457 — the same markup twice, with different words in it).
 *
 * Only the CHROME is shared: the header with its playlist filter, the
 * two-card category grid, and the list view with its back button and search
 * box. Everything that drifts — counts, card copy, the mosaic, and the rows —
 * is supplied by the caller, because the two pools disagree on nearly all of
 * it (see -sync.pools.ts).
 *
 * The vanilla toggles between the grid and the list with `style.display`
 * (1591-1613); here the view is state and only one of the two renders. The
 * search box being CLEARED on every entry to a list (1610-1611, 1471) is the
 * caller's business — it owns the query.
 */

import type { ReactNode } from 'react';

import type { PoolMosaicRow } from '../-sync.pools';

export interface PoolCategoryCardProps {
  /** Drives the card, count and top-bar modifier classes. */
  tone: 'failed' | 'matched';
  icon: string;
  count: number;
  label: string;
  onOpen: () => void;
  /**
   * The Discovery Pool's matched card only (1315-1350). Null keeps the flat
   * gradient, which is also what Wing It always gets.
   */
  mosaic?: PoolMosaicRow[] | null;
  /** The vanilla id the mosaic was injected into; kept for parity. */
  backgroundId?: string;
}

export function PoolCategoryCard({
  tone,
  icon,
  count,
  label,
  onOpen,
  mosaic,
  backgroundId,
}: PoolCategoryCardProps) {
  return (
    <div className={`pool-category-card ${tone}`} onClick={onOpen}>
      {mosaic ? (
        <div className="wishlist-mosaic-background" id={backgroundId}>
          {mosaic.map((row, r) => (
            <div className="wishlist-mosaic-row-wrapper" key={r}>
              <div
                className={`wishlist-mosaic-row${row.scrollRight ? ' scroll-right' : ''}`}
                style={
                  {
                    '--speed': `${row.speedSeconds}s`,
                    animationDelay: `${row.delaySeconds}s`,
                  } as React.CSSProperties
                }
              >
                {row.tiles.map((url, i) => (
                  <div className="wishlist-mosaic-tile" key={i}>
                    <div
                      className="wishlist-mosaic-image"
                      style={{ backgroundImage: `url('${url}')` }}
                    />
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className={`pool-category-fallback ${tone}`} id={backgroundId} />
      )}
      <div className="pool-category-overlay" />
      <div className="pool-category-content">
        <div className="pool-category-icon">{icon}</div>
        <div className={`pool-category-count ${tone}`}>{count}</div>
        <div className="pool-category-label">{label}</div>
      </div>
      <div className={`pool-category-top-bar ${tone}`} />
    </div>
  );
}

export interface PoolListProps {
  title: string;
  query: string;
  onQuery: (query: string) => void;
  onBack: () => void;
  children: ReactNode;
}

export interface PoolModalProps {
  /** The vanilla overlay id — 'discovery-pool-overlay' / 'wing-it-pool-overlay'. */
  id: string;
  title: string;
  /** The header's quick-info spans, which differ per pool. */
  chips: ReactNode;
  playlists: { id: number; name: string }[];
  playlistFilter: string;
  onPlaylistFilter: (value: string) => void;
  onClose: () => void;
  /** The two category cards. */
  cards: ReactNode;
  /** null → the category grid is showing. */
  list: PoolListProps | null;
}

export function PoolModal({
  id,
  title,
  chips,
  playlists,
  playlistFilter,
  onPlaylistFilter,
  onClose,
  cards,
  list,
}: PoolModalProps) {
  return (
    <div
      className="modal-overlay"
      id={id}
      style={{ display: 'flex' }}
      onClick={(e) => {
        // Only the backdrop closes (1236, 1390).
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal-container playlist-modal">
        <div className="playlist-modal-header">
          <div className="playlist-header-content">
            <h2>{title}</h2>
            <div className="playlist-quick-info">
              {chips}
              <select
                className="pool-playlist-filter"
                value={playlistFilter}
                onChange={(e) => onPlaylistFilter(e.target.value)}
              >
                <option value="">All Playlists</option>
                {playlists.map((p) => (
                  <option value={p.id} key={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <span className="playlist-modal-close" onClick={onClose}>
            ×
          </span>
        </div>

        <div className="playlist-modal-body">
          {list ? (
            <div className="pool-list-view">
              <div className="pool-list-header">
                <button type="button" className="pool-back-btn" onClick={list.onBack}>
                  ← Back
                </button>
                <span className="pool-list-title">{list.title}</span>
                <input
                  type="text"
                  className="pool-list-search"
                  placeholder="Filter tracks..."
                  value={list.query}
                  onChange={(e) => list.onQuery(e.target.value)}
                />
              </div>
              <div className="pool-list-content">{list.children}</div>
            </div>
          ) : (
            <div className="pool-category-grid">{cards}</div>
          )}
        </div>

        <div className="playlist-modal-footer">
          <div className="playlist-modal-footer-left" />
          <div className="playlist-modal-footer-right">
            <button
              type="button"
              className="playlist-modal-btn playlist-modal-btn-secondary"
              onClick={onClose}
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** The `<div class="pool-empty">` both list renderers fall back to. */
export function PoolEmpty({ children }: { children: ReactNode }) {
  return <div className="pool-empty">{children}</div>;
}
