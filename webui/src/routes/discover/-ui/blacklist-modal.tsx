import type { BlacklistEntry, BlacklistSearchArtist } from '../-discover.blacklist';

import {
  BLACKLIST_EMPTY,
  BLACKLIST_LOAD_FAILED,
  BLACKLIST_NO_RESULTS,
  BLACKLIST_SEARCH_PLACEHOLDER,
  BLACKLIST_SUBTITLE,
  BLACKLIST_TITLE,
  blacklistEntryDate,
} from '../-discover.blacklist';

/**
 * The Blocked Artists modal.
 *
 * Transcribed from `openDiscoveryBlacklistModal` + `_dblSearch` + `_dblLoadList`
 * (discover.js 5058-5186). Controlled: the page hook owns the fetches, the
 * 300ms debounce and the min-length gate (`blacklistQueryTooShort` — checked
 * BEFORE the debounce), plus the block/unblock effects.
 *
 * List states: the vanilla seeds `#dbl-list` with "Loading..." inside the
 * `.discover-blacklist-empty` styling and reuses that same class for the
 * empty and failed copy — one class, three texts.
 */

export interface BlacklistModalProps {
  query: string;
  /** null = the dropdown is hidden (short query / blurred / fetch error). */
  results: BlacklistSearchArtist[] | null;
  entries: BlacklistEntry[];
  listPhase: 'loading' | 'error' | 'ready';
  onQueryChange: (query: string) => void;
  onBlock: (artistName: string) => void;
  onUnblock: (entry: BlacklistEntry) => void;
  onClose: () => void;
}

export function BlacklistModal({
  query,
  results,
  entries,
  listPhase,
  onQueryChange,
  onBlock,
  onUnblock,
  onClose,
}: BlacklistModalProps) {
  return (
    <div
      id="discovery-blacklist-modal-overlay"
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="discover-blacklist-modal">
        <div className="discover-blacklist-modal-header">
          <h2>{BLACKLIST_TITLE}</h2>
          <p>{BLACKLIST_SUBTITLE}</p>
          <button type="button" className="watch-all-close" onClick={onClose}>
            &times;
          </button>
        </div>
        <div className="discover-blacklist-modal-search">
          <input
            type="text"
            id="dbl-search-input"
            placeholder={BLACKLIST_SEARCH_PLACEHOLDER}
            autoComplete="off"
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
          />
          {results !== null && (
            <div id="dbl-search-results" className="dbl-search-results">
              {results.length === 0 ? (
                <div className="dbl-search-empty">{BLACKLIST_NO_RESULTS}</div>
              ) : (
                results.map((a, i) => (
                  <div
                    key={`${a.name ?? ''}-${i}`}
                    className="dbl-search-item"
                    onClick={() => onBlock(a.name ?? '')}
                  >
                    {a.image_url ? (
                      <img src={a.image_url} className="dbl-search-img" alt="" />
                    ) : (
                      <div className="dbl-search-img-placeholder">🎤</div>
                    )}
                    <span className="dbl-search-name">{a.name ?? ''}</span>
                    <span className="dbl-search-action">Block</span>
                  </div>
                ))
              )}
            </div>
          )}
        </div>
        <div className="discover-blacklist-modal-list" id="dbl-list">
          {listPhase === 'loading' ? (
            <div className="discover-blacklist-empty">Loading...</div>
          ) : listPhase === 'error' ? (
            <div className="discover-blacklist-empty">{BLACKLIST_LOAD_FAILED}</div>
          ) : entries.length === 0 ? (
            <div className="discover-blacklist-empty">{BLACKLIST_EMPTY}</div>
          ) : (
            entries.map((e) => (
              <div key={String(e.id ?? e.artist_name)} className="discover-blacklist-item">
                <span className="discover-blacklist-name">{e.artist_name ?? ''}</span>
                <span className="discover-blacklist-date">{blacklistEntryDate(e.created_at)}</span>
                <button
                  type="button"
                  className="discover-blacklist-remove"
                  title="Unblock"
                  onClick={() => onUnblock(e)}
                >
                  ✕
                </button>
              </div>
            ))
          )}
        </div>
        <div className="discover-blacklist-modal-footer">
          <button type="button" className="watch-all-btn watch-all-btn-cancel" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
