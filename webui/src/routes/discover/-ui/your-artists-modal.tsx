import type { YourArtist } from '../-discover.your-artists';
import type { ArtistsModalSort, ArtistsModalState } from '../-discover.your-artists-actions';
import type { SourceLogos } from './your-artists-shelf';

import {
  ARTISTS_MODAL_EMPTY,
  ARTISTS_MODAL_ERROR,
  ARTISTS_MODAL_FILTERS,
  ARTISTS_MODAL_SORTS,
  artistsModalPager,
  artistsModalSubtitle,
} from '../-discover.your-artists-actions';
import { YourArtistCard } from './your-artists-shelf';

/**
 * The View All modal for Your Artists.
 *
 * Transcribed from `openYourArtistsModal` + `_yaLoadModal` (discover.js
 * 5723-5826). The grid reuses the shelf's card component — the vanilla calls
 * the same `_renderYourArtistCard` from both places (5259, 5807), and a second
 * transcription is exactly how the two would drift apart.
 *
 * The component is CONTROLLED. The vanilla keeps `window._yaModalState` and
 * re-fetches from inline handlers; here the state lives in the page hook,
 * which owns the fetch, the 300ms search debounce, and the reducer
 * (`applyArtistsModalFilter` — whose documented divergence resets to page 1
 * on search/sort changes too, not just the source pills).
 *
 * `total` is null until the first response, which is what renders the
 * vanilla's initial "Loading..." subtitle (5732). It then STAYS at its last
 * value across reloads and errors, as the vanilla's subtitle span does.
 */

export interface YourArtistsModalProps {
  state: ArtistsModalState;
  total: number | null;
  artists: YourArtist[];
  phase: 'loading' | 'error' | 'ready';
  logos: SourceLogos;
  buildDetailPath: (id: string, source: string) => string;
  /** The caller routes this through `applyArtistsModalFilter`. */
  onFilter: (change: Partial<{ source: string; sort: ArtistsModalSort; search: string }>) => void;
  onPage: (page: number) => void;
  onClose: () => void;
  onOpenInfo: (artist: YourArtist) => void;
  onToggleWatchlist: (artist: YourArtist) => void;
}

export function YourArtistsModal({
  state,
  total,
  artists,
  phase,
  logos,
  buildDetailPath,
  onFilter,
  onPage,
  onClose,
  onOpenInfo,
  onToggleWatchlist,
}: YourArtistsModalProps) {
  const pager = artistsModalPager(total ?? 0, state.page);

  return (
    <div
      id="your-artists-modal-overlay"
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="ya-modal">
        <div className="ya-modal-header">
          <div>
            <h2 className="ya-modal-title">Your Artists</h2>
            <p className="ya-modal-subtitle" id="ya-modal-subtitle">
              {total === null ? 'Loading...' : artistsModalSubtitle(total)}
            </p>
          </div>
          <button type="button" className="watch-all-close" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="ya-modal-toolbar">
          <input
            type="text"
            id="ya-modal-search"
            className="ya-modal-search"
            placeholder="Search artists..."
            value={state.search}
            onChange={(e) => onFilter({ search: e.target.value })}
          />
          <div className="ya-modal-filters">
            {ARTISTS_MODAL_FILTERS.map((f) => (
              <button
                type="button"
                key={f.source}
                className={state.source === f.source ? 'ya-filter-btn active' : 'ya-filter-btn'}
                data-source={f.source}
                onClick={() => onFilter({ source: f.source })}
              >
                {f.label}
              </button>
            ))}
          </div>
          <select
            className="ya-modal-sort"
            id="ya-modal-sort"
            value={state.sort}
            onChange={(e) => onFilter({ sort: e.target.value as ArtistsModalSort })}
          >
            {ARTISTS_MODAL_SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </div>
        <div className="ya-modal-body" id="ya-modal-body">
          {phase === 'loading' ? (
            <div className="cache-health-loading">
              <div className="watch-all-loading-spinner" />
              <div>Loading...</div>
            </div>
          ) : phase === 'error' ? (
            <div className="failed-mb-empty">{ARTISTS_MODAL_ERROR}</div>
          ) : artists.length === 0 ? (
            <div className="failed-mb-empty">{ARTISTS_MODAL_EMPTY}</div>
          ) : (
            <div className="ya-modal-grid">
              {artists.map((artist) => (
                <YourArtistCard
                  key={String(artist.id ?? artist.artist_name)}
                  artist={artist}
                  logos={logos}
                  buildDetailPath={buildDetailPath}
                  onOpenInfo={onOpenInfo}
                  onToggleWatchlist={onToggleWatchlist}
                />
              ))}
            </div>
          )}
        </div>
        <div className="ya-modal-footer" id="ya-modal-footer">
          {/* The vanilla empties the footer on an empty result (5801) and only
              renders the pager after a successful non-empty load (5813). */}
          {phase === 'ready' && artists.length > 0 && pager.visible && (
            <div className="failed-mb-pagination">
              <button
                type="button"
                className="failed-mb-btn-sm"
                disabled={pager.prevDisabled}
                onClick={() => onPage(state.page - 1)}
              >
                Prev
              </button>
              <span>{pager.label}</span>
              <button
                type="button"
                className="failed-mb-btn-sm"
                disabled={pager.nextDisabled}
                onClick={() => onPage(state.page + 1)}
              >
                Next
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
