import { useState } from 'react';

import {
  type BulkEditValues,
  bulkEditTitle,
  bulkEditUpdates,
  EMPTY_BULK_EDIT,
} from '../-artist-detail.enhanced';

interface Props {
  /** Track ids currently ticked across every open album. */
  selected: Set<string>;
  isAdmin: boolean;
  onClear: () => void;
  /** Applies a batch edit to the loaded albums, as updateLocalEnhancedData did. */
  onEdited: (trackIds: string[], updates: Record<string, unknown>) => void;
}

/**
 * The bulk action bar (#enhanced-bulk-bar) and its Batch Edit modal.
 *
 * Visible only while something is ticked, and only for an admin — the vanilla
 * hid it outright for everyone else rather than showing disabled actions.
 */
export function EnhancedBulkBar({ selected, isAdmin, onClear, onEdited }: Props) {
  const [editing, setEditing] = useState(false);
  const trackIds = [...selected];
  const visible = isAdmin && trackIds.length > 0;

  const analyzeReplayGain = async () => {
    try {
      const response = await fetch('/api/library/tracks/analyze-replaygain-batch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_ids: trackIds }),
      });
      const data = await response.json();
      if (!data.success) {
        window.showToast?.(`ReplayGain: ${data.error}`, 'error');
        return;
      }
      window.showToast?.(`ReplayGain analysis started for ${trackIds.length} tracks…`, 'info');
      window._pollBatchRgStatus?.();
    } catch {
      window.showToast?.('Failed to start batch ReplayGain analysis', 'error');
    }
  };

  return (
    <>
      <div className={`enhanced-bulk-bar${visible ? ' visible' : ''}`} id="enhanced-bulk-bar">
        <div className="enhanced-bulk-bar-info">
          <span className="enhanced-bulk-bar-count" id="enhanced-bulk-count">
            {trackIds.length}
          </span>
          <span className="enhanced-bulk-bar-label">tracks selected</span>
        </div>
        <div className="enhanced-bulk-bar-actions">
          <button
            type="button"
            className="btn btn--sm btn--secondary enhanced-bulk-btn"
            onClick={() => setEditing(true)}
          >
            Edit Selected
          </button>
          <button
            type="button"
            className="btn btn--sm btn--secondary enhanced-bulk-btn tag-write"
            // The vanilla modal takes the ids explicitly, so it works across
            // the boundary without reading the old selection state.
            onClick={() => window.showBatchTagPreview?.(trackIds, null)}
          >
            Write Tags
          </button>
          <button
            type="button"
            className="btn btn--sm btn--secondary enhanced-bulk-btn rg-analyze"
            onClick={() => void analyzeReplayGain()}
          >
            ReplayGain
          </button>
          <button
            type="button"
            className="btn btn--sm btn--danger enhanced-bulk-btn"
            onClick={onClear}
          >
            Clear Selection
          </button>
        </div>
      </div>

      {editing ? (
        <BulkEditModal
          trackIds={trackIds}
          onClose={() => setEditing(false)}
          onApplied={(updates) => {
            setEditing(false);
            onEdited(trackIds, updates);
            onClear();
          }}
        />
      ) : null}
    </>
  );
}

function BulkEditModal({
  trackIds,
  onClose,
  onApplied,
}: {
  trackIds: string[];
  onClose: () => void;
  onApplied: (updates: Record<string, unknown>) => void;
}) {
  const [values, setValues] = useState<BulkEditValues>(EMPTY_BULK_EDIT);

  const set = (field: keyof BulkEditValues) => (e: { target: { value: string } }) =>
    setValues((current) => ({ ...current, [field]: e.target.value }));

  const apply = async () => {
    const updates = bulkEditUpdates(values);
    if (Object.keys(updates).length === 0) {
      window.showToast?.('No changes to apply', 'error');
      return;
    }
    try {
      const response = await fetch('/api/library/tracks/batch', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ track_ids: trackIds, updates }),
      });
      const result = await response.json();
      if (!result.success) throw new Error(result.error);

      window.showToast?.(`Updated ${result.updated_count} tracks`, 'success');
      onApplied(updates);
    } catch (error) {
      window.showToast?.(`Bulk edit failed: ${(error as Error).message}`, 'error');
    }
  };

  return (
    <div className="modal-overlay" id="enhanced-bulk-edit-overlay">
      <div className="enhanced-bulk-modal">
        <div className="enhanced-bulk-modal-header">
          <h3 id="enhanced-bulk-modal-title">{bulkEditTitle(trackIds.length)}</h3>
        </div>
        <div className="enhanced-bulk-modal-body" id="enhanced-bulk-modal-body">
          {/* Every field is "leave blank to skip": one value goes to many
              tracks, so an empty box must never mean "clear it on all". */}
          <div className="enhanced-bulk-modal-field">
            <label>Track Number (leave blank to skip)</label>
            <input
              type="number"
              id="bulk-edit-track-number"
              placeholder="Track number..."
              min="1"
              value={values.track_number}
              onChange={set('track_number')}
            />
          </div>
          <div className="enhanced-bulk-modal-field">
            <label>BPM (leave blank to skip)</label>
            <input
              type="number"
              id="bulk-edit-bpm"
              placeholder="BPM..."
              step="0.1"
              value={values.bpm}
              onChange={set('bpm')}
            />
          </div>
          <div className="enhanced-bulk-modal-field">
            <label>Style (leave blank to skip)</label>
            <input
              type="text"
              id="bulk-edit-style"
              placeholder="Style..."
              value={values.style}
              onChange={set('style')}
            />
          </div>
          <div className="enhanced-bulk-modal-field">
            <label>Mood (leave blank to skip)</label>
            <input
              type="text"
              id="bulk-edit-mood"
              placeholder="Mood..."
              value={values.mood}
              onChange={set('mood')}
            />
          </div>
          <div className="enhanced-bulk-modal-field">
            <label>Explicit</label>
            <select id="bulk-edit-explicit" value={values.explicit} onChange={set('explicit')}>
              <option value="">-- No change --</option>
              <option value="0">No</option>
              <option value="1">Yes</option>
            </select>
          </div>
        </div>
        <div className="enhanced-bulk-modal-footer">
          <button type="button" className="btn btn--sm btn--secondary" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn--sm btn--primary" onClick={() => void apply()}>
            Apply
          </button>
        </div>
      </div>
    </div>
  );
}
