import clsx from 'clsx';
import { useEffect, useState } from 'react';

import type { SlotMovePreview, SlotMoveTagChange } from '../-library-v2.slot-move';
import type { LibrarySong } from '../-library-v2.songs';

import {
  applySlotMove,
  candidateQuality,
  formatClock,
  previewSlotMove,
  searchSlotMoveCandidates,
  tagValue,
} from '../-library-v2.slot-move';
import { BodyPortal } from '../../artist-detail/-ui/portal';
import styles from './move-track-here-modal.module.css';

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

  // BodyPortal is load-bearing (#2241): the ⋯ menu lives in the album table's
  // actions cell, and a dialog rendered there took the cell's right align and
  // nowrap and the missing row's italic.
  return (
    <BodyPortal>
      <div
        id="move-track-here-overlay"
        className="modal-overlay"
        onClick={(e) => {
          if (e.target === e.currentTarget) close();
        }}
      >
        <div
          className={styles.dialog}
          role="dialog"
          aria-modal="true"
          aria-label="Move a track here"
        >
          <div className={styles.header}>
            <div className={styles.heading}>
              <h3 className={styles.title}>Move a track here</h3>
              <div className={styles.subtitle}>
                Pick a track you already have. Its file moves into this album, nothing is copied.
              </div>
            </div>
            <button
              className={styles.close}
              type="button"
              aria-label="Close"
              disabled={busy}
              onClick={close}
            >
              ×
            </button>
          </div>
          <div className={styles.body}>
            <div className={styles.slot}>
              <div className={styles.eyebrow}>Missing album slot</div>
              <div className={styles.slotTitle}>
                #{String(missingNumber ?? '?')} {missingTitle || 'Unknown Track'}
              </div>
              <div className={styles.slotMeta}>
                {artistName} · {albumTitle}
              </div>
            </div>
            <div className={styles.search}>
              <input
                className={styles.searchInput}
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
                className="btn btn--sm btn--secondary"
                type="button"
                disabled={busy}
                onClick={() => void runSearch(query)}
              >
                Search
              </button>
            </div>
            <div className={styles.results} data-testid="move-track-results">
              {searchError ? (
                <div className={clsx(styles.hint, styles.hintError)}>Error: {searchError}</div>
              ) : results === null ? (
                <div className={styles.hint}>Searching...</div>
              ) : results.length === 0 ? (
                <div className={styles.hint}>
                  {query.trim()
                    ? 'No tracks with a file match that. Try the artist or a shorter title.'
                    : 'Enter a title or artist to search.'}
                </div>
              ) : (
                results.map((song) => {
                  const id = String(song.track_id);
                  const isSelected = selected ? String(selected.track_id) === id : false;
                  const meta = `${song.artist_name || ''}${song.album_title ? ` · ${song.album_title}` : ''}`;
                  const quality = candidateQuality(song);
                  return (
                    <div
                      className={clsx(
                        styles.row,
                        isSelected && styles.rowSelected,
                        busy && styles.rowBusy,
                      )}
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
                      <span className={styles.radio} />
                      <span className={styles.rowMain}>
                        <span className={styles.rowTitle} title={song.title || undefined}>
                          {song.title || 'Unknown'}
                        </span>
                        <span className={styles.rowMeta} title={meta || undefined}>
                          {meta}
                        </span>
                        <span className={styles.rowPath} title={song.file_path}>
                          {song.file_path}
                        </span>
                      </span>
                      <span className={styles.rowSide}>
                        {song.duration ? <span>{formatClock(song.duration)}</span> : null}
                        {quality ? <span>{quality}</span> : null}
                      </span>
                    </div>
                  );
                })
              )}
            </div>
            {selected && busy && !preview ? (
              <div className={styles.note}>Working out what moves...</div>
            ) : null}
            {previewError ? (
              <div className={styles.error} role="alert">
                {previewError}
              </div>
            ) : null}
            {preview ? <MovePreview preview={preview} /> : null}
          </div>
          <div className={styles.footer}>
            <button
              className="btn btn--sm btn--secondary"
              type="button"
              disabled={busy}
              onClick={close}
            >
              Cancel
            </button>
            <button
              className="btn btn--sm btn--primary"
              type="button"
              disabled={!preview || busy}
              onClick={() => void move()}
            >
              {busy && preview ? 'Moving...' : 'Move here'}
            </button>
          </div>
        </div>
      </div>
    </BodyPortal>
  );
}

/** Provider and MusicBrainz ids: real changes, but nobody reads them first. */
export function isIdTag(field: string): boolean {
  return /\bid$/i.test(field.trim());
}

function MovePreview({ preview }: { preview: SlotMovePreview }) {
  const { source, path, tags } = preview;
  const [showIds, setShowIds] = useState(false);
  const named = tags.changes.filter((change) => !isIdTag(change.field));
  const ids = tags.changes.filter((change) => isIdTag(change.field));
  const rows = showIds ? [...named, ...ids] : named;
  return (
    <section className={styles.plan} data-testid="move-track-preview" aria-label="What happens">
      <h4 className={styles.planHeading}>What happens</h4>
      <p className={styles.planSentence}>
        &quot;{source.title}&quot; leaves {source.album_title || 'its album'} and fills this slot.
        {preview.source_album_left_empty
          ? ` ${source.album_title || 'Its old album'} has nothing else on it, so it leaves your library.`
          : ''}
      </p>
      <div>
        <div className={styles.planLabel}>File</div>
        {path.new && !path.unchanged ? (
          <dl className={styles.move} data-testid="move-track-file">
            <dt>From</dt>
            <dd title={path.current}>{path.current}</dd>
            <dt>To</dt>
            <dd className={styles.moveTo} title={path.new}>
              {path.new}
            </dd>
          </dl>
        ) : (
          <dl className={styles.move} data-testid="move-track-file">
            <dt>Stays at</dt>
            <dd title={path.current}>{path.current}</dd>
          </dl>
        )}
        {path.new && path.unchanged ? (
          <div className={styles.note}>It is already where this album puts it.</div>
        ) : null}
        {!path.new && path.reason ? <div className={styles.note}>{path.reason}</div> : null}
        {path.collision ? (
          <div className={styles.note}>
            Another file of this album is already at that path, so it stays.
          </div>
        ) : null}
      </div>
      <div>
        <div className={styles.planLabel}>Tags</div>
        {tags.changes.length ? (
          <>
            <table className={styles.tags} aria-label="Tags that change">
              <thead>
                <tr>
                  <th scope="col">Tag</th>
                  <th scope="col">Now → After</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((change) => (
                  <TagRow key={change.field} change={change} />
                ))}
              </tbody>
            </table>
            {ids.length ? (
              <button
                type="button"
                className={styles.more}
                aria-expanded={showIds}
                onClick={() => setShowIds((v) => !v)}
              >
                {showIds
                  ? 'Hide the ids'
                  : named.length
                    ? `and ${ids.length} ${ids.length === 1 ? 'id' : 'ids'}`
                    : `${ids.length} ${ids.length === 1 ? 'id changes' : 'ids change'}`}
              </button>
            ) : null}
          </>
        ) : (
          <div className={styles.note}>
            {tags.error ? `Tags: ${tags.error}` : 'The tags already match this slot.'}
          </div>
        )}
      </div>
    </section>
  );
}

function TagRow({ change }: { change: SlotMoveTagChange }) {
  return (
    <tr>
      <th scope="row">{change.field}</th>
      <td>
        <span className={styles.tagFrom}>{tagValue(change.from)}</span>
        <span className={styles.arrow}>→</span>
        <span>{tagValue(change.to)}</span>
      </td>
    </tr>
  );
}
