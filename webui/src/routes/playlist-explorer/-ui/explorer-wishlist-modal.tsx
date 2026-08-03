/**
 * Add to Wishlist (explorerAddToWishlist :636, _explorerWishlistSubmit :755).
 *
 * Reuses the discography modal's classes and CSS wholesale, which is what the
 * vanilla did — right down to appending to document.body. The BodyPortal comes
 * from the artist-detail port for the same reason the stream reader does:
 * duplicating it would give the trap it documents two places to come back.
 */

import { Fragment, useEffect, useMemo, useRef, useState } from 'react';

import { BodyPortal } from '@/routes/artist-detail/-ui/portal';

import type { ExplorerArtistSection } from '../-explorer.types';

import { explorerAlbumStatusText, submitExplorerWishlist } from '../-explorer.api';
import { explorerSelectionTotals } from '../-explorer.core';
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
  type ExplorerWishlistFilters,
} from '../-explorer.wishlist';

const FILTER_LABELS: Record<string, string> = {
  album: 'Albums',
  ep: 'EPs',
  single: 'Singles',
};

type AlbumProgress = { status: 'waiting' | 'done' | 'error'; text: string };

export interface ExplorerWishlistModalProps {
  sections: ExplorerArtistSection[];
  onClose: () => void;
  /**
   * Fires once the whole run finishes. The page marks its ENTIRE selection as
   * added, not just what was submitted — a release you ticked and then filtered
   * out is still marked. That is the vanilla's behaviour (:876 iterates
   * `_explorer.selectedAlbums`, not the submitted set).
   */
  onFinished: (totalAdded: number) => void;
}

export function ExplorerWishlistModal({
  sections,
  onClose,
  onFinished,
}: ExplorerWishlistModalProps) {
  const cards = useMemo(() => explorerWishlistCards(sections), [sections]);
  const [checked, setChecked] = useState(() => explorerWishlistDefaultChecked(cards));
  const [filters, setFilters] = useState<ExplorerWishlistFilters>(
    EXPLORER_WISHLIST_DEFAULT_FILTERS,
  );
  const [phase, setPhase] = useState<'select' | 'running' | 'done'>('select');
  const [progress, setProgress] = useState<Record<string, AlbumProgress>>({});
  const [totalAdded, setTotalAdded] = useState(0);
  const [visible, setVisible] = useState(false);
  /** The albums the run is actually working through, frozen at submit time. */
  const [running, setRunning] = useState<typeof cards>([]);

  // The vanilla added `.visible` on the next frame so the CSS transition had a
  // frame at the starting opacity to animate away from.
  useEffect(() => {
    const frame = requestAnimationFrame(() => setVisible(true));
    return () => cancelAnimationFrame(frame);
  }, []);

  const active = explorerWishlistActive(cards, checked, filters);
  const footer = explorerWishlistFooter(active);
  const totals = explorerSelectionTotals(sections);
  const heroImage = sections[0]?.image || '';

  // Set on mount, not just cleared on unmount: React's StrictMode mounts,
  // unmounts and remounts in development, and a cleanup-only ref would stay
  // false for the whole real lifetime.
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  function toggleFilter(type: string) {
    setFilters((current) => ({ ...current, [type]: !current[type] }));
  }

  function setAllChecked(all: boolean) {
    setChecked(all ? new Set(cards.map((card) => card.albumId)) : new Set());
  }

  async function submit() {
    const groups = groupWishlistByArtist(active);
    setRunning(active);
    setProgress(
      Object.fromEntries(
        active.map((card) => [card.albumId, { status: 'waiting', text: 'Waiting...' } as const]),
      ),
    );
    setPhase('running');

    const added = await submitExplorerWishlist(groups, (albumId, update) => {
      if (!alive.current) return;
      const id = String(albumId);
      if (update.status === 'done') {
        setProgress((current) => ({
          ...current,
          [id]: { status: 'done', text: explorerAlbumStatusText(update) },
        }));
      } else if (update.status === 'error') {
        setProgress((current) => ({
          ...current,
          [id]: { status: 'error', text: String(update.message ?? 'Error') },
        }));
      }
    });

    if (!alive.current) return;
    setTotalAdded(added);
    setPhase('done');
    window.showToast?.(`Added ${added} tracks to wishlist`, 'success');
    onFinished(added);
  }

  return (
    <BodyPortal>
      <div
        className={`discog-modal-overlay${visible ? ' visible' : ''}`}
        id="explorer-wishlist-overlay"
      >
        <div className="discog-modal">
          <div className="discog-modal-hero" style={{ backgroundImage: `url('${heroImage}')` }}>
            <div className="discog-modal-hero-overlay" />
            <div className="discog-modal-hero-content">
              <h2 className="discog-modal-title">Add to Wishlist</h2>
              <p className="discog-modal-artist">
                {totals.artists} artist{totals.artists !== 1 ? 's' : ''} · {totals.albums} releases
              </p>
            </div>
            <button type="button" className="discog-modal-close" onClick={onClose}>
              ×
            </button>
          </div>

          {phase === 'select' ? (
            <div className="discog-filter-bar">
              <div className="discog-filters">
                {EXPLORER_WISHLIST_FILTER_TYPES.map((type) => (
                  <button
                    type="button"
                    key={type}
                    className={`discog-filter${filters[type] ? ' active' : ''}`}
                    data-type={type}
                    onClick={() => toggleFilter(type)}
                  >
                    {FILTER_LABELS[type]}
                  </button>
                ))}
              </div>
              <div className="discog-select-actions">
                <button
                  type="button"
                  className="discog-select-btn"
                  onClick={() => setAllChecked(true)}
                >
                  Select All
                </button>
                <button
                  type="button"
                  className="discog-select-btn"
                  onClick={() => setAllChecked(false)}
                >
                  Deselect
                </button>
              </div>
            </div>
          ) : null}

          {phase === 'select' ? (
            <div className="discog-grid" id="explorer-wishlist-grid">
              {/* A Fragment, not a wrapper element: the vanilla appended the
                  header and the cards as flat siblings of the grid, and a real
                  node between them would break the grid layout. */}
              {sections.map((section, sectionIndex) => (
                <Fragment key={`${sectionIndex}-${section.name}`}>
                  <div className="discog-section-header">{section.name}</div>
                  {cards
                    .filter((card) => card.sectionIndex === sectionIndex)
                    .map((card) => (
                      <label
                        key={card.albumId}
                        className={`discog-card${card.owned ? ' owned' : ''}`}
                        data-type={card.type}
                        data-artist-id={String(card.artistId)}
                        style={{
                          animationDelay: `${card.indexInSection * 0.03}s`,
                          display: explorerWishlistCardVisible(card.type, filters)
                            ? undefined
                            : 'none',
                        }}
                      >
                        <input
                          type="checkbox"
                          className="discog-card-cb"
                          data-album-id={card.albumId}
                          data-tracks={card.tracks}
                          checked={checked.has(card.albumId)}
                          onChange={() =>
                            setChecked((current) => {
                              const next = new Set(current);
                              if (next.has(card.albumId)) next.delete(card.albumId);
                              else next.add(card.albumId);
                              return next;
                            })
                          }
                        />
                        <div className="discog-card-art">
                          {card.album.image_url ? (
                            <img src={card.album.image_url} alt="" loading="lazy" />
                          ) : (
                            <div className="discog-card-art-placeholder">♫</div>
                          )}
                          {card.owned ? <span className="discog-card-status">✓</span> : null}
                        </div>
                        <div className="discog-card-info">
                          <div className="discog-card-title">{card.album.title || 'Unknown'}</div>
                          <div className="discog-card-meta">
                            {card.album.year ? `${card.album.year} · ` : ''}
                            {card.typeLabel} · {card.album.track_count || '?'} tracks
                          </div>
                        </div>
                        <div className="discog-card-check" />
                      </label>
                    ))}
                </Fragment>
              ))}
            </div>
          ) : (
            <div className="discog-progress" id="explorer-wishlist-progress">
              {running.map((card) => {
                const state = progress[card.albumId] ?? { status: 'waiting', text: 'Waiting...' };
                return (
                  <div
                    key={card.albumId}
                    className={`discog-progress-item ${state.status === 'waiting' ? 'active' : state.status}`}
                    id={`explorer-prog-${card.albumId}`}
                  >
                    <div className="discog-prog-art">
                      {card.album.image_url ? <img src={card.album.image_url} alt="" /> : '♫'}
                    </div>
                    <div className="discog-prog-info">
                      <div className="discog-prog-title">{card.album.title || 'Unknown'}</div>
                      <div className="discog-prog-status">{state.text}</div>
                    </div>
                    <div className="discog-prog-icon">
                      {state.status === 'waiting' ? (
                        <div className="discog-spinner" />
                      ) : state.status === 'done' ? (
                        <span style={{ color: '#4CAF50' }}>✓</span>
                      ) : (
                        <span style={{ color: '#ff4757' }}>✗</span>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <div className="discog-footer" id="explorer-wishlist-footer">
            <div className="discog-footer-info" id="explorer-wishlist-info">
              {phase === 'select'
                ? footer.info
                : phase === 'running'
                  ? 'Processing...'
                  : explorerWishlistDoneText(totalAdded)}
            </div>
            <div className="discog-footer-actions">
              <button type="button" className="discog-cancel-btn" onClick={onClose}>
                {phase === 'done' ? 'Close' : 'Cancel'}
              </button>
              {phase === 'select' ? (
                <button
                  type="button"
                  className="discog-submit-btn"
                  id="explorer-wishlist-submit"
                  disabled={footer.disabled}
                  onClick={() => void submit()}
                >
                  <span className="discog-submit-icon">⬇</span>
                  <span id="explorer-wishlist-submit-text">{footer.submitText}</span>
                </button>
              ) : null}
            </div>
          </div>
        </div>
      </div>
    </BodyPortal>
  );
}
