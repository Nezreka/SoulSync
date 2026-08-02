import { useEffect, useState } from 'react';

import type { EnhancedAlbum } from '../-artist-detail.enhanced';
import type { ReorganizePreviewTrack, ReorganizeSource } from '../-artist-detail.reorganize';

import {
  classifyPreviewTrack,
  fetchAlbumReorganizeSources,
  fetchGlobalReorganizeSources,
  fetchReorganizePreview,
  queueReorganizeAllRequest,
  queueReorganizeRequest,
  readReorganizeMode,
  refreshReorganizeQueue,
  summarizeReorganizePreview,
  writeReorganizeMode,
} from '../-artist-detail.reorganize';

/**
 * The Reorganize modals (showReorganizeModal library.js:5845 and
 * _showReorganizeAllModal 6174). Queue model: apply enqueues and closes;
 * progress arrives through the Reorganize Status panel, never a locked button.
 *
 * The metadata-mode pick (#592 — "tags" reads embedded file tags, zero API
 * calls) persists in localStorage and hides the source picker, since tags are
 * read straight off the file.
 */

const MODE_HINT =
  '"API" queries your metadata source for the canonical tracklist. "Embedded tags" reads each file\'s own tags as the source of truth — useful for well-tagged libraries and avoids API calls.';

function ModeSection({ mode, onChange }: { mode: string; onChange: (mode: string) => void }) {
  return (
    <div className="reorganize-source-section">
      <label className="reorganize-label">Metadata Mode</label>
      <div className="reorganize-template-hint">{MODE_HINT}</div>
      <select
        id="reorganize-mode-select"
        className="reorganize-template-input"
        value={mode}
        onChange={(e) => {
          onChange(e.target.value);
          writeReorganizeMode(e.target.value);
        }}
      >
        <option value="api">API metadata (default)</option>
        <option value="tags">Embedded file tags</option>
      </select>
    </div>
  );
}

function SourceSection({
  label,
  hint,
  visible,
  sources,
  emptyMessage,
  source,
  onChange,
}: {
  label: string;
  hint: string;
  /** Hidden when mode = 'tags' — the picker is irrelevant there. */
  visible: boolean;
  sources: ReorganizeSource[];
  emptyMessage: string | null;
  source: string;
  onChange: (source: string) => void;
}) {
  return (
    <div
      className="reorganize-source-section"
      id="reorganize-source-section"
      style={visible ? undefined : { display: 'none' }}
    >
      <label className="reorganize-label">{label}</label>
      <div className="reorganize-template-hint">{hint}</div>
      <select
        id="reorganize-source-select"
        className="reorganize-template-input"
        value={source}
        onChange={(e) => onChange(e.target.value)}
      >
        <option value="">Use configured primary (auto)</option>
        {sources.map((s) => (
          <option value={s.source} key={s.source}>
            {s.label || s.source}
          </option>
        ))}
        {emptyMessage ? <option disabled>{emptyMessage}</option> : null}
      </select>
    </div>
  );
}

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
  const [mode, setMode] = useState(() => readReorganizeMode());
  const [source, setSource] = useState('');
  const [sources, setSources] = useState<ReorganizeSource[] | null>(null);
  const [action, setAction] = useState('full');
  const [preview, setPreview] = useState<{
    loading?: boolean;
    error?: string;
    tracks?: ReorganizePreviewTrack[];
  } | null>(null);
  const [applying, setApplying] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchAlbumReorganizeSources(album.id)
      .then((list) => {
        if (!cancelled) setSources(list);
      })
      .catch((error: unknown) => {
        console.error('Failed to load reorganize sources:', error);
        if (!cancelled) setSources([]);
      });
    return () => {
      cancelled = true;
    };
    // The album never changes for a mounted modal.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loadPreview = async () => {
    setPreview({ loading: true });
    try {
      const result = await fetchReorganizePreview(album.id, source, mode);
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
      const message = await queueReorganizeRequest(album.id, String(album.title || 'album'), {
        source,
        mode,
        renameOnly: action === 'rename',
      });
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
        <ModeSection mode={mode} onChange={setMode} />
        <SourceSection
          label="Metadata Source"
          hint="Pick which source to read the album's tracklist from. Defaults to your configured primary. Reorganize uses your global download template, same as fresh downloads."
          visible={mode !== 'tags'}
          sources={sources ?? []}
          emptyMessage={
            sources && sources.length === 0 ? 'No sources available — run enrichment first' : null
          }
          source={source}
          onChange={setSource}
        />

        <div className="reorganize-source-section">
          <label className="reorganize-label">Action</label>
          <div className="reorganize-template-hint">
            "Full reorganize" re-tags and re-checks every track through the import pipeline —
            thorough, but slow and it re-touches every file. "Rename only" just moves files to your
            current naming scheme: no re-tagging, no quality/AcoustID checks, and only files whose
            name actually changes are touched. Tip: renaming can reset play counts / date-added on
            your media server.
          </div>
          <select
            id="reorganize-action-select"
            className="reorganize-template-input"
            value={action}
            onChange={(e) => setAction(e.target.value)}
          >
            <option value="full">Full reorganize (default)</option>
            <option value="rename">Rename only (skip post-processing)</option>
          </select>
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
  const [mode, setMode] = useState(() => readReorganizeMode());
  const [source, setSource] = useState('');
  const [sources, setSources] = useState<ReorganizeSource[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchGlobalReorganizeSources()
      .then((list) => {
        if (!cancelled) setSources(list);
      })
      .catch((error: unknown) => console.error('Failed to load reorganize sources:', error));
    return () => {
      cancelled = true;
    };
  }, []);

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
      const { message, tone } = await queueReorganizeAllRequest(artistId, artistName, {
        source,
        mode,
      });
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
        <ModeSection mode={mode} onChange={setMode} />
        <SourceSection
          label="Metadata Source (applies to all albums)"
          hint="Pick which source to read tracklists from. Albums without an ID for that source will be skipped. Reorganize uses your global download template, same as fresh downloads."
          visible={mode !== 'tags'}
          sources={sources}
          emptyMessage={null}
          source={source}
          onChange={setSource}
        />
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
