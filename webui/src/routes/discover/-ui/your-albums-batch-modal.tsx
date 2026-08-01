import { useEffect, useState } from 'react';

import type { BatchProgressState, BatchRow } from '../-discover.your-albums-actions';

import {
  BATCH_DONE_TEXT,
  BATCH_PROCESSING_INFO,
  batchCardAnimationDelay,
  batchCardMeta,
  batchFooter,
  batchModalSubtitle,
  batchProgressKey,
  batchSummary,
  selectedBatchRows,
} from '../-discover.your-albums-actions';

/**
 * The Your Albums batch modal — "Add Missing Albums to Wishlist".
 *
 * Transcribed from `_openYourAlbumsBatchModal` + its card/progress renderers
 * (discover.js 1770-2030). It reuses the library Download Discography flow's
 * `.discog-*` classes wholesale; the one structural difference the vanilla
 * notes — each card carries its own artist+source — lives in `batchCardMeta`.
 *
 * Three deliberate absences, all the vanilla's:
 * - NO backdrop-click close. Unlike every other discover modal, the overlay
 *   has no click handler (1784-1788) — only Cancel and the ✕ close it.
 * - `.discog-filters` renders EMPTY (1801): the library modal populates it,
 *   this one never does.
 * - Cards are `<label>`s; the checkbox is the only interaction.
 *
 * The phase walk matches the vanilla's DOM mutations: `select` shows the grid
 * and filter bar; `running` hides both, shows the progress list and swaps the
 * footer info for the processing line while the submit button vanishes;
 * `done` brings the button back — disabled, reading "Done" — with the totals
 * in the footer. The stream itself (fetch, ndjson, reducer) is the hook's.
 */

export interface YourAlbumsBatchModalProps {
  rows: BatchRow[];
  /** `_index` values of the checked rows — the join key, not a position. */
  selected: number[];
  phase: 'select' | 'running' | 'done';
  progress: BatchProgressState | null;
  onToggleRow: (index: number, checked: boolean) => void;
  onSelectAll: (select: boolean) => void;
  onSubmit: () => void;
  onClose: () => void;
}

export function YourAlbumsBatchModal({
  rows,
  selected,
  phase,
  progress,
  onToggleRow,
  onSelectAll,
  onSubmit,
  onClose,
}: YourAlbumsBatchModalProps) {
  // The vanilla adds `visible` on the next frame (1825) so the CSS transition
  // runs; mounting straight to visible would skip it.
  const [visible, setVisible] = useState(false);
  useEffect(() => setVisible(true), []);

  const selectedRows = selectedBatchRows(rows, selected);
  const footer = batchFooter(selectedRows);
  const selecting = phase === 'select';

  return (
    <div
      className={visible ? 'discog-modal-overlay visible' : 'discog-modal-overlay'}
      id="your-albums-batch-modal-overlay"
    >
      <div className="discog-modal">
        <div className="discog-modal-hero">
          <div className="discog-modal-hero-overlay" />
          <div className="discog-modal-hero-content">
            <h2 className="discog-modal-title">Add Missing Albums to Wishlist</h2>
            <p className="discog-modal-artist">{batchModalSubtitle(rows.length)}</p>
          </div>
          <button type="button" className="discog-modal-close" onClick={onClose}>
            &times;
          </button>
        </div>
        {selecting && (
          <div className="discog-filter-bar">
            <div className="discog-filters" />
            <div className="discog-select-actions">
              <button type="button" className="discog-select-btn" onClick={() => onSelectAll(true)}>
                Select All
              </button>
              <button
                type="button"
                className="discog-select-btn"
                onClick={() => onSelectAll(false)}
              >
                Deselect All
              </button>
            </div>
          </div>
        )}
        {selecting && (
          <div className="discog-grid" id="your-albums-batch-grid">
            {rows.map((row, i) => (
              <label
                key={row._index}
                className="discog-card"
                data-type="album"
                style={{ animationDelay: batchCardAnimationDelay(i) }}
              >
                <input
                  type="checkbox"
                  className="your-albums-batch-cb"
                  data-row-index={row._index}
                  data-tracks={row.total_tracks || 0}
                  checked={selected.includes(row._index)}
                  onChange={(e) => onToggleRow(row._index, e.target.checked)}
                />
                <div className="discog-card-art">
                  {row.image_url ? (
                    <img src={row.image_url} alt="" loading="lazy" />
                  ) : (
                    <div className="discog-card-art-placeholder">🎵</div>
                  )}
                </div>
                <div className="discog-card-info">
                  <div className="discog-card-title">{row.album_name || ''}</div>
                  <div className="discog-card-meta">{batchCardMeta(row)}</div>
                </div>
                <div className="discog-card-check" />
              </label>
            ))}
          </div>
        )}
        {!selecting && progress && (
          <div className="discog-progress" id="your-albums-batch-progress">
            {selectedRows.map((row) => {
              const key = batchProgressKey(row._src);
              const item = progress.items[key] ?? { status: 'waiting' as const, text: '' };
              return (
                <div
                  key={key}
                  className={`discog-progress-item ${item.status === 'waiting' ? 'active' : item.status}`}
                  id={`your-albums-batch-prog-${key}`}
                >
                  <div className="discog-prog-art">
                    {row.image_url ? <img src={row.image_url} alt="" /> : '🎵'}
                  </div>
                  <div className="discog-prog-info">
                    <div className="discog-prog-title">{row.album_name || ''}</div>
                    <div className="discog-prog-status">{item.text}</div>
                  </div>
                  <div className="discog-prog-icon">
                    {item.status === 'waiting' ? (
                      <div className="discog-spinner" />
                    ) : item.status === 'done' ? (
                      '✓'
                    ) : (
                      '✗'
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
        <div className="discog-footer" id="your-albums-batch-footer">
          <div className="discog-footer-info" id="your-albums-batch-footer-info">
            {phase === 'select'
              ? footer.info
              : phase === 'running'
                ? BATCH_PROCESSING_INFO
                : batchSummary(progress ?? { totalAdded: 0, totalSkipped: 0, items: {} }).info}
          </div>
          <div className="discog-footer-actions">
            <button type="button" className="discog-cancel-btn" onClick={onClose}>
              Cancel
            </button>
            {/* The submit button leaves the layout mid-run (2024) and returns
                disabled once the stream ends. */}
            {phase !== 'running' && (
              <button
                type="button"
                className="discog-submit-btn"
                id="your-albums-batch-submit-btn"
                disabled={phase === 'done' || footer.submitDisabled}
                onClick={onSubmit}
              >
                <span className="discog-submit-icon">⬇</span>
                <span id="your-albums-batch-submit-text">
                  {phase === 'done' ? BATCH_DONE_TEXT : footer.submitText}
                </span>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
