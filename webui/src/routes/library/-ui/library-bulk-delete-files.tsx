/**
 * #2333: the bulk bar's "Delete files…". It asks first, saying exactly what
 * goes: how many artists, albums and files, how big, and whether they can be
 * restored. A big delete asks for the word DELETE typed out as well.
 */

import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { DialogFrame, DialogHeader } from '@/components/dialog';

import {
  LIBRARY_V2_QUERY_KEY,
  previewLibraryV2BulkFileDelete,
  type LibraryV2BulkFileDeletePreview,
} from '../-library-v2.api';
import styles from './library-v2-page.module.css';

/** past this, the word has to be typed */
export const TYPE_TO_CONFIRM_ARTISTS = 5;
export const TYPE_TO_CONFIRM_FILES = 200;
export const CONFIRM_WORD = 'DELETE';

const plural = (n: number, one: string, many = `${one}s`) =>
  `${n.toLocaleString()} ${n === 1 ? one : many}`;

export function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unit = units[0];
  for (let i = 1; i < units.length && value >= 1024; i += 1) {
    value /= 1024;
    unit = units[i];
  }
  return `${value.toFixed(1)} ${unit}`;
}

export function deleteSummary(preview: LibraryV2BulkFileDeletePreview): string {
  return `Delete ${plural(preview.artists, 'artist')}: ${plural(preview.albums, 'album')}, ${plural(preview.files, 'file')}, ${formatSize(preview.total_size)}.`;
}

export function restoreNote(preview: LibraryV2BulkFileDeletePreview): string {
  if (preview.mode !== 'quarantine') return 'The files are deleted for good and can’t be restored.';
  if (preview.keep_days > 0)
    return `Files go to the deleted-files folder and can be restored for ${plural(preview.keep_days, 'day')}.`;
  return 'Files go to the deleted-files folder and can be restored from there until you empty it.';
}

export function needsTypedConfirm(preview: LibraryV2BulkFileDeletePreview): boolean {
  return preview.artists > TYPE_TO_CONFIRM_ARTISTS || preview.files > TYPE_TO_CONFIRM_FILES;
}

export function BulkDeleteFilesDialog({
  ids,
  onCancel,
  onConfirm,
}: {
  ids: number[];
  onCancel: () => void;
  /** the preview, so the bar can skip what it already knows is refused */
  onConfirm: (preview: LibraryV2BulkFileDeletePreview) => void;
}) {
  const [typed, setTyped] = useState('');
  const preview = useQuery({
    queryKey: [...LIBRARY_V2_QUERY_KEY, 'bulk-delete-files-preview', ids],
    queryFn: () => previewLibraryV2BulkFileDelete(ids),
    staleTime: 0,
    gcTime: 0,
  });
  const data = preview.data;
  const typedOk = !data || !needsTypedConfirm(data) || typed.trim() === CONFIRM_WORD;
  const deletable = data ? data.artists - data.blocked.length : 0;
  const canDelete = Boolean(data) && deletable > 0 && typedOk;

  return (
    <DialogFrame
      open
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
      className={`${styles.modal} ${styles.modalFramed}`}
    >
      <DialogHeader title="Delete files" closeLabel="Close" compact className={styles.dialogHead} />
      <div className={styles.modalContent}>
        {preview.isLoading ? (
          <p className={styles.muted}>Counting the files of {plural(ids.length, 'artist')}…</p>
        ) : null}
        {preview.isError ? (
          <div className={styles.mutationError} role="alert">
            {preview.error instanceof Error ? preview.error.message : 'Delete preview failed'}
          </div>
        ) : null}
        {data ? (
          <>
            <p>
              <strong>{deleteSummary(data)}</strong>
            </p>
            <div className={styles.fileRemovalSummary}>
              <span>
                <strong>{data.artists.toLocaleString()}</strong>{' '}
                {data.artists === 1 ? 'artist' : 'artists'}
              </span>
              <span>
                <strong>{data.albums.toLocaleString()}</strong>{' '}
                {data.albums === 1 ? 'album' : 'albums'}
              </span>
              <span>
                <strong>{data.files.toLocaleString()}</strong> {data.files === 1 ? 'file' : 'files'}
              </span>
              <span>
                <strong>{formatSize(data.total_size)}</strong> total
              </span>
            </div>
            <p>{restoreNote(data)}</p>
            <p>The artists and their releases come off your library and every watchlist.</p>
            {data.missing > 0 ? (
              <div className={styles.mutedNotice}>
                {plural(data.missing, 'file')} {data.missing === 1 ? 'is' : 'are'} already gone from
                disk; only {data.missing === 1 ? 'its entry goes' : 'their entries go'}.
              </div>
            ) : null}
            {data.blocked.length > 0 ? (
              <div className={styles.mutationError} role="alert">
                {plural(data.blocked.length, 'artist')} will be skipped and keep{' '}
                {data.blocked.length === 1 ? 'its' : 'their'} files:{' '}
                {data.blocked.map((b) => `${b.name} (${b.reason})`).join('; ')}
              </div>
            ) : null}
            {needsTypedConfirm(data) && deletable > 0 ? (
              <label className={styles.muted}>
                Type {CONFIRM_WORD} to confirm
                <input
                  type="text"
                  className={styles.searchInput}
                  aria-label={`Type ${CONFIRM_WORD} to confirm`}
                  autoComplete="off"
                  spellCheck={false}
                  value={typed}
                  onChange={(event) => setTyped(event.target.value)}
                />
              </label>
            ) : null}
          </>
        ) : null}
        <div className={styles.modalActions}>
          <button type="button" className={styles.btnGhost} onClick={onCancel}>
            Cancel
          </button>
          <button
            type="button"
            className={styles.btnDanger}
            disabled={!canDelete}
            onClick={() => data && onConfirm(data)}
          >
            {data ? `Delete ${plural(data.files, 'file')}` : 'Delete files'}
          </button>
        </div>
      </div>
    </DialogFrame>
  );
}
