import { useEffect, useState } from 'react';

import type { TagPreview } from '../-artist-detail.tags-rg';

import {
  fetchTagPreview,
  offersServerSync,
  serverSyncLabel,
  writeTagsRequest,
} from '../-artist-detail.tags-rg';

/**
 * Single-track "Write Tags to File" (showTagPreview library.js:5334): the
 * current-file-tag vs DB-value diff table, the embed-cover checkbox (default
 * on), and the sync-to-server offer for Plex/Jellyfin — never Navidrome,
 * which picks tag changes up on its own.
 */

export function TagPreviewModal({ trackId, onClose }: { trackId: unknown; onClose: () => void }) {
  const [preview, setPreview] = useState<TagPreview | null>(null);
  const [embedCover, setEmbedCover] = useState(true);
  const [syncToServer, setSyncToServer] = useState(false);
  const [writing, setWriting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    void fetchTagPreview(trackId)
      .then((result) => {
        if (!cancelled) setPreview(result);
      })
      .catch((e: Error) => {
        if (!cancelled)
          setPreview({ diff: [], hasChanges: false, serverType: null, error: e.message });
      });
    return () => {
      cancelled = true;
    };
  }, [trackId]);

  const write = async () => {
    if (!preview || writing) return;
    setWriting(true);
    try {
      const message = await writeTagsRequest(
        trackId,
        embedCover,
        syncToServer && offersServerSync(preview.serverType),
        preview.serverType,
      );
      window.showToast?.(message, 'success');
      onClose();
      return;
    } catch (error) {
      window.showToast?.(`Failed to write tags: ${(error as Error).message}`, 'error');
    }
    setWriting(false);
  };

  // The vanilla enabled Write when there were changes OR the embed-cover box
  // was ticked (5392-5395): re-embedding art is a valid write on its own.
  const canWrite = Boolean(preview && !preview.error && (preview.hasChanges || embedCover));

  return (
    <div
      id="tag-preview-overlay"
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="tag-preview-modal">
        <div className="enhanced-bulk-modal-header">
          <h3 id="tag-preview-title">Write Tags to File</h3>
          <button className="enhanced-bulk-modal-close" type="button" onClick={onClose}>
            ×
          </button>
        </div>
        <div id="tag-preview-body">
          {!preview ? (
            <div className="tag-preview-loading">Loading tag comparison...</div>
          ) : preview.error ? (
            <div className="tag-preview-error">{preview.error}</div>
          ) : (
            <>
              <TagDiffTable diff={preview.diff} changedOnly={false} />
              {!preview.hasChanges ? (
                <div className="tag-preview-no-changes">File tags already match DB metadata</div>
              ) : null}
            </>
          )}
        </div>
        <div className="enhanced-bulk-modal-footer">
          <label className="tag-preview-option">
            <input
              type="checkbox"
              id="tag-preview-embed-cover"
              checked={embedCover}
              onChange={(e) => setEmbedCover(e.target.checked)}
            />
            Embed cover art
          </label>
          {preview && offersServerSync(preview.serverType) ? (
            <label className="tag-preview-option" id="tag-preview-sync-label">
              <input
                type="checkbox"
                id="tag-preview-sync-server"
                checked={syncToServer}
                onChange={(e) => setSyncToServer(e.target.checked)}
              />
              <span id="tag-preview-sync-text">{serverSyncLabel(preview.serverType)}</span>
            </label>
          ) : null}
          <div style={{ marginLeft: 'auto' }}>
            <button className="btn btn--sm btn--secondary" type="button" onClick={onClose}>
              Cancel
            </button>
            <button
              className="btn btn--sm btn--primary"
              id="tag-preview-write-btn"
              type="button"
              disabled={!canWrite || writing}
              onClick={() => void write()}
            >
              {writing ? 'Writing...' : 'Write Tags'}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/** The diff table both tag modals share (5369-5384 / 5583-5595). */
export function TagDiffTable({
  diff,
  changedOnly,
}: {
  diff: { field: string; file_value?: string; db_value?: string; changed?: boolean }[];
  changedOnly: boolean;
}) {
  const rows = changedOnly ? diff.filter((d) => d.changed) : diff;
  return (
    <table className="tag-preview-table">
      <thead>
        <tr>
          <th>Field</th>
          <th>{changedOnly ? 'Current File' : 'Current File Tag'}</th>
          <th />
          <th>{changedOnly ? 'New Value' : 'DB Value'}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((d) => (
          <tr className={d.changed ? 'tag-diff-changed' : 'tag-diff-same'} key={d.field}>
            <td className="tag-field-name">{d.field}</td>
            <td className="tag-file-value">
              {d.file_value || <span className="tag-empty">empty</span>}
            </td>
            <td className="tag-diff-indicator">
              {d.changed ? (
                <span className="tag-diff-arrow">→</span>
              ) : (
                <span className="tag-diff-check">✓</span>
              )}
            </td>
            <td className="tag-db-value">
              {d.db_value || <span className="tag-empty">empty</span>}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
