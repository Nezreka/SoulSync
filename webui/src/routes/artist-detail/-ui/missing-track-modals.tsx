import { useEffect, useRef, useState } from 'react';

import type { EnhancedAlbum, EnhancedTrack } from '../-artist-detail.enhanced';
import type { LibrarySearchTrack, MissingTrackArtist } from '../-artist-detail.missing-track';

import {
  importExistingTrackRequest,
  importStageText,
  searchLibraryTracksRequest,
  wishlistEnhancedMissingTrack,
} from '../-artist-detail.missing-track';

/**
 * Missing-track management (openMissingTrackManageModal, library.js:4884):
 * a two-option chooser, then either the shared wishlist-add flow ("Add to
 * Library") or the "I Have This" copy-import modal.
 */
export function MissingTrackManageModal({
  track,
  album,
  artist,
  onImported,
  onClose,
}: {
  track: EnhancedTrack;
  album: EnhancedAlbum;
  artist: MissingTrackArtist;
  /** A finished import folds the fresh payload / reloads (5203-5209). */
  onImported: (updatedData: Record<string, unknown> | null) => void;
  onClose: () => void;
}) {
  const [having, setHaving] = useState(false);

  if (having) {
    return (
      <HaveTrackModal
        track={track}
        album={album}
        artist={artist}
        onImported={onImported}
        onClose={onClose}
      />
    );
  }

  return (
    <div
      id="enhanced-missing-manage-overlay"
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="confirm-modal enhanced-missing-manage-modal">
        <div className="confirm-modal-header">
          <h2>Manage Missing Track</h2>
          <button className="confirm-modal-close" type="button" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="confirm-modal-body enhanced-missing-manage-body">
          <div className="enhanced-missing-manage-target">
            <div className="enhanced-have-target-label">Missing album slot</div>
            <div className="enhanced-have-target-title">
              #{String(track.track_number || '?')} {String(track.title || 'Unknown Track')}
            </div>
            <div className="enhanced-have-target-meta">
              {artist.name} · {String(album.title || '')}
            </div>
          </div>
          <div className="enhanced-missing-manage-options">
            <button
              className="enhanced-missing-option primary"
              type="button"
              data-action="library"
              onClick={() => {
                onClose();
                void wishlistEnhancedMissingTrack(track, album, artist, false);
              }}
            >
              <span className="enhanced-missing-option-icon">+</span>
              <span>
                <span className="enhanced-missing-option-title">Add to Library</span>
                <span className="enhanced-missing-option-desc">
                  Open the normal library-add flow with this exact track context.
                </span>
              </span>
            </button>
            <button
              className="enhanced-missing-option"
              type="button"
              data-action="have"
              onClick={() => setHaving(true)}
            >
              <span className="enhanced-missing-option-icon">OK</span>
              <span>
                <span className="enhanced-missing-option-title">I Have This</span>
                <span className="enhanced-missing-option-desc">
                  Copy an existing file and process it into this album slot. The original stays
                  untouched.
                </span>
              </span>
            </button>
          </div>
        </div>
        <div className="confirm-modal-actions">
          <button className="modal-button modal-button--secondary" type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}

/**
 * "I Have This" (openHaveMissingTrackModal, library.js:4954): pick an existing
 * library file; SoulSync copies it, writes the missing track's tags, and
 * places the copy in this album. Closing is blocked while the import runs.
 */
export function HaveTrackModal({
  track,
  album,
  artist,
  onImported,
  onClose,
}: {
  track: EnhancedTrack;
  album: EnhancedAlbum;
  artist: MissingTrackArtist;
  onImported: (updatedData: Record<string, unknown> | null) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(`${track.title || ''} ${artist.name}`.trim());
  const [results, setResults] = useState<LibrarySearchTrack[] | null>(null);
  const [searchError, setSearchError] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [status, setStatus] = useState<{
    title: string;
    detail: string;
    tone: 'working' | 'error' | 'success';
  } | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const runSearch = async (value: string) => {
    if (!value.trim()) {
      setResults([]);
      setSearchError('');
      return;
    }
    setSelectedId(null);
    setResults(null);
    setSearchError('');
    try {
      setResults(await searchLibraryTracksRequest(value.trim()));
    } catch (error) {
      setSearchError((error as Error).message);
      setResults([]);
    }
  };

  useEffect(() => {
    void runSearch(query);
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
    // Auto-search once on open, as the vanilla did (5230).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const selected = (results ?? []).find((r) => String(r.id) === selectedId);
  const selectedSummary = selected
    ? `${selected.title || 'Unknown'}${selected.album_title ? ` from ${selected.album_title}` : ''}`
    : '';

  const doImport = async () => {
    if (!selectedId) return;
    setImporting(true);
    setStatus({
      title: 'Importing selected file',
      detail: selectedSummary ? `Using ${selectedSummary}.` : 'Using the selected library track.',
      tone: 'working',
    });
    const start = Date.now();
    timerRef.current = setInterval(() => {
      const seconds = Math.floor((Date.now() - start) / 1000);
      setElapsed(seconds);
      setStatus((prev) =>
        prev && prev.tone === 'working' ? { ...prev, detail: importStageText(seconds) } : prev,
      );
    }, 250);
    try {
      const { updatedData } = await importExistingTrackRequest(
        album,
        track,
        artist.name,
        selectedId,
      );
      if (timerRef.current) clearInterval(timerRef.current);
      setStatus({
        title: 'Import complete',
        detail: 'The copied file is now being shown in this album.',
        tone: 'success',
      });
      window.showToast?.('Track imported. Original file was left untouched.', 'success');
      onImported(updatedData);
      setTimeout(onClose, 650);
    } catch (error) {
      if (timerRef.current) clearInterval(timerRef.current);
      setImporting(false);
      setStatus({ title: 'Import failed', detail: (error as Error).message, tone: 'error' });
      window.showToast?.(`Import failed: ${(error as Error).message}`, 'error');
    }
  };

  const close = () => {
    if (importing) return;
    onClose();
  };

  return (
    <div
      id="enhanced-have-track-overlay"
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div className="enhanced-manual-match-modal enhanced-have-track-modal">
        <div className="enhanced-bulk-modal-header">
          <div>
            <h3>I Have This Track</h3>
            <div className="enhanced-have-subtitle">
              Use an existing file as the source audio. SoulSync will copy it into this album.
            </div>
          </div>
          <button
            className="enhanced-bulk-modal-close"
            type="button"
            disabled={importing}
            onClick={close}
          >
            ×
          </button>
        </div>
        <div className="enhanced-have-target">
          <div className="enhanced-have-target-label">Missing album slot</div>
          <div className="enhanced-have-target-title">
            #{String(track.track_number || '?')} {String(track.title || 'Unknown Track')}
          </div>
          <div className="enhanced-have-target-meta">
            {artist.name} · {String(album.title || '')}
          </div>
        </div>
        <div className="enhanced-match-search-row">
          <input
            className="enhanced-match-search-input"
            id="enhanced-have-track-search"
            type="text"
            value={query}
            placeholder="Search your library..."
            disabled={importing}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !importing) void runSearch(query);
            }}
          />
          <button
            className="enhanced-enrich-btn"
            id="enhanced-have-track-search-btn"
            type="button"
            disabled={importing}
            onClick={() => void runSearch(query)}
          >
            Search
          </button>
        </div>
        <div className="enhanced-match-results" id="enhanced-have-track-results">
          {searchError ? (
            <div className="enhanced-match-results-hint" style={{ color: '#ff6b6b' }}>
              Error: {searchError}
            </div>
          ) : results === null ? (
            <div className="enhanced-loading">Searching...</div>
          ) : results.length === 0 ? (
            <div className="enhanced-match-results-hint">
              {query.trim()
                ? 'No library tracks found. Try a different search.'
                : 'Enter a title or artist to search.'}
            </div>
          ) : (
            results
              .filter((r) => r.id)
              .map((result) => {
                const id = String(result.id);
                const fileName = result.file_path
                  ? result.file_path.split(/[\\/]/).pop()
                  : 'No file path';
                return (
                  <div
                    className={`enhanced-have-result-row${selectedId === id ? ' selected' : ''}${importing ? ' disabled' : ''}`}
                    role="button"
                    tabIndex={0}
                    aria-pressed={selectedId === id}
                    key={id}
                    onClick={() => {
                      if (!importing) setSelectedId(id);
                    }}
                    onKeyDown={(e) => {
                      if ((e.key === 'Enter' || e.key === ' ') && !importing) {
                        e.preventDefault();
                        setSelectedId(id);
                      }
                    }}
                  >
                    <span className="enhanced-have-radio" />
                    <span className="enhanced-have-result-main">
                      <span className="enhanced-have-result-title">
                        {result.title || 'Unknown'}
                      </span>
                      <span className="enhanced-have-result-meta">
                        {result.artist_name || ''}
                        {result.album_title ? ` · ${result.album_title}` : ''}
                      </span>
                      <span className="enhanced-have-result-file">{fileName}</span>
                    </span>
                    <span className="enhanced-have-result-side">
                      {result.duration ? <span>{formatClock(result.duration)}</span> : null}
                      {result.bitrate ? <span>{result.bitrate} kbps</span> : null}
                    </span>
                  </div>
                );
              })
          )}
        </div>
        <div className="enhanced-have-selected" id="enhanced-have-selected" hidden={!selected}>
          <span>Selected</span>
          <strong>{selectedSummary}</strong>
        </div>
        <div className="enhanced-have-note">
          The selected file stays in its current album/folder. SoulSync copies it, writes the
          missing track's tags, and places the copy in this album.
        </div>
        {status ? (
          <div
            className={`enhanced-have-import-status${status.tone === 'error' ? ' error' : ''}${status.tone === 'success' ? ' success' : ''}`}
            id="enhanced-have-import-status"
          >
            <div className="enhanced-have-import-status-top">
              <span className="enhanced-have-import-spinner" />
              <span className="enhanced-have-import-title">{status.title}</span>
              <span className="enhanced-have-import-time">{elapsed}s</span>
            </div>
            <div className="enhanced-have-import-detail">{status.detail}</div>
          </div>
        ) : null}
        <div className="enhanced-bulk-modal-footer">
          <button
            className="btn btn--sm btn--secondary enhanced-bulk-btn"
            id="enhanced-have-cancel"
            type="button"
            disabled={importing}
            onClick={close}
          >
            Cancel
          </button>
          <button
            className="btn btn--sm btn--primary enhanced-bulk-btn"
            id="enhanced-have-confirm"
            type="button"
            disabled={!selectedId || importing}
            onClick={() => void doImport()}
          >
            {importing ? 'Importing...' : 'Import Track'}
          </button>
        </div>
      </div>
    </div>
  );
}

function formatClock(ms: number): string {
  return `${Math.floor(ms / 60000)}:${String(Math.floor((ms % 60000) / 1000)).padStart(2, '0')}`;
}
