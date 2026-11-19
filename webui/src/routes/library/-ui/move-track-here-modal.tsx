import { useEffect, useState } from 'react';

import type { SlotMovePreview } from '../-library-v2.slot-move';
import type { LibrarySong } from '../-library-v2.songs';

import {
  applySlotMove,
  candidateQuality,
  formatClock,
  previewSlotMove,
  searchSlotMoveCandidates,
  tagValue,
} from '../-library-v2.slot-move';

/**
 * #2193 "Move a track here": a missing album track takes a track you already
 * have. Search your library, pick the track, see what moves where and which
 * tags change, then move it. The file moves (never a copy), so there is no
 * duplicate left behind.
 */
export function MoveTrackHereModal({
  missingTrackId,
  missingTitle,
  missingNumber,
  albumTitle,
  artistName,
  onMoved,
  onClose,
}: {
  missingTrackId: number;
  missingTitle: string;
  missingNumber: number | null | undefined;
  albumTitle: string;
  artistName: string;
  onMoved: () => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState(missingTitle);
  const [results, setResults] = useState<LibrarySong[] | null>(null);
  const [searchError, setSearchError] = useState('');
  const [selected, setSelected] = useState<LibrarySong | null>(null);
  const [preview, setPreview] = useState<SlotMovePreview | null>(null);
  const [previewError, setPreviewError] = useState('');
  const [busy, setBusy] = useState(false);

  const runSearch = async (value: string) => {
    setSelected(null);
    setPreview(null);
    setPreviewError('');
    if (!value.trim()) {
      setResults([]);
      setSearchError('');
      return;
    }
    setResults(null);
    setSearchError('');
    try {
      setResults(await searchSlotMoveCandidates(value.trim(), missingTrackId));
    } catch (error) {
      setSearchError((error as Error).message);
      setResults([]);
    }
  };

  useEffect(() => {
    void runSearch(query);
    // searched once on open, prefilled with the missing track's title
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const pick = async (song: LibrarySong) => {
    setSelected(song);
    setPreview(null);
    setPreviewError('');
    setBusy(true);
    try {
      setPreview(await previewSlotMove(missingTrackId, Number(song.track_id)));
    } catch (error) {
      setPreviewError((error as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const move = async () => {
    if (!selected || !preview) return;
    setBusy(true);
    try {
      const result = await applySlotMove(missingTrackId, Number(selected.track_id));
      const filed =
        result.organize?.queued || result.organize?.reason === 'already_queued'
          ? ' The file is being moved into the album folder.'
          : '';
      window.showToast?.(`Moved "${selected.title}" into ${albumTitle}.${filed}`, 'success');
      onMoved();
      onClose();
    } catch (error) {
      setPreviewError((error as Error).message);
      setBusy(false);
    }
  };

  const close = () => {
    if (!busy) onClose();
  };

  return (
    <div
      id="move-track-here-overlay"
      className="modal-overlay"
      onClick={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        className="enhanced-manual-match-modal enhanced-have-track-modal"
        role="dialog"
        aria-label="Move a track here"
      >
        <div className="enhanced-bulk-modal-header">
          <div>
            <h3>Move a track here</h3>
            <div className="enhanced-have-subtitle">
              Pick a track you already have. Its file moves into this album, nothing is copied.
            </div>
          </div>
          <button
            className="enhanced-bulk-modal-close"
            type="button"
            aria-label="Close"
            disabled={busy}
            onClick={close}
          >
            ×
          </button>
        </div>
        <div className="enhanced-have-target">
          <div className="enhanced-have-target-label">Missing album slot</div>
          <div className="enhanced-have-target-title">
            #{String(missingNumber ?? '?')} {missingTitle || 'Unknown Track'}
          </div>
          <div className="enhanced-have-target-meta">
            {artistName} · {albumTitle}
          </div>
        </div>
        <div className="enhanced-match-search-row">
          <input
            className="enhanced-match-search-input"
            type="text"
            value={query}
            aria-label="Search your library"
            placeholder="Search your library by title or artist..."
            disabled={busy}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !busy) void runSearch(query);
            }}
          />
          <button
            className="enhanced-enrich-btn"
            type="button"
            disabled={busy}
            onClick={() => void runSearch(query)}
          >
            Search
          </button>
        </div>
        <div className="enhanced-match-results" data-testid="move-track-results">
          {searchError ? (
            <div className="enhanced-match-results-hint">Error: {searchError}</div>
          ) : results === null ? (
            <div className="enhanced-loading">Searching...</div>
          ) : results.length === 0 ? (
            <div className="enhanced-match-results-hint">
              {query.trim()
                ? 'No tracks with a file match that. Try the artist or a shorter title.'
                : 'Enter a title or artist to search.'}
            </div>
          ) : (
            results.map((song) => {
              const id = String(song.track_id);
              const isSelected = selected ? String(selected.track_id) === id : false;
              return (
                <div
                  className={`enhanced-have-result-row${isSelected ? ' selected' : ''}${busy ? ' disabled' : ''}`}
                  role="button"
                  tabIndex={0}
                  aria-pressed={isSelected}
                  key={id}
                  onClick={() => {
                    if (!busy) void pick(song);
                  }}
                  onKeyDown={(e) => {
                    if ((e.key === 'Enter' || e.key === ' ') && !busy) {
                      e.preventDefault();
                      void pick(song);
                    }
                  }}
                >
                  <span className="enhanced-have-radio" />
                  <span className="enhanced-have-result-main">
                    <span className="enhanced-have-result-title">{song.title || 'Unknown'}</span>
                    <span className="enhanced-have-result-meta">
                      {song.artist_name || ''}
                      {song.album_title ? ` · ${song.album_title}` : ''}
                    </span>
                    <span className="enhanced-have-result-file" title={song.file_path}>
                      {song.file_path}
                    </span>
                  </span>
                  <span className="enhanced-have-result-side">
                    {song.duration ? <span>{formatClock(song.duration)}</span> : null}
                    {candidateQuality(song) ? <span>{candidateQuality(song)}</span> : null}
                  </span>
                </div>
              );
            })
          )}
        </div>
        {selected && busy && !preview ? (
          <div className="enhanced-have-note">Working out what moves...</div>
        ) : null}
        {previewError ? (
          <div className="enhanced-have-import-status error" role="alert">
            <div className="enhanced-have-import-detail">{previewError}</div>
          </div>
        ) : null}
        {preview ? <MovePreview preview={preview} /> : null}
        <div className="enhanced-bulk-modal-footer">
          <button
            className="btn btn--sm btn--secondary enhanced-bulk-btn"
            type="button"
            disabled={busy}
            onClick={close}
          >
            Cancel
          </button>
          <button
            className="btn btn--sm btn--primary enhanced-bulk-btn"
            type="button"
            disabled={!preview || busy}
            onClick={() => void move()}
          >
            {busy && preview ? 'Moving...' : 'Move here'}
          </button>
        </div>
      </div>
    </div>
  );
}

function MovePreview({ preview }: { preview: SlotMovePreview }) {
  const { source, path, tags } = preview;
  return (
    <div className="enhanced-have-selected" data-testid="move-track-preview">
      <span>What happens</span>
      <strong>
        &quot;{source.title}&quot; leaves {source.album_title || 'its album'} and fills this slot
      </strong>
      <div className="enhanced-have-note">
        {path.new && !path.unchanged ? (
          <>
            File: {path.current} → {path.new}
          </>
        ) : path.new ? (
          <>File stays at {path.current}, it is already where this album puts it</>
        ) : (
          <>
            File stays at {path.current}
            {path.reason ? ` (${path.reason})` : ''}
          </>
        )}
        {path.collision ? ' Another file of this album is already at that path, so it stays.' : ''}
      </div>
      {tags.changes.length ? (
        <ul className="enhanced-have-note" aria-label="Tags that change">
          {tags.changes.map((change) => (
            <li key={change.field}>
              {change.field}: {tagValue(change.from)} → {tagValue(change.to)}
            </li>
          ))}
        </ul>
      ) : (
        <div className="enhanced-have-note">
          {tags.error ? `Tags: ${tags.error}` : 'The tags already match this slot.'}
        </div>
      )}
      {preview.source_album_left_empty ? (
        <div className="enhanced-have-note">
          {source.album_title || 'Its old album'} has nothing else on it, so it leaves your library.
        </div>
      ) : null}
    </div>
  );
}
