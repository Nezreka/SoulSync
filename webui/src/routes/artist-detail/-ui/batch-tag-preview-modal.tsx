import { useEffect, useState } from 'react';

import type { BatchTagPreview, BatchTagTrack } from '../-artist-detail.tags-rg';

import {
  fetchBatchTagPreview,
  offersServerSync,
  serverSyncLabel,
  startBatchWriteTags,
} from '../-artist-detail.tags-rg';
import { TagDiffTable } from './tag-preview-modal';

/**
 * Batch "Write Tags" (showBatchTagPreview library.js:5469): one preview for a
 * whole album or the ticked selection. Changed tracks come expanded showing
 * only their changed fields; unavailable tracks show their error; unchanged
 * ones collapse into a single "already up to date" group. Writing kicks off
 * the background batch and closes — progress arrives as toasts from the
 * status poller, which outlives this modal on purpose.
 */

export function BatchTagPreviewModal({
  trackIds,
  albumTitle,
  onClose,
}: {
  trackIds: unknown[];
  /** Set for the per-album entry; null for "N tracks" from the bulk bar. */
  albumTitle: string | null;
  onClose: () => void;
}) {
  const [preview, setPreview] = useState<BatchTagPreview | null>(null);
  const [embedCover, setEmbedCover] = useState(true);
  const [syncToServer, setSyncToServer] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetchBatchTagPreview(trackIds)
      .then((result) => {
        if (!cancelled) setPreview(result);
      })
      .catch((e: Error) => {
        if (!cancelled) setPreview({ tracks: [], serverType: null, error: e.message });
      });
    return () => {
      cancelled = true;
    };
    // The id list never changes for a mounted preview.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const withChanges = preview?.tracks.filter((t) => t.has_changes) ?? [];
  const errored = preview?.tracks.filter((t) => t.error) ?? [];
  const unchanged = preview?.tracks.filter((t) => !t.error && !t.has_changes) ?? [];

  const write = () => {
    const sync = syncToServer && offersServerSync(preview?.serverType ?? null);
    onClose();
    void startBatchWriteTags(trackIds, embedCover, sync);
  };

  return (
    <div
      id="batch-tag-preview-overlay"
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="batch-tag-preview-modal">
        <div className="enhanced-bulk-modal-header">
          <h3 id="batch-tag-preview-title">
            {albumTitle ? `Write Tags — ${albumTitle}` : `Write Tags — ${trackIds.length} Tracks`}
          </h3>
          <button className="enhanced-bulk-modal-close" type="button" onClick={onClose}>
            ×
          </button>
        </div>

        <div id="batch-tag-preview-summary">
          {preview && !preview.error ? (
            <div className="batch-tag-summary">
              {withChanges.length > 0 ? (
                <span className="batch-tag-stat changed">{withChanges.length} with changes</span>
              ) : null}
              {unchanged.length > 0 ? (
                <span className="batch-tag-stat unchanged">{unchanged.length} unchanged</span>
              ) : null}
              {errored.length > 0 ? (
                <span className="batch-tag-stat errored">{errored.length} unavailable</span>
              ) : null}
            </div>
          ) : null}
        </div>

        <div id="batch-tag-preview-body">
          {!preview ? (
            <div className="tag-preview-loading">Loading tag previews...</div>
          ) : preview.error ? (
            <div className="tag-preview-error">{preview.error}</div>
          ) : (
            <>
              {withChanges.map((track, index) => (
                <BatchTrackDiff track={track} key={`c${index}`} />
              ))}
              {errored.map((track, index) => (
                <div className="batch-tag-track error" key={`e${index}`}>
                  <div className="batch-tag-track-header">
                    <span className="batch-tag-track-number">{track.track_number || '—'}</span>
                    <span className="batch-tag-track-title">{track.title}</span>
                    <span className="batch-tag-track-status error">{track.error}</span>
                  </div>
                </div>
              ))}
              {unchanged.length > 0 ? <UnchangedGroup tracks={unchanged} /> : null}
              {withChanges.length === 0 && errored.length === 0 ? (
                <div className="tag-preview-no-changes">
                  All file tags already match DB metadata
                </div>
              ) : null}
            </>
          )}
        </div>

        <div className="enhanced-bulk-modal-footer">
          <label className="tag-preview-option">
            <input
              type="checkbox"
              id="batch-tag-preview-embed-cover"
              checked={embedCover}
              onChange={(e) => setEmbedCover(e.target.checked)}
            />
            Embed cover art
          </label>
          {preview && offersServerSync(preview.serverType) ? (
            <label className="tag-preview-option" id="batch-tag-preview-sync-label">
              <input
                type="checkbox"
                id="batch-tag-preview-sync-server"
                checked={syncToServer}
                onChange={(e) => setSyncToServer(e.target.checked)}
              />
              <span id="batch-tag-preview-sync-text">{serverSyncLabel(preview.serverType)}</span>
            </label>
          ) : null}
          <div style={{ marginLeft: 'auto' }}>
            <button className="btn btn--sm btn--secondary" type="button" onClick={onClose}>
              Cancel
            </button>
            <button
              className="btn btn--sm btn--primary"
              id="batch-tag-preview-write-btn"
              type="button"
              disabled={withChanges.length === 0}
              onClick={write}
            >
              Write Tags
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** A changed track: expanded by default, only its CHANGED fields shown (5574). */
function BatchTrackDiff({ track }: { track: BatchTagTrack }) {
  const [expanded, setExpanded] = useState(true);
  return (
    <div className={`batch-tag-track${expanded ? ' expanded' : ''}`}>
      <div className="batch-tag-track-header" onClick={() => setExpanded((open) => !open)}>
        <span className="batch-tag-track-number">{track.track_number || '—'}</span>
        <span className="batch-tag-track-title">{track.title}</span>
        <span className="batch-tag-track-status changed">
          {track.changed_count} field{track.changed_count !== 1 ? 's' : ''} changed
        </span>
        <span className="batch-tag-chevron">▾</span>
      </div>
      {expanded ? (
        <div className="batch-tag-track-diff">
          <TagDiffTable diff={track.diff ?? []} changedOnly />
        </div>
      ) : null}
    </div>
  );
}

function UnchangedGroup({ tracks }: { tracks: BatchTagTrack[] }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className={`batch-tag-unchanged-group${expanded ? ' expanded' : ''}`}>
      <div className="batch-tag-unchanged-header" onClick={() => setExpanded((open) => !open)}>
        <span>
          {tracks.length} track{tracks.length !== 1 ? 's' : ''} already up to date
        </span>
        <span className="batch-tag-chevron">▾</span>
      </div>
      {expanded ? (
        <div className="batch-tag-unchanged-list">
          {tracks.map((track, index) => (
            <div className="batch-tag-track-row unchanged" key={index}>
              <span className="batch-tag-track-number">{track.track_number || '—'}</span>
              <span className="batch-tag-track-title">{track.title}</span>
              <span className="batch-tag-track-status ok">✓ Tags match</span>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
