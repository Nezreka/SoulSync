import { useState } from 'react';

import type { LibraryV2AlbumSummary } from '../-library-v2.types';

import styles from './library-v2-page.module.css';
import { LibraryToolDialog } from './tool-dialog';

export function ArtistMissingDownload({
  albums,
  artistName,
  disabled,
  onDownload,
}: {
  albums: LibraryV2AlbumSummary[];
  artistName: string;
  disabled: boolean;
  onDownload: (albumIds: number[]) => void;
}) {
  const missing = albums.filter((a) => a.tracks_missing > 0);
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  if (!missing.length) return null;
  return (
    <>
      <button
        type="button"
        className={styles.btnGhost}
        disabled={disabled}
        onClick={() => {
          setSelected(new Set(missing.map((a) => a.id)));
          setOpen(true);
        }}
      >
        Download missing
      </button>
      {open ? (
        <LibraryToolDialog
          title={`Download missing — ${artistName}`}
          description="Choose releases to download their missing tracks once. Your monitoring and quality profiles stay as set."
          onClose={() => setOpen(false)}
          fitContent
          footer={
            <div className={styles.modalActions}>
              <button type="button" className={styles.btnGhost} onClick={() => setOpen(false)}>
                Cancel
              </button>
              <button
                type="button"
                className={styles.btnPrimary}
                disabled={disabled || !selected.size}
                onClick={() => {
                  onDownload([...selected]);
                  setOpen(false);
                }}
              >
                Download selected
              </button>
            </div>
          }
        >
          <div className={styles.resultsWrap}>
            {missing.map((a) => (
              <label className={styles.previewToolbar} key={a.id}>
                <input
                  type="checkbox"
                  aria-label={a.title}
                  checked={selected.has(a.id)}
                  onChange={() =>
                    setSelected((previous) => {
                      const next = new Set(previous);
                      if (next.has(a.id)) next.delete(a.id);
                      else next.add(a.id);
                      return next;
                    })
                  }
                />
                <span>{a.title}</span>
                <span className={styles.previewQuiet}>{a.tracks_missing} missing</span>
              </label>
            ))}
          </div>
        </LibraryToolDialog>
      ) : null}
    </>
  );
}
