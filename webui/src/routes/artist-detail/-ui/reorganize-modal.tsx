import { useState } from 'react';

import type { EnhancedAlbum } from '../-artist-detail.enhanced';
import type { ReorganizePreviewTrack } from '../-artist-detail.reorganize';

import {
  classifyPreviewTrack,
  fetchReorganizePreview,
  queueReorganizeAllRequest,
  queueReorganizeRequest,
  refreshReorganizeQueue,
  summarizeReorganizePreview,
} from '../-artist-detail.reorganize';

/**
 * The Reorganize modals (showReorganizeModal library.js:5845 and
 * _showReorganizeAllModal 6174). Queue model: apply enqueues and closes;
 * progress arrives through the Reorganize Status panel, never a locked button.
 *
 * There is nothing to configure. A reorganize moves the album's files to the
 * paths the current template dictates, computed from the library's own rows —
 * so there is no metadata source to pick (#592's "read the tags instead" and
 * #875's "rename only" are both just what it does now), and the preview needs
 * no network call.
 */

function ModalShell({
  title,
  onClose,
  children,
  footer,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  footer: React.ReactNode;
}) {
  return (
    <div
      className="modal-overlay"
      id="reorganize-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="enhanced-bulk-modal reorganize-modal">
        <div className="enhanced-bulk-modal-header">
          <h3 id="reorganize-modal-title">{title}</h3>
          <button className="enhanced-bulk-modal-close" type="button" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="enhanced-bulk-modal-body" id="reorganize-modal-body">
          {children}
        </div>
        <div className="enhanced-bulk-modal-footer" id="reorganize-modal-footer">
          <button
            className="btn btn--sm btn--secondary enhanced-bulk-btn"
            type="button"
            onClick={onClose}
          >
            Cancel
          </button>
          {footer}
        </div>
      </div>
    </div>
  );
}

export function ReorganizeModal({ album, onClose }: { album: EnhancedAlbum; onClose: () => void }) {
  const [preview, setPreview] = useState<{
    loading?: boolean;
    error?: string;
    tracks?: ReorganizePreviewTrack[];
  } | null>(null);
  const [applying, setApplying] = useState(false);

  const loadPreview = async () => {
    setPreview({ loading: true });
    try {
      const result = await fetchReorganizePreview(album.id);
      if (result.error) setPreview({ error: result.error });
      else setPreview({ tracks: result.tracks });
    } catch (error) {
      setPreview({ error: `Error: ${(error as Error).message}` });
    }
  };

  const summary = preview?.tracks ? summarizeReorganizePreview(preview.tracks) : null;
  const canApply = Boolean(summary?.canApply) && !applying;

  const apply = async () => {
    setApplying(true);
    try {
      const message = await queueReorganizeRequest(album.id, String(album.title || 'album'));
      onClose();
      window.showToast?.(message, 'info');
      // Wake the status panel so the new item lands immediately rather than
      // waiting for the next poll tick.
      void refreshReorganizeQueue();
    } catch (error) {
      window.showToast?.(`Reorganize failed: ${(error as Error).message}`, 'error');
      setApplying(false);
    }
  };

  return (
    <ModalShell
      title={`Reorganize: ${album.title || 'Album'}`}
      onClose={onClose}
      footer={
        <button
          className="btn btn--sm btn--primary enhanced-bulk-btn"
          id="reorganize-apply-btn"
          type="button"
          disabled={!canApply}
          onClick={() => void apply()}
        >
          {applying ? 'Queueing...' : 'Apply'}
        </button>
      }
    >
      <div className="reorganize-content">
        <div className="reorganize-source-section">
          <div className="reorganize-template-hint">
            Moves this album's files to the paths your current naming scheme dictates, using the
            titles the library holds. Tags and audio are left byte-for-byte alone, and only files
            whose path actually changes are touched. Tip: renaming can reset play counts /
            date-added on your media server.
          </div>
        </div>

        <div className="reorganize-preview-section">
          <div className="reorganize-preview-header">
            <label className="reorganize-label">Preview</label>
            <button
              className="reorganize-preview-btn"
              type="button"
              onClick={() => void loadPreview()}
            >
              Generate Preview
            </button>
          </div>
          <div id="reorganize-preview-body" className="reorganize-preview-body">
            {!preview ? (
              <div className="reorganize-preview-hint">
                Click "Generate Preview" to see how files will be reorganized.
              </div>
            ) : preview.loading ? (
              <div className="reorganize-preview-loading">Loading preview...</div>
            ) : preview.error ? (
              <div className="reorganize-preview-error">{preview.error}</div>
            ) : (preview.tracks ?? []).length === 0 ? (
              <div className="reorganize-preview-hint">No tracks found.</div>
            ) : (
              <>
                <div className="reorganize-preview-summary">
                  {summary?.chips.map((chip) => (
                    <span className={`reorganize-stat ${chip.className}`} key={chip.text}>
                      {chip.text}
                    </span>
                  ))}
                </div>
                <PreviewTable tracks={preview.tracks ?? []} />
              </>
            )}
          </div>
        </div>
      </div>
    </ModalShell>
  );
}

function PreviewTable({ tracks }: { tracks: ReorganizePreviewTrack[] }) {
  return (
    <table className="reorganize-preview-table">
      <thead>
        <tr>
          <th>#</th>
          <th>Title</th>
          <th>Current Path</th>
          <th />
          <th>New Path</th>
        </tr>
      </thead>
      <tbody>
        {tracks.map((t, index) => {
          const view = classifyPreviewTrack(t);
          return (
            <tr className={view.rowClass} key={index}>
              <td>{t.track_number || ''}</td>
              <td>{t.title}</td>
              <td className="reorganize-path">
                {view.currentMissing ? <em>File not found</em> : t.current_path}
              </td>
              <td className="reorganize-arrow">{view.arrow}</td>
              <td className="reorganize-path">
                {view.newCell.kind === 'reason' ? (
                  <em>{view.newCell.text}</em>
                ) : view.newCell.kind === 'path' ? (
                  <>
                    {view.newCell.text}
                    {view.newCell.collision ? <em> (collision)</em> : null}
                  </>
                ) : null}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function ReorganizeAllModal({
  albums,
  artistId,
  artistName,
  onClose,
}: {
  albums: EnhancedAlbum[];
  artistId: unknown;
  artistName: string;
  onClose: () => void;
}) {
  const queueAll = async () => {
    const total = albums.length;
    const confirmed = await window.showConfirmDialog?.({
      title: 'Reorganize All Albums',
      message: `This will queue ${total} album${total !== 1 ? 's' : ''} for ${artistName} using your configured download template. Files will be moved and renamed. This cannot be undone.`,
      confirmText: 'Queue All',
      destructive: false,
    });
    if (!confirmed) return;
    onClose();
    try {
      const { message, tone } = await queueReorganizeAllRequest(artistId, artistName);
      window.showToast?.(message, tone);
      void refreshReorganizeQueue();
    } catch (error) {
      window.showToast?.(`Reorganize-all failed: ${(error as Error).message}`, 'error');
    }
  };

  return (
    <ModalShell
      title={`Reorganize All Albums — ${artistName}`}
      onClose={onClose}
      footer={
        <button
          className="btn btn--sm btn--primary enhanced-bulk-btn"
          id="reorganize-apply-btn"
          type="button"
          onClick={() => void queueAll()}
        >
          Reorganize All
        </button>
      }
    >
      <div className="reorganize-content">
        <div className="reorganize-source-section">
          <div className="reorganize-template-hint">
            Moves every album's files to the paths your current naming scheme dictates, using the
            titles the library holds. Tags and audio are left alone.
          </div>
        </div>
        <div style={{ marginTop: 14 }}>
          <label className="reorganize-label">
            {albums.length} album{albums.length !== 1 ? 's' : ''} will be reorganized:
          </label>
          <div
            style={{
              maxHeight: 200,
              overflowY: 'auto',
              marginTop: 6,
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 8,
              padding: '6px 10px',
            }}
          >
            {albums.map((a, i) => (
              <div
                style={{
                  padding: '4px 0',
                  fontSize: '0.88em',
                  color: 'rgba(255,255,255,0.7)',
                  borderBottom: i < albums.length - 1 ? '1px solid rgba(255,255,255,0.04)' : 'none',
                }}
                key={String(a.id)}
              >
                {String(a.title || '')}{' '}
                <span style={{ color: 'rgba(255,255,255,0.3)' }}>
                  ({a.tracks ? a.tracks.length : '?'} tracks)
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </ModalShell>
  );
}
