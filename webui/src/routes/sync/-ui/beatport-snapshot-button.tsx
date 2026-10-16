/**
 * "Save snapshot" in the beatport discovery modal.
 *
 * a mirrored beatport list follows the live chart, so last week's tracks drop
 * out of it. a snapshot keeps today's list as its own playlist ("Hype Top 10 ·
 * 2026-10-16") that never refreshes, next to the live one. only for lists
 * that change; a release never does.
 */

import { useState } from 'react';

import { isLiveBeatportRef, saveBeatportSnapshot } from '../-beatport.lists';

export function BeatportSnapshotButton({ chartRef }: { chartRef: string }) {
  const [saving, setSaving] = useState(false);
  if (!isLiveBeatportRef(chartRef)) return null;

  const onClick = async () => {
    setSaving(true);
    try {
      const result = await saveBeatportSnapshot(chartRef);
      if (result.success) {
        window.showToast?.(
          `Saved "${result.name}" (${result.track_count} tracks) to your mirrored playlists`,
          'success',
        );
      } else {
        window.showToast?.(result.error || 'Snapshot failed', 'error');
      }
    } catch {
      window.showToast?.('Snapshot failed', 'error');
    } finally {
      setSaving(false);
    }
  };

  return (
    <button
      type="button"
      className="modal-btn modal-btn-secondary"
      disabled={saving}
      title="Keep today's list as its own playlist that never changes"
      onClick={() => void onClick()}
    >
      {saving ? 'Saving…' : '📸 Save snapshot'}
    </button>
  );
}
