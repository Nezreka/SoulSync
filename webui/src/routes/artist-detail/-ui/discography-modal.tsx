import { useEffect, useState } from 'react';

import type {
  DiscogAlbumUpdate,
  DiscogFilters,
  DiscogModalData,
  DiscogRelease,
} from '../-artist-detail.discography-modal';

import {
  buildDiscographyPayload,
  DISCOG_DEFAULT_FILTERS,
  discogCardView,
  discogCardVisible,
  discogFooter,
  discogItemStatus,
  loadDiscographyForModal,
  streamDiscographyDownload,
} from '../-artist-detail.discography-modal';
import { BodyPortal } from './portal';

/**
 * The Download Discography modal (openDiscographyModal, library.js:580):
 * type + content filters (#877), completion-aware cards with unowned releases
 * pre-checked, and the per-album NDJSON progress view with honest statuses
 * (#830). Deluxe-first ordering and per-entry gap-fill sources (#1067) live in
 * buildDiscographyPayload.
 */

type ProgressState = Record<
  string,
  { status: 'waiting' | 'active' | 'done' | 'skipped' | 'error'; text: string }
>;

export function DiscographyModal({
  libraryArtistId,
  artistName,
  artistImage,
  onClose,
}: {
  libraryArtistId: unknown;
  artistName: string;
  artistImage: string;
  onClose: () => void;
}) {
  const [data, setData] = useState<DiscogModalData | null>(null);
  const [filters, setFilters] = useState<DiscogFilters>(DISCOG_DEFAULT_FILTERS);
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [phase, setPhase] = useState<'pick' | 'progress'>('pick');
  const [progress, setProgress] = useState<ProgressState>({});
  const [totals, setTotals] = useState<{ total_added: number; total_skipped: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    window.showToast?.('Loading discography...', 'info');
    void loadDiscographyForModal(libraryArtistId, artistName).then((result) => {
      if (cancelled) return;
      if (!result) {
        window.showToast?.(
          'No discography found. Try searching this artist from the Search page instead.',
          'error',
        );
        onClose();
        return;
      }
      setData(result);
      // Unowned releases come pre-checked. The library path has no completion
      // cache (that belonged to the OLD search page), so everything starts on.
      const next = new Set<string>();
      for (const release of result.releases) {
        if (discogCardView(release, {}).checkedByDefault) next.add(String(release.id));
      }
      setChecked(next);
    });
    return () => {
      cancelled = true;
    };
    // One load per mounted modal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!data) return null;

  const cards = data.releases.map((release) => ({
    release,
    view: discogCardView(release, {}),
    visible: discogCardVisible(discogCardView(release, {}), release._type, filters),
  }));
  const visibleChecked = cards.filter((c) => c.visible && checked.has(String(c.release.id)));
  const footer = discogFooter(visibleChecked.map((c) => ({ tracks: c.view.tracks })));

  const toggleFilter = (key: keyof DiscogFilters) =>
    setFilters((prev) => ({ ...prev, [key]: !prev[key] }));

  const selectAll = (select: boolean) => {
    const next = new Set(checked);
    for (const card of cards) {
      if (!card.visible) continue;
      if (select) next.add(String(card.release.id));
      else next.delete(String(card.release.id));
    }
    setChecked(next);
  };

  const start = async () => {
    if (visibleChecked.length === 0 || !data) return;
    // The download payload is built from VISIBLE checked cards (#877).
    const entries = visibleChecked.map((c) => ({
      id: c.release.id,
      name: c.view.albumName,
      tracks: c.view.tracks,
      gapSource: c.release._gap_source || null,
    }));
    setPhase('progress');
    const initial: ProgressState = {};
    for (const c of visibleChecked) {
      initial[String(c.release.id)] = { status: 'active', text: 'Waiting...' };
    }
    setProgress(initial);

    try {
      await streamDiscographyDownload(
        data.artist.id,
        buildDiscographyPayload(entries, data.artist),
        (update: DiscogAlbumUpdate) => {
          const id = String(update.album_id);
          setProgress((prev) => ({
            ...prev,
            [id]:
              update.status === 'done'
                ? {
                    status: (update.tracks_added || 0) > 0 ? 'done' : 'skipped',
                    text: discogItemStatus(update),
                  }
                : update.status === 'error'
                  ? { status: 'error', text: update.message || 'Error' }
                  : {
                      status: 'active',
                      text: `Processing ${update.tracks_total ?? '?'} tracks...`,
                    },
          }));
        },
        (finished) => setTotals(finished),
      );
    } catch (error) {
      window.showToast?.(`Discography download failed: ${(error as Error).message}`, 'error');
    }
  };

  // BodyPortal is load-bearing: this mounts from inside the hero, whose
  // backdrop-filter makes it the containing block for position:fixed —
  // rendered in place, the overlay is clamped to the hero box and cut off.
  return (
    <BodyPortal>
      <div className="discog-modal-overlay visible" id="discog-modal-overlay">
        <div className="discog-modal">
          <div
            className="discog-modal-hero"
            style={artistImage ? { backgroundImage: `url('${artistImage}')` } : undefined}
          >
            <div className="discog-modal-hero-overlay" />
            <div className="discog-modal-hero-content">
              <h2 className="discog-modal-title">Download Discography</h2>
              <p className="discog-modal-artist">{artistName}</p>
            </div>
            <button className="discog-modal-close" type="button" onClick={onClose}>
              ×
            </button>
          </div>

          {phase === 'pick' ? (
            <div className="discog-filter-bar">
              <div className="discog-filters">
                {(
                  [
                    ['album', 'Albums'],
                    ['ep', 'EPs'],
                    ['single', 'Singles'],
                    ['live', 'Live'],
                    ['compilations', 'Compilations'],
                    ['featured', 'Featured'],
                  ] as [keyof DiscogFilters, string][]
                ).map(([key, label]) => (
                  <button
                    className={`discog-filter${filters[key] ? ' active' : ''}`}
                    type="button"
                    key={key}
                    onClick={() => toggleFilter(key)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <div className="discog-select-actions">
                <button className="discog-select-btn" type="button" onClick={() => selectAll(true)}>
                  Select All
                </button>
                <button
                  className="discog-select-btn"
                  type="button"
                  onClick={() => selectAll(false)}
                >
                  Deselect All
                </button>
              </div>
            </div>
          ) : null}

          {phase === 'pick' ? (
            <div className="discog-grid" id="discog-grid">
              {cards.map((card, index) => (
                <DiscogCard
                  key={`${card.release._type}-${String(card.release.id)}-${index}`}
                  release={card.release}
                  view={card.view}
                  visible={card.visible}
                  index={index}
                  checked={checked.has(String(card.release.id))}
                  onToggle={() => {
                    const id = String(card.release.id);
                    const next = new Set(checked);
                    if (next.has(id)) next.delete(id);
                    else next.add(id);
                    setChecked(next);
                  }}
                />
              ))}
            </div>
          ) : (
            <div className="discog-progress" id="discog-progress">
              {visibleChecked.map((card) => {
                const state = progress[String(card.release.id)];
                return (
                  <div
                    className={`discog-progress-item${state ? ` ${state.status}` : ''}`}
                    id={`discog-prog-${String(card.release.id)}`}
                    key={String(card.release.id)}
                  >
                    <div className="discog-prog-art">
                      {card.release.image_url ? <img src={card.release.image_url} alt="" /> : '🎵'}
                    </div>
                    <div className="discog-prog-info">
                      <div className="discog-prog-title">{card.view.albumName}</div>
                      <div className="discog-prog-status">{state?.text ?? 'Waiting...'}</div>
                    </div>
                    <div className="discog-prog-icon">
                      {state?.status === 'done' ? (
                        <span className="discog-check">✓</span>
                      ) : state?.status === 'skipped' ? (
                        <span className="discog-skip">—</span>
                      ) : state?.status === 'error' ? (
                        <span className="discog-error">✗</span>
                      ) : (
                        <div className="discog-spinner" />
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <div className="discog-footer" id="discog-footer">
            <div className="discog-footer-info" id="discog-footer-info">
              {phase === 'pick'
                ? footer.info
                : totals
                  ? `Done — ${totals.total_added} tracks added, ${totals.total_skipped} skipped`
                  : 'Processing... this may take a moment'}
            </div>
            <div className="discog-footer-actions">
              {phase === 'pick' ? (
                <>
                  <button className="discog-cancel-btn" type="button" onClick={onClose}>
                    Cancel
                  </button>
                  <button
                    className="discog-submit-btn"
                    id="discog-submit-btn"
                    type="button"
                    disabled={footer.disabled}
                    onClick={() => void start()}
                  >
                    <span className="discog-submit-icon">⬇</span>
                    <span id="discog-submit-text">{footer.submitText}</span>
                  </button>
                </>
              ) : (
                <>
                  <button className="discog-cancel-btn" type="button" onClick={onClose}>
                    Close
                  </button>
                  {totals && totals.total_added > 0 ? (
                    <button
                      className="discog-submit-btn"
                      type="button"
                      onClick={() => {
                        onClose();
                        void fetch('/api/wishlist/process', { method: 'POST' });
                        window.showToast?.('Wishlist processing started', 'success');
                      }}
                    >
                      <span className="discog-submit-icon">🚀</span>
                      <span>Process Wishlist Now</span>
                    </button>
                  ) : null}
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </BodyPortal>
  );
}

function DiscogCard({
  release,
  view,
  visible,
  index,
  checked,
  onToggle,
}: {
  release: DiscogRelease;
  view: ReturnType<typeof discogCardView>;
  visible: boolean;
  index: number;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <label
      className={`discog-card ${view.statusClass}`.trim()}
      data-type={release._type}
      data-is-live={String(view.isLive)}
      data-is-compilation={String(view.isCompilation)}
      data-is-featured={String(view.isFeatured)}
      style={{
        animationDelay: `${index * 0.03}s`,
        display: visible ? undefined : 'none',
      }}
    >
      <input
        type="checkbox"
        className="discog-card-cb"
        data-album-id={String(release.id)}
        data-album-name={view.albumName}
        data-tracks={String(view.tracks)}
        data-gap-source={release._gap_source || ''}
        checked={checked}
        onChange={onToggle}
      />
      <div className="discog-card-art">
        {release.image_url ? (
          <img src={release.image_url} alt="" loading="lazy" />
        ) : (
          <div className="discog-card-art-placeholder">🎵</div>
        )}
        {view.statusIcon ? <span className="discog-card-status">{view.statusIcon}</span> : null}
      </div>
      <div className="discog-card-info">
        <div className="discog-card-title">
          {view.albumName}
          {release.explicit === true ? <span className="explicit-badge"> E</span> : null}
        </div>
        <div className="discog-card-meta">
          {view.year}
          {view.year && view.tracks ? ' · ' : ''}
          {view.tracks ? `${view.tracks} tracks` : ''}
          {release._gap_source ? (
            <>
              {' · '}
              <span className="discog-gap-src">{release._gap_source}</span>
            </>
          ) : null}
        </div>
      </div>
      <div className="discog-card-check" />
    </label>
  );
}
