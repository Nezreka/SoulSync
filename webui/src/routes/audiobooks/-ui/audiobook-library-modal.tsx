import { useEffect, useMemo, useState } from 'react';

import type { AudiobookLibraryEntry } from '../-audiobooks.types';

import { deleteLibraryBook, fetchLibrary } from '../-audiobooks.api';
import { AudiobookOverlay } from './audiobook-overlay';
import styles from './audiobooks-page.module.css';

function gb(bytes: number): string {
  if (!bytes) return '';
  const value = bytes / 1024 ** 3;
  return value >= 1 ? `${value.toFixed(1)} GB` : `${Math.round(bytes / 1024 ** 2)} MB`;
}

function hours(minutes: number): string {
  if (!minutes) return '';
  const h = Math.floor(minutes / 60);
  const m = minutes % 60;
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

/**
 * What is actually on disk.
 *
 * Reads the library table the daily scan keeps honest, so this is what SoulSync
 * believes it has rather than a fresh walk of the folder — the same record that
 * decides the Owned badges, which means anything wrong here is visible and
 * fixable rather than hidden.
 *
 * Deleting sends the folder to the recycle bin, so a mis-click is recoverable
 * for as long as the keep window allows.
 */
export function AudiobookLibraryModal({ onClose }: { onClose: () => void }) {
  const [books, setBooks] = useState<AudiobookLibraryEntry[]>([]);
  const [totalBytes, setTotalBytes] = useState(0);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [message, setMessage] = useState('');
  const [filter, setFilter] = useState('');

  useEffect(() => {
    void (async () => {
      const { books: rows, totalBytes: total } = await fetchLibrary();
      setBooks(rows);
      setTotalBytes(total);
      setLoading(false);
    })();
  }, []);

  const shown = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return books;
    return books.filter(
      (b) =>
        b.title.toLowerCase().includes(needle) ||
        b.author.toLowerCase().includes(needle) ||
        b.series_title.toLowerCase().includes(needle),
    );
  }, [books, filter]);

  const remove = async (book: AudiobookLibraryEntry) => {
    const ok = await window.showConfirmDialog?.({
      title: 'Delete this book',
      message: `Delete "${book.title}" from disk? It goes to the recycle bin first, so you can put it back.`,
      confirmText: 'Delete',
      destructive: true,
    });
    if (ok === false) return;

    setBusy(book.asin);
    const result = await deleteLibraryBook(book.asin);
    setBusy('');
    if (result.ok) {
      setBooks((prev) => prev.filter((b) => b.asin !== book.asin));
      setMessage(
        result.recycled
          ? `"${book.title}" moved to the recycle bin.`
          : `"${book.title}" was deleted.`,
      );
    } else {
      setMessage(result.error || 'Could not delete that book.');
    }
  };

  return (
    <AudiobookOverlay onClose={onClose} label="Your audiobook library">
      <div className={styles.modal}>
        <header className={styles.modalHeader}>
          <div>
            <span className={styles.modalEyebrow}>Library</span>
            <h2 className={styles.modalTitle}>
              {books.length} {books.length === 1 ? 'book' : 'books'}
              {totalBytes > 0 && <span className={styles.librarySize}> · {gb(totalBytes)}</span>}
            </h2>
          </div>
          <button type="button" className={styles.modalClose} onClick={onClose} aria-label="Close">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M6 6l12 12M18 6L6 18"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            </svg>
          </button>
        </header>

        {message && <p className={styles.modalMessage}>{message}</p>}

        <div className={styles.modalBody}>
          {loading ? (
            <div className={styles.modalLoading}>
              <span className={styles.spinner} aria-hidden="true" />
              Reading your library…
            </div>
          ) : books.length === 0 ? (
            <div className={styles.emptyState}>
              <h3>Nothing here yet</h3>
              <p>
                Books appear once they have downloaded and been filed. The daily library scan also
                picks up anything already on disk that SoulSync imported before.
              </p>
            </div>
          ) : (
            <>
              {books.length > 6 && (
                <input
                  type="search"
                  className={styles.libraryFilter}
                  placeholder="Filter by title, author or series…"
                  value={filter}
                  onChange={(e) => setFilter(e.target.value)}
                />
              )}

              <ul className={styles.releaseList}>
                {shown.map((book) => (
                  <li className={styles.releaseRow} key={book.asin}>
                    <div className={styles.releaseMain}>
                      <span className={styles.releaseTitle}>{book.title}</span>
                      <div className={styles.releaseTags}>
                        {book.author && <span className={styles.releaseTag}>{book.author}</span>}
                        {book.series_title && (
                          <span className={styles.releaseTag}>
                            {book.series_title}
                            {book.series_sequence ? ` #${book.series_sequence}` : ''}
                          </span>
                        )}
                        {book.narrator && (
                          <span className={styles.releaseTag}>🎙️ {book.narrator}</span>
                        )}
                        {book.audio_format && (
                          <span className={styles.releaseTag}>
                            {book.audio_format.toUpperCase()}
                          </span>
                        )}
                        {book.file_count > 0 && (
                          <span className={styles.releaseTag}>
                            {book.file_count} {book.file_count === 1 ? 'file' : 'files'}
                          </span>
                        )}
                        {book.size_bytes > 0 && (
                          <span className={styles.releaseTag}>{gb(book.size_bytes)}</span>
                        )}
                        {book.runtime_minutes > 0 && (
                          <span className={styles.releaseTag}>{hours(book.runtime_minutes)}</span>
                        )}
                      </div>
                      <span className={styles.releaseReasons} title={book.path}>
                        {book.path}
                      </span>
                    </div>

                    <button
                      type="button"
                      className={styles.libraryDelete}
                      onClick={() => void remove(book)}
                      disabled={busy === book.asin}
                      title="Delete from disk. Goes to the recycle bin first."
                    >
                      {busy === book.asin ? 'Deleting…' : 'Delete'}
                    </button>
                  </li>
                ))}
              </ul>

              {shown.length === 0 && (
                <p className={styles.blocklistNote}>Nothing matches &quot;{filter}&quot;.</p>
              )}
            </>
          )}
        </div>
      </div>
    </AudiobookOverlay>
  );
}
